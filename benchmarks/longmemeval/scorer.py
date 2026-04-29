"""LongMemEval scoring for RAVEN cold-run.

Background
----------
The official LongMemEval evaluation (`evaluate_qa.py`) uses GPT-4o as a
judge to grade *generated* answers from an LLM-backed memory system. RAVEN
v1.0 is a memory-validation pipeline: it returns ranked, scored memory
entries, not synthesized natural-language answers. Routing RAVEN's outputs
through an LLM and grading with another LLM would conflate retrieval
quality with generation quality, and would also require external API
calls — both of which are out of scope for the cold-run baseline.

We therefore use the **retrieval-style** evaluation that the LongMemEval
authors also publish (`print_retrieval_metrics.py`), adapted for RAVEN's
turn-level memory representation:

  - **session-level recall@k** — did RAVEN's top-k include at least one
    memory entry sourced from an evidence session?
  - **turn-level recall@k**    — did RAVEN's top-k include the specific
    `has_answer=True` turn from an evidence session?
  - **answer-substring hit**   — does any approved memory entry contain
    a normalized substring of the gold answer?

For abstention questions (`question_id` suffix `_abs`):
  - **abstention correct** if RAVEN's response status is REFUSED *or* if
    no top-k memory contains the gold answer substring (the answer field
    on abstention questions is an unanswerability explanation; we treat
    "system did not surface a confident memory" as the abstention signal).

These three metrics are reported per question_type. We deliberately do
*not* combine them into a single composite score — each measures a
different facet of memory recall and we want the cold-run report to be
honest about which facets RAVEN already handles vs. which need work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for substring match."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def answer_substring_hit(gold_answer: str, candidate_texts: Iterable[str]) -> bool:
    """True if normalized gold_answer is a substring of any candidate.

    For multi-clause answers, we also check token-overlap >= 0.5 as a
    fallback (so e.g. gold "GPS system not functioning correctly" hits
    a memory containing "GPS isn't working right after the service").
    """
    g_norm = normalize(gold_answer)
    if not g_norm:
        return False
    g_tokens = set(g_norm.split())
    for c in candidate_texts:
        c_norm = normalize(c)
        if g_norm and g_norm in c_norm:
            return True
        if g_tokens:
            c_tokens = set(c_norm.split())
            if g_tokens and len(g_tokens & c_tokens) / len(g_tokens) >= 0.5:
                return True
    return False


@dataclass
class QuestionResult:
    question_id: str
    question_type: str
    is_abstention: bool
    # primary metrics
    session_recall_at_5: float = 0.0
    session_recall_at_10: float = 0.0
    turn_recall_at_5: float = 0.0
    turn_recall_at_10: float = 0.0
    answer_hit_top1: bool = False
    answer_hit_top5: bool = False
    answer_hit_top10: bool = False
    abstention_correct: bool = False
    # diagnostics
    raven_status: str = ""
    n_retrieved: int = 0
    n_approved: int = 0
    latency_ms: float = 0.0
    note: str = ""


def score_question(
    *,
    question_id: str,
    question_type: str,
    is_abstention: bool,
    gold_answer: str,
    answer_session_ids: set[str],
    has_answer_turn_keys: set[str],   # set of (session_id, turn_index) joined as "sid::idx"
    ranked_memory_keys: list[str],     # ordered: each is "session_id::turn_index" for source memories
    ranked_memory_texts: list[str],    # parallel to ranked_memory_keys
    raven_status: str,
    n_approved: int,
    latency_ms: float,
) -> QuestionResult:
    """Compute LongMemEval-style metrics for a single question."""
    res = QuestionResult(
        question_id=question_id,
        question_type=question_type,
        is_abstention=is_abstention,
        raven_status=raven_status,
        n_retrieved=len(ranked_memory_keys),
        n_approved=n_approved,
        latency_ms=latency_ms,
    )

    def _session_recall(k: int) -> float:
        if not answer_session_ids:
            return 1.0  # no evidence sessions => trivially satisfied
        topk = ranked_memory_keys[:k]
        seen_sessions = {key.split("::", 1)[0] for key in topk}
        hits = len(seen_sessions & answer_session_ids)
        return hits / max(len(answer_session_ids), 1)

    def _turn_recall(k: int) -> float:
        if not has_answer_turn_keys:
            return 1.0
        topk = set(ranked_memory_keys[:k])
        return len(topk & has_answer_turn_keys) / max(len(has_answer_turn_keys), 1)

    res.session_recall_at_5 = _session_recall(5)
    res.session_recall_at_10 = _session_recall(10)
    res.turn_recall_at_5 = _turn_recall(5)
    res.turn_recall_at_10 = _turn_recall(10)

    res.answer_hit_top1 = answer_substring_hit(gold_answer, ranked_memory_texts[:1])
    res.answer_hit_top5 = answer_substring_hit(gold_answer, ranked_memory_texts[:5])
    res.answer_hit_top10 = answer_substring_hit(gold_answer, ranked_memory_texts[:10])

    if is_abstention:
        # Correct abstention = RAVEN refused OR top-5 contains no answer-substring hit
        res.abstention_correct = (raven_status == "REFUSED") or (not res.answer_hit_top5)
    else:
        res.abstention_correct = False  # not applicable

    return res


@dataclass
class TypeReport:
    question_type: str
    n: int = 0
    n_abstention: int = 0
    n_answerable: int = 0
    mean_session_recall_at_5: float = 0.0
    mean_session_recall_at_10: float = 0.0
    mean_turn_recall_at_5: float = 0.0
    mean_turn_recall_at_10: float = 0.0
    answer_hit_top1_rate: float = 0.0
    answer_hit_top5_rate: float = 0.0
    answer_hit_top10_rate: float = 0.0
    abstention_accuracy: float = 0.0   # over abstention subset only (NaN if 0)


@dataclass
class OverallReport:
    n: int
    by_type: list[TypeReport]
    overall_session_recall_at_5: float
    overall_session_recall_at_10: float
    overall_turn_recall_at_5: float
    overall_turn_recall_at_10: float
    overall_answer_hit_top5: float
    overall_answer_hit_top10: float
    overall_abstention_accuracy: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float


def aggregate(results: list[QuestionResult]) -> OverallReport:
    """Aggregate per-question results into per-type and overall report."""
    if not results:
        return OverallReport(0, [], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    by_type_buckets: dict[str, list[QuestionResult]] = {}
    for r in results:
        by_type_buckets.setdefault(r.question_type, []).append(r)

    type_reports: list[TypeReport] = []
    for qtype, bucket in sorted(by_type_buckets.items()):
        ans_subset = [r for r in bucket if not r.is_abstention]
        abs_subset = [r for r in bucket if r.is_abstention]
        # answerable subset drives recall metrics; abstention subset drives abstention accuracy
        m = lambda f, lst: (sum(f(r) for r in lst) / len(lst)) if lst else 0.0
        type_reports.append(TypeReport(
            question_type=qtype,
            n=len(bucket),
            n_abstention=len(abs_subset),
            n_answerable=len(ans_subset),
            mean_session_recall_at_5=m(lambda r: r.session_recall_at_5, ans_subset),
            mean_session_recall_at_10=m(lambda r: r.session_recall_at_10, ans_subset),
            mean_turn_recall_at_5=m(lambda r: r.turn_recall_at_5, ans_subset),
            mean_turn_recall_at_10=m(lambda r: r.turn_recall_at_10, ans_subset),
            answer_hit_top1_rate=m(lambda r: 1.0 if r.answer_hit_top1 else 0.0, ans_subset),
            answer_hit_top5_rate=m(lambda r: 1.0 if r.answer_hit_top5 else 0.0, ans_subset),
            answer_hit_top10_rate=m(lambda r: 1.0 if r.answer_hit_top10 else 0.0, ans_subset),
            abstention_accuracy=m(lambda r: 1.0 if r.abstention_correct else 0.0, abs_subset),
        ))

    answerable = [r for r in results if not r.is_abstention]
    abstention = [r for r in results if r.is_abstention]
    avg = lambda f, lst: (sum(f(r) for r in lst) / len(lst)) if lst else 0.0

    latencies = sorted(r.latency_ms for r in results)
    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round((p / 100.0) * (len(latencies) - 1))))
        return latencies[idx]

    return OverallReport(
        n=len(results),
        by_type=type_reports,
        overall_session_recall_at_5=avg(lambda r: r.session_recall_at_5, answerable),
        overall_session_recall_at_10=avg(lambda r: r.session_recall_at_10, answerable),
        overall_turn_recall_at_5=avg(lambda r: r.turn_recall_at_5, answerable),
        overall_turn_recall_at_10=avg(lambda r: r.turn_recall_at_10, answerable),
        overall_answer_hit_top5=avg(lambda r: 1.0 if r.answer_hit_top5 else 0.0, answerable),
        overall_answer_hit_top10=avg(lambda r: 1.0 if r.answer_hit_top10 else 0.0, answerable),
        overall_abstention_accuracy=avg(lambda r: 1.0 if r.abstention_correct else 0.0, abstention),
        latency_p50_ms=pct(50),
        latency_p95_ms=pct(95),
        latency_p99_ms=pct(99),
    )
