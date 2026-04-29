# RAVEN Changelog

## [1.2.1] — 2026-04-29 — Phase 2.2 fix-01 (now_override)

### Added

- **`RAVENPipeline.recall(now=...)` and `recall_v2(now=...)`** — optional
  corpus-relative reference time threaded into ECLIPSE. When supplied,
  decay is computed relative to `now` instead of wall-clock. When omitted
  (default `None`), behavior is byte-for-byte identical to v1.0 / v1.1 /
  v1.2.0. Pure additive; no breaking changes.
- LongMemEval harness + token-efficiency benchmark wired to pass
  `now=q.question_timestamp` per question.

### Resolved

- **LME-010** (architectural): ECLIPSE decay vs `time.time()` collapsed
  chat-turn AURORA composites. Gate now produces meaningful approvals on
  chat-turn data:

  | metric                                  | v1.2.0 | v1.2.1 | delta |
  | --------------------------------------- | -----: | -----: | ----: |
  | RAVEN APPROVED (calibration N=400)      |     3  |   117  | +114  |
  | RAVEN APPROVED (held-out N=100)         |     2  |    34  |  +32  |
  | Approval-quality median composite       |  0.676 | 0.663  | ≥ 0.6 floor preserved |
  | Quality-controlled token-reduction subset | 22.0% | 37.8%  | +15.8 pts |

### Known (new)

- **LME-012**: LongMemEval substring scorer ranks `(approved+rejected)`
  by `retrieval_score`, so AURORA's filtering doesn't propagate to A@5.
  Held-out A@5 unchanged at 79.8% even after LME-010 unblock. Tracked in
  `GAPS.md`; fix shape is a composite-ranked scorer variant.

### Tests

- 322 tests, 89% coverage (was 316 / 89% at v1.2.0).
- 6 new tests in `tests/test_pipeline.py` covering `now_override`
  determinism, default-preservation, and chat-turn composite rescue.

---

## [1.2.0] — 2026-04-29 — Phase 2.1 calibration sprint

### Added

**Calibration profile system** (`raven/calibration/`)
- `CalibrationProfile` — frozen bundle of calibration knobs (currently
  `aurora_threshold`; `decay_overrides` reserved for Phase 2.2).
- `RAVENPipeline(calibration_profile=...)` — name-keyed profile selection.
  Default `"factual"` preserves v1.0 behaviour byte-for-byte; explicit
  `aurora_threshold=...` still wins.
- Profile-scoped registry — no shared global state across profiles.
- Built-in profiles: `factual` (0.80), `chat_turn` (0.65).
- Tiny YAML loader (no PyYAML dependency).

**LongMemEval calibration sprint** (`benchmarks/longmemeval/`)
- 80/20 corpus split (seed=42, sealed in `phase2.1_split.md`).
- Held-out access fence (`heldout_guard.run_held_out_validation()`)
  requires Day 5 marker file; architectural enforcement, not policy.
- Token-efficiency instrumentation (`token_efficiency.py`, tiktoken
  cl100k_base; quality-controlled headline only).
- Per-fix attribution doc + loss profile + held-out validation report.

### Phase 2.1 results

* **Calibration profile system shipped.** Backward-compat preserved.
* **chat_turn profile validates direction, magnitude bounded by LME-010.**
  AURORA approves 3/400 (calibration) and 2/100 (held-out). Approval
  quality 0.676 (above 0.6 audit floor). No off-target regression.
* **Token-efficiency wedge: DISCONFIRMED at v1.1.** Published honestly.
* **Held-out A@5 = 79.8%** (single shot, chat_turn profile).

### Tests

- 316 tests, 89% coverage (was 280 / 89% at v1.1.0).
- 36 new tests covering split determinism, held-out guard, token
  efficiency, calibration profile YAML, AURORA threshold threading.

### Optional dependencies

- New `[bench]` extra installs `tiktoken` for token-efficiency runs.

---

## [1.1.0] — 2026-04-29 — Phase 1 capabilities + LongMemEval cold baseline

### Added

**Capability 1.1 — Contradiction Reconciliation** (`raven/reconciliation.py`)
- `reconcile()` function turns PULSAR contradictions into typed
  `ResolvedClaim` verdicts via a four-rule hierarchy: identity-protect,
  well-grounded supersedes superseded, evidence-strength comparison,
  temporal recency tiebreak.
- `ReconciliationContext` carries METEOR entities, NOVA causal edges,
  QUASAR importance scores into the reconciliation decision.
- `RAVENPipeline.reconcile_contradictions()` — convenience method that
  runs PULSAR detection + reconciliation in one pass.
- Audit hash (SHA-256 of winner.id + loser.id + basis + evidence_chain)
  on every `ResolvedClaim` for chain-of-custody replay.

**Capability 1.2 — Decay-Aware Recall** (`raven/decay/`)
- `DecayPolicy` per-class registry with 6 built-in policies:
  `factual_short` (1d half-life), `factual_long` (30d), `preference`
  (90d), `transactional` (4h), `contextual` (7d), `identity` (no decay).
- `MemoryEntry.memory_class` field + transitional `metadata['memory_class']`
  fallback for older corpora.
- `eclipse.apply_class_aware_decay()` — class-aware path; v1.0 functions
  (`apply_decay`, `decay_weight`, `is_stale`, etc.) preserved unchanged.

**Capability 1.3 — Structured Refusal** (`raven/refusal.py`)
- `RefusalReason` taxonomy: `insufficient_evidence`,
  `conflicting_evidence_unresolvable`, `staleness_threshold_exceeded`,
  `identity_ambiguous`, `scope_violation`.
- `AuroraVerdict` — superset of v1.0 `RavenResponse`; existing callers
  unchanged. New `RAVENPipeline.recall_v2()` returns typed verdict.
- `validate_aurora_v2()` — companion to `run_aurora()` that produces the
  typed verdict. v1.0 `run_aurora` entrypoint unchanged.
- `scope_allowlist` parameter on `recall_v2` — refuses with
  `type="scope_violation"` BEFORE any retrieval when query content tokens
  fall outside the declared topic allowlist.
- Audit hash on every `RefusalReason` and `AuroraVerdict`.

**Phase 1 type surface** (`raven/types/phase1.py`)
- `Memory` (alias for `MemoryEntry`), `EvidenceNode`, `MemoryClass`,
  `ResolvedClaim`, `DecayPolicy`, `RefusalReason`, `AuroraVerdict`.
- All `frozen=True`. `AuroraVerdict.__post_init__` enforces
  decision/refusal_reason coherence.

**LongMemEval cold-run baseline** (`benchmarks/longmemeval/`)
- Loader, harness, scorer for the 500-instance LongMemEval oracle
  corpus.
- v1.0 cold-run results: A@5 = 73.4%, A@10 = 80.0%, sR@5 = 94.9%,
  abstention 100%. AURORA refused 473/500 questions (chat-turn vs
  fact-style threshold mismatch — addressed in Phase 2.1).
- p50 latency 50 ms.

### Tests
- 280 tests, 89% coverage (was 87 / 94% at v1.0.0).
- Phase 1 capability sub-corpora at 100% on sealed reconciliation,
  decay, and refusal benchmarks.

---

## [1.0.0] — 2026-04-24

Initial public release.

### Added

**Core types** (`raven/types.py`)
- `MemoryEntry` — memory unit with text, timestamp, validity window, entity tags,
  supersedes chain, and arbitrary metadata
- `ScoredMemory` — entry + composite AURORA score + per-engine breakdown
- `Contradiction` — flagged conflict between two entries with type and confidence
- `CausalEdge` — directed causal relationship between entries (NOVA output)
- `PipelineTrace` — per-request audit log (entity count, edge count, latency, etc.)
- `AuroraInput` — unified input bundle for the AURORA gate
- `RavenResponse` — complete pipeline response with status, approved memories,
  flagged contradictions, rejected memories, and pipeline trace

**Validation pipeline** (`raven/validation/`)
- **METEOR** — entity extraction and alias resolution with Levenshtein-distance
  fuzzy matching and configurable alias map
- **NOVA** — causal chain construction from CAUSAL_KEYWORDS + word overlap;
  computes entry centrality for AURORA bonus (max +0.10)
- **ECLIPSE** — exponential temporal decay (configurable half-life, default 30d);
  staleness detection via `validity_end` and `supersedes_id` chains
- **PULSAR** — contradiction detection: absolutist word conflict, predicate
  negation, and temporal supersedence; 90-day active detection window
- **QUASAR** — importance ranking via decision keywords, source authority weights,
  recency boost, importance markers, and causal centrality bonus
- **AURORA** — composite confidence gate; WEIGHTS = {eclipse: 0.25, quasar: 0.45,
  pulsar: 0.30}; thresholds APPROVE=0.80, CONDITIONAL=0.60, REFUSE=0.30

**Storage** (`raven/storage/`)
- `TFIDFEmbedder` — deterministic character-trigram + word-unigram embedder;
  512-dim hash-bucketed, L2-normalized; no model download required
- `SentenceTransformerEmbedder` — optional semantic backend (opt-in)
- `RAVENStore` — SQLite-backed hybrid search: semantic cosine similarity +
  keyword BM25-style scoring + temporal recency + entity tag weighting

**Pipeline** (`raven/pipeline.py`)
- `RAVENPipeline` — orchestrates full METEOR→NOVA→ECLIPSE→PULSAR→QUASAR→AURORA
  flow with configurable `top_k`, `aurora_threshold`, `half_life_days`

**CLI** (`cli/raven.py`)
- `raven recall <query>` — retrieve and validate memories, print ranked results
- `raven remember <text>` — ingest a new memory entry
- `raven ingest <file>` — bulk ingest from JSONL file
- `raven status` — print store statistics and pipeline configuration

**MUNINN Benchmark** (`benchmarks/muninn/`)
- Corpus: 500 entries across 6 hazard modes (contradiction, staleness,
  importance-inversion, entity-resolution, causal-coherence, refusal-warranted)
  plus 50 clean retrieval entries; SHA-256 sealed
- Scoring harness with 6 baselines: raw passthrough, recency filter,
  simulated Zep, simulated Mem0, retrieval-only ablation, and RAVEN full
- Per-hazard metrics: F1, precision, recall, status accuracy

**Results (MUNINN v1.0):**

| Baseline | F1 | Status Acc |
|---|---|---|
| raw_passthrough | 0.762 | 87.5% |
| recency_filter | 0.762 | 87.5% |
| simulated_zep | 0.762 | 87.5% |
| simulated_mem0 | 0.536 | 70.5% |
| raven_retrieval_only (ablation) | 0.762 | 87.5% |
| **raven_full** | **0.889** | **94.0%** |

**Tests**
- 87 tests, 94% coverage
- Full suite: `pytest tests/ --cov=raven`

---

## Roadmap

Tracked in `GAPS.md`. Highest-priority items:

- **LME-002** — LLM-answerer adapter for synthesis-required categories
  (~71% of remaining LongMemEval A@5 loss is synthesis, not retrieval).
- **LME-012** — Composite-ranked harness scorer variant so the AURORA
  gate's surfacing rate can affect A@5 (the LME-010 fix unblocked the
  gate; LME-012 is what lets the unblock register on the metric).
- **LME-003** — `SentenceTransformerEmbedder` for paraphrase recall.
- **LME-004** — Date-aware query intent ("first", "latest", "before").
- **LME-005** — Wire official LongMemEval `evaluate_qa.py` LLM-judge.
- **GAPS-003** — PostgreSQL/Redis storage backend.
- **GAPS-001** — Semantic contradiction detection via embedding distance.
- **[2.0.0]** — Multi-user / shared store support.
