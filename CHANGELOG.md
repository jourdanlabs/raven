# RAVEN Changelog

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
