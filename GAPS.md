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

## LongMemEval Cold-Run Findings (Phase 2.1 baseline, 2026-04-27)

The first read-only cold-run of `RAVENPipeline` against LongMemEval surfaced
five structural gaps. None of these are bugs — they are scope boundaries of
v1.0 that Phase 2.1 should close. Full report at
`benchmarks/longmemeval/v1.0-cold-results.md`.

### LME-001 — AURORA over-refusal on chat-turn corpora
On 500 LongMemEval questions, **0** had any APPROVED memories. 473 / 500
returned status REFUSED. The AURORA `APPROVE_THRESHOLD = 0.80` plus the
default 30-day decay applied to raw chat turns keeps every entry below the
gate, even for queries where retrieval is healthy (session_recall@10 = 98.7%).
RAVEN's defaults are calibrated for fact-style memories (MUNINN-style), not
conversational turns.

**Impact:** High. AURORA gating is the user-facing trust signal; if it
refuses everything on chat data it's not useful as a gate. Phase 2.1 should
ship a corpus-aware threshold or a chat-turn-specific calibration profile.

### LME-002 — No answer synthesis (counting / yes-no / paraphrase)
RAVEN returns ranked memory entries, not synthesized answers. Categories
that require synthesis (multi-session counting, yes/no inference, rubric-
style preference responses) are penalized by substring scoring even when
retrieval is perfect. Examples:
- "How many model kits have I worked on?" → gold `5`, requires count across sessions
- "Is my mom using the same grocery list method?" → gold "Yes.", inference required
- "Recommend resources for video editing" → gold is a *rubric* describing desired response style

**Impact:** Medium. RAVEN is by-design a memory-validation layer, not an
answerer. But a downstream LLM-answerer adapter (cf. LME-005) is the natural
complement and unlocks the official LongMemEval metric.

### LME-003 — Lexical TFIDF undercredits paraphrase
TFIDFEmbedder is char-trigram lexical. Queries like "What breed is my dog?"
fail to retrieve "I have a Golden Retriever" even when both are in the same
session, because the lexical overlap is below the threshold of other
session turns that share more keywords with the query. Fix: enable
`SentenceTransformerEmbedder` opt-in and benchmark the A/B delta.

**Impact:** Medium–High. Single-session-user turn_recall@5 = 0.906 is good
but turn_recall@5 on multi-session and temporal-reasoning is 0.46/0.56 —
much lower. Semantic retrieval likely closes most of that gap.

### LME-004 — No date-aware query intent
Temporal-reasoning questions like "the *first* issue I had" require ordering
evidence by date. RAVEN's QUASAR+ECLIPSE weights are time-aware on the entry
side (recency bonus, decay) but not query-side (no detection of "first" /
"latest" / "before" / "after" intent). Phase 2.1 candidate: temporal-intent
classifier feeding a per-query temporal weight.

**Impact:** Medium. temporal-reasoning category is 30.7% miss rate vs 4.7%
for single-session-user.

### LME-005 — No LLM-answerer adapter for official LongMemEval metric
The cold-run reports retrieval-style metrics (session/turn recall + answer
substring in top-k). MemPalace's published 96.6% uses the official
`evaluate_qa.py` GPT-4o judge. To compare apples-to-apples we need an
adapter that routes RAVEN's top-k through an LLM and grades the synthesized
answer with the official judge. Defer to Phase 2.1 follow-on.

**Impact:** High for marketing/comparison narrative. Low for engineering
truth — retrieval-style metrics are more honest about what RAVEN itself
contributes vs. what a downstream LLM contributes.

### LME-006 — Per-question fresh-store overhead
Cold-run rebuilds a fresh `RAVENStore(":memory:")` per question. The 80 ms
median per-question latency is dominated by haystack ingest (30–80 turns).
For real deployments this is moot (you ingest once, query many times), but
for benchmark scaling this is an artifact to flag. Could be amortized by
ingesting all 500 haystacks once and indexing by question_id, but only if
we keep the per-question session boundary discipline.

**Impact:** Low. Only matters if benchmark wall-time becomes a bottleneck.

| ID       | Area      | Description                                                              |
| -------- | --------- | ------------------------------------------------------------------------ |
| LME-001  | AURORA    | Recalibrate APPROVE_THRESHOLD for chat-turn corpora                      |
| LME-002  | Pipeline  | Add LLM-answerer adapter for synthesis-required categories                |
| LME-003  | Retrieval | Enable + benchmark `SentenceTransformerEmbedder` for paraphrase recall    |
| LME-004  | Retrieval | Detect query temporal intent ("first", "latest", "before") and reweight  |
| LME-005  | Benchmark | Wire `evaluate_qa.py` so we can report the official LongMemEval QA score |
| LME-006  | Benchmark | (Optional) Amortize haystack ingest across questions                     |

---

## Capability 1.2 — Decay-Aware Recall (sub-agent B, 2026-04-27)

### DEC-001 — Heuristic classifier is regex-only
`raven.storage.migrations.classify_text` uses regexes + token lists to
backfill `memory_class` for legacy rows. It misses semantic patterns
(e.g. "the project codename is RAVEN" lands as `factual_long` but is
arguably identity-adjacent). The migration intentionally flags every
sub-threshold classification with `review_required = 1` so an operator
can fix them; long-term we should swap in an embedding-based classifier
(or an LLM judge with confidence calibration). The structure is in
place — `classify_text` is the only swap point.

**Impact:** Low for fresh installs (new entries get the right class at
ingest time). Medium for legacy v1.0 corpora — operator review is the
mitigation until an embedding classifier lands.

### DEC-002 — `raven/types.py` legacy module shadowed by `raven/types/` package
Step 0 introduced the `raven/types/` package but did not delete the
top-level `raven/types.py` module. Python's package-over-module
precedence means the package wins and the .py file is dead code, but
it shows up in coverage reports as 0% covered. Cleanup is out of scope
for Capability 1.2 (types/ is locked); recommend a one-line cleanup PR
after Phase 1 sub-agents merge.

**Impact:** Cosmetic. No functional consequence.

### DEC-003 — MemPalace + LLM-judge baselines are stubs
`corpus/decay_benchmark/scoring/run_baselines.py` ships
`baseline_mempalace_stub` (constant 0.5) and `baseline_llm_judge_stub`
(deterministic-seed random in [floor, 1.0]). They produce honest 0% /
~10% scores and are structured for swap-in once real implementations
exist. Documented in the file header so reviewers don't mistake them
for tuned baselines.

**Impact:** Low. The relative ranking against `no_decay` /
`uniform_decay` / `raven_v2` is still meaningful, and the stub presence
is loud in the JSON output.

### DEC-004 — Migration is opt-in for v0
`run_migrations()` only runs automatically on `RAVENStore` construction
when `RAVEN_RUN_MIGRATIONS=1` is set. The CLI's `raven migrate run`
also requires the flag as a safety interlock. Phase 2 should flip the
default and add a `raven migrate dry-run` to preview the backfill
before committing.

**Impact:** Low. Operator awareness — documented in CLI help text.

### DEC-005 — `confidence_at_ingest` multiplies the curve
`class_aware_weight` does `confidence_at_ingest * 0.5 ** (age / hl)`
floored at the policy floor. A 0.5-confidence-at-ingest entry can
therefore never exceed 0.5 even when fresh (modulo the floor). This
is the spec'd behaviour but worth flagging because future tuning of
QUASAR's importance scoring needs to compose with it.

**Impact:** None today; flagged for Phase 2 tuning.

| ID       | Area     | Description                                                                    |
| -------- | -------- | ------------------------------------------------------------------------------ |
| DEC-001  | Migrate  | Replace regex classifier in `classify_text` with embedding-based classifier    |
| DEC-002  | Cleanup  | Delete dead `raven/types.py` module (cleanup PR after Phase 1 merges)          |
| DEC-003  | Baselines| Replace MemPalace + LLM-judge stubs in DECAY scoring harness                   |
| DEC-004  | Migrate  | Flip `RAVEN_RUN_MIGRATIONS` default in Phase 2; add `raven migrate dry-run`    |
| DEC-005  | Decay    | Document/tune interaction between `confidence_at_ingest` and class-aware floor |
