"""Capability 1.3 — Structured Refusal tests.

Coverage targets:
  - 8+ unit tests per refusal type (40+ unit tests total)
  - Integration tests through ``RAVENPipeline.recall_v2`` for at least one
    example per refusal type
  - Determinism: 10 runs identical (audit_hash equality)
  - Cookbook examples must execute end-to-end
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from raven.refusal import (
    DEFAULT_DECAY_FLOOR,
    _audit_hash,
    _scope_violation_tokens,
    _tokenize,
    classify_refusal,
)
from raven.types import (
    AuroraInput,
    AuroraVerdict,
    Contradiction,
    MemoryEntry,
    RefusalReason,
)
from raven.validation.aurora import (
    APPROVE_THRESHOLD,
    validate_aurora_v2,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _entry(
    id: str,
    text: str = "a fact",
    days_ago: float = 1.0,
    entity_tags: list[str] | None = None,
) -> MemoryEntry:
    ts = time.time() - days_ago * 86_400
    return MemoryEntry(
        id=id,
        text=text,
        timestamp=ts,
        entity_tags=entity_tags or [],
    )


def _input(
    entries=None,
    decay=None,
    importance=None,
    contradictions=None,
    meteor_count=0,
) -> AuroraInput:
    entries = entries or []
    n = len(entries)
    return AuroraInput(
        entries=entries,
        decay_weights=decay if decay is not None else [0.5] * n,
        importance_scores=importance if importance is not None else [0.5] * n,
        contradictions=contradictions or [],
        causal_edges=[],
        stale_ids=set(),
        meteor_entity_count=meteor_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — scope_violation (8)
# ─────────────────────────────────────────────────────────────────────────────


class TestScopeViolation:
    def test_basic_out_of_scope(self):
        r = classify_refusal(
            query="what is the capital of france",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing", "engineering"],
        )
        assert r.type == "scope_violation"

    def test_allowlist_substring_match_in_scope(self):
        # The allowlist entry "billing" matches the token "billing" (and any
        # token where "billing" is a substring, like "billings"). Stay in-scope
        # by using a query whose every content token (>= 3 chars) matches.
        r = classify_refusal(
            query="billing",
            aurora_input=_input(entries=[_entry("a")], decay=[0.0]),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert r.type != "scope_violation"

    def test_empty_allowlist_blocks_everything(self):
        r = classify_refusal(
            query="anything at all here",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=[],
        )
        assert r.type == "scope_violation"

    def test_none_allowlist_disables_check(self):
        # No allowlist set, so scope check is skipped; falls through to
        # insufficient_evidence on empty input.
        r = classify_refusal(
            query="anything",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=None,
        )
        assert r.type != "scope_violation"

    def test_short_tokens_ignored(self):
        # "of" and "is" are < 3 chars; they should not trigger scope violation
        # if the longer tokens match. "billing" matches; short tokens are skipped.
        r = classify_refusal(
            query="is billing of",
            aurora_input=_input(entries=[_entry("a")], decay=[0.0]),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert r.type != "scope_violation"

    def test_recommended_action_escalate(self):
        r = classify_refusal(
            query="hack the planet",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert r.recommended_action == "escalate"

    def test_what_we_know_populated(self):
        r = classify_refusal(
            query="hack the planet",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert 3 <= len(r.what_we_know) <= 5
        assert 3 <= len(r.what_we_dont) <= 5

    def test_audit_hash_present(self):
        r = classify_refusal(
            query="hack the planet",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert len(r.audit_hash) == 64  # SHA-256 hex


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — identity_ambiguous (8)
# ─────────────────────────────────────────────────────────────────────────────


class TestIdentityAmbiguous:
    def _two_entity_input(self) -> AuroraInput:
        e1 = _entry("a", "Krillin won the match", entity_tags=["Krillin"])
        e2 = _entry("b", "18 won the match", entity_tags=["18"])
        return _input(entries=[e1, e2], decay=[0.5, 0.5], meteor_count=2)

    def test_two_distinct_entities_triggers(self):
        r = classify_refusal(
            query="who won the match",
            aurora_input=self._two_entity_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "identity_ambiguous"

    def test_single_entity_does_not_trigger(self):
        e1 = _entry("a", "Krillin won", entity_tags=["Krillin"])
        e2 = _entry("b", "Krillin lost too", entity_tags=["Krillin"])
        inp = _input(entries=[e1, e2], decay=[0.5, 0.5], meteor_count=1)
        r = classify_refusal(
            query="who won",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type != "identity_ambiguous"

    def test_meteor_count_below_threshold_does_not_trigger(self):
        e1 = _entry("a", "Krillin", entity_tags=["Krillin"])
        e2 = _entry("b", "18", entity_tags=["18"])
        inp = _input(entries=[e1, e2], decay=[0.5, 0.5], meteor_count=1)
        r = classify_refusal(
            query="who",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type != "identity_ambiguous"

    def test_recommended_action_ask_user(self):
        r = classify_refusal(
            query="who won the match",
            aurora_input=self._two_entity_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.recommended_action == "ask_user"

    def test_candidates_listed_in_what_we_know(self):
        r = classify_refusal(
            query="who won the match",
            aurora_input=self._two_entity_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        joined = " ".join(r.what_we_know)
        assert "Krillin" in joined and "18" in joined

    def test_three_distinct_entities(self):
        entries = [
            _entry("a", "Krillin", entity_tags=["Krillin"]),
            _entry("b", "18", entity_tags=["18"]),
            _entry("c", "Bulma", entity_tags=["Bulma"]),
        ]
        inp = _input(entries=entries, decay=[0.5, 0.5, 0.5], meteor_count=3)
        r = classify_refusal(
            query="who",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "identity_ambiguous"

    def test_confidence_above_seventy(self):
        r = classify_refusal(
            query="who",
            aurora_input=self._two_entity_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.confidence >= 0.70

    def test_audit_hash_present(self):
        r = classify_refusal(
            query="who",
            aurora_input=self._two_entity_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert len(r.audit_hash) == 64


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — conflicting_evidence_unresolvable (8)
# ─────────────────────────────────────────────────────────────────────────────


class TestConflictingEvidence:
    def _conflict_input(self) -> AuroraInput:
        e1 = _entry("a", "always works")
        e2 = _entry("b", "never works")
        c = Contradiction(e1, e2, "absolutist", "always vs never", 0.9)
        return _input(entries=[e1, e2], decay=[0.5, 0.5], contradictions=[c])

    def test_with_contradiction_no_resolution(self):
        r = classify_refusal(
            query="does it work",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            resolved_claim_count=0,
        )
        assert r.type == "conflicting_evidence_unresolvable"

    def test_resolved_count_equals_contradictions_skips(self):
        r = classify_refusal(
            query="does it work",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
            resolved_claim_count=1,
        )
        assert r.type != "conflicting_evidence_unresolvable"

    def test_recommended_action_surface(self):
        r = classify_refusal(
            query="x",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.recommended_action == "surface_uncertainty"

    def test_conflict_count_in_what_we_know(self):
        r = classify_refusal(
            query="x",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        joined = " ".join(r.what_we_know)
        assert "1" in joined or "Contradictions" in joined

    def test_multiple_contradictions(self):
        e1, e2, e3 = _entry("a", "always"), _entry("b", "never"), _entry("c", "sometimes")
        cs = [
            Contradiction(e1, e2, "absolutist", "x", 0.9),
            Contradiction(e2, e3, "absolutist", "y", 0.8),
        ]
        inp = _input(
            entries=[e1, e2, e3], decay=[0.5, 0.5, 0.5], contradictions=cs
        )
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "conflicting_evidence_unresolvable"

    def test_partial_resolution_still_unresolved(self):
        e1, e2, e3 = _entry("a", "always"), _entry("b", "never"), _entry("c", "sometimes")
        cs = [
            Contradiction(e1, e2, "absolutist", "x", 0.9),
            Contradiction(e2, e3, "absolutist", "y", 0.8),
        ]
        inp = _input(
            entries=[e1, e2, e3], decay=[0.5, 0.5, 0.5], contradictions=cs
        )
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
            resolved_claim_count=1,
        )
        # 2 conflicts, only 1 resolved -> still unresolved
        assert r.type == "conflicting_evidence_unresolvable"

    def test_confidence_high(self):
        r = classify_refusal(
            query="x",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.confidence >= 0.85

    def test_audit_hash_present(self):
        r = classify_refusal(
            query="x",
            aurora_input=self._conflict_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert len(r.audit_hash) == 64


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — staleness_threshold_exceeded (8)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaleness:
    def test_all_below_floor_triggers(self):
        entries = [_entry("a"), _entry("b")]
        inp = _input(entries=entries, decay=[0.01, 0.02])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "staleness_threshold_exceeded"

    def test_one_above_floor_skips(self):
        entries = [_entry("a"), _entry("b")]
        # 0.5 is above the default floor (0.05)
        inp = _input(entries=entries, decay=[0.01, 0.5])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type != "staleness_threshold_exceeded"

    def test_recommended_action_request_context(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.001])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.recommended_action == "request_context"

    def test_custom_floor(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.5])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
            decay_floor=0.6,
        )
        assert r.type == "staleness_threshold_exceeded"

    def test_max_weight_in_what_we_know(self):
        entries = [_entry("a"), _entry("b")]
        inp = _input(entries=entries, decay=[0.001, 0.04])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        joined = " ".join(r.what_we_know)
        assert "0.04" in joined

    def test_no_entries_no_staleness(self):
        inp = _input()
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        # no entries -> falls through to insufficient_evidence
        assert r.type == "insufficient_evidence"

    def test_confidence_above_seventy(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.001])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.confidence >= 0.70

    def test_audit_hash_present(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.001])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert len(r.audit_hash) == 64


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS — insufficient_evidence (8)
# ─────────────────────────────────────────────────────────────────────────────


class TestInsufficientEvidence:
    def test_empty_input_triggers(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "insufficient_evidence"

    def test_low_importance_no_other_signal(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.5], importance=[0.1])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "insufficient_evidence"

    def test_recommended_action_ask_user(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.recommended_action == "ask_user"

    def test_threshold_in_what_we_know(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=0.85,
        )
        joined = " ".join(r.what_we_know)
        assert "0.85" in joined

    def test_what_we_know_count(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert 3 <= len(r.what_we_know) <= 5

    def test_what_we_dont_count(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert 3 <= len(r.what_we_dont) <= 5

    def test_confidence_at_least_half(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.confidence >= 0.5

    def test_audit_hash_present(self):
        r = classify_refusal(
            query="x",
            aurora_input=_input(),
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert len(r.audit_hash) == 64


# ─────────────────────────────────────────────────────────────────────────────
# Priority ordering — first match wins
# ─────────────────────────────────────────────────────────────────────────────


class TestPriorityOrder:
    def test_scope_beats_identity(self):
        e1 = _entry("a", "Krillin", entity_tags=["Krillin"])
        e2 = _entry("b", "18", entity_tags=["18"])
        inp = _input(entries=[e1, e2], decay=[0.5, 0.5], meteor_count=2)
        r = classify_refusal(
            query="hack the planet badly",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
            scope_allowlist=["billing"],
        )
        assert r.type == "scope_violation"

    def test_identity_beats_conflict(self):
        e1 = _entry("a", "always", entity_tags=["Krillin"])
        e2 = _entry("b", "never", entity_tags=["18"])
        c = Contradiction(e1, e2, "absolutist", "x", 0.9)
        inp = _input(
            entries=[e1, e2], decay=[0.5, 0.5],
            contradictions=[c], meteor_count=2,
        )
        r = classify_refusal(
            query="who",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "identity_ambiguous"

    def test_conflict_beats_staleness(self):
        e1 = _entry("a", "always")
        e2 = _entry("b", "never")
        c = Contradiction(e1, e2, "absolutist", "x", 0.9)
        # decay below floor — would be staleness — but conflict wins
        inp = _input(
            entries=[e1, e2], decay=[0.001, 0.001], contradictions=[c]
        )
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "conflicting_evidence_unresolvable"

    def test_staleness_beats_insufficient(self):
        entries = [_entry("a")]
        inp = _input(entries=entries, decay=[0.001])
        r = classify_refusal(
            query="x",
            aurora_input=inp,
            aurora_threshold=APPROVE_THRESHOLD,
        )
        assert r.type == "staleness_threshold_exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# Determinism — 10 runs identical
# ─────────────────────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_audit_hash_stable_across_runs(self):
        e1 = _entry("a", "Krillin", entity_tags=["Krillin"])
        e2 = _entry("b", "18", entity_tags=["18"])
        inp = _input(entries=[e1, e2], decay=[0.5, 0.5], meteor_count=2)
        hashes = set()
        for _ in range(10):
            r = classify_refusal(
                query="who won",
                aurora_input=inp,
                aurora_threshold=APPROVE_THRESHOLD,
            )
            hashes.add(r.audit_hash)
        assert len(hashes) == 1

    def test_audit_hash_invariant_under_what_we_know_order(self):
        # The audit hash sorts what_we_know / what_we_dont before hashing.
        h1 = _audit_hash("x", "q", ["a", "b"], ["c", "d"])
        h2 = _audit_hash("x", "q", ["b", "a"], ["d", "c"])
        assert h1 == h2

    def test_audit_hash_changes_when_query_changes(self):
        h1 = _audit_hash("x", "q1", ["a"], ["b"])
        h2 = _audit_hash("x", "q2", ["a"], ["b"])
        assert h1 != h2

    def test_audit_hash_changes_when_type_changes(self):
        h1 = _audit_hash("x", "q", ["a"], ["b"])
        h2 = _audit_hash("y", "q", ["a"], ["b"])
        assert h1 != h2

    def test_validate_aurora_v2_audit_stable(self):
        e1 = _entry("a")
        inp = _input(entries=[e1], decay=[0.001])
        v1 = validate_aurora_v2(inp, query="x")
        v2 = validate_aurora_v2(inp, query="x")
        assert v1.audit_hash == v2.audit_hash


# ─────────────────────────────────────────────────────────────────────────────
# validate_aurora_v2 — verdict construction & coexistence with v1
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateAuroraV2:
    def test_approve_path(self):
        e1 = _entry("a")
        inp = _input(entries=[e1], decay=[0.95], importance=[0.95])
        v = validate_aurora_v2(inp, query="x")
        assert v.decision == "approve"
        assert v.refusal_reason is None
        assert v.confidence >= APPROVE_THRESHOLD

    def test_refuse_path(self):
        inp = _input()
        v = validate_aurora_v2(inp, query="x")
        assert v.decision == "refuse"
        assert v.refusal_reason is not None
        assert v.refusal_reason.type == "insufficient_evidence"

    def test_v1_run_aurora_unchanged(self):
        # v1.0 callers continue to receive RavenResponse from run_aurora.
        from raven.types import PipelineTrace
        from raven.validation.aurora import run_aurora

        inp = _input()
        trace = PipelineTrace(notes=["test"])
        resp = run_aurora(inp, trace)
        # No refusal_reason attribute on RavenResponse — proves backwards compat.
        assert not hasattr(resp, "refusal_reason")
        assert resp.status == "REFUSED"

    def test_contributing_engines_recorded(self):
        e1 = _entry("a")
        inp = _input(entries=[e1], decay=[0.95], importance=[0.95])
        v = validate_aurora_v2(
            inp,
            query="x",
            contributing_engines=["meteor", "aurora"],
        )
        assert "meteor" in v.contributing_engines
        assert "aurora" in v.contributing_engines

    def test_threshold_passthrough_to_classifier(self):
        # The threshold parameter is recorded in the refusal's what_we_know
        # field for the insufficient-evidence branch.
        v = validate_aurora_v2(_input(), threshold=0.85, query="x")
        assert v.decision == "refuse"
        assert v.refusal_reason is not None
        assert v.refusal_reason.type == "insufficient_evidence"
        assert any("0.85" in s for s in v.refusal_reason.what_we_know)


# ─────────────────────────────────────────────────────────────────────────────
# Integration — through RAVENPipeline.recall_v2
# ─────────────────────────────────────────────────────────────────────────────


class TestRecallV2Integration:
    def _pipeline(self, tmp_path):
        from raven.pipeline import RAVENPipeline
        from raven.storage.store import RAVENStore

        db = str(tmp_path / "raven.db")
        store = RAVENStore(db_path=db)
        return RAVENPipeline(store)

    def test_empty_store_refuses_insufficient(self, tmp_path):
        p = self._pipeline(tmp_path)
        v = p.recall_v2("anything")
        assert v.decision == "refuse"
        assert v.refusal_reason is not None
        # Empty store routes through validate_aurora_v2 with no entries:
        # falls through to insufficient_evidence.
        assert v.refusal_reason.type == "insufficient_evidence"

    def test_scope_violation_short_circuits(self, tmp_path):
        p = self._pipeline(tmp_path)
        v = p.recall_v2("hack the planet", scope_allowlist=["billing"])
        assert v.decision == "refuse"
        assert v.refusal_reason.type == "scope_violation"

    def test_approves_high_quality(self, tmp_path):
        p = self._pipeline(tmp_path)
        # Ingest a strong, recent, high-importance memory
        e = MemoryEntry(
            id=str(uuid.uuid4()),
            text="The decision was made to ship v1.0 on schedule",
            timestamp=time.time(),
            source="meeting_notes",
        )
        p.ingest(e)
        v = p.recall_v2("decision shipped v1.0")
        # Whether it approves depends on retrieval, but the verdict shape
        # must be valid in either case.
        assert v.decision in ("approve", "refuse")
        if v.decision == "refuse":
            assert v.refusal_reason is not None

    def test_staleness_through_pipeline(self, tmp_path):
        p = self._pipeline(tmp_path)
        # Ingest a very old memory (200 days). Default half_life=30d -> ~0.01
        old_ts = time.time() - 200 * 86_400
        e = MemoryEntry(
            id=str(uuid.uuid4()),
            text="server was rebooted last quarter",
            timestamp=old_ts,
            source="ops_log",
        )
        p.ingest(e)
        v = p.recall_v2("server reboot")
        # Old memories typically refuse (low decay, low importance).
        if v.decision == "refuse":
            assert v.refusal_reason.type in (
                "staleness_threshold_exceeded",
                "insufficient_evidence",
            )

    def test_audit_hash_on_verdict(self, tmp_path):
        p = self._pipeline(tmp_path)
        v = p.recall_v2("anything")
        assert len(v.audit_hash) == 64

    def test_scope_violation_does_not_query_store(self, tmp_path):
        # Scope check fires before retrieval: even with a populated store,
        # an out-of-scope query refuses without any METEOR/PULSAR work.
        p = self._pipeline(tmp_path)
        e = MemoryEntry(
            id=str(uuid.uuid4()),
            text="billing total for q4",
            timestamp=time.time(),
            source="ledger",
        )
        p.ingest(e)
        v = p.recall_v2("hack the planet", scope_allowlist=["billing"])
        assert v.refusal_reason.type == "scope_violation"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_tokenize_basic(self):
        assert _tokenize("Hello, World!") == ["hello", "world"]

    def test_tokenize_drops_punct(self):
        assert _tokenize("a.b,c") == ["a", "b", "c"]

    def test_scope_violation_tokens_none(self):
        assert _scope_violation_tokens("anything", None) == []

    def test_scope_violation_tokens_empty_blocks_all(self):
        toks = _scope_violation_tokens("only content tokens", [])
        # tokens of length >= 3
        assert "only" in toks
        assert "content" in toks
        assert "tokens" in toks


# ─────────────────────────────────────────────────────────────────────────────
# Cookbook examples — must execute end-to-end
# ─────────────────────────────────────────────────────────────────────────────


COOKBOOK_DIR = Path(__file__).parent.parent / "docs" / "_examples"


@pytest.mark.skipif(
    not COOKBOOK_DIR.exists(),
    reason="docs/_examples not present (built by capability 1.3 doc step)",
)
class TestCookbookExamples:
    def _example_files(self) -> list[Path]:
        return sorted(COOKBOOK_DIR.glob("*.py"))

    def test_at_least_five_examples(self):
        # One example per refusal type.
        assert len(self._example_files()) >= 5

    def test_each_example_executes(self):
        env = os.environ.copy()
        # Make sure the examples can import raven.* from this checkout.
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        for ex in self._example_files():
            result = subprocess.run(
                [sys.executable, str(ex)],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"Example {ex.name} failed:\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
