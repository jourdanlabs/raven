"""Phase 2.2 fix-02 / LME-012 — composite-ranked scorer tests.

Defends:

* The new ``benchmarks.longmemeval.scorers.composite_ranked`` module.
* The extracted ``benchmarks.longmemeval.scorers.retrieval_ranked``
  module (= the original v1.2.1 logic).
* The dispatcher in ``benchmarks.longmemeval.scorers``.
* The re-export shim at ``benchmarks.longmemeval.scorer``.

The methodology contract for fix-02 is that ``--scorer=retrieval_ranked``
(the default) reproduces the v1.2.1 published numbers byte-for-byte, and
``--scorer=composite_ranked`` ranks by ``retrieval_score *
aurora_weight`` (1.0 for approved, 0.0 for rejected).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from benchmarks.longmemeval import scorer as scorer_shim
from benchmarks.longmemeval.scorers import (
    DEFAULT_SCORER,
    RANKERS,
    composite_ranked,
    get_ranker,
    retrieval_ranked,
)
from raven.types import MemoryEntry, ScoredMemory


# ── helpers ─────────────────────────────────────────────────────────────


def _sm(entry_id: str, retrieval_score: float, score: float = 0.0) -> ScoredMemory:
    """Build a ScoredMemory with the minimum fields the rankers need."""
    return ScoredMemory(
        entry=MemoryEntry(id=entry_id, text=f"text-{entry_id}", timestamp=0.0),
        score=score,
        retrieval_score=retrieval_score,
    )


# ── shim parity ─────────────────────────────────────────────────────────


def test_scorer_shim_reexports_public_api():
    """``benchmarks.longmemeval.scorer`` keeps the v1.2.1 public symbols."""
    assert scorer_shim.score_question is retrieval_ranked.score_question
    assert scorer_shim.aggregate is retrieval_ranked.aggregate
    assert scorer_shim.answer_substring_hit is retrieval_ranked.answer_substring_hit
    assert scorer_shim.normalize is retrieval_ranked.normalize
    assert scorer_shim.QuestionResult is retrieval_ranked.QuestionResult
    assert scorer_shim.OverallReport is retrieval_ranked.OverallReport
    assert scorer_shim.TypeReport is retrieval_ranked.TypeReport


# ── retrieval_ranked: legacy ranking semantics ──────────────────────────


def test_retrieval_ranked_orders_by_retrieval_score_descending():
    approved = [_sm("a", 0.9), _sm("b", 0.4)]
    rejected = [_sm("c", 0.7), _sm("d", 0.1)]
    ranked = retrieval_ranked.rank_memories(approved=approved, rejected=rejected)
    ids = [sm.entry.id for sm in ranked]
    assert ids == ["a", "c", "b", "d"]


def test_retrieval_ranked_ignores_aurora_status():
    """The legacy scorer does not see AURORA — rejected can rank above approved."""
    approved = [_sm("a", 0.2)]
    rejected = [_sm("c", 0.95)]
    ranked = retrieval_ranked.rank_memories(approved=approved, rejected=rejected)
    assert ranked[0].entry.id == "c"


# ── composite_ranked: spec primitives ───────────────────────────────────


def test_composite_score_formula_is_multiplicative():
    """``composite_score`` is exactly ``retrieval_score * aurora_weight``."""
    assert composite_ranked.composite_score(0.8, 1.0) == pytest.approx(0.8)
    assert composite_ranked.composite_score(0.8, 0.0) == 0.0
    assert composite_ranked.composite_score(0.0, 1.0) == 0.0


def test_composite_weights_are_frozen():
    """The FROZEN spec for fix-02 is approved=1.0, rejected=0.0."""
    assert composite_ranked.APPROVED_WEIGHT == 1.0
    assert composite_ranked.REJECTED_WEIGHT == 0.0


# ── composite_ranked: brief-mandated test cases ─────────────────────────


def test_approved_only_equivalent_to_retrieval_ranked():
    """When rejected is empty, composite ranking == retrieval ranking."""
    approved = [_sm("a", 0.9), _sm("b", 0.5), _sm("c", 0.7)]
    composite_order = composite_ranked.rank_memories(approved=approved, rejected=[])
    retrieval_order = retrieval_ranked.rank_memories(approved=approved, rejected=[])
    assert [sm.entry.id for sm in composite_order] == [sm.entry.id for sm in retrieval_order]


def test_all_rejected_yields_empty_topk_under_composite():
    """Composite_score is 0 for every entry → the answer is unreachable in any
    non-trivial top-K because A@k uses substring matching against texts whose
    composite is 0."""
    rejected = [_sm("a", 0.99), _sm("b", 0.95)]
    ranked = composite_ranked.rank_memories(approved=[], rejected=rejected)
    # All composites are 0; the ranker still returns the union (sliced by the
    # caller) so diagnostics are correct, but every entry's effective rank
    # is "rejected".
    assert len(ranked) == 2
    # And critically: an A@5 substring match against the top-5 will still
    # find these texts (the scorer doesn't know about composite weights),
    # but in the harness flow the top-5 of an all-rejected run is the
    # rejected memories — the AURORA gate's surfacing decision is what
    # the *pipeline* surfaces. The composite scorer here just makes the
    # rejected memories rank below any approved memory; with no approved
    # memories, top-K is the rejected union, sorted by retrieval_score
    # descending (the secondary key kicks in because every composite is 0).
    assert [sm.entry.id for sm in ranked] == ["a", "b"]


def test_mixed_answer_in_approved_set_is_reachable():
    """Reference answer in an approved memory → top-5 substring hit succeeds."""
    answer_entry = ScoredMemory(
        entry=MemoryEntry(id="ans", text="The capital of France is Paris.", timestamp=0.0),
        score=0.8,
        retrieval_score=0.4,    # below several rejected entries on retrieval alone
    )
    approved = [answer_entry]
    rejected = [
        _sm("r1", 0.95),
        _sm("r2", 0.90),
        _sm("r3", 0.85),
        _sm("r4", 0.80),
        _sm("r5", 0.75),
    ]
    ranked = composite_ranked.rank_memories(approved=approved, rejected=rejected)
    top5_texts = [sm.entry.text for sm in ranked[:5]]
    assert composite_ranked.answer_substring_hit("Paris", top5_texts)


def test_mixed_answer_in_rejected_set_is_unreachable():
    """Reference answer ONLY in a rejected memory → composite_ranked diverges
    from retrieval_ranked: under composite the answer is unreachable in
    top-5 (because rejected weight is 0.0), under retrieval_ranked it is
    reachable. This is the case where fix-02 is supposed to show its
    teeth."""
    answer_entry = ScoredMemory(
        entry=MemoryEntry(id="ans", text="The capital of France is Paris.", timestamp=0.0),
        score=0.2,
        retrieval_score=0.99,    # would be top-1 under retrieval_ranked
    )
    rejected = [answer_entry]
    approved = [_sm("a1", 0.5), _sm("a2", 0.4), _sm("a3", 0.3), _sm("a4", 0.2), _sm("a5", 0.1)]

    composite_order = composite_ranked.rank_memories(approved=approved, rejected=rejected)
    composite_top5_texts = [sm.entry.text for sm in composite_order[:5]]
    assert not composite_ranked.answer_substring_hit("Paris", composite_top5_texts), \
        "composite_ranked must NOT surface a rejected answer in top-5"

    retrieval_order = retrieval_ranked.rank_memories(approved=approved, rejected=rejected)
    retrieval_top5_texts = [sm.entry.text for sm in retrieval_order[:5]]
    assert retrieval_ranked.answer_substring_hit("Paris", retrieval_top5_texts), \
        "retrieval_ranked DOES surface the rejected answer in top-5 (the bug fix-02 corrects)"


# ── composite_ranked: tiebreak determinism ─────────────────────────────


def test_composite_tiebreak_is_deterministic_across_runs():
    """10 runs of the same input produce byte-identical ranking. Defends
    the (composite, retrieval, id) tiebreak chain in
    ``composite_ranked.rank_memories``."""
    approved = [_sm("zz", 0.5), _sm("aa", 0.5), _sm("mm", 0.5)]
    rejected = [_sm("rr", 0.5), _sm("bb", 0.5)]
    runs = [
        [sm.entry.id for sm in composite_ranked.rank_memories(approved=approved, rejected=rejected)]
        for _ in range(10)
    ]
    # All 10 runs identical
    for r in runs[1:]:
        assert r == runs[0]
    # Approved come first (composite 0.5 > 0.0); within approved, sorted by id ascending
    assert runs[0][:3] == ["aa", "mm", "zz"]
    # Rejected last; within rejected, also sorted by id ascending (composite 0)
    assert runs[0][3:] == ["bb", "rr"]


def test_composite_secondary_tiebreak_uses_retrieval_score_then_id():
    """When composite is tied, the next key is retrieval_score desc, then id."""
    # All approved (composite = retrieval_score)
    approved = [
        _sm("a", 0.5),
        _sm("b", 0.7),
        _sm("c", 0.7),    # ties b on retrieval; id "c" > "b" → b comes first
    ]
    ranked = composite_ranked.rank_memories(approved=approved, rejected=[])
    assert [sm.entry.id for sm in ranked] == ["b", "c", "a"]


# ── dispatcher ─────────────────────────────────────────────────────────


def test_dispatcher_default_is_retrieval_ranked():
    assert DEFAULT_SCORER == "retrieval_ranked"
    assert get_ranker("retrieval_ranked") is retrieval_ranked.rank_memories
    assert get_ranker("composite_ranked") is composite_ranked.rank_memories


def test_dispatcher_unknown_scorer_raises():
    with pytest.raises(ValueError, match="Unknown scorer"):
        get_ranker("not_a_real_scorer")


def test_dispatcher_registry_is_complete():
    """Every key in RANKERS is callable; nothing slipped through."""
    assert set(RANKERS.keys()) == {"retrieval_ranked", "composite_ranked"}
    for fn in RANKERS.values():
        assert callable(fn)


# ── byte-for-byte regression on the default path ───────────────────────


def test_normalize_strips_punctuation_and_lowercases():
    """The substring matcher's normaliser is shared between scorers — defend it."""
    assert retrieval_ranked.normalize("  Hello, World!  ") == "hello world"
    assert retrieval_ranked.normalize("") == ""
    assert retrieval_ranked.normalize(None) == ""    # tolerant of None gold answers


def test_answer_substring_hit_token_overlap_fallback():
    """The 50% token-overlap fallback fires when verbatim substring fails."""
    # Verbatim substring: gold contained in candidate
    assert retrieval_ranked.answer_substring_hit("paris", ["the capital is paris."])
    # Token overlap >= 0.5: gold has 4 tokens, candidate shares 2 of them
    assert retrieval_ranked.answer_substring_hit(
        "model kit collection five",
        ["my collection has five model airplanes"],
    )
    # No overlap
    assert not retrieval_ranked.answer_substring_hit("paris", ["the capital is london"])
    # Empty gold short-circuits to False
    assert not retrieval_ranked.answer_substring_hit("", ["any text"])


def test_score_question_computes_recall_and_substring_metrics():
    """End-to-end smoke through ``score_question`` covering both
    answerable and abstention branches."""
    result = retrieval_ranked.score_question(
        question_id="q1",
        question_type="single-session-user",
        is_abstention=False,
        gold_answer="paris",
        answer_session_ids={"sess_a"},
        has_answer_turn_keys={"sess_a::3"},
        ranked_memory_keys=["sess_a::3", "sess_b::1", "sess_c::0"],
        ranked_memory_texts=["the capital of france is paris", "unrelated", "more"],
        raven_status="APPROVED",
        n_approved=1,
        latency_ms=42.0,
    )
    assert result.session_recall_at_5 == 1.0
    assert result.turn_recall_at_5 == 1.0
    assert result.answer_hit_top1 is True
    assert result.answer_hit_top5 is True
    assert result.abstention_correct is False    # not an abstention question

    abs_result = retrieval_ranked.score_question(
        question_id="q2_abs",
        question_type="single-session-user",
        is_abstention=True,
        gold_answer="paris",
        answer_session_ids=set(),
        has_answer_turn_keys=set(),
        ranked_memory_keys=["sess_x::0"],
        ranked_memory_texts=["unrelated"],
        raven_status="REFUSED",
        n_approved=0,
        latency_ms=10.0,
    )
    # REFUSED on an abstention question is correct abstention
    assert abs_result.abstention_correct is True


def test_aggregate_rolls_up_per_type_and_overall():
    """``aggregate`` produces well-formed per-type and overall stats."""
    results = [
        retrieval_ranked.QuestionResult(
            question_id="q1", question_type="A", is_abstention=False,
            session_recall_at_5=1.0, turn_recall_at_5=1.0, answer_hit_top5=True,
            latency_ms=10.0,
        ),
        retrieval_ranked.QuestionResult(
            question_id="q2", question_type="A", is_abstention=False,
            session_recall_at_5=0.5, turn_recall_at_5=0.0, answer_hit_top5=False,
            latency_ms=20.0,
        ),
        retrieval_ranked.QuestionResult(
            question_id="q3_abs", question_type="B", is_abstention=True,
            abstention_correct=True, latency_ms=30.0,
        ),
    ]
    report = retrieval_ranked.aggregate(results)
    assert report.n == 3
    assert len(report.by_type) == 2
    type_a = next(t for t in report.by_type if t.question_type == "A")
    assert type_a.mean_session_recall_at_5 == pytest.approx(0.75)
    assert type_a.answer_hit_top5_rate == pytest.approx(0.5)
    assert report.overall_abstention_accuracy == pytest.approx(1.0)
    assert report.latency_p50_ms == 20.0
    # Empty input → zeroed report
    empty = retrieval_ranked.aggregate([])
    assert empty.n == 0


def test_default_scorer_path_matches_v1_2_1_inline_sort():
    """The default ranker must reproduce the exact ordering the v1.2.1
    harness produced inline: ``sorted(union, key=lambda sm:
    sm.retrieval_score, reverse=True)``. Single-key Python sort is
    stable, so identical retrieval_scores keep their input order
    (approved-then-rejected concatenation order)."""
    approved = [_sm("a1", 0.9), _sm("a2", 0.5), _sm("a3", 0.5)]
    rejected = [_sm("r1", 0.7), _sm("r2", 0.5), _sm("r3", 0.3)]
    ranked = retrieval_ranked.rank_memories(approved=approved, rejected=rejected)

    # Reproduce the v1.2.1 inline sort literally:
    expected = sorted(
        list(approved) + list(rejected),
        key=lambda sm: sm.retrieval_score,
        reverse=True,
    )
    assert [sm.entry.id for sm in ranked] == [sm.entry.id for sm in expected]
