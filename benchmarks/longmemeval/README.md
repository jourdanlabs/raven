# LongMemEval Benchmark for RAVEN

Cold-run harness that scores `raven.pipeline.RAVENPipeline` against the
[LongMemEval](https://github.com/xiaowu0162/LongMemEval) benchmark
(Wu et al., ICLR 2025). 500 questions across six question_types
(`single-session-user`, `single-session-assistant`,
`single-session-preference`, `multi-session`, `knowledge-update`,
`temporal-reasoning`).

> **The current cold-run results live in [`v1.0-cold-results.md`](v1.0-cold-results.md).**
> Read that first.

## What this is

A read-only benchmark — it does not modify any RAVEN engine code. For each
LongMemEval question we:

1. Build a fresh `RAVENStore(":memory:")` and `RAVENPipeline(store, top_k=20)`.
2. Ingest each turn of the haystack as a `MemoryEntry` (timestamps from the
   session date, no entity_tags / topic_tags injected).
3. Call `pipeline.recall(question)`.
4. Score retrieval at session/turn level + answer-substring presence in top-k.
5. Score abstention questions (qids ending in `_abs`) as correct if RAVEN
   REFUSED OR no top-5 entry contained the gold answer substring.

We use defaults across the board (no tuning). See `v1.0-cold-results.md`
"Configuration" for the exact knobs.

## Why we don't use the official `evaluate_qa.py`

LongMemEval's official evaluator routes a memory system's *natural-language
answers* through GPT-4o as a judge. RAVEN v1.0 is a memory-validation
pipeline that returns **ranked memory entries**, not synthesized answers.
A like-for-like comparison would require gluing an LLM answerer in front of
RAVEN — out of scope for the cold-run baseline (and would conflate
retrieval quality with answer-generation quality).

We instead use the LongMemEval authors' published *retrieval-style* metrics
(`print_retrieval_metrics.py`): session-level recall@k, turn-level recall@k,
plus an additional answer-substring hit metric in top-k. These metrics are
reproducible without any external API calls.

## Required dependencies

- Python 3.11+
- `numpy>=1.26` (already a RAVEN dependency)
- `requests` only if you fetch via Python — `curl` is fine

No external dataset library is required — the harness reads the official JSON
directly.

## Acquiring the dataset (~15 MB)

The dataset is **not vendored** into this repo. Fetch it from Hugging Face:

```bash
mkdir -p /tmp/longmemeval_data
cd /tmp/longmemeval_data
curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json
```

The harness looks for the file at:
1. `$LONGMEMEVAL_DATA` (env var, if set)
2. `/tmp/longmemeval_data/longmemeval_oracle.json`
3. `~/.cache/longmemeval/longmemeval_oracle.json`

To use a custom path:

```bash
LONGMEMEVAL_DATA=/path/to/longmemeval_oracle.json \
    .venv/bin/python -m benchmarks.longmemeval.harness
```

## Running

From the repo root:

```bash
# Smoke test (5 questions, ~0.5 s)
.venv/bin/python -m benchmarks.longmemeval.harness --limit 5

# Full run (500 questions, ~40 s)
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --output v1.0-cold-results.json
```

`PYTHONHASHSEED=0` is recommended for reproducibility — RAVEN's
`TFIDFEmbedder` uses `hash(token) % dim` for vector bucketing, so without a
fixed hash seed each Python process produces slightly different embeddings.
The variance is ~1–2% on the headline numbers and does not change rank-order
of categories, but pin the seed if you want bit-stable comparisons.

### Flags

| Flag        | Default            | Description                                            |
| ----------- | :----------------: | ------------------------------------------------------ |
| `--data`    | (auto)             | Override path to `longmemeval_oracle.json`             |
| `--limit N` | None               | Run only first N questions (smoke test)                |
| `--top-k`   | 20                 | RAVEN pipeline `top_k`                                 |
| `--profile` | `factual`          | Calibration profile (`factual` | `chat_turn` | …)      |
| `--scorer`  | `retrieval_ranked` | Scoring variant — see "Scorer variants" below          |
| `--output`  | None               | Write per-question + aggregate JSON                    |
| `--quiet`   | False              | Suppress progress + report; useful from other scripts  |

### Scorer variants

Two scorers ship in `benchmarks/longmemeval/scorers/`:

| Name                | Behaviour                                                                     |
| ------------------- | ----------------------------------------------------------------------------- |
| `retrieval_ranked`  | Default. Ranks `(approved + rejected)` by `retrieval_score` descending. AURORA's gate is **invisible** to A@k under this scorer. Reproduces the v1.0 → v1.2.1 published numbers byte-for-byte. |
| `composite_ranked`  | Phase 2.2 fix-02 / LME-012. Ranks the union by `retrieval_score × aurora_weight` (1.0 for approved, 0.0 for rejected). Propagates AURORA's filtering into A@k. The substring matcher is unchanged — only the ranking is different. |

The substring matcher (`answer_substring_hit`) is shared between the two
variants; only the ranking step differs. The default is
`retrieval_ranked` so existing scripts and reproducibility recipes
written against v1.2.1 produce identical numbers without any flag
change.

```bash
# v1.2.1 baseline (no flag needed):
.venv/bin/python -m benchmarks.longmemeval.harness --profile chat_turn

# fix-02 / LME-012 ranking — gate-aware A@k:
.venv/bin/python -m benchmarks.longmemeval.harness \
    --profile chat_turn --scorer composite_ranked
```

The same `--scorer` flag is accepted by
`benchmarks.longmemeval.token_efficiency`; it is recorded in the output
JSON's `config` block for audit but is informational on that surface
(the token-efficiency calculation already operates on the
AURORA-approved set, regardless of scorer choice).

## How long it takes

| Environment                              | Wall time         |
| ---------------------------------------- | ----------------- |
| MacBook (M-series), in-process, 500 Qs   | ~40 s             |
| Smoke test (5 Qs)                        | <1 s              |

A 500-question run is ~80 ms/question on average; latency scales with
haystack size, dominated by ECLIPSE+QUASAR sweeps over all retrieved entries.

## Files

| File                                | Purpose                                                  |
| ----------------------------------- | -------------------------------------------------------- |
| `harness.py`                        | Runner: builds pipeline per question, ingests, scores    |
| `loader.py`                         | Loads `longmemeval_oracle.json` into typed dataclasses   |
| `scorer.py`                         | Re-export shim (preserves the v1.2.1 public symbols)     |
| `scorers/__init__.py`               | Scorer dispatcher (`get_ranker`, `RANKERS`, default)     |
| `scorers/retrieval_ranked.py`       | Default scorer — ranks by `retrieval_score` descending    |
| `scorers/composite_ranked.py`       | LME-012 scorer — ranks by `retrieval_score × aurora_weight` |
| `token_efficiency.py`               | Token-payload measurement vs naïve passthrough           |
| `heldout_guard.py`                  | Day-5 marker fence around the held-out partition         |
| `v1.0-cold-results.md`              | The canonical v1.0 cold-run report                       |
| `README.md`                         | This file                                                |

## Known blockers / caveats

1. **The dataset is not vendored.** If you need to re-run offline, mirror it
   somewhere your environment can reach.
2. **No semantic embeddings in cold-run.** Install the optional extra
   (`pip install 'raven[semantic]'`) and patch `RAVENStore(embedder=...)` in
   `harness.py` if you want to A/B against TFIDF — that becomes a Phase 2.1
   experiment, not a cold-run.
3. **Substring scoring under-credits paraphrase.** A query+gold pair like
   ("What breed?", "Golden Retriever") only gets credit if "Golden Retriever"
   appears verbatim in a retrieved turn. This is intentional for the cold-run
   — it gives a strict lower bound and avoids LLM-judge variability.
4. **METEOR's DEFAULT_ALIASES contains JL-domain entities** that don't
   overlap with LongMemEval content. They are loaded but inert.

## Re-running and adding a new results report

Add a new file (e.g., `v1.1-results.md`) — **do not overwrite
`v1.0-cold-results.md`**. The cold-run baseline is canonical and is the bar
later runs are measured against.
