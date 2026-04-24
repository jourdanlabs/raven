# RAVEN

**AI memory built for trust.**

Retrieval is a solved problem. Trust isn't.

---

## What RAVEN is

RAVEN is a full-stack AI memory system optimized for validation — not just retrieval. Most memory systems return what they find. RAVEN returns only what it can defend: validated, ranked, contradiction-free results with a confidence score and a full audit trail. When there's no trustworthy answer, RAVEN refuses rather than guessing.

## The problem

AI agents need memory. Existing systems optimize for retrieval — find relevant text, return it. That's necessary but not sufficient. Raw retrieval returns:

- Contradictory facts (both stored, neither resolved)
- Stale information (superseded but not flagged)
- Importance-inverted noise (semantically close but low-value results outranking high-value ones)
- Fragmented entity facts (same person stored under multiple names, never merged)
- Isolated facts that only make sense in causal context

RAVEN addresses the layer above retrieval: whether to trust what you found.

## The architecture

Local-first. MIT licensed. Deterministic pipeline — no LLM calls at v1 runtime. All engines run offline.

```
Query
  │
  ▼
RAVEN Retrieval ── hybrid search (semantic + keyword + temporal + entity)
  │
  ▼
METEOR ─── entity normalization (aliases → canonical forms)
  │
  ▼
NOVA ────── causal chain construction ("A caused B" linking)
  │
  ▼
ECLIPSE ─── temporal decay (exponential recency weighting, staleness detection)
  │
  ▼
PULSAR ──── contradiction detection (absolutist conflicts, predicate negation, supersede detection)
  │
  ▼
QUASAR ──── importance ranking (decisions > milestones > general > noise)
  │
  ▼
AURORA ──── confidence gate (threshold 0.80; below → REFUSED)
  │
  ▼
RavenResponse
  ├── approved_memories (scored, ranked)
  ├── flagged_contradictions
  ├── rejected_memories (with reasons)
  └── pipeline_trace (full per-engine audit)
```

Every response carries a pipeline trace. You can see exactly why each chunk passed or failed.

## Install

```bash
pip install raven
```

For semantic embedding upgrades (optional — TF-IDF is the default):
```bash
pip install "raven[semantic]"
```

## Quickstart

```python
import time, uuid
from raven.storage.store import RAVENStore
from raven.pipeline import RAVENPipeline
from raven.types import MemoryEntry

store = RAVENStore("~/.raven/raven.db")
pipeline = RAVENPipeline(store)

# Ingest memories
pipeline.ingest(MemoryEntry(
    id=str(uuid.uuid4()),
    text="TJ decided to ship RAVEN v1 on 2026-04-23",
    timestamp=time.time(),
    source="decision_log",
))

# Query with full validation
response = pipeline.recall("When was RAVEN v1 shipped?")
print(response.status)          # APPROVED / CONDITIONAL / REFUSED / REJECTED
print(response.overall_confidence)
for mem in response.top(3):
    print(mem.entry.text, "→", round(mem.score, 3))
```

## CLI

```bash
# Store a memory
raven remember "TJ decided to ship RAVEN v1 today" --source decision_log

# Query
raven recall "RAVEN ship decision"

# Stats
raven status

# Bulk ingest from JSONL
raven ingest memories.jsonl
```

## Benchmark — MUNINN

RAVEN is validated on MUNINN, a benchmark we designed to measure memory *validation* — the failure modes that retrieval-only systems don't address. MUNINN corpus: 500 entries, 6 hazard categories (contradiction, staleness, importance inversion, entity resolution, causal coherence, refusal-warranted), sealed SHA.

We also cross-publish on LongMemEval — the industry-standard retrieval benchmark. Results on both. Methodology on neither optimized post-hoc.

Benchmark results: `benchmarks/muninn/CHECKPOINT_A_RESULTS.md`

MemPalace is an excellent open-source retrieval system and defines the state of the art on LongMemEval. RAVEN approaches memory from a different architectural angle — validation-first — which is why we publish our own benchmark alongside theirs.

## Engines (v1 — all deterministic, no LLM calls)

| Engine | Role |
|--------|------|
| **METEOR** | Entity normalization — aliases → canonical forms via exact lookup + Levenshtein |
| **NOVA** | Causal chain construction — keyword detection + entity overlap → directed graph |
| **ECLIPSE** | Temporal decay — exponential recency weighting, staleness detection, supersede tracking |
| **PULSAR** | Contradiction detection — absolutist conflicts, predicate negation, temporal supersede |
| **QUASAR** | Importance ranking — decisions, milestones, recency, source authority, causal centrality |
| **AURORA** | Confidence gate — composite score ≥ 0.80 → APPROVED; no approved results → REFUSED |

v2 roadmap: LLM-upgraded NOVA (causal inference) and PULSAR (semantic contradiction) as opt-in features.

## Architecture decisions

**Why TF-IDF as the default embedder?**
Fully deterministic, zero model downloads, reproducible across environments. For production use cases that benefit from semantic similarity, swap in `SentenceTransformerEmbedder` — it implements the same interface.

**Why SQLite?**
Portable, zero-ops, testable with `:memory:`. RAVEN is local-first by design. Cloud backends are a future adapter concern.

**Why refuse rather than guess?**
An agent reasoning over corrupted memory reasons badly. A refused query is honest; a confidently wrong answer is dangerous. AURORA's refusal path is a first-class outcome, not an error state.

## License

MIT. Built by JourdanLabs CRUCIBLE division.

---

*RAVEN is named after Raven Lenore (2000–2020).*
