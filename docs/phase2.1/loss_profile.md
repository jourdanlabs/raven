# Phase 2.1 — Calibration-set loss profile

**Status:** Day 1 deliverable. Profiled BEFORE any calibration change.
**Partition:** `benchmarks/longmemeval/calibration.json`
(SHA-256 `d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0`)
**Code under test:** RAVEN v1.1.0 (commit `984c083`), default profile (factual).
**Run:** `PYTHONHASHSEED=0 python -m benchmarks.longmemeval.harness --data benchmarks/longmemeval/calibration.json`
**Token efficiency:** `python -m benchmarks.longmemeval.token_efficiency --partition calibration`

---

## Headline numbers (calibration set, N=400)

| metric                   | value |
| ------------------------ | ----: |
| answerable N             | 376   |
| abstention N             |  24   |
| **A@5 (answerable)**     | **71.8%** |
| A@10 (answerable)        | 77.9% |
| sR@5  (answerable)       | 95.2% |
| tR@5  (answerable)       | 65.2% |
| abstention accuracy      | 100% (24/24) |
| AURORA APPROVED count    | **0 / 400** |

The A@5 number is reported from the harness's ranked
`approved_memories + rejected_memories` (sorted by `retrieval_score`).
**Every one of the 400 questions ended with `n_approved == 0`** — the v1.1
default AURORA gate (threshold 0.80, 30-day decay) never approved a single
memory across the entire calibration set. This duplicates the v1.0
cold-run finding (LME-001, "AURORA over-refusal on chat-turn corpora").

## Token efficiency baseline (calibration set)

`python -m benchmarks.longmemeval.token_efficiency --partition calibration`

| metric                                            | value |
| ------------------------------------------------- | ----: |
| total passthrough tokens (top-K=20 per question)  | 1,892,489 |
| total RAVEN-surfaced tokens (post-AURORA)         | 0     |
| refused                                            | 378 / 400 |
| naïve median token reduction                       | 100.0% |
| naïve A@5 passthrough                              | 69.8%  |
| naïve A@5 RAVEN                                    | 0.0%   |
| naïve quality delta                                | **−69.8 pts** |
| quality-controlled subset (RAVEN ≥ passthrough on A@5) | 121 / 400 (30.2%) |
| quality-controlled median reduction                | +100% |

This is the **methodology trap** the brief warns about: "100% token
reduction" is real but bought at −70 pts of A@5. The headline number
RAVEN can publish honestly is the *quality-controlled* one, and even that
is bought today by RAVEN refusing every question that wasn't already a
loss for passthrough.

The token-efficiency wedge is currently **disconfirmed at the v1.1
defaults**. Whether it becomes confirmed under chat-turn calibration is
the empirical question of Phase 2.1; the methodology requires we report
the answer either way.

---

## Failure-by-category breakdown (calibration set)

A "failure" is an answerable question whose top-5 contained no
gold-answer substring hit (the metric used for the A@5 headline).

| question_type             | N | failures | miss% | share-of-loss | cum |
| ------------------------- | -: | -------: | ----: | ------------: | --: |
| multi-session             | 96 | 43 | 44.8% | 40.6% |  40.6% |
| temporal-reasoning        | 98 | 32 | 32.7% | 30.2% |  70.8% |
| single-session-preference | 27 | 21 | 77.8% | 19.8% |  90.6% |
| knowledge-update          | 57 |  8 | 14.0% |  7.5% |  98.1% |
| single-session-user       | 48 |  2 |  4.2% |  1.9% | 100.0% |

Five categories cover 100 % of A@5 loss. The brief asks for the top 5
that account for `>80 %` — the natural cutoff falls at the same five
categories so there is no ambiguity about scope.

## RAVEN status distribution on answerable questions

| status      | N   | %    |
| ----------- | --: | ---: |
| REFUSED     | 354 | 94.1% |
| REJECTED    |  20 |  5.3% |
| CONDITIONAL |   2 |  0.5% |
| APPROVED    |   0 |  0.0% |

`APPROVED == 0` across the entire calibration set means the AURORA gate
is currently a no-op for ranking purposes (the harness re-ranks
`approved + rejected` by `retrieval_score`) but the **token-efficiency
finding above shows the gate IS the wedge for token payload**. Until the
gate approves *something* on chat-turn data, RAVEN's token-efficiency
story is "we send nothing, so quality collapses".

---

## Top-5 failure categories with category-level diagnoses

The motivations below are deliberately category-level. The brief's
methodology rule (2) rejects any per-question hypothesis. Where a
specific question is mentioned it is illustrative of the category, not
the target — calibration changes will be measured against the entire
category bucket.

### Category 1 — multi-session synthesis-required answers (43 fails, 40.6% of loss)

**Failure mode:** RAVEN retrieves the right session(s) (sR@5 ≥ 0.5 on
all 43) and frequently the right turn(s) (tR@5 > 0 on 30/43; tR@5 ≥
1.0 on 11/43), but the gold answer is a *synthesised* string that is
not present verbatim in any retrieved turn:

* counting questions ("how many model kits have I worked on?" → gold
  `5`)
* yes/no inference ("is my mom using the same grocery list method?" →
  gold `Yes.`)
* summarisation ("what topics did we discuss?" → gold is a comma-
  separated list none of the turns produced)

**Engine that owns the gate:** None of RAVEN's engines synthesise
answers. This is **structural**, not calibration, and out of Phase 2.1
scope. The honest action is to flag it as such and not pretend a
threshold tweak fixes it.

**Phase 2.1 calibration change considered:** None. Documented in
GAPS.md and deferred to LME-002 (LLM-answerer adapter). The 11 fails
where tR@5 ≥ 1.0 are unrecoverable without synthesis; the 13 fails
where tR@5 == 0 are turn-level retrieval losses (next-priority below).

### Category 2 — temporal-reasoning synthesis (32 fails, 30.2% of loss)

**Failure mode:** Same shape as Category 1. tR@5 == 0 on only 5 of 32;
the other 27 retrieved at least one answer turn but the gold string
required ordering ("the *first* issue", "*before* I moved") that
RAVEN's lexical retrieval does not select for. Gold answers are usually
a paraphrase of the chosen turn, not a substring.

**Engine that owns the gate:** Partially structural (synthesis), partially
QUASAR/ECLIPSE (date-aware ranking would help the 5 tR@5 == 0 cases).
The recommended Phase 2.1 work — class-aware decay tuning so that
*older* but evidence-bearing turns aren't drowned out by recent chatter
— is in scope for the chat-turn profile, but expected gain is small
(at most ~5/32 fails, ~5% of category loss).

### Category 3 — single-session-preference rubric-style gold (21 fails, 19.8% of loss)

**Failure mode:** 10 / 21 of these have tR@5 == 1.0 (perfect retrieval
of the answer turn) but the gold field is a *rubric describing the
desired response style*, not text expected to appear in memory.
Example: gold = "The user would prefer responses that suggest resources
specifically tailored to Adobe Premiere Pro…" — that string never
appears in any turn; the user's turn says "I use Adobe Premiere Pro".

**Engine that owns the gate:** AURORA refuses 27/27 of this category
(0 approved). For the 10 perfect-retrieval cases, **flipping AURORA
from REFUSED to APPROVED would surface the right turn** and increase
the chance a downstream LLM grader scores a hit (LongMemEval's official
metric uses GPT-4o judging, so a paraphrased preference is a hit even
if the substring scoring misses it).

**Phase 2.1 calibration change considered:** Lower the AURORA approve
threshold for the `preference` memory class so that high-importance
preference statements (the user's stated tool / brand / approach) cross
the gate. Expected category gain on substring scoring: low (~2-5%).
Expected gain on a future LLM-judge metric: high. **This is the cleanest
"reduce token payload at equal quality" lever** — but per the brief
methodology rule (token efficiency is OBSERVED, not OPTIMIZED), token
savings does not motivate this fix; user trust does.

### Category 4 — knowledge-update yes/no inference (8 fails, 7.5% of loss)

**Failure mode:** 2 / 8 have tR@5 ≥ 1.0 (perfect retrieval, gold is a
single-word inference like "Yes."). 5 / 8 have tR@5 between 0 and 1
(answer turn ranked below threshold inside a recovered session). 1 has
tR@5 == 0.

**Engine that owns the gate:** Mixed — synthesis (yes/no inference)
plus turn-level ranking inside a session. Not a clean calibration
target.

**Phase 2.1 calibration change considered:** None — too few fails to
attribute to a single lever, mostly the same synthesis pattern as
Categories 1-2.

### Category 5 — single-session-user paraphrase (2 fails, 1.9% of loss)

**Failure mode:** Both have tR@5 == 0 with sR@5 == 1.0 — the right
session was retrieved but the specific turn naming the answer was
out-ranked by sibling turns from the same session.

**Engine that owns the gate:** Storage-layer retrieval (TFIDF over
char-trigrams loses to lexical-overlap rivals on paraphrased queries —
LME-003).

**Phase 2.1 calibration change considered:** None — only 2 fails.
Tracked as LME-003 (semantic embeddings opt-in) for a future sprint.

---

## Calibration changes proposed for Day 2-4

Based on the table above, **only one calibration target survives the
"category-motivated, ≥ 5% of A@5 loss" filter**:

* **Fix 01 — chat-turn AURORA threshold + per-class decay floors so the
  gate stops refusing 100% of the corpus.** Targets all five categories
  but the *measurable* gain on substring scoring is concentrated in
  Category 3 (single-session-preference) and Category 5 (paraphrase).
  More importantly, it unlocks the AURORA approval pathway, which is
  the prerequisite for ANY token-efficiency story other than "RAVEN
  refuses everything" (the disconfirmed wedge).

The brief asks for "category-by-category" calibration across the top 5,
but the loss profile honestly does not support 5 distinct fixes:

* Categories 1 and 4 (synthesis) are structural and outside calibration
  scope.
* Category 2 (temporal) has at most ~5% of category loss addressable by
  ranking, not threshold tuning.
* Category 5 (paraphrase) has 2 fails — too small to attribute.
* Category 3 is the one clean lever and it benefits from the same
  threshold work as the rest of the chat-turn corpus.

The honest Day 2-4 plan is therefore **one fix attempt** (Fix 01,
chat-turn calibration profile), measured rigorously per the per-fix
attribution template, plus a documented "no calibration change
warranted" entry for each of Categories 1, 2, 4, 5 so the THEMIS
framing test sees that we considered each category and explained why
we did or did not act.

This is Day 1's deliverable per the brief: "**No calibration changes
until this is published.**"

---

## What this profile does NOT measure

1. **No LLM-judge scoring.** Substring matching penalises rubric-style
   preference golds and yes/no inferences regardless of retrieval
   quality. Categories 1, 3, and 4 are partially "scoring artefacts"
   that an LLM judge would credit.
2. **No determinism check yet.** Day 2 calibration runs include a
   10-identical-runs determinism gate; the baseline number above is
   from a single run.
3. **No held-out numbers.** By design — the held-out partition
   (`heldout.json`, SHA `fab8c0edbc761424f57fce2f632010729644535cbae9936deb7dff199dee3c1c`)
   is locked behind `benchmarks.longmemeval.heldout_guard.run_held_out_validation`
   and will be opened exactly once on Day 5.
