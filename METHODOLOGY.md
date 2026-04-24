# MUNINN Benchmark Methodology

## Overview

MUNINN is a held-out evaluation suite for AI memory systems. It tests whether a
system correctly surfaces valid memories and suppresses invalid ones across six
hazard modes that appear frequently in production deployments.

**Corpus:** 500 entries, deterministically generated. SHA-256 sealed.  
**Queries:** 200 scenarios (25 per hazard mode + 50 clean).  
**Reference time:** 2026-04-24 00:00:00 UTC (`_NOW = 1745481600.0`) — fixed so
scores are reproducible regardless of when the harness is run.

---

## Hazard Modes

### 1. Contradiction (75 entries, 25 queries)

Pairs of entries that directly conflict — one using absolutist language ("always",
"never") against an incompatible absolute in the other, or predicate-negation
where one entry denies what the other asserts.

**Detection criteria (PULSAR):**
- Both entries share ≥ 2 content words of length ≥ 4
- Both contain absolutist words from different sets, OR
- One has a negation marker and the other does not (with ≥ 3 shared words)
- Entries fall within the 90-day detection window

**Expected behavior:** conflicted entries are rejected; neutral supporting entries
that don't share the conflict are approved.

---

### 2. Staleness (75 entries, 25 queries)

Entries with explicitly expired validity windows (`validity_end` in the past) or
entries that have been formally superseded by a newer entry (`supersedes_id`).

**Detection criteria (ECLIPSE):**
- `validity_end` is set and `< now` → stale
- Another entry's `supersedes_id` references this entry's ID → superseded

**Expected behavior:** stale/superseded entries are rejected with score 0.0;
current replacement entries are approved.

---

### 3. Importance-Inversion (75 entries, 25 queries)

Groups where a trivial recent entry (source="agent", no decision keywords, 3 days
old) would rank above a critical older entry (source="decision_log", decision
keyword + importance marker, 35 days old) under naive recency-first ordering.

**Scoring design:**
- High-importance entry: QUASAR score ≈ 0.95, AURORA composite ≈ 0.84 (APPROVED)
- Low-importance entry: QUASAR score ≈ 0.56, AURORA composite ≈ 0.77 (REJECTED)

**Expected behavior:** RAVEN approves the important-but-older entry and rejects
the trivial-but-recent one. Naive recency-first baselines invert this.

---

### 4. Entity-Resolution (75 entries, 25 queries)

Three complementary entries about the same entity under different name forms
(e.g., "Leland" / "Captain Jourdan" / "LE Jourdan II"). No contradictions —
entries are additive, not conflicting.

**Expected behavior:** all three entries approved; PULSAR should NOT produce
false-positive contradictions on name-alias variations. Tests precision of
conflict detection.

---

### 5. Causal-Coherence (75 entries, 25 queries)

Three entries forming a causal chain: A (root cause) → B (intermediate effect) →
C (final outcome). Entry B contains a causal keyword ("consequently", "triggered",
etc.) and shares ≥ 2 content words with A; entry C similarly references B.

**Expected behavior:** NOVA builds edges A→B and B→C; B receives maximum causal
centrality bonus (0.10), pushing borderline entries over the approval threshold.
All three entries approved.

---

### 6. Refusal-Warranted (75 entries, 25 queries)

All three entries in the group have `validity_end` set to 60 days in the past.
AURORA assigns score 0.0 to each; the overall confidence falls below 0.30.

**Expected behavior:** AURORA returns REFUSED status. Systems that don't check
validity windows surface garbage data.

---

### 7. Clean (50 entries, 50 queries)

High-quality single-entry scenarios. Entries: source="decision_log", decision
keywords, recent timestamps (1–5 days old). No conflicts, no staleness.

**Expected behavior:** all entries approved with AURORA score ≈ 0.95–0.97.
Serves as a baseline regression test — a system that fails clean queries is
broken, not conservative.

---

## Scoring

For each query scenario, each baseline returns:
- `status`: `APPROVED | CONDITIONAL | REJECTED | REFUSED`
- `approved_ids`: set of entry IDs the baseline considers valid

Metrics per scenario:

| Metric | Formula |
|---|---|
| `status_match` | `actual_status == expected_status` |
| `precision` | `|approved ∩ expected| / |approved|` (1.0 if both empty) |
| `recall` | `|approved ∩ expected| / |expected|` (0.0 if expected non-empty and approved empty) |
| `F1` | `2 × precision × recall / (precision + recall)` |

Per-hazard aggregation: mean across all scenarios in that mode.

**Overall MUNINN score:** mean F1 across all 200 scenarios.

---

## Baselines

| Baseline | Description |
|---|---|
| `raw_passthrough` | Approve everything. Upper bound on recall, lower bound on precision. |
| `recency_filter` | Top-3 by timestamp only. No validation. |
| `simulated_zep` | Near-duplicate dedup by word overlap + recency sort. Approximates Zep graph-memory deduplication behavior. |
| `simulated_mem0` | Keyword importance + recency score with threshold. Approximates Mem0 LLM-importance scoring behavior. |
| `raven_retrieval_only` | TF-IDF cosine similarity to query, top-3 approved, no validation pipeline. Ablation isolating retrieval contribution. |
| `raven_full` | Complete RAVEN pipeline: NOVA → ECLIPSE → PULSAR → QUASAR → AURORA. |

**On simulated baselines:** Zep and Mem0 simulations are approximations based on
their documented behavior, not bindings to their actual SDKs. Results reflect
behavioral category differences, not exact per-product numbers.

---

## Results (2026-04-24)

| Baseline | F1 | Status Acc |
|---|---|---|
| raw_passthrough | 0.762 | 87.5% |
| recency_filter | 0.762 | 87.5% |
| simulated_zep | 0.762 | 87.5% |
| simulated_mem0 | 0.536 | 70.5% |
| raven_retrieval_only | 0.762 | 87.5% |
| **raven_full** | **0.889** | **94.0%** |

### Key differentiators

**refusal_warranted:** RAVEN 1.000 vs all non-mem0 baselines 0.000. The most
consequential hazard — surfacing expired data is worse than returning nothing.

**importance_inversion:** RAVEN 1.000 vs naive baselines 0.800. QUASAR's
composite scoring overrides recency order when importance diverges significantly.

**contradiction:** RAVEN 0.800 vs naive baselines 0.500. PULSAR detects and
rejects conflicted entries; the neutral supporting entry is correctly approved.

**causal_coherence:** RAVEN 0.812 — partial. Borderline entries benefit from
NOVA bonus, but not all causal entries reach AURORA approval threshold at
35-day-old decay. Area for improvement.

---

## Corpus Integrity

The corpus is SHA-256 sealed:

```
sha256: see benchmarks/muninn/corpus/corpus.sha256
```

The harness verifies the seal before scoring. To regenerate:

```bash
python3 -m benchmarks.muninn.corpus.generate
python3 -m benchmarks.muninn.scoring.harness --output results.json
```
