"""Tests for Capability 1.1 — Contradiction Reconciliation.

Coverage:
  - 4+ unit tests per reconciliation_basis path  (16 unit tests minimum)
  - 1 integration test through the pipeline reconciliation hook
  - 1 determinism test (10 identical runs → byte-identical audit_hash)
  - Edge-case tests (same memory twice, None inputs, non-overlapping classes)
"""
from __future__ import annotations

import time
import uuid

import pytest

from raven.reconciliation import (
    CLASS_RANK,
    DEFAULT_MEMORY_CLASSES,
    ReconciliationContext,
    compute_audit_hash,
    derive_memory_class,
    reconcile,
)
from raven.types import (
    CausalEdge,
    EvidenceNode,
    MemoryClass,
    MemoryEntry,
    ResolvedClaim,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _entry(
    id: str,
    text: str,
    *,
    days_ago: float = 1.0,
    memory_class: str = "contextual",
    entity_tags: list[str] | None = None,
    topic_tags: list[str] | None = None,
    validity_end: float | None = None,
) -> MemoryEntry:
    md = {"memory_class": memory_class}
    return MemoryEntry(
        id=id,
        text=text,
        timestamp=time.time() - days_ago * 86_400,
        source="test",
        entity_tags=entity_tags or [],
        topic_tags=topic_tags or [],
        confidence_at_ingest=1.0,
        validity_end=validity_end,
        metadata=md,
    )


# ── Rule (a): IDENTITY ──────────────────────────────────────────────────────


class TestIdentityRule:
    def test_identity_beats_contextual_on_shared_entity(self):
        a = _entry("a", "TJ goes by Sok casually", memory_class="contextual",
                   entity_tags=["TJ"])
        b = _entry("b", "TJ is Leland Jourdan officially", memory_class="identity",
                   entity_tags=["TJ"])
        ctx = ReconciliationContext(meteor_entities={"a": ["TJ"], "b": ["TJ"]})
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "identity"
        assert result.winner.id == "b"
        assert result.loser.id == "a"

    def test_identity_wins_even_when_older(self):
        # Identity memory is older but should still win
        a = _entry("a", "TJ is sometimes Sok", days_ago=1, memory_class="contextual",
                   entity_tags=["TJ"])
        b = _entry("b", "TJ is Leland Jourdan", days_ago=200, memory_class="identity",
                   entity_tags=["TJ"])
        ctx = ReconciliationContext(meteor_entities={"a": ["TJ"], "b": ["TJ"]})
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "identity"
        assert result.winner.id == "b"  # identity wins despite being older

    def test_identity_rule_skipped_when_no_entity_overlap(self):
        # Different entities → identity rule cannot fire
        a = _entry("a", "Bulma is sometimes BulmaSequel", memory_class="contextual",
                   entity_tags=["Bulma"])
        b = _entry("b", "Krillin is the agent identity", memory_class="identity",
                   entity_tags=["Krillin"])
        ctx = ReconciliationContext(meteor_entities={"a": ["Bulma"], "b": ["Krillin"]})
        result = reconcile(a, b, context=ctx)
        # Should fall through to next applicable rule. Same timestamps, same
        # depth, so eventually rule (d) fires on class rank.
        # Identity (b) > contextual (a), so b still wins by class rank.
        assert result is not None
        assert result.reconciliation_basis != "identity"
        assert result.winner.id == "b"

    def test_identity_evidence_chain_includes_meteor_node(self):
        a = _entry("a", "TJ casually as Sok", memory_class="contextual",
                   entity_tags=["TJ"])
        b = _entry("b", "TJ officially as Leland", memory_class="identity",
                   entity_tags=["TJ"])
        ctx = ReconciliationContext(meteor_entities={"a": ["TJ"], "b": ["TJ"]})
        result = reconcile(a, b, context=ctx)
        engines = {n.engine for n in result.evidence_chain}
        assert "meteor" in engines
        assert "pulsar" in engines

    def test_identity_rule_skipped_when_both_identity(self):
        # Both identity → identity rule does not fire (no winner this way).
        # Same timestamp, same depth, same class rank → returns None.
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="TJ alias one", timestamp=ts,
                        entity_tags=["TJ"], metadata={"memory_class": "identity"})
        b = MemoryEntry(id="b", text="TJ alias two", timestamp=ts,
                        entity_tags=["TJ"], metadata={"memory_class": "identity"})
        ctx = ReconciliationContext(meteor_entities={"a": ["TJ"], "b": ["TJ"]})
        result = reconcile(a, b, context=ctx)
        assert result is None  # all rules tied/inapplicable


# ── Rule (b): TEMPORAL ──────────────────────────────────────────────────────


class TestTemporalRule:
    def test_newer_wins_when_both_grounded(self):
        a = _entry("a", "old fact", days_ago=30)
        b = _entry("b", "new fact", days_ago=1)
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "temporal"
        assert result.winner.id == "b"

    def test_temporal_rule_records_delta_in_evidence(self):
        a = _entry("a", "old", days_ago=10)
        b = _entry("b", "new", days_ago=1)
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        engines = {n.engine for n in result.evidence_chain}
        assert "eclipse" in engines
        # finding string includes day delta
        finding = next(n.finding for n in result.evidence_chain if n.engine == "eclipse")
        assert "newer" in finding

    def test_temporal_skips_when_one_memory_stale(self):
        # b is newer but its validity_end is in the past → not well-grounded
        now = time.time()
        a = _entry("a", "old", days_ago=30)
        b = MemoryEntry(
            id="b", text="newer-but-expired",
            timestamp=now - 1 * 86_400,
            validity_end=now - 12 * 3600,  # expired 12h ago
            metadata={"memory_class": "contextual"},
        )
        ctx = ReconciliationContext(now=now)
        # Not well-grounded → temporal rule skipped, falls through. Same class
        # rank, no causal depth, so result should be None.
        result = reconcile(a, b, context=ctx)
        assert result is None

    def test_temporal_skips_on_equal_timestamps(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "contextual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "contextual"})
        ctx = ReconciliationContext()
        # Same timestamp → temporal skipped, falls through. No depth, same
        # class → None.
        result = reconcile(a, b, context=ctx)
        assert result is None

    def test_temporal_picks_b_consistently(self):
        # Reverse order check: order of (a,b) shouldn't change winner
        a = _entry("a", "newer", days_ago=1)
        b = _entry("b", "older", days_ago=30)
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        assert result.winner.id == "a"  # a is newer here
        # Reverse:
        result2 = reconcile(b, a, context=ctx)
        assert result2.winner.id == "a"


# ── Rule (c): EVIDENCE_STRENGTH ─────────────────────────────────────────────


class TestEvidenceStrengthRule:
    def test_deeper_chain_wins(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        # Two edges incident on a (weight 1.0 each), zero on b
        edges = [
            CausalEdge(from_id="x1", to_id="a", relation="caused", weight=1.0),
            CausalEdge(from_id="a", to_id="x2", relation="caused", weight=1.0),
        ]
        ctx = ReconciliationContext(causal_edges=edges)
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "evidence_strength"
        assert result.winner.id == "a"

    def test_evidence_evidence_chain_includes_nova(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        edges = [CausalEdge(from_id="x", to_id="a", relation="caused", weight=0.8)]
        ctx = ReconciliationContext(causal_edges=edges)
        result = reconcile(a, b, context=ctx)
        engines = {n.engine for n in result.evidence_chain}
        assert "nova" in engines

    def test_equal_depth_tie_falls_through(self):
        # Both have one edge → tie → falls through to importance. Same class,
        # same rank → None.
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        edges = [
            CausalEdge(from_id="x", to_id="a", relation="caused", weight=1.0),
            CausalEdge(from_id="x", to_id="b", relation="caused", weight=1.0),
        ]
        ctx = ReconciliationContext(causal_edges=edges)
        result = reconcile(a, b, context=ctx)
        assert result is None

    def test_weighted_depth_wins_over_count_tie(self):
        # Both have one edge, but a's edge is weight 0.9 vs b's 0.3.
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        edges = [
            CausalEdge(from_id="x", to_id="a", relation="caused", weight=0.9),
            CausalEdge(from_id="x", to_id="b", relation="caused", weight=0.3),
        ]
        ctx = ReconciliationContext(causal_edges=edges)
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "evidence_strength"
        assert result.winner.id == "a"

    def test_no_edges_skips_rule(self):
        # No edges → rule c yields tie, falls through. Different class so
        # rule d fires.
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "transactional"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        ctx = ReconciliationContext(causal_edges=[])
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "importance"
        assert result.winner.id == "b"  # factual > transactional


# ── Rule (d): IMPORTANCE ────────────────────────────────────────────────────


class TestImportanceRule:
    def test_factual_beats_transactional(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "transactional"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        assert result is not None
        assert result.reconciliation_basis == "importance"
        assert result.winner.id == "b"

    def test_preference_beats_contextual(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "contextual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "preference"})
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        assert result.reconciliation_basis == "importance"
        assert result.winner.id == "b"

    def test_class_rank_ordering_correct(self):
        assert CLASS_RANK["identity"] > CLASS_RANK["factual"]
        assert CLASS_RANK["factual"] > CLASS_RANK["preference"]
        assert CLASS_RANK["preference"] > CLASS_RANK["contextual"]
        assert CLASS_RANK["contextual"] > CLASS_RANK["transactional"]

    def test_tied_class_falls_through_to_none(self):
        # Same class, same time, no edges, no entity overlap → None
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        ctx = ReconciliationContext()
        assert reconcile(a, b, context=ctx) is None

    def test_importance_evidence_chain_includes_quasar(self):
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts, metadata={"memory_class": "transactional"})
        b = MemoryEntry(id="b", text="B", timestamp=ts, metadata={"memory_class": "factual"})
        ctx = ReconciliationContext()
        result = reconcile(a, b, context=ctx)
        engines = {n.engine for n in result.evidence_chain}
        assert "quasar" in engines


# ── Determinism ─────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_ten_identical_runs_produce_same_audit_hash(self):
        a = _entry("a", "old", days_ago=30)
        b = _entry("b", "new", days_ago=1)
        ctx = ReconciliationContext()
        hashes = [reconcile(a, b, context=ctx).audit_hash for _ in range(10)]
        assert len(set(hashes)) == 1, f"Non-determinism: got {set(hashes)}"

    def test_audit_hash_is_64_hex_chars(self):
        a = _entry("a", "old", days_ago=30)
        b = _entry("b", "new", days_ago=1)
        ctx = ReconciliationContext()
        h = reconcile(a, b, context=ctx).audit_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_inputs_yield_different_hashes(self):
        a = _entry("a", "old", days_ago=30)
        b = _entry("b", "new", days_ago=1)
        c = _entry("c", "newer", days_ago=0.5)
        ctx = ReconciliationContext()
        h_ab = reconcile(a, b, context=ctx).audit_hash
        h_ac = reconcile(a, c, context=ctx).audit_hash
        assert h_ab != h_ac

    def test_compute_audit_hash_sort_stability(self):
        # Same evidence in different order → same hash (sorted internally)
        ev1 = [
            EvidenceNode(engine="meteor", finding="a", score=1.0, timestamp=0),
            EvidenceNode(engine="nova", finding="b", score=1.0, timestamp=0),
        ]
        ev2 = [
            EvidenceNode(engine="nova", finding="b", score=1.0, timestamp=0),
            EvidenceNode(engine="meteor", finding="a", score=1.0, timestamp=0),
        ]
        h1 = compute_audit_hash("w", "l", "temporal", ev1)
        h2 = compute_audit_hash("w", "l", "temporal", ev2)
        assert h1 == h2


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_same_memory_twice_returns_none(self):
        a = _entry("a", "fact", days_ago=1)
        ctx = ReconciliationContext()
        assert reconcile(a, a, context=ctx) is None

    def test_none_input_raises_value_error(self):
        a = _entry("a", "fact", days_ago=1)
        ctx = ReconciliationContext()
        with pytest.raises(ValueError):
            reconcile(None, a, context=ctx)
        with pytest.raises(ValueError):
            reconcile(a, None, context=ctx)
        with pytest.raises(ValueError):
            reconcile(None, None, context=ctx)

    def test_no_overlap_no_tie_breakers_returns_none(self):
        # Different entities, same timestamp, same class, no edges → None
        ts = time.time() - 5 * 86_400
        a = MemoryEntry(id="a", text="A", timestamp=ts,
                        entity_tags=["X"], metadata={"memory_class": "factual"})
        b = MemoryEntry(id="b", text="B", timestamp=ts,
                        entity_tags=["Y"], metadata={"memory_class": "factual"})
        ctx = ReconciliationContext(meteor_entities={"a": ["X"], "b": ["Y"]})
        assert reconcile(a, b, context=ctx) is None

    def test_derive_memory_class_default(self):
        # No metadata, no topic_tags → contextual
        e = MemoryEntry(id="e", text="x", timestamp=0)
        cls = derive_memory_class(e)
        assert cls.name == "contextual"

    def test_derive_memory_class_from_metadata(self):
        e = MemoryEntry(id="e", text="x", timestamp=0, metadata={"memory_class": "factual"})
        assert derive_memory_class(e).name == "factual"

    def test_derive_memory_class_from_topic_tags(self):
        e = MemoryEntry(id="e", text="x", timestamp=0, topic_tags=["identity"])
        assert derive_memory_class(e).name == "identity"

    def test_derive_memory_class_override_wins(self):
        e = MemoryEntry(id="e", text="x", timestamp=0, metadata={"memory_class": "contextual"})
        assert derive_memory_class(e, override="identity").name == "identity"


# ── Integration test through pipeline hook ──────────────────────────────────


class TestPipelineHook:
    def test_reconcile_contradictions_returns_resolved_claims(self):
        from raven.pipeline import RAVENPipeline
        from raven.storage.store import RAVENStore

        store = RAVENStore(db_path=":memory:")
        p = RAVENPipeline(store)

        # Two contradictory entries (PULSAR-detectable)
        a = _entry("a", "RAVEN deploy must always run on Monday morning every week",
                   days_ago=14)
        b = _entry("b", "RAVEN deploy must never run on Monday morning every week",
                   days_ago=1)
        # Make them PULSAR-friendly
        results = p.reconcile_contradictions([a, b])
        assert len(results) >= 1
        rc = results[0]
        assert isinstance(rc, ResolvedClaim)
        # b is newer & well-grounded → temporal wins
        assert rc.winner.id == "b"
        assert rc.audit_hash  # populated

    def test_reconcile_contradictions_empty_list(self):
        from raven.pipeline import RAVENPipeline
        from raven.storage.store import RAVENStore

        p = RAVENPipeline(RAVENStore(db_path=":memory:"))
        assert p.reconcile_contradictions([]) == []

    def test_reconcile_contradictions_no_conflict(self):
        from raven.pipeline import RAVENPipeline
        from raven.storage.store import RAVENStore

        p = RAVENPipeline(RAVENStore(db_path=":memory:"))
        # Unrelated entries — PULSAR finds no contradiction
        a = _entry("a", "weather report Houston sunny", days_ago=1)
        b = _entry("b", "lunch meeting cafe Thursday", days_ago=2)
        assert p.reconcile_contradictions([a, b]) == []


# ── Corpus validation (sanity) ──────────────────────────────────────────────


class TestCorpus:
    """Smoke check that the sealed corpus is loadable and the manifest matches."""

    def test_corpus_manifest_loads_and_pairs_count(self):
        import json
        from pathlib import Path
        manifest_path = Path(__file__).resolve().parents[1] / "corpus" / "muninn_v2" / "reconciliation" / "manifest.json"
        if not manifest_path.exists():
            pytest.skip("corpus not built yet")
        manifest = json.loads(manifest_path.read_text())
        assert manifest["summary"]["pairs"] == 25
        per_basis = manifest["summary"]["per_basis"]
        assert sum(per_basis.values()) == 25
        for b in ("temporal", "importance", "evidence_strength", "identity"):
            assert per_basis[b] in (6, 7)
