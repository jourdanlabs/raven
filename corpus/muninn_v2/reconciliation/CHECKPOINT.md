# MUNINN v2 — Reconciliation corpus checkpoint

_Generated 2026-04-29T06:39:41Z_

## Corpus

- **SHA-256**: `5ec679af53bcbb610d491133f1759bd07534abc467c68c2783f64d8d901c90af`
- **Total entries**: 100
- **Reconciliable pairs**: 25
- **Distractors**: 50

### Per-basis pair counts

| basis | count |
|---|---|
| evidence_strength | 6 |
| identity | 6 |
| importance | 6 |
| temporal | 7 |

## Baseline scores

| baseline | overall | temporal | importance | evidence_strength | identity |
|---|---|---|---|---|---|
| `pass_through` | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `mempalace_passthrough` | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `raven_v1` | 0.180 | 0.286 | 0.250 | 0.000 | 0.167 |
| `raven_v2` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `llm_judge_stub` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Baseline notes

- `pass_through` — returns both memories, no reconciliation. Floor.
- `mempalace_passthrough` — best-faith model of MemPalace's surface-both behavior.
- `raven_v1` — v1.0 PULSAR detects contradiction → refuses (no winner). Scored 0.5 per refusal as partial credit (correctly identifies conflict, cannot reconcile).
- `raven_v2` — calls `raven.reconciliation.reconcile()`. Target: >= 0.90.
- `llm_judge_stub` — **STUB ONLY**. Returns labelled winner directly. Real implementation requires GPT-4-class call (median of 5). Replace `_call_llm_judge()` in `run_baselines.py` to enable.

## Reproduction

```bash
.venv/bin/python corpus/muninn_v2/reconciliation/build_corpus.py
.venv/bin/python corpus/muninn_v2/reconciliation/scoring/run_baselines.py
```

## Sealed

Corpus is reproducible: `build_corpus.py` is deterministic (timestamps anchored to `ANCHOR_TS = 2025-01-01T00:00:00Z`, all ids stable strings). Re-running yields byte-identical entries → identical SHA-256.
