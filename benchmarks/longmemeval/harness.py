"""LongMemEval harness for RAVEN v1.0 cold-run.

Runs `RAVENPipeline` against every LongMemEval question with a fresh
in-memory store per question, measures retrieval recall, top-k answer
hit rate, abstention accuracy, and per-question latency.

NO TUNING. Defaults only:
  - top_k = 20
  - aurora_threshold = aurora.APPROVE_THRESHOLD (default)
  - half_life_days = eclipse.DEFAULT_HALF_LIFE_DAYS (default)
  - embedder = TFIDFEmbedder (lexical-only, no sentence-transformers)

Usage:
    python -m benchmarks.longmemeval.harness                  # full run
    python -m benchmarks.longmemeval.harness --limit 25       # smoke test
    python -m benchmarks.longmemeval.harness --output run.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.types import MemoryEntry

from benchmarks.longmemeval.loader import LMEQuestion, load_questions
from benchmarks.longmemeval.scorer import (
    OverallReport,
    QuestionResult,
    aggregate,
    score_question,
)


def _turn_text(role: str, content: str) -> str:
    """Format a turn as a single memory entry text."""
    role_label = "USER" if role == "user" else "ASSISTANT"
    return f"[{role_label}] {content}"


def build_memory_entries(q: LMEQuestion) -> tuple[list[MemoryEntry], dict[str, str], set[str]]:
    """Convert a LongMemEval question's haystack into MemoryEntry rows.

    Returns:
        entries           : list[MemoryEntry] — one entry per turn
        id_to_key         : dict[entry.id -> "session_id::turn_index"]
        has_answer_keys   : set of "session_id::turn_index" for evidence turns
    """
    entries: list[MemoryEntry] = []
    id_to_key: dict[str, str] = {}
    has_answer_keys: set[str] = set()

    for sess in q.haystack_sessions:
        ts = sess.timestamp or q.question_timestamp or time.time()
        for idx, turn in enumerate(sess.turns):
            key = f"{sess.session_id}::{idx}"
            entry_id = str(uuid.uuid4())
            entry = MemoryEntry(
                id=entry_id,
                text=_turn_text(turn.role, turn.content),
                timestamp=ts + idx * 0.001,  # preserve intra-session ordering
                source=f"longmemeval:{sess.session_id}",
                metadata={"session_id": sess.session_id, "turn_index": idx, "role": turn.role},
            )
            entries.append(entry)
            id_to_key[entry_id] = key
            if turn.has_answer:
                has_answer_keys.add(key)
    return entries, id_to_key, has_answer_keys


def run_one(
    q: LMEQuestion,
    top_k: int = 20,
    calibration_profile: str = "factual",
) -> QuestionResult:
    """Run RAVEN against a single LongMemEval question.

    Phase 2.1: ``calibration_profile`` selects the AURORA threshold.
    Defaults to ``"factual"`` so the v1.0 cold-run baseline is exactly
    reproducible.
    """
    store = RAVENStore(":memory:")
    pipeline = RAVENPipeline(
        store=store, top_k=top_k, calibration_profile=calibration_profile,
    )

    entries, id_to_key, has_answer_keys = build_memory_entries(q)

    # Ingest haystack.
    for e in entries:
        store.ingest(e)

    # Recall.
    # Phase 2.2 fix-01: pass corpus-relative ``now`` (the question's own
    # timestamp) so ECLIPSE decay computes against the corpus time origin
    # instead of wall-clock. Without this, LongMemEval haystacks dated
    # 2023-2024 replayed in 2026 see decay weight ~1e-7 and the AURORA
    # composite collapses below any approval-quality-preserving threshold.
    # See ``docs/phase2.2/fixes/01_now_override.md`` and GAPS.md LME-010.
    # Falls back to wall-clock if the corpus didn't ship a parseable
    # question_date (rare; ``_parse_date`` returns 0.0 in that case).
    now_override = q.question_timestamp if q.question_timestamp else None
    response = pipeline.recall(q.question, now=now_override)

    # Build ranked memory list. RAVEN returns approved + rejected after
    # AURORA gating; for retrieval-style scoring we want the full ranked
    # set as RAVEN saw it. We rank by retrieval_score (already backfilled
    # onto ScoredMemory) over (approved + rejected), descending.
    scored = list(response.approved_memories) + list(response.rejected_memories)
    scored.sort(key=lambda sm: sm.retrieval_score, reverse=True)

    ranked_memory_keys: list[str] = []
    ranked_memory_texts: list[str] = []
    for sm in scored:
        key = id_to_key.get(sm.entry.id)
        if key is None:
            continue
        ranked_memory_keys.append(key)
        ranked_memory_texts.append(sm.entry.text)

    store.close()

    return score_question(
        question_id=q.question_id,
        question_type=q.question_type,
        is_abstention=q.is_abstention,
        gold_answer=q.answer,
        answer_session_ids=q.answer_session_ids,
        has_answer_turn_keys=has_answer_keys,
        ranked_memory_keys=ranked_memory_keys,
        ranked_memory_texts=ranked_memory_texts,
        raven_status=response.status,
        n_approved=len(response.approved_memories),
        latency_ms=response.pipeline_trace.latency_ms,
    )


def run_all(
    questions: list[LMEQuestion],
    top_k: int = 20,
    progress_every: int = 25,
    calibration_profile: str = "factual",
) -> list[QuestionResult]:
    results: list[QuestionResult] = []
    t0 = time.perf_counter()
    for i, q in enumerate(questions):
        try:
            results.append(run_one(
                q, top_k=top_k, calibration_profile=calibration_profile,
            ))
        except Exception as exc:  # noqa: BLE001
            # Record failure; do not abort the whole run.
            results.append(QuestionResult(
                question_id=q.question_id,
                question_type=q.question_type,
                is_abstention=q.is_abstention,
                raven_status="ERROR",
                note=f"{type(exc).__name__}: {exc}",
            ))
        if progress_every and (i + 1) % progress_every == 0:
            elapsed = time.perf_counter() - t0
            print(
                f"  [{i+1:>3}/{len(questions)}] "
                f"elapsed={elapsed:6.1f}s  "
                f"avg={elapsed*1000/(i+1):6.1f} ms/q",
                flush=True,
            )
    return results


def print_report(report: OverallReport) -> None:
    print()
    print("=" * 86)
    print(f"  LongMemEval cold-run · RAVEN v1.0 · N={report.n}")
    print("=" * 86)
    w = 28
    head = (
        f"  {'question_type':<{w}} {'N':>4} {'sR@5':>6} {'sR@10':>6} "
        f"{'tR@5':>6} {'tR@10':>6} {'A@1':>6} {'A@5':>6} {'A@10':>6} {'absAcc':>7}"
    )
    print(head)
    print(f"  {'-'*w} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")
    for t in report.by_type:
        abs_str = f"{t.abstention_accuracy:>7.1%}" if t.n_abstention else f"{'-':>7}"
        print(
            f"  {t.question_type:<{w}} {t.n:>4} "
            f"{t.mean_session_recall_at_5:>6.3f} {t.mean_session_recall_at_10:>6.3f} "
            f"{t.mean_turn_recall_at_5:>6.3f} {t.mean_turn_recall_at_10:>6.3f} "
            f"{t.answer_hit_top1_rate:>6.3f} {t.answer_hit_top5_rate:>6.3f} "
            f"{t.answer_hit_top10_rate:>6.3f} {abs_str}"
        )
    print(f"  {'-'*w} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")
    print(
        f"  {'OVERALL (answerable)':<{w}} {report.n:>4} "
        f"{report.overall_session_recall_at_5:>6.3f} {report.overall_session_recall_at_10:>6.3f} "
        f"{report.overall_turn_recall_at_5:>6.3f} {report.overall_turn_recall_at_10:>6.3f} "
        f"{'':>6} {report.overall_answer_hit_top5:>6.3f} {report.overall_answer_hit_top10:>6.3f} "
        f"{report.overall_abstention_accuracy:>7.1%}"
    )
    print()
    print(f"  Latency:  p50={report.latency_p50_ms:.1f} ms   "
          f"p95={report.latency_p95_ms:.1f} ms   "
          f"p99={report.latency_p99_ms:.1f} ms")
    print()
    print("  sR@k = session-level recall@k     tR@k = turn-level recall@k")
    print("  A@k  = answer-substring hit rate in top-k     absAcc = abstention accuracy")
    print()


def serialize_report(report: OverallReport, results: list[QuestionResult]) -> dict:
    return {
        "n": report.n,
        "overall": {
            "session_recall_at_5": report.overall_session_recall_at_5,
            "session_recall_at_10": report.overall_session_recall_at_10,
            "turn_recall_at_5": report.overall_turn_recall_at_5,
            "turn_recall_at_10": report.overall_turn_recall_at_10,
            "answer_hit_top5": report.overall_answer_hit_top5,
            "answer_hit_top10": report.overall_answer_hit_top10,
            "abstention_accuracy": report.overall_abstention_accuracy,
            "latency_p50_ms": report.latency_p50_ms,
            "latency_p95_ms": report.latency_p95_ms,
            "latency_p99_ms": report.latency_p99_ms,
        },
        "by_type": [asdict(t) for t in report.by_type],
        "per_question": [asdict(r) for r in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAVEN v1.0 cold-run on LongMemEval")
    parser.add_argument("--data", type=str, default=None,
                        help="Path to longmemeval_oracle.json (default: env LONGMEMEVAL_DATA or /tmp cache)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of questions (smoke test)")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--profile", type=str, default="factual",
        help="Calibration profile name (factual | chat_turn | ...).",
    )
    parser.add_argument("--output", type=str, default=None,
                        help="Write per-question + aggregate JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    questions = load_questions(args.data)
    if args.limit:
        questions = questions[: args.limit]

    if not args.quiet:
        print(f"\nLongMemEval · RAVEN run · profile={args.profile}")
        print(f"  N = {len(questions)} questions")
        print(f"  top_k = {args.top_k}")
        print(f"  embedder = TFIDFEmbedder (lexical-only)")

    t0 = time.perf_counter()
    results = run_all(
        questions, top_k=args.top_k, calibration_profile=args.profile,
    )
    elapsed = time.perf_counter() - t0

    report = aggregate(results)
    if not args.quiet:
        print_report(report)
        print(f"  total wall time: {elapsed:.1f} s\n")

    if args.output:
        out = serialize_report(report, results)
        out["wall_time_s"] = elapsed
        out["config"] = {
            "top_k": args.top_k,
            "embedder": "TFIDFEmbedder",
            "raven_version": "1.1.0",
            "calibration_profile": args.profile,
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f"  wrote {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
