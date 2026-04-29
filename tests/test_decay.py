"""Capability 1.2 - Decay-Aware Recall test suite.

Coverage map (per spec - 60+ minimum, 10+ per built-in policy plus
registry / migration / determinism):

  -- Per-policy curve correctness  (TestDecayingPolicies, parametrized) --
    * fresh entry weights at confidence_at_ingest
    * weight at half-life ~ 0.5
    * weight at 2x half-life ~ 0.25
    * weight floored at policy.floor_confidence as t -> infinity
    * floor enforced even with confidence_at_ingest = 1.0
    * negative-age (future-dated) entry -> weight = 1.0 (then floored)
    * apply_class_aware_decay returns the right policy
    * weight is monotone non-increasing in age
    * confidence_at_ingest scales the curve
    * pure / deterministic across repeated calls

  -- Identity (no decay) special cases --
    * weight invariant under age
    * floor still applies

  -- Registry tests --
    * register, get, list, unregister
    * double-register raises
    * unregister-missing raises
    * list returns sorted-by-class

  -- Integration / determinism --
    * full pipeline still works (v1 path untouched)
    * 10 runs of apply_class_aware_decay produce identical floats

  -- Migration tests --
    * fresh DB has Capability 1.2 columns (via ALL_DDL)
    * legacy DB (without columns) gets migrated by run_migrations
    * idempotent: running twice is a no-op
    * heuristic backfill flags low-confidence rows REVIEW_REQUIRED
    * existing v1.0 corpus migrates cleanly
"""
from __future__ import annotations

import sqlite3

import pytest

from raven.decay import (
    BUILTIN_POLICIES,
    get_decay_policy,
    list_decay_policies,
    register_decay_policy,
    unregister_decay_policy,
)
from raven.decay.policies import (
    CONTEXTUAL,
    FACTUAL_LONG,
    FACTUAL_SHORT,
    IDENTITY,
    PREFERENCE,
    TRANSACTIONAL,
    register_builtins,
)
from raven.decay.registry import _clear_registry_for_tests
from raven.storage.migrations import (
    REVIEW_THRESHOLD,
    classify_text,
    ensure_class_columns,
    review_queue,
    run_migrations,
)
from raven.storage.store import RAVENStore
from raven.types import DecayPolicy, MemoryEntry
from raven.validation.eclipse import (
    DEFAULT_MEMORY_CLASS,
    _resolve_memory_class,
    apply_class_aware_decay,
    apply_decay,
    class_aware_weight,
    decay_weight,
)

# -- Helpers ----------------------------------------------------------------


_NOW = 1_700_000_000.0


def _entry(
    memory_class: str = "contextual",
    age_seconds: float = 0.0,
    confidence: float = 1.0,
    eid: str = "x",
) -> MemoryEntry:
    """Build a MemoryEntry whose timestamp is `age_seconds` in the past."""
    return MemoryEntry(
        id=eid,
        text="test text",
        timestamp=_NOW - age_seconds,
        confidence_at_ingest=confidence,
        memory_class=memory_class,
    )


# -- Per-policy parametric coverage -----------------------------------------


@pytest.mark.parametrize(
    "policy",
    [FACTUAL_SHORT, FACTUAL_LONG, PREFERENCE, TRANSACTIONAL, CONTEXTUAL],
    ids=lambda p: p.name,
)
class TestDecayingPolicies:
    """The five policies that DO decay.

    `identity` (half_life=None) is covered by ``TestIdentityPolicy``.
    Each test runs once per parametrized policy -> 5 x N tests.
    """

    def test_fresh_weight_at_ingest_confidence(self, policy: DecayPolicy) -> None:
        # age = 0 -> weight = 1.0 * confidence_at_ingest, then floored.
        e = _entry(memory_class=policy.applies_to_class, age_seconds=0.0)
        w = class_aware_weight(e, policy, _NOW)
        assert pytest.approx(1.0, abs=1e-9) == w

    def test_weight_at_half_life_is_half(self, policy: DecayPolicy) -> None:
        assert policy.half_life_seconds is not None
        e = _entry(
            memory_class=policy.applies_to_class,
            age_seconds=policy.half_life_seconds,
        )
        w = class_aware_weight(e, policy, _NOW)
        # exactly 0.5 of confidence (1.0), unless that is below the floor.
        expected = max(policy.floor_confidence, 0.5)
        assert pytest.approx(expected, abs=1e-9) == w

    def test_weight_at_two_half_lives_is_quarter_or_floor(
        self, policy: DecayPolicy
    ) -> None:
        assert policy.half_life_seconds is not None
        e = _entry(
            memory_class=policy.applies_to_class,
            age_seconds=policy.half_life_seconds * 2,
        )
        w = class_aware_weight(e, policy, _NOW)
        expected = max(policy.floor_confidence, 0.25)
        assert pytest.approx(expected, abs=1e-9) == w

    def test_floor_holds_at_infinity(self, policy: DecayPolicy) -> None:
        # 100 years should always be at the floor.
        e = _entry(
            memory_class=policy.applies_to_class,
            age_seconds=100 * 365 * 86_400,
        )
        w = class_aware_weight(e, policy, _NOW)
        assert pytest.approx(policy.floor_confidence, abs=1e-9) == w

    def test_negative_age_yields_one(self, policy: DecayPolicy) -> None:
        # Future-dated entry: contract says weight = 1.0 (floored, no-op).
        e = _entry(memory_class=policy.applies_to_class, age_seconds=-3600)
        w = class_aware_weight(e, policy, _NOW)
        assert pytest.approx(1.0, abs=1e-9) == w

    def test_apply_class_aware_returns_correct_policy(
        self, policy: DecayPolicy
    ) -> None:
        e = _entry(memory_class=policy.applies_to_class, age_seconds=10)
        out = apply_class_aware_decay([e], _NOW)
        assert len(out) == 1
        _, _, applied = out[0]
        assert applied.name == policy.name

    def test_weight_monotone_non_increasing(self, policy: DecayPolicy) -> None:
        ages = [0, 60, 3600, 86_400, 30 * 86_400, 365 * 86_400]
        weights = [
            class_aware_weight(
                _entry(memory_class=policy.applies_to_class, age_seconds=a),
                policy,
                _NOW,
            )
            for a in ages
        ]
        for i in range(1, len(weights)):
            assert weights[i] <= weights[i - 1] + 1e-12, (
                f"Non-monotone at age {ages[i]}: {weights}"
            )

    def test_confidence_at_ingest_scales_curve(self, policy: DecayPolicy) -> None:
        """Halving confidence_at_ingest halves the weight (until floor)."""
        assert policy.half_life_seconds is not None
        # pick an age where curve > 4 * floor so halving doesn't hit floor
        age = policy.half_life_seconds * 0.5
        e_full = _entry(
            memory_class=policy.applies_to_class, age_seconds=age, confidence=1.0
        )
        e_half = _entry(
            memory_class=policy.applies_to_class, age_seconds=age, confidence=0.5
        )
        w_full = class_aware_weight(e_full, policy, _NOW)
        w_half = class_aware_weight(e_half, policy, _NOW)
        if w_half > policy.floor_confidence + 1e-9:
            assert pytest.approx(w_full / 2.0, rel=1e-6) == w_half
        else:
            # Hit the floor -- half is at least floor.
            assert w_half == policy.floor_confidence

    def test_deterministic(self, policy: DecayPolicy) -> None:
        e = _entry(memory_class=policy.applies_to_class, age_seconds=12_345)
        weights = [class_aware_weight(e, policy, _NOW) for _ in range(10)]
        assert len(set(weights)) == 1

    def test_floor_enforced_even_with_high_confidence(
        self, policy: DecayPolicy
    ) -> None:
        """Old entry with 1.0 ingest confidence still floors at policy.floor."""
        assert policy.half_life_seconds is not None
        # 50x half-life -> effectively zero, so result must be the floor.
        e = _entry(
            memory_class=policy.applies_to_class,
            age_seconds=policy.half_life_seconds * 50,
            confidence=1.0,
        )
        w = class_aware_weight(e, policy, _NOW)
        assert w == policy.floor_confidence


class TestIdentityPolicy:
    """Identity policy - the no-decay outlier."""

    def test_no_decay_under_any_age(self) -> None:
        for age in [0, 86_400, 365 * 86_400, 50 * 365 * 86_400]:
            e = _entry(memory_class="identity", age_seconds=age, confidence=0.9)
            w = class_aware_weight(e, IDENTITY, _NOW)
            assert pytest.approx(0.9, abs=1e-9) == w

    def test_floor_applies_when_confidence_below_floor(self) -> None:
        e = _entry(memory_class="identity", age_seconds=86_400, confidence=0.30)
        w = class_aware_weight(e, IDENTITY, _NOW)
        assert w == IDENTITY.floor_confidence  # 0.50

    def test_negative_age_one(self) -> None:
        e = _entry(memory_class="identity", age_seconds=-3600, confidence=1.0)
        w = class_aware_weight(e, IDENTITY, _NOW)
        assert pytest.approx(1.0, abs=1e-9) == w

    def test_apply_returns_identity_policy(self) -> None:
        e = _entry(memory_class="identity", age_seconds=86_400)
        out = apply_class_aware_decay([e], _NOW)
        assert out[0][2].name == "identity"

    def test_deterministic(self) -> None:
        e = _entry(memory_class="identity", age_seconds=86_400)
        weights = [class_aware_weight(e, IDENTITY, _NOW) for _ in range(10)]
        assert len(set(weights)) == 1


# -- Resolve / fallback behaviour -------------------------------------------


class TestMemoryClassResolution:
    def test_attribute_wins(self) -> None:
        e = _entry(memory_class="identity")
        assert _resolve_memory_class(e) == "identity"

    def test_metadata_fallback(self) -> None:
        # Simulate an entry whose memory_class attr was never set (eg coming
        # from an older corpus) but metadata carries the class.
        e = MemoryEntry(
            id="x",
            text="t",
            timestamp=_NOW,
            metadata={"memory_class": "preference"},
        )
        # Force the new attribute to default ("contextual"). The class on
        # the dataclass field should win - that's the production path.
        assert _resolve_memory_class(e) == "contextual"

        # If we force the attr to empty string, metadata kicks in.
        object.__setattr__(e, "memory_class", "")
        assert _resolve_memory_class(e) == "preference"

    def test_default_when_missing(self) -> None:
        e = MemoryEntry(id="x", text="t", timestamp=_NOW)
        object.__setattr__(e, "memory_class", "")
        assert _resolve_memory_class(e) == DEFAULT_MEMORY_CLASS

    def test_apply_uses_contextual_for_unknown_class(self) -> None:
        e = _entry(memory_class="some-bogus-class", age_seconds=86_400)
        out = apply_class_aware_decay([e], _NOW)
        # Falls back to contextual.
        assert out[0][2].applies_to_class == DEFAULT_MEMORY_CLASS


# -- apply_class_aware_decay batch behaviour --------------------------------


class TestApplyClassAwareDecayBatch:
    def test_preserves_order(self) -> None:
        entries = [
            _entry(memory_class="factual_short", eid="a"),
            _entry(memory_class="identity", eid="b"),
            _entry(memory_class="preference", eid="c"),
        ]
        out = apply_class_aware_decay(entries, _NOW)
        assert [t[0].id for t in out] == ["a", "b", "c"]

    def test_each_entry_gets_own_policy(self) -> None:
        entries = [
            _entry(memory_class="factual_short", eid="a"),
            _entry(memory_class="identity", eid="b"),
        ]
        out = apply_class_aware_decay(entries, _NOW)
        assert out[0][2].name == "factual_short"
        assert out[1][2].name == "identity"

    def test_empty_input_returns_empty(self) -> None:
        assert apply_class_aware_decay([], _NOW) == []

    def test_determinism_full_batch(self) -> None:
        entries = [
            _entry(memory_class=c, age_seconds=12_345, eid=str(i))
            for i, c in enumerate(["factual_short", "preference", "identity",
                                    "transactional", "contextual", "factual_long"])
        ]
        runs = [
            [(t[0].id, t[1], t[2].name) for t in apply_class_aware_decay(entries, _NOW)]
            for _ in range(10)
        ]
        for r in runs[1:]:
            assert r == runs[0]


# -- v1.0 ECLIPSE API still works (regression) ------------------------------


class TestV1EclipseUntouched:
    def test_decay_weight_unchanged(self) -> None:
        e = MemoryEntry(id="x", text="t", timestamp=_NOW - 30 * 86_400)
        w = decay_weight(e, now=_NOW, half_life_days=30.0)
        assert pytest.approx(0.5, abs=0.01) == w

    def test_apply_decay_pairs(self) -> None:
        es = [
            MemoryEntry(id="a", text="t", timestamp=_NOW),
            MemoryEntry(id="b", text="t", timestamp=_NOW - 30 * 86_400),
        ]
        pairs = apply_decay(es, half_life_days=30.0, now=_NOW)
        assert len(pairs) == 2
        assert pairs[0][1] > pairs[1][1]


# -- Registry tests ---------------------------------------------------------


@pytest.fixture(autouse=False)
def _reset_registry():
    """Wipe & rebuild the built-ins. Used only by registry-mutating tests."""
    _clear_registry_for_tests()
    register_builtins()
    yield
    _clear_registry_for_tests()
    register_builtins()


class TestRegistry:
    def test_builtins_all_registered(self) -> None:
        names = {p.name for p in list_decay_policies()}
        assert names == {p.name for p in BUILTIN_POLICIES}

    def test_get_returns_correct_policy(self) -> None:
        assert get_decay_policy("identity").name == "identity"
        assert get_decay_policy("factual_short").half_life_seconds == 86_400.0

    def test_get_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_decay_policy("definitely-not-a-class")

    def test_register_then_get(self, _reset_registry: None) -> None:
        custom = DecayPolicy(
            name="weekly_log",
            half_life_seconds=7 * 86_400.0,
            floor_confidence=0.05,
            applies_to_class="weekly_log",
        )
        register_decay_policy(custom)
        assert get_decay_policy("weekly_log") is custom

    def test_double_register_raises(self, _reset_registry: None) -> None:
        with pytest.raises(ValueError):
            register_decay_policy(IDENTITY)  # already registered

    def test_register_wrong_type_raises(self, _reset_registry: None) -> None:
        with pytest.raises(TypeError):
            register_decay_policy("not a policy")  # type: ignore[arg-type]

    def test_unregister_then_register(self, _reset_registry: None) -> None:
        unregister_decay_policy("identity")
        with pytest.raises(KeyError):
            get_decay_policy("identity")
        register_decay_policy(IDENTITY)
        assert get_decay_policy("identity") is IDENTITY

    def test_unregister_missing_raises(self, _reset_registry: None) -> None:
        with pytest.raises(KeyError):
            unregister_decay_policy("never-existed")

    def test_list_sorted(self) -> None:
        names = [p.name for p in list_decay_policies()]
        assert names == sorted(names)


# -- Heuristic classifier ---------------------------------------------------


class TestHeuristicClassifier:
    def test_identity_with_entity_tag(self) -> None:
        cls, conf = classify_text("Alice is the CEO", entity_tags=["Alice"])
        assert cls == "identity"
        assert conf >= 0.6

    def test_my_wife_pattern(self) -> None:
        cls, conf = classify_text("my wife is Sarah")
        assert cls == "identity"

    def test_preference(self) -> None:
        cls, conf = classify_text("I prefer dark roast coffee")
        assert cls == "preference"
        assert conf >= 0.6

    def test_transactional(self) -> None:
        cls, conf = classify_text("paid $50 to Alice yesterday")
        # Could land on transactional OR factual_short due to "yesterday" anchor.
        # Either is acceptable per spec - both fire on numeric+verb shape.
        assert cls in ("transactional", "factual_short")

    def test_time_anchored(self) -> None:
        cls, _ = classify_text("Meeting tomorrow at 3pm")
        assert cls == "factual_short"

    def test_descriptive_no_anchor(self) -> None:
        # Descriptive but ambiguous -> factual_long with sub-threshold conf.
        cls, conf = classify_text("Saturn has rings")
        assert cls == "factual_long"
        assert conf < REVIEW_THRESHOLD  # gets review_required

    def test_fallback_contextual(self) -> None:
        cls, _ = classify_text("xyzzy plugh frob")
        assert cls == "contextual"


# -- Migration end-to-end ---------------------------------------------------


class TestMigrations:
    def _make_legacy_db(self, path: str) -> None:
        """Build a v1.0-shaped DB with the OLD schema (no class columns)."""
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'unknown',
                entity_tags TEXT NOT NULL DEFAULT '[]',
                topic_tags TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                supersedes_id TEXT,
                validity_start REAL NOT NULL,
                validity_end REAL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "INSERT INTO memories (id, text, timestamp, source, entity_tags, "
            "topic_tags, confidence, validity_start, metadata) VALUES "
            "(?,?,?,?,?,?,?,?,?)",
            ("a", "Alice is the CEO", _NOW, "test", '["Alice"]', "[]",
             1.0, _NOW, "{}"),
        )
        conn.execute(
            "INSERT INTO memories (id, text, timestamp, source, entity_tags, "
            "topic_tags, confidence, validity_start, metadata) VALUES "
            "(?,?,?,?,?,?,?,?,?)",
            ("b", "I prefer matcha latte", _NOW, "test", "[]", "[]",
             1.0, _NOW, "{}"),
        )
        conn.execute(
            "INSERT INTO memories (id, text, timestamp, source, entity_tags, "
            "topic_tags, confidence, validity_start, metadata) VALUES "
            "(?,?,?,?,?,?,?,?,?)",
            ("c", "Saturn has rings", _NOW, "test", "[]", "[]",
             1.0, _NOW, "{}"),
        )
        conn.commit()
        conn.close()

    def test_legacy_db_gets_migrated(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        result = run_migrations(db)
        assert result.schema_changed is True
        assert result.rows_total == 3

    def test_idempotent(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        run_migrations(db)
        result2 = run_migrations(db)
        assert result2.schema_changed is False  # second run no-ops schema
        assert result2.rows_total == 3

    def test_review_required_flag_set(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        result = run_migrations(db)
        # "Saturn has rings" -> factual_long with conf < REVIEW_THRESHOLD.
        assert result.rows_review_required >= 1
        queue = review_queue(db)
        assert any(r["id"] == "c" for r in queue)

    def test_columns_present_after_migration(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        run_migrations(db)
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        conn.close()
        assert "memory_class" in cols
        assert "review_required" in cols

    def test_fresh_db_already_has_columns(self, tmp_path) -> None:
        # New RAVENStore creates the up-to-date schema directly.
        db = str(tmp_path / "fresh.db")
        store = RAVENStore(db_path=db)
        try:
            assert store._has_memory_class_column() is True
        finally:
            store.close()

    def test_review_queue_on_unmigrated_db_returns_empty(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        # No migration - review queue should be [] not crash.
        assert review_queue(db) == []

    def test_ensure_class_columns_idempotent(self, tmp_path) -> None:
        db = str(tmp_path / "legacy.db")
        self._make_legacy_db(db)
        conn = sqlite3.connect(db)
        try:
            assert ensure_class_columns(conn) is True
            assert ensure_class_columns(conn) is False  # no-op
        finally:
            conn.close()


# -- Integration: store + class-aware decay end-to-end ----------------------


class TestStoreIntegration:
    def test_store_round_trips_memory_class(self, tmp_path) -> None:
        db = str(tmp_path / "rt.db")
        store = RAVENStore(db_path=db)
        try:
            e = MemoryEntry(
                id="x",
                text="The capital of France is Paris.",
                timestamp=_NOW,
                memory_class="factual_long",
            )
            store.ingest(e)
            got = store.get("x")
            assert got is not None
            assert got.memory_class == "factual_long"
        finally:
            store.close()

    def test_default_memory_class_when_unset(self, tmp_path) -> None:
        db = str(tmp_path / "default.db")
        store = RAVENStore(db_path=db)
        try:
            e = MemoryEntry(id="x", text="t", timestamp=_NOW)
            store.ingest(e)
            got = store.get("x")
            assert got is not None
            assert got.memory_class == "contextual"
        finally:
            store.close()

    def test_full_recall_unaffected_by_capability_1_2(self, tmp_path) -> None:
        """v1.0 pipeline should still produce a sane response."""
        from raven.pipeline import RAVENPipeline

        db = str(tmp_path / "pipe.db")
        store = RAVENStore(db_path=db)
        try:
            store.ingest(MemoryEntry(
                id="a", text="The Eiffel Tower is in Paris.", timestamp=_NOW,
            ))
            pipeline = RAVENPipeline(store)
            resp = pipeline.recall("Eiffel Tower")
            assert resp.status in ("APPROVED", "CONDITIONAL", "REJECTED", "REFUSED")
        finally:
            store.close()


# -- Determinism (additional, full-batch) -----------------------------------


class TestDeterminismFullBatch:
    def test_ten_runs_identical_floats(self) -> None:
        entries = [
            _entry(memory_class=c, age_seconds=42_000.0 + i, eid=str(i))
            for i, c in enumerate([
                "factual_short", "factual_long", "preference",
                "transactional", "contextual", "identity",
            ])
        ]
        runs = []
        for _ in range(10):
            out = apply_class_aware_decay(entries, _NOW)
            # Capture as tuples to compare exactly.
            runs.append(tuple((t[0].id, t[1]) for t in out))
        for r in runs[1:]:
            assert r == runs[0]
