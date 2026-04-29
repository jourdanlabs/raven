"""Token-efficiency measurement for the LongMemEval harness.

Hypothesis under test
---------------------
RAVEN's filtering reduces the token payload a downstream LLM has to read,
**at equal-or-better answer quality**, compared with raw passthrough of
the same top-K retrieval. Either half of that statement in isolation is
not publishable: cheap-but-wrong is worse than the baseline, and
correct-but-equally-expensive defeats the wedge narrative.

Per-query the harness computes:

* ``tokens_passthrough`` — total ``cl100k_base`` tokens of the top-K
  *retrieved* memory texts before any RAVEN gating. Mirrors what a naïve
  retriever would shove into an LLM's context window.
* ``tokens_raven`` — total ``cl100k_base`` tokens of the memory texts
  RAVEN would actually surface to the LLM after the full pipeline:
  reconciled, decay-applied, refusal-filtered, importance-ranked. If
  RAVEN refuses, this is 0 (RAVEN's contract is to surface nothing
  rather than surface garbage).
* ``a_at_5_passthrough`` / ``a_at_5_raven`` — answer-substring presence
  in the top-5 of each regime, computed against the gold answer with the
  same scoring rule as :mod:`benchmarks.longmemeval.scorer`. Quality is
  the gate; the savings number is meaningless without it.

Reporting rules (NON-NEGOTIABLE)
--------------------------------
1. Token efficiency is **observed, not optimized**. Threshold tuning may
   not cite token-savings as a motivating signal. (Enforced by per-fix
   attribution-doc review.)
2. The headline metric is *% token reduction at equal-or-better answer
   quality*. Token savings reported in isolation is a methodology
   violation.
3. Three publishable outcomes — Confirmed / Mixed / Disconfirmed — are
   all valid. The methodology brand publishes whichever holds.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from benchmarks.longmemeval.harness import build_memory_entries
from benchmarks.longmemeval.heldout_guard import (
    HELDOUT_PATH,
    HeldOutAccessError,
    held_out_unlocked,
    load_calibration_questions,
)
from benchmarks.longmemeval.loader import LMEQuestion, load_questions
from benchmarks.longmemeval.scorer import answer_substring_hit
from benchmarks.longmemeval.scorers import DEFAULT_SCORER
from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore

DEFAULT_TOP_K = 20
DEFAULT_K_FOR_QUALITY = 5  # A@5 — matches the LongMemEval cold-run headline


# ── tokenizer (cl100k_base) ─────────────────────────────────────────────────


def _get_encoder():
    """Return a cl100k_base encoder.

    ``tiktoken`` is a soft dependency of the bench extra. We import lazily
    so the rest of the bench package keeps loading even if a contributor
    forgot to ``pip install -e '.[bench]'``.
    """
    try:
        import tiktoken
    except ImportError as exc:  # pragma: no cover - exercised only on missing dep
        raise ImportError(
            "tiktoken is required for token-efficiency measurement. "
            "Install with: pip install -e '.[bench]'  (or pip install tiktoken)"
        ) from exc
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(texts: list[str], encoder=None) -> int:
    """Sum cl100k_base token counts across ``texts``.

    Empty strings (and the all-whitespace strings that come out of empty
    chat turns) tokenize to zero — they cost nothing to send. We do NOT
    add per-message overhead (no chat-template wrapping); the bare-text
    count is the apples-to-apples cost of the *memory payload*, not the
    cost of any particular prompt format.
    """
    encoder = encoder or _get_encoder()
    if not texts:
        return 0
    return sum(len(encoder.encode(t)) for t in texts)


# ── per-question record ─────────────────────────────────────────────────────


@dataclass
class TokenQueryRecord:
    """One question's token-efficiency receipt.

    All numeric fields are populated for every question; ``raven_status``
    explains why ``tokens_raven`` may be zero on REFUSED responses.
    """

    question_id: str
    question_type: str
    is_abstention: bool
    raven_status: str

    n_passthrough: int
    n_raven_surfaced: int

    tokens_passthrough: int
    tokens_raven: int
    token_reduction_ratio: float  # 1 - (tokens_raven / tokens_passthrough); negative if RAVEN sends MORE

    a_at_5_passthrough: bool
    a_at_5_raven: bool
    quality_delta: int  # +1 RAVEN better, 0 equal, -1 RAVEN worse


@dataclass
class TokenEfficiencyReport:
    """Aggregate roll-up grouped by question type and overall.

    All ratios are reported as fractions in [-inf, 1.0]. A ratio of 0.4
    means RAVEN sent 40 % fewer tokens than passthrough on that subset.
    Negative ratios mean RAVEN sent MORE tokens (expected when RAVEN
    re-ranks but does not filter).
    """

    n: int
    by_type: dict
    overall: dict
    quality_controlled_overall: dict
    config: dict
    per_query: list[dict] = field(default_factory=list)


# ── per-question runner ─────────────────────────────────────────────────────


def measure_one(
    q: LMEQuestion,
    *,
    pipeline_factory,
    top_k: int = DEFAULT_TOP_K,
    k_for_quality: int = DEFAULT_K_FOR_QUALITY,
    encoder=None,
) -> TokenQueryRecord:
    """Measure token efficiency on a single question.

    ``pipeline_factory`` returns a fresh ``(store, pipeline)`` pair per
    question; we keep this explicit (rather than building inside
    :func:`measure_one`) so calibration variants can inject a configured
    pipeline without us caring about the configuration knobs.
    """
    store, pipeline = pipeline_factory()
    entries, id_to_key, _has_answer_keys = build_memory_entries(q)

    # Build a text lookup so we can compute the passthrough payload from
    # the *retrieved* (pre-AURORA) ranking the store returns.
    text_by_id = {e.id: e.text for e in entries}

    for e in entries:
        store.ingest(e)

    # Passthrough = the retrieval layer's top_k, ranked by retrieval score
    # before METEOR/NOVA/ECLIPSE/PULSAR/QUASAR/AURORA.
    query_entities = pipeline._meteor.tag_entities(q.question)
    raw_results = pipeline.store.search(
        q.question, top_k=top_k, entity_tags=query_entities
    )
    passthrough_entries = [e for e, _ in raw_results]
    passthrough_texts = [text_by_id.get(e.id, e.text) for e in passthrough_entries]

    # Run the full pipeline; what RAVEN would surface is the approved set
    # post-gate (A's confidence-filtered output). When AURORA refuses,
    # RAVEN surfaces nothing — by design — so tokens_raven = 0.
    #
    # Phase 2.2 fix-01: thread the corpus-relative question_timestamp
    # through ECLIPSE so token-efficiency runs see the same fair-decay
    # regime as the harness. Otherwise the token numbers would reflect
    # the LME-010 over-refusal artefact and not the calibrated gate.
    now_override = q.question_timestamp if q.question_timestamp else None
    response = pipeline.recall(q.question, now=now_override)
    raven_surfaced_texts = [sm.entry.text for sm in response.approved_memories]

    tokens_passthrough = count_tokens(passthrough_texts, encoder)
    tokens_raven = count_tokens(raven_surfaced_texts, encoder)

    if tokens_passthrough > 0:
        ratio = 1.0 - (tokens_raven / tokens_passthrough)
    else:
        # No retrieval, no payload to compare. We mark this 0 so it
        # neutrally averages out rather than inflating the headline.
        ratio = 0.0

    a_pt = answer_substring_hit(q.answer, passthrough_texts[:k_for_quality])
    a_rv = answer_substring_hit(q.answer, raven_surfaced_texts[:k_for_quality])

    quality_delta = (1 if a_rv and not a_pt else 0) + (-1 if a_pt and not a_rv else 0)

    store.close()

    return TokenQueryRecord(
        question_id=q.question_id,
        question_type=q.question_type,
        is_abstention=q.is_abstention,
        raven_status=response.status,
        n_passthrough=len(passthrough_texts),
        n_raven_surfaced=len(raven_surfaced_texts),
        tokens_passthrough=tokens_passthrough,
        tokens_raven=tokens_raven,
        token_reduction_ratio=ratio,
        a_at_5_passthrough=a_pt,
        a_at_5_raven=a_rv,
        quality_delta=quality_delta,
    )


def _default_pipeline_factory(
    top_k: int = DEFAULT_TOP_K,
    calibration_profile: str = "factual",
):
    """Build a RAVEN pipeline factory for token-efficiency runs.

    The default ``calibration_profile="factual"`` matches the v1.0
    cold-run baseline byte-for-byte. Phase 2.1 chat-turn runs pass
    ``calibration_profile="chat_turn"`` to swap calibration without
    touching anything else — that's the *only* thing that varies between
    profile comparisons, which is what makes the comparison clean.
    """
    def _factory():
        store = RAVENStore(":memory:")
        return store, RAVENPipeline(
            store=store, top_k=top_k,
            calibration_profile=calibration_profile,
        )
    return _factory


# ── aggregation ─────────────────────────────────────────────────────────────


def _ratio_stats(ratios: list[float]) -> dict:
    if not ratios:
        return {"n": 0, "median": 0.0, "p25": 0.0, "p75": 0.0, "p95": 0.0, "mean": 0.0}
    s = sorted(ratios)
    n = len(s)
    def pct(p: float) -> float:
        idx = min(n - 1, int(round((p / 100.0) * (n - 1))))
        return s[idx]
    return {
        "n": n,
        "median": pct(50),
        "p25": pct(25),
        "p75": pct(75),
        "p95": pct(95),
        "mean": statistics.fmean(ratios),
    }


def _quality_stats(records: list[TokenQueryRecord]) -> dict:
    if not records:
        return {"n": 0, "a_at_5_passthrough": 0.0, "a_at_5_raven": 0.0, "delta_pts": 0.0}
    n = len(records)
    pt_hits = sum(1 for r in records if r.a_at_5_passthrough)
    rv_hits = sum(1 for r in records if r.a_at_5_raven)
    return {
        "n": n,
        "a_at_5_passthrough": pt_hits / n,
        "a_at_5_raven": rv_hits / n,
        "delta_pts": (rv_hits - pt_hits) / n,
    }


def aggregate_token_records(
    records: list[TokenQueryRecord],
    *,
    config: dict | None = None,
) -> TokenEfficiencyReport:
    """Aggregate per-question records into a publishable report.

    Computes:
      * per-type stats (token reduction + quality)
      * overall stats
      * a quality-controlled headline: token reduction restricted to the
        subset of queries where RAVEN's A@5 was equal-or-better than
        passthrough. This is the **only** headline the THEMIS framing
        test will accept.
    """
    by_type: dict[str, list[TokenQueryRecord]] = {}
    for r in records:
        by_type.setdefault(r.question_type, []).append(r)

    by_type_out: dict[str, dict] = {}
    for qtype, bucket in sorted(by_type.items()):
        ratios = [r.token_reduction_ratio for r in bucket]
        by_type_out[qtype] = {
            "n": len(bucket),
            "tokens_passthrough_total": sum(r.tokens_passthrough for r in bucket),
            "tokens_raven_total": sum(r.tokens_raven for r in bucket),
            "token_reduction_stats": _ratio_stats(ratios),
            "quality": _quality_stats(bucket),
            "n_refused": sum(1 for r in bucket if r.raven_status == "REFUSED"),
        }

    overall_ratios = [r.token_reduction_ratio for r in records]
    overall_quality = _quality_stats(records)
    overall = {
        "n": len(records),
        "tokens_passthrough_total": sum(r.tokens_passthrough for r in records),
        "tokens_raven_total": sum(r.tokens_raven for r in records),
        "token_reduction_stats": _ratio_stats(overall_ratios),
        "quality": overall_quality,
        "n_refused": sum(1 for r in records if r.raven_status == "REFUSED"),
    }

    # Quality-controlled headline: the subset where RAVEN didn't lose
    # quality vs passthrough. Token reduction outside this subset is a
    # methodology trap — never publish it as the headline.
    qc_subset = [r for r in records if r.quality_delta >= 0]
    qc_ratios = [r.token_reduction_ratio for r in qc_subset]
    quality_controlled = {
        "n": len(qc_subset),
        "fraction_of_corpus": (len(qc_subset) / len(records)) if records else 0.0,
        "token_reduction_stats": _ratio_stats(qc_ratios),
        "headline_pct_reduction_at_equal_or_better_quality": (
            _ratio_stats(qc_ratios)["median"]
        ),
    }

    return TokenEfficiencyReport(
        n=len(records),
        by_type=by_type_out,
        overall=overall,
        quality_controlled_overall=quality_controlled,
        config=config or {},
        per_query=[asdict(r) for r in records],
    )


# ── public runner ──────────────────────────────────────────────────────────


def measure_corpus(
    questions: list[LMEQuestion],
    *,
    pipeline_factory=None,
    top_k: int = DEFAULT_TOP_K,
    k_for_quality: int = DEFAULT_K_FOR_QUALITY,
    progress_every: int = 25,
    calibration_profile: str = "factual",
    scorer: str = DEFAULT_SCORER,
) -> TokenEfficiencyReport:
    """Run token-efficiency measurement across ``questions``.

    Defaults to the v1.0 pipeline factory. Phase 2.1 calibration variants
    pass their own factory — that's the *only* thing that changes between
    runs, which is what makes the comparison clean.

    Phase 2.2 fix-02 (LME-012): ``scorer`` is recorded in the report's
    ``config`` for audit. Token efficiency's A@5 metric uses
    ``response.approved_memories`` — i.e., the AURORA-surfaced set —
    which is already the composite-ranked surface by construction.
    The scorer choice is therefore informational here, not behavioural;
    we record it so per-scorer reports are unambiguous in the JSON.
    """
    pipeline_factory = pipeline_factory or _default_pipeline_factory(
        top_k=top_k, calibration_profile=calibration_profile,
    )
    encoder = _get_encoder()

    records: list[TokenQueryRecord] = []
    t0 = time.perf_counter()
    for i, q in enumerate(questions):
        try:
            records.append(measure_one(
                q,
                pipeline_factory=pipeline_factory,
                top_k=top_k,
                k_for_quality=k_for_quality,
                encoder=encoder,
            ))
        except Exception as exc:  # noqa: BLE001
            records.append(TokenQueryRecord(
                question_id=q.question_id,
                question_type=q.question_type,
                is_abstention=q.is_abstention,
                raven_status=f"ERROR:{type(exc).__name__}",
                n_passthrough=0,
                n_raven_surfaced=0,
                tokens_passthrough=0,
                tokens_raven=0,
                token_reduction_ratio=0.0,
                a_at_5_passthrough=False,
                a_at_5_raven=False,
                quality_delta=0,
            ))
        if progress_every and (i + 1) % progress_every == 0:
            elapsed = time.perf_counter() - t0
            print(
                f"  [{i+1:>3}/{len(questions)}] elapsed={elapsed:6.1f}s "
                f"avg={elapsed*1000/(i+1):6.1f} ms/q",
                flush=True,
            )

    return aggregate_token_records(
        records,
        config={
            "top_k": top_k,
            "k_for_quality": k_for_quality,
            "tokenizer": "tiktoken cl100k_base",
            "raven_version": "1.1.0",
            "calibration_profile": calibration_profile,
            "scorer": scorer,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Token-efficiency measurement for RAVEN on LongMemEval"
    )
    parser.add_argument(
        "--partition",
        choices=["calibration", "heldout", "all"],
        default="calibration",
        help=(
            "Which split to measure. 'heldout' requires the Day 5 marker — "
            "use heldout_guard.run_held_out_validation() programmatically "
            "in production."
        ),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--profile", type=str, default="factual",
        help="Calibration profile name (factual | chat_turn | ...).",
    )
    parser.add_argument(
        "--scorer", type=str, default=DEFAULT_SCORER,
        choices=["retrieval_ranked", "composite_ranked"],
        help=(
            "Scorer label recorded in the output config. Token "
            "efficiency's surface is the AURORA-approved set regardless "
            "of scorer (composite_ranked already filters to approved); "
            "this flag exists so per-scorer reports are self-describing."
        ),
    )
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    if args.partition == "calibration":
        questions = load_calibration_questions()
    elif args.partition == "heldout":
        if not held_out_unlocked():
            raise HeldOutAccessError(
                "Held-out partition is locked. Use the Day 5 unlock + "
                "heldout_guard.run_held_out_validation() workflow."
            )
        questions = load_questions(HELDOUT_PATH)
    else:
        # 'all' is intentionally only for debugging the harness itself
        # before the split exists. Never use for publishable numbers.
        from benchmarks.longmemeval.loader import find_dataset_path
        questions = load_questions(find_dataset_path())

    if args.limit:
        questions = questions[: args.limit]

    print(
        f"Token-efficiency · partition={args.partition} · profile={args.profile} "
        f"· scorer={args.scorer} · N={len(questions)}"
    )
    report = measure_corpus(
        questions, top_k=args.top_k, calibration_profile=args.profile,
        scorer=args.scorer,
    )

    overall = report.overall
    qc = report.quality_controlled_overall
    print()
    print("== overall ==")
    print(f"  N                          : {overall['n']}")
    print(f"  tokens passthrough total   : {overall['tokens_passthrough_total']:,}")
    print(f"  tokens RAVEN total         : {overall['tokens_raven_total']:,}")
    print(f"  refused                    : {overall['n_refused']}")
    print(f"  median token reduction     : {overall['token_reduction_stats']['median']*100:6.1f}%")
    print(f"  A@5 passthrough            : {overall['quality']['a_at_5_passthrough']*100:6.1f}%")
    print(f"  A@5 RAVEN                  : {overall['quality']['a_at_5_raven']*100:6.1f}%")
    print(f"  quality delta              : {overall['quality']['delta_pts']*100:+6.1f} pts")
    print()
    print("== quality-controlled headline (RAVEN >= passthrough on A@5) ==")
    print(f"  N                          : {qc['n']}  ({qc['fraction_of_corpus']*100:.1f}% of corpus)")
    print(f"  median token reduction     : {qc['headline_pct_reduction_at_equal_or_better_quality']*100:+6.1f}%")
    print()

    if args.output:
        Path(args.output).write_text(json.dumps(asdict(report), indent=2))
        print(f"wrote {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
