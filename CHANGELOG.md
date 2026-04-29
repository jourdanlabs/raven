# RAVEN Changelog

## [1.2.0] — Phase 2.1 calibration sprint (PENDING — Captain merges + tags)

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

- **[1.1.0]** PostgreSQL/Redis storage backend (GAPS-003)
- **[1.1.0]** Semantic contradiction detection via embedding distance (GAPS-001)
- **[1.2.0]** Real-world corpus sampled from production sessions (GAPS-005)
- **[1.2.0]** Direct Zep and Mem0 SDK baseline bindings (GAPS-006)
- **[2.0.0]** Multi-user / shared store support
