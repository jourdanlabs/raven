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

---

## Capability 1.3 — Structured Refusal (added in this sub-agent's PR)

### Reconciliation handshake is loosely coupled
`raven.refusal.classify_refusal` accepts `resolved_claim_count` as a plain
integer, not a list of `ResolvedClaim` objects. This is intentional for the
sprint — Sub-A's reconciliation surface is on a parallel branch and the
contract has not yet stabilized — but it means the classifier cannot inspect
*which* contradictions were resolved, only how many. Once Sub-A's
`ResolvedClaim` API lands on main, the classifier should accept the list and
cross-reference it against `aurora_input.contradictions` to detect partial
resolutions per-pair (the current `len(contradictions) > resolved_count`
check is a conservative approximation).

**Impact:** Low for now (no Sub-A wiring on this branch), Medium once Sub-A
merges. Tracked as GAPS-007 below.

### Scope allowlist is substring-based, not semantic
`scope_violation` matches query tokens against allowlist entries via
case-insensitive substring containment in either direction. This handles
common operator-defined topic prefixes (e.g., "billing" matching
"billings") but does **not** handle synonyms ("payment" vs "remittance"),
multilingual queries, or hierarchical scopes (allowing "finance" should
arguably allow "billing" and "invoicing"). Operators must enumerate every
in-scope token explicitly.

**Impact:** Medium. Acceptable for v1 — operator policy is opaque to RAVEN.
Tracked as GAPS-008.

### Staleness floor is global, not per-class
`classify_refusal` accepts a `decay_floor` parameter, defaulting to
`0.05` (the lowest of the built-in `DecayPolicy` floors). When Sub-B's
class-aware decay policies land, the classifier should look up the
per-memory floor (via `MemoryClass.decay_curve`) instead of using a single
global floor. Today, an "identity"-class memory (floor=0.5) and a
"transactional"-class memory (floor=0.05) are both compared against `0.05`,
which under-counts staleness for the identity class.

**Impact:** Medium. Tracked as GAPS-009.

### LLM-judge baseline is a stub
The `LLM-judge` baseline in `corpus/refusal_benchmark/scoring/run_baselines.py`
emits deterministic random predictions across the five refusal types. It is
plumbed end-to-end so a future implementer can swap in a real chat-completion
call without touching the harness, but **the published precision number for
LLM-judge is not a real comparison**. Tracked as GAPS-010.

### Refusal benchmark corpus is templated, not adversarial
The 200-query corpus follows fixed templates (40 per type) chosen to
exercise the classifier's branches predictably. RAVEN_v2 scores 100% on it
because the templates were designed to fall cleanly into one bucket each.
A real-world adversarial corpus (queries that genuinely sit on the boundary
between two types) would lower precision and is needed before claiming
broad coverage. Tracked as GAPS-011.

| GAPS-007 | Refusal | Wire `ResolvedClaim` list into `classify_refusal` once Sub-A merges |
| GAPS-008 | Refusal | Replace substring scope-allowlist with semantic / hierarchical match |
| GAPS-009 | Refusal | Use class-specific decay floors once Sub-B's policy registry lands |
| GAPS-010 | Refusal | Replace `LLM-judge` stub with a real LLM call |
| GAPS-011 | Refusal | Add an adversarial-boundary refusal corpus (mixed-type ambiguous queries) |
