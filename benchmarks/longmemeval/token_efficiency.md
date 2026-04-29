# LongMemEval — RAVEN token-efficiency report

**Sprint:** Phase 2.1 (calibration + token-efficiency).
**Run dates:** 2026-04-29.
**Tokenizer:** `tiktoken` `cl100k_base` (OpenAI standard).
**Methodology disclosure:** Token efficiency is OBSERVED, not OPTIMIZED.
The chat_turn AURORA threshold (0.65) was set from composite-formula
arithmetic on chat-turn empirics, not from token-efficiency tuning.
Reporting follows the brief's mandatory headline rule: *% token reduction
at equal-or-better answer quality* — never reported in isolation.

---

## Headline outcome (Phase 2.1)

**DISCONFIRMED.** RAVEN's chat_turn calibration profile reduces token
payload essentially to zero (RAVEN refuses 95-95% of queries on both
calibration and held-out partitions), but does so at large quality cost
on the substring-A@5 metric.

| partition   | passthrough tokens | RAVEN tokens | A@5 passthrough | A@5 RAVEN | quality delta | quality-controlled subset |
| ----------- | -----------------: | -----------: | --------------: | --------: | ------------: | ------------------------: |
| calibration | 1,892,489          | 3,165        | 69.8%           | 0.2%      | **−69.5 pts** | 122 / 400 (30.5%)         |
| held-out    | 527,478            | 262          | 78.0%           | 0.0%      | **−78.0 pts** | 22 / 100 (22.0%)          |

The quality-controlled subset (where RAVEN ≥ passthrough on A@5) shows
+100 % median token reduction, but inspection reveals this only counts
queries where passthrough also failed — RAVEN's "savings" are bought by
refusing on questions where neither system would have been right.

There is no honest token-efficiency wedge to publish at the v1.1
calibration. Per the brief's three publishable outcomes:

* **Confirmed**: ≥ 15 % token reduction at equal-or-better quality
* **Mixed**: tokens reduce but quality degrades
* **Disconfirmed**: filtering doesn't reduce token payload meaningfully

We are in **Disconfirmed**. The wedge narrative is killed for v1.1; the
methodology requires we publish that finding alongside the engineering
work that produced it.

## Per-category breakdown (calibration, factual baseline)

| question_type             | N  | tokens passthrough | tokens RAVEN | A@5 passthrough | A@5 RAVEN |
| ------------------------- | -: | -----------------: | -----------: | --------------: | --------: |
| knowledge-update          | 61 | 290k               | 0            | 86.0 %          | 0 %       |
| multi-session             | 105| 564k               | 0            | 49.5 %          | 0 %       |
| single-session-assistant  | 50 | 247k               | 0            | 100.0 %         | 0 %       |
| single-session-preference | 27 | 106k               | 0            | 22.2 %          | 0 %       |
| single-session-user       | 54 | 251k               | 0            | 98.1 %          | 0 %       |
| temporal-reasoning        | 103| 434k               | 0            | 60.2 %          | 0 %       |

(Numbers from `/tmp/token_eff_baseline.json`. The factual baseline
RAVEN-tokens column is uniformly 0 because the v1.1 default AURORA
threshold approves nothing on chat-turn corpora.)

## Per-category breakdown (calibration, chat_turn calibration)

| question_type             | N  | tokens passthrough | tokens RAVEN | RAVEN tokens / Q on approvals |
| ------------------------- | -: | -----------------: | -----------: | ----------------------------: |
| knowledge-update          | 61 | 290k               | 0            | (no approvals)                |
| multi-session             | 105| 564k               | 0            | (no approvals)                |
| single-session-assistant  | 50 | 247k               | 636          | 1 question approved           |
| single-session-preference | 27 | 106k               | 2,529        | 2 questions approved          |
| single-session-user       | 54 | 251k               | 0            | (no approvals)                |
| temporal-reasoning        | 103| 434k               | 0            | (no approvals)                |

The 3,165 total RAVEN-surfaced tokens are concentrated in the two
categories the loss profile predicted (single-session-preference and
single-session-assistant). The volume is too small to materially affect
the headline ratio.

## Why the wedge is disconfirmed

The architectural blocker (LME-010 in GAPS.md, full diagnosis in
`docs/phase2.1/fixes/01_chat_turn_aurora_threshold.md`) is the ECLIPSE
exponential-decay-vs-`time.time()` interaction:

1. LongMemEval haystack timestamps are 2023-2024.
2. `time.time()` at recall is 2026.
3. Default 30-day half-life → `0.5^(700 days / 30) ≈ 1e-7`.
4. Composite = `decay*0.25 + importance*0.45 + pulsar*0.30 + nova_bonus`
   reduces to roughly `importance * 0.45 + 0.30`.
5. Empirical median composite across 599 retrieved entries on a
   30-question sub-sample: **0.21** (max 0.46).
6. No threshold ≥ 0.5 (the lowest value that preserves approval quality
   ≥ 0.6) can approve any meaningful fraction of these entries.

Threshold tuning alone cannot bridge this gap. Class-aware decay (which
would let "preference" memories use the 90-day-half-life policy with
floor 0.30) requires an upstream classifier — that's architectural,
not calibration, and out of Phase 2.1 scope per the brief's hard rule
(4).

Phase 2.2 needs to address the decay-vs-now interaction at ingest
before chat-turn AURORA approval rates can move enough to produce a
meaningful token-efficiency story.

## Reproducibility

```bash
cd /Users/sokpyeon/projects/raven
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition calibration --profile factual --output tok_eff_factual.json
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition calibration --profile chat_turn --output tok_eff_chatturn.json
# Held-out: use heldout_guard.run_held_out_validation() — see v1.1_calibrated_results.md
```
