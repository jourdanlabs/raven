# RAVEN — Known Gaps and Limitations

## Validation Pipeline

### PULSAR false-negative rate
PULSAR uses surface-level pattern matching (absolutist words, negation markers,
word overlap). Semantic contradictions that don't use absolutist language or
explicit negation are not detected. Example: "The server is online" vs "The
server went down at 2am" would not be flagged because there are no shared
content words and no absolutist/negation patterns.

**Impact:** Medium. Most production contradictions involve explicit "always/never"
language or clear predicate negation. Deep semantic contradiction detection would
require an LLM-based component.

### ECLIPSE decay granularity
The half-life model uses a single global decay rate (default 30 days). Entries
of very different temporal significance (a "today's meeting time" entry vs a
"founding decision" entry) decay at the same rate. A per-entry importance-weighted
decay would be more accurate.

**Impact:** Low. QUASAR importance scoring partially compensates by up-weighting
high-significance entries in AURORA's composite.

### NOVA causal detection scope
NOVA only detects causal edges between entries that explicitly use causal
keywords ("therefore", "consequently", etc.). Implied causality ("Service
restarted. Latency improved.") is not detected.

**Impact:** Low–Medium. The causal bonus is additive and bounded at 0.10, so
missed causal edges reduce AURORA scores slightly rather than causing rejections.

### QUASAR recency ceiling
QUASAR's recency boost is capped at +0.15 (entries < 1 day old). Very recent
entries get only a small boost relative to their importance score. This is
intentional but means flash news may underweight compared to stable facts.

**Impact:** Low. By design — importance should dominate recency for most queries.

---

## Storage and Retrieval

### TF-IDF trigram embedder limitations
The default TFIDFEmbedder uses character trigrams bucketed into 512 dimensions.
It handles typos and subword overlap well but is poor at:
- Semantic similarity without lexical overlap ("car" vs "automobile")
- Cross-lingual queries
- Very short texts (< 5 words)

**Mitigation:** The `SentenceTransformerEmbedder` opt-in backend handles semantic
similarity. Requires `pip install raven[semantic]`.

### SQLite single-writer bottleneck
RAVENStore uses SQLite which is single-writer. High-throughput ingest (> ~500
entries/second) will serialize on WAL locks.

**Impact:** Low for current use cases. RAVEN is designed for personal and
small-team deployment where ingest volume is moderate.

### No distributed storage
The store is file-local (SQLite + JSONL corpus). No built-in support for
multi-node deployments or shared stores across users.

**Impact:** Medium for enterprise use cases. Acceptable for v1.0 scope.

---

## Benchmark (MUNINN)

### Simulated baselines are approximations
The `simulated_zep` and `simulated_mem0` baselines approximate documented
behavior, not SDK bindings. Real Zep and Mem0 deployments may perform better
or worse than their simulations on specific hazard modes.

### Corpus is synthetic
All 500 entries were deterministically generated. Real-world memory corpora
have different noise characteristics, typos, ambiguous phrasing, and entity
resolution challenges that the synthetic corpus does not capture.

### No multi-turn or session-level evaluation
MUNINN evaluates single-query, pre-selected entry sets. It does not test:
- Multi-turn conversation state tracking
- Session boundary detection
- Entity graph consistency across sessions

### Causal-coherence recall gap (0.707)
Seven of 25 causal-coherence scenarios have entries that don't reach the 0.80
AURORA threshold despite NOVA bonus. Root cause: these entries use "triggered"
or "started" (lower QUASAR keyword scores: 0.58) as causal markers, combined
with 30-day-old decay. Addressed in GAPS-002 tracking item.

---

## Open Tracking Items

| ID | Area | Description |
|---|---|---|
| GAPS-001 | PULSAR | Add semantic contradiction detection via embedding cosine distance |
| GAPS-002 | Corpus | Adjust causal-coherence entry text to ensure all 25 scenarios cross approval threshold |
| GAPS-003 | Storage | Add optional PostgreSQL/Redis backend for enterprise deployments |
| GAPS-004 | METEOR | Expand entity alias dictionary beyond 50 default entries |
| GAPS-005 | Benchmark | Add real-world corpus sampled from anonymized production sessions |
| GAPS-006 | Baselines | Add actual Zep and Mem0 SDK bindings for direct comparison |
| GAPS-007 | Reconciliation | `MemoryEntry.memory_class` field is added by Sub B (capability 1.2). Until that lands, `raven.reconciliation.derive_memory_class()` infers via `metadata['memory_class']` → `topic_tags` → `"contextual"` fallback. Once 1.2 ships, swap derive_memory_class call sites for `entry.memory_class`. |
| GAPS-008 | Reconciliation | METEOR's `tag_entities()` matches by lowercase substring against the canonical alias registry. Entities not in the registry produce zero overlap → identity rule cannot fire. Workaround: register custom entities via `METEORConfig(extra_aliases=...)` per-deployment. Long-term: add free-form NER fallback. |
| GAPS-009 | Reconciliation | LLM-judge baseline in `corpus/muninn_v2/reconciliation/scoring/run_baselines.py` is a stub. Real GPT-4-class call (median of 5) costs ~$0.15 per pair × 25 pairs = ~$4 per full run. Replace `_call_llm_judge()` when budget allows. |
| GAPS-010 | Reconciliation | Rule (c) uses NOVA weighted edge-depth (`sum(edge.weight)` for incident edges), not raw count. NOVA's word-overlap heuristic is permissive enough that both sides of a pair often pick up at least one chain edge — weighted depth is what discriminates. Document the weight choice; raw count would have produced ties for ~all evidence_strength corpus pairs. |

---

## Capability 1.1 — Reconciliation (added 2025-04-29)

### PULSAR detection blind spots affect raven_v1 baseline

The MUNINN v2 reconciliation corpus contains 25 pairs split into two
realism modes per basis: ~half use absolutist/negation language that v1.0
PULSAR can detect, and ~half are paraphrased restatements (e.g. "TJ prefers
email" vs "TJ prefers Telegram") that PULSAR's surface heuristics cannot
flag. The raven_v1 baseline scores ~18% as a result — it correctly
refuses on the PULSAR-detectable subset (0.5 partial credit each) and
gets nothing on the rest. This honestly reflects v1.0's contradiction
blindspot; addressing it requires GAPS-001 (semantic contradiction
detection).

### Identity-rule confidence floor

`reconcile()` floors reconciliation confidence at 0.5 even when the rule
fires deterministically (e.g., identity always wins). This is a deliberate
choice: confidence reflects RAVEN's confidence in *the act of
reconciling*, not in the winner's content. A future refinement may raise
the floor for non-reconcilable classes (identity) since those rules are
effectively rule-based, not score-based.
