"""LongMemEval scorer — re-export shim.

The original ``benchmarks/longmemeval/scorer.py`` shipped a single
substring scoring + aggregation surface. Phase 2.2 fix-02 (LME-012)
moved that surface into a package so a second scorer variant
(``composite_ranked``) can ship alongside the original without changing
its numbers.

This file remains importable so any external caller that did
``from benchmarks.longmemeval.scorer import score_question, aggregate, ...``
keeps working byte-for-byte. The actual implementation lives in
``benchmarks.longmemeval.scorers.retrieval_ranked``.

For the new scorer, see ``benchmarks.longmemeval.scorers.composite_ranked``.
"""
from __future__ import annotations

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
    "OverallReport",
    "QuestionResult",
    "TypeReport",
    "aggregate",
    "answer_substring_hit",
    "normalize",
    "score_question",
]
