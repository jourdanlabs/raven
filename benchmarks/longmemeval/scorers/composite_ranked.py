"""Composite-ranked LongMemEval scorer (Phase 2.2 fix-02 / LME-012).

Why this exists
---------------
v1.2.1 (Phase 2.2 fix-01, ``now_override``) unblocked AURORA on chat-turn
data — calibration approvals went from 3/400 to 117/400 and held-out
from 2/100 to 34/100, with approval-quality preserved. But the published
A@5 number stayed flat at **79.8 %** on held-out because the scorer it
was measured against (``scorers.retrieval_ranked``) ranks
``(approved + rejected)`` purely by retrieval similarity. **The AURORA
gate's filtering decision is invisible to that metric.**

The composite-ranked scorer makes the gate visible: it ranks the union
by

    composite_score = base_retrieval_score × aurora_weight

with ``aurora_weight = 1.0`` for AURORA-approved memories and
``aurora_weight = 0.0`` for AURORA-rejected memories (Option A: rejected
memories are pushed to the back of the ranking and effectively excluded
from any non-trivial ``top_k``). The substring matcher used to compute
A@k is **unchanged** — only the ranking is different.

FROZEN SPEC (set BEFORE measurement)
------------------------------------
The constants and weighting rule below are the methodology contract for
this scorer. They are intentionally simple and intentionally not tunable
from observed numbers — the methodology brand bans the
"adjust-weights-to-make-A@5-look-better" loop. If the spec produces a
disconfirming number, we ship the disconfirmation honestly. Any future
spec revision must be motivated from architectural first principles
(e.g. a real downstream-LLM consumer needs to see *some* rejected
memories), not from observed measurements.

If a future sprint introduces a configurable rejected-weight ``r`` (so
``aurora_weight = r`` for rejected memories), it must:

1. land behind a default of ``r = 0.0`` to preserve this scorer's
   numbers byte-for-byte;
2. ship its own attribution doc justifying the new spec from
   first principles, not from a measurement that motivated the change.

That is explicitly out of scope for this version. Brief hard stop #5
governs.

Tie-breaking
------------
Two memories with identical ``composite_score`` are ranked by:

  1. original ``retrieval_score`` descending (preserves the upstream
     retrieval ordering as the natural tiebreak);
  2. then by ``entry.id`` ascending (deterministic floor — entry ids
     are hex uuids, lex-sortable, stable across runs given
     ``PYTHONHASHSEED=0``).

The two-level tiebreak makes the ranking byte-stable across two
``PYTHONHASHSEED=0`` runs. Single-key Python sort is stable, so the
explicit id-tiebreak only fires when retrieval_score is exactly equal
between two entries — which is rare on TF-IDF cosine but happens on
zero-overlap retrieval (both 0.0). The id tiebreak ensures even those
ties are deterministic.
"""
from __future__ import annotations

# FROZEN SPEC — see module docstring.
APPROVED_WEIGHT = 1.0
REJECTED_WEIGHT = 0.0


def composite_score(retrieval_score: float, aurora_weight: float) -> float:
    """Return ``retrieval_score * aurora_weight``.

    Pulled out as a named function so unit tests can pin the formula
    without depending on the ranking surface.
    """
    return retrieval_score * aurora_weight


def rank_memories(
    *,
    approved: list,    # list[ScoredMemory] — AURORA-approved
    rejected: list,    # list[ScoredMemory] — AURORA-rejected
) -> list:
    """Rank the union of ``(approved, rejected)`` by composite score.

    Approved memories receive ``aurora_weight = APPROVED_WEIGHT (1.0)``,
    rejected memories receive ``aurora_weight = REJECTED_WEIGHT (0.0)``.
    The ranking is stable and deterministic given ``PYTHONHASHSEED=0``.

    Returns the sorted list. Caller is responsible for slicing to the
    desired ``top_k`` for downstream substring scoring; this function
    does NOT slice — it gives the full ordered union so diagnostics
    (``n_retrieved`` etc.) stay correct.
    """
    annotated = [
        (sm, composite_score(sm.retrieval_score, APPROVED_WEIGHT))
        for sm in approved
    ] + [
        (sm, composite_score(sm.retrieval_score, REJECTED_WEIGHT))
        for sm in rejected
    ]
    # Sort by:
    #   1. composite descending  (primary FROZEN-spec ordering)
    #   2. retrieval_score descending  (preserves upstream order on ties)
    #   3. entry.id ascending  (deterministic floor for true ties)
    annotated.sort(key=lambda pair: (-pair[1], -pair[0].retrieval_score, pair[0].entry.id))
    return [sm for sm, _ in annotated]


# Re-export the QuestionResult / OverallReport / aggregate / score_question /
# answer_substring_hit symbols from the retrieval-ranked scorer. The
# substring matcher and aggregation are unchanged across the two
# scorers — *only the ranking* is different. Re-exporting keeps the
# public scorers API uniform.
from benchmarks.longmemeval.scorers.retrieval_ranked import (
    OverallReport,
    QuestionResult,
    TypeReport,
    aggregate,
    answer_substring_hit,
    normalize,
    score_question,
)

__all__ = [
    "APPROVED_WEIGHT",
    "REJECTED_WEIGHT",
    "OverallReport",
    "QuestionResult",
    "TypeReport",
    "aggregate",
    "answer_substring_hit",
    "composite_score",
    "normalize",
    "rank_memories",
    "score_question",
]
