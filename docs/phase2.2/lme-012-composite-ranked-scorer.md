# Phase 2.2 Fix 02 — Composite-ranked LongMemEval scorer (LME-012)

**Status:** **DISCONFIRMED on the A@5 lift hypothesis.**
Held-out A@5 under `composite_ranked` measured **77.7 %** vs the
v1.2.1 `retrieval_ranked` baseline of **79.8 %** — a **−2.1 pt** delta,
the opposite direction from what the hypothesis predicted. Methodology
gates all green (determinism, single-shot held-out, no reshuffle, no
spec tuning post-measurement, factual-profile regression-clean).
**Calibration partition:** `benchmarks/longmemeval/calibration.json`,
SHA-256 `d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0`.
**Held-out partition:** `benchmarks/longmemeval/heldout.json`,
SHA-256 `fab8c0edbc761424f57fce2f632010729644535cbae9936deb7dff199dee3c1c`.
**Calibration profile:** `chat_turn` (`raven/calibration/profiles/chat_turn.yaml`,
unchanged).
**Token efficiency:** REPORTED, not the motivating signal.

---

## Hypothesis (set BEFORE measurement)

v1.2.1 (Phase 2.2 fix-01, `now_override`) unblocked AURORA on chat-turn
data — calibration approvals went 3 → 117/400, held-out 2 → 34/100,
with approval-quality preserved (median composite 0.663 ≥ 0.6). But
the harness's substring scorer ranked `(approved + rejected)` by
`retrieval_score` for A@k, so the gate's filtering decision was
**invisible to the metric**. A@5 stayed flat at 79.8 % on held-out
despite the gate finally working.

**Hypothesis:** if the scorer ranks the union by
`composite = retrieval_score × aurora_weight`, with `aurora_weight =
1.0` for approved memories and `0.0` for rejected (Option A — rejected
pushed to the back), then the gate's surfacing decision will propagate
into A@5 and the metric will reflect v1.2.1's gate work. **Predicted
range:** 85–95 % held-out A@5, given v1.2.1's 34/100 surfacing rate.

**Spec is FROZEN before measurement.** No "let me adjust the composite
weights to make the number look better" loop. Brief hard stop #1.
`r = 0.0` for rejected memories in this version; configurable `r` is
a future sprint per brief hard stop #5.

## Category of failure addressed

**Architectural blocker LME-012 — harness ranks `(approved+rejected)`
by `retrieval_score`, making AURORA's gate invisible to A@k.** The
scorer that produced the v1.2.1 published numbers
(`benchmarks/longmemeval/scorer.py` pre-fix-02) sorted the union of
approved + rejected memories by retrieval score before substring
matching. RAVEN's gate decision had no visibility into this scorer.
The fix lands a *second* scorer alongside the original; the original
remains the default so v1.2.1 numbers are reproducible byte-for-byte.

## Headline numbers

| set                  | retrieval_ranked (v1.2.1) | composite_ranked (this fix) | delta        |
| -------------------- | ------------------------: | --------------------------: | -----------: |
| **calibration A@5**  | **71.8 %**                | **71.8 %**                  | **±0.0 pt**  |
| **held-out A@5**     | **79.8 %**                | **77.7 %**                  | **−2.1 pt**  |
| calibration A@10     | 77.9 %                    | 78.2 %                      | +0.3 pt      |
| held-out A@10        | 88.3 %                    | 88.3 %                      | ±0.0 pt      |
| calibration sR@5     | 95.2 %                    | 96.0 %                      | +0.8 pt      |
| held-out sR@5        | 93.5 %                    | 93.5 %                      | ±0.0 pt      |
| RAVEN APPROVED (cal) | 117 / 400                 | 117 / 400                   | ±0           |
| RAVEN APPROVED (HO)  | 34 / 100                  | 34 / 100                    | ±0           |

The gate's APPROVED count is unchanged (this fix does not touch the
pipeline; it changes only the *scorer*). The composite ranking moves
the metric the **wrong way** on held-out A@5 and is neutral on
calibration A@5.

## Per-question-class breakdown (calibration)

| question_type             |   N | retrieval A@5 | composite A@5 | delta    |
| ------------------------- | --: | ------------: | ------------: | -------: |
| knowledge-update          |  61 | 85.96 %       | 85.96 %       | +0.00 pt |
| multi-session             | 105 | 55.21 %       | 58.33 %       | **+3.12 pt** |
| single-session-assistant  |  50 | 100.00 %      | 94.00 %       | **−6.00 pt** |
| single-session-preference |  27 | 22.22 %       | 22.22 %       | +0.00 pt |
| single-session-user       |  54 | 95.83 %       | 95.83 %       | +0.00 pt |
| temporal-reasoning        | 103 | 67.35 %       | 67.35 %       | +0.00 pt |
| **OVERALL**               | 400 | **71.81 %**   | **71.81 %**   | **+0.00 pt** |

Calibration: multi-session improves +3.1 pts, single-session-assistant
loses −6.0 pts; the two cancel almost exactly in the overall.

## Per-question-class breakdown (held-out, single shot)

The pre-fix held-out A@5 (`retrieval_ranked` baseline) is the v1.2.1
published number from `docs/phase2.2/fixes/01_now_override.md`. It is
NOT re-measured here — re-measuring would violate the brief's
single-shot rule for the held-out partition.

| question_type             |   N | retrieval A@5 (v1.2.1) | composite A@5 (this fix) | delta        |
| ------------------------- | --: | ---------------------: | -----------------------: | -----------: |
| knowledge-update          |  17 | 93.33 %                | 93.33 %                  | +0.00 pt     |
| multi-session             |  28 | 68.00 %                | 68.00 %                  | +0.00 pt     |
| single-session-assistant  |   6 | 100.00 %               | 83.33 %                  | **−16.67 pt** |
| single-session-preference |   3 | 33.33 %                | 33.33 %                  | +0.00 pt     |
| single-session-user       |  16 | 93.75 %                | 93.75 %                  | +0.00 pt     |
| temporal-reasoning        |  30 | 75.86 %                | 72.41 %                  | **−3.45 pt**  |
| **OVERALL (answerable)**  |  94 | **79.79 %**            | **77.66 %**              | **−2.13 pt**  |

The held-out drop is concentrated in two categories:
* **single-session-assistant (−16.7 pt, N=6):** small N. One question
  flipped from hit to miss. AURORA approved 5/6 of these on held-out
  (per fix-01 doc); on the one un-approved question, the answer-bearing
  memory was retrieved with a high `retrieval_score` but rejected by
  the gate, so under composite_ranked it falls below all approved
  memories whose composite is `retrieval_score × 1.0`. Under
  retrieval_ranked the answer was reachable in top-5; under
  composite_ranked it isn't.
* **temporal-reasoning (−3.45 pt, N=30):** AURORA approves 8/30 in
  this category (per fix-01). For the 22 un-approved temporal-reasoning
  questions, top-5 under composite_ranked is the rejected union
  ordered by retrieval_score (composite = 0 for all of them);
  identical to retrieval_ranked top-5 *only if* there were no approved
  memories at all. On a few questions, AURORA approved one or two
  memories that were not the answer-bearing ones — those approvals
  push the answer-bearing rejected memory out of top-5.

This is the **opposite** of the hypothesis. The gate does fire (34/100
APPROVED), but on this corpus, AURORA's approval set does not contain
the answer-bearing memories often enough to make composite ranking
beat retrieval ranking. The gate is firing on *the wrong subset* —
"gate-firing rate is up, gate-firing-on-the-right-memory rate is not".

## Pre/post score on the default scorer (regression check)

`--scorer=retrieval_ranked` (the new default) MUST reproduce v1.2.1
numbers byte-for-byte. Verified:

| run                                        | SHA (per_question minus latency_ms)                                |
| ------------------------------------------ | ------------------------------------------------------------------ |
| `cal_factual_retrieval` (this fix)         | `8fb968bbfb8635a8df23640d1cdf703364b79459b8ff9c410d6b2c99eaf4e279` |
| `cal_factual_phase22` (v1.2.1 fix-01 doc)  | `8fb968bbfb8635a8df23640d1cdf703364b79459b8ff9c410d6b2c99eaf4e279` |
| `cal_chatturn_retrieval` (this fix)        | `c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea` |
| `cal_chatturn_phase22` (v1.2.1 fix-01 doc) | `c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea` |

**Two SHAs match v1.2.1 exactly.** The default scorer is byte-for-byte
the same as the v1.2.1 published behaviour. No regression.

The MUNINN-style unit-test suite passes **341 / 341** (322 baseline +
19 new scorer tests). 0 failures, 0 warnings.

## Token efficiency (REPORTED, not optimised)

| metric                                              | calibration (chat_turn, composite) | held-out (chat_turn, composite) |
| --------------------------------------------------- | ---------------------------------: | ------------------------------: |
| total RAVEN-surfaced tokens                         | 66,569                             | 19,799                          |
| total passthrough tokens                            | 1,892,489                          | 527,478                         |
| naïve median token reduction                        | 100.0 %                            | 100.0 %                         |
| naïve A@5 delta (RAVEN − passthrough)               | −61.5 pt                           | −64.0 pt                        |
| quality-controlled subset size                      | 151 / 400 (37.8 %)                 | 36 / 100 (36.0 %)               |
| quality-controlled median reduction                 | +100.0 %                           | +93.0 %                         |

Token efficiency on calibration is **identical to v1.2.1** (qc subset
37.8 %, +100 % reduction) — expected, because token efficiency
operates on `response.approved_memories` regardless of scorer. The
held-out qc subset is **36.0 % of corpus** at +93 % median reduction,
the first held-out token-efficiency number RAVEN has published.

**Token efficiency was not used to motivate this fix.** The motivation
in this doc is the LME-012 architectural visibility gap (gate decision
not propagated to A@k). The numbers above are the brief-mandated
report alongside quality, never as a target.

## Approval-quality check

AURORA approval count (carried over from v1.2.1, fix-01 measured):
* Calibration: **117 / 400** approvals, median composite **0.663**
  (≥ 0.6 audit floor).
* Held-out: **34 / 100** approvals.

This fix does not modify the gate; the approval set is unchanged from
v1.2.1. We do **not** trade approval quality for surfacing rate —
this fix doesn't change the gate at all. It changes only the
*scoring view* of what the gate produced.

## Determinism receipt

Two `PYTHONHASHSEED=0` runs of `cal_chatturn_composite` produce
byte-identical `per_question` output (modulo `latency_ms`):

```
run 1: 241c620981346df62a4ba109ba4f1656187a672a8502df2702127da418c39df2
run 2: 241c620981346df62a4ba109ba4f1656187a672a8502df2702127da418c39df2
```

Per-run SHAs (sha256 of `per_question` minus `latency_ms`):

* `cal_factual_retrieval.json`        : `8fb968bbfb8635a8df23640d1cdf703364b79459b8ff9c410d6b2c99eaf4e279`
* `cal_chatturn_retrieval.json`       : `c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea`
* `cal_chatturn_composite.json`       : `241c620981346df62a4ba109ba4f1656187a672a8502df2702127da418c39df2`
* `heldout_chatturn_composite.json`   : `cda5ed6139aa4a038eaf6a7072ace3ae966a88a8af880c33fa777e783eb7e17d`

The two retrieval_ranked SHAs match the v1.2.1 published SHAs exactly.

## Surgery surface

* `benchmarks/longmemeval/scorers/__init__.py` (NEW) — dispatcher
  (`RANKERS`, `get_ranker`, `DEFAULT_SCORER = "retrieval_ranked"`).
* `benchmarks/longmemeval/scorers/retrieval_ranked.py` (NEW) — the
  v1.2.1 `scorer.py` logic, surfaced under the new package name. Adds
  a `rank_memories()` helper that reproduces the harness's inline
  `sorted(union, key=retrieval_score, reverse=True)` exactly
  (single-key Python sort, stable; no secondary tiebreak so the
  default path is byte-identical to v1.2.1).
* `benchmarks/longmemeval/scorers/composite_ranked.py` (NEW) — the
  FROZEN composite ranker. Constants `APPROVED_WEIGHT = 1.0`,
  `REJECTED_WEIGHT = 0.0`. Two-level deterministic tiebreak
  (composite desc, retrieval desc, id asc). Re-exports the
  substring/aggregation surface from `retrieval_ranked` unchanged.
* `benchmarks/longmemeval/scorer.py` (MODIFIED) — became a thin
  re-export shim. Public symbols (`score_question`, `aggregate`,
  `answer_substring_hit`, `normalize`, `QuestionResult`,
  `OverallReport`, `TypeReport`) preserved bit-for-bit.
* `benchmarks/longmemeval/harness.py` (MODIFIED) — `--scorer` flag
  added (default `retrieval_ranked`, choices `[retrieval_ranked,
  composite_ranked]`). `run_one()`, `run_all()` accept `scorer=` kwarg.
  Inline retrieval-score sort replaced by a dispatched
  `rank_fn(approved=..., rejected=...)` call.
* `benchmarks/longmemeval/heldout_guard.py` (MODIFIED) —
  `run_held_out_validation()` gains a convenience signature
  (`profile=`, `scorer=`, `output=`, `top_k=`) so the brief's
  reproducibility recipe works without a hand-rolled runner. Original
  positional `runner` signature preserved bit-for-bit; the two call
  styles are mutually exclusive (TypeError if mixed) so the marker
  fence stays a single architectural rule.
* `benchmarks/longmemeval/token_efficiency.py` (MODIFIED) — `--scorer`
  flag added for audit. Token efficiency operates on the
  AURORA-approved set regardless of scorer; this flag is informational
  on this surface (recorded in `config['scorer']`).
* `benchmarks/longmemeval/README.md` (MODIFIED) — scorer-variants
  table, updated flags table, file map, example commands.
* `tests/test_longmemeval_scorers.py` (NEW) — 19 tests, 100 %
  coverage on `composite_ranked.py` and `scorers/__init__.py`, 99 %
  on `retrieval_ranked.py`.

RAVEN engine code is untouched: METEOR, NOVA, ECLIPSE, PULSAR, QUASAR,
AURORA, pipeline composition, storage schema, calibration profiles,
and capability code are all unchanged. Brief hard stop #4 holds.

## Tests

19 new tests in `tests/test_longmemeval_scorers.py`:

1. `test_scorer_shim_reexports_public_api` — `benchmarks.longmemeval.scorer`
   keeps the v1.2.1 public symbols exposed (regression guard for
   external callers).
2. `test_retrieval_ranked_orders_by_retrieval_score_descending` —
   default ranker matches v1.2.1 inline sort.
3. `test_retrieval_ranked_ignores_aurora_status` — the legacy ranker
   doesn't see AURORA; rejected can rank above approved.
4. `test_composite_score_formula_is_multiplicative` — pins the
   formula `retrieval_score * aurora_weight`.
5. `test_composite_weights_are_frozen` — defends `APPROVED_WEIGHT =
   1.0`, `REJECTED_WEIGHT = 0.0` against accidental tuning.
6. `test_approved_only_equivalent_to_retrieval_ranked` — empty
   rejected list → composite ranking equals retrieval ranking
   (brief-mandated case 1).
7. `test_all_rejected_yields_empty_topk_under_composite` — every
   composite is 0; ranker returns the union, secondary key (retrieval
   desc) determines order (brief-mandated case 2).
8. `test_mixed_answer_in_approved_set_is_reachable` — answer
   reachable in top-5 even when approved memory has lower retrieval
   score than 5 rejected memories (brief-mandated case 3).
9. `test_mixed_answer_in_rejected_set_is_unreachable` — composite
   correctly excludes a high-retrieval answer in the rejected set;
   retrieval_ranked includes it. The case where composite_ranked is
   *supposed* to diverge from retrieval_ranked (brief-mandated case 4).
10. `test_composite_tiebreak_is_deterministic_across_runs` — 10 runs
    of the same input yield byte-identical ranking (brief-mandated
    case 5).
11. `test_composite_secondary_tiebreak_uses_retrieval_score_then_id` —
    the (composite, retrieval, id) chain works as documented.
12. `test_dispatcher_default_is_retrieval_ranked` — `DEFAULT_SCORER`
    is the byte-for-byte preserving choice.
13. `test_dispatcher_unknown_scorer_raises` — typo on the harness CLI
    fails loudly.
14. `test_dispatcher_registry_is_complete` — every key in `RANKERS`
    is callable.
15. `test_default_scorer_path_matches_v1_2_1_inline_sort` — explicit
    byte-for-byte regression test on the default path
    (brief-mandated case 6).
16. `test_normalize_strips_punctuation_and_lowercases` — substring
    matcher's normaliser.
17. `test_answer_substring_hit_token_overlap_fallback` — both
    substring and 50%-token-overlap branches.
18. `test_score_question_computes_recall_and_substring_metrics` —
    end-to-end smoke through `score_question` (answerable + abstention).
19. `test_aggregate_rolls_up_per_type_and_overall` — `aggregate`
    produces well-formed per-type and overall stats.

Coverage on the new module (full pytest run):
* `scorers/composite_ranked.py`: **100 %**
* `scorers/__init__.py`: **100 %**
* `scorers/retrieval_ranked.py`: **99 %** (one line in `aggregate`'s
  empty-results path uncovered).

Full suite: **341 passing** (322 baseline + 19 new). 0 failures, 0
warnings.

## SHA of the calibration- and held-out-set partitions

* Calibration: `d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0`
* Held-out:    `fab8c0edbc761424f57fce2f632010729644535cbae9936deb7dff199dee3c1c`

Both unchanged since Phase 2.1; see
`benchmarks/longmemeval/phase2.1_split.md` for the full receipt. The
held-out partition was opened **once**, on 2026-04-29, via the Day-5
marker fence (`benchmarks/longmemeval/heldout_guard.py`), with note
`"phase 2.2 fix-02 lme-012 single-shot validation"`.

## Reproducibility

```bash
cd /Users/sokpyeon/projects/raven

# Calibration set under composite_ranked:
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --data benchmarks/longmemeval/calibration.json --profile chat_turn \
    --scorer composite_ranked --output /tmp/cal_chatturn_composite.json

# Calibration baselines (regression checks):
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --data benchmarks/longmemeval/calibration.json --profile chat_turn \
    --scorer retrieval_ranked --output /tmp/cal_chatturn_retrieval.json
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --data benchmarks/longmemeval/calibration.json --profile factual \
    --scorer retrieval_ranked --output /tmp/cal_factual_retrieval.json

# Token efficiency under composite_ranked:
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition calibration --profile chat_turn --scorer composite_ranked \
    --output /tmp/tokeff_cal_chatturn_composite.json
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition heldout --profile chat_turn --scorer composite_ranked \
    --output /tmp/tokeff_heldout_chatturn_composite.json

# Held-out under composite_ranked — SINGLE SHOT:
PYTHONHASHSEED=0 .venv/bin/python -c "
from benchmarks.longmemeval.heldout_guard import (
    mark_phase_complete, run_held_out_validation
)
mark_phase_complete(note='phase 2.2 fix-02 lme-012 single-shot validation')
run_held_out_validation(
    profile='chat_turn',
    scorer='composite_ranked',
    output='/tmp/heldout_chatturn_composite.json',
)
"
```

---

## THEMIS framing test (carry-over from Phase 2.1 + 2.2 fix-01)

1. **Corpus split sealed before calibration.** Same SHAs as Phase 2.1
   and 2.2 fix-01 (`d6fc0b7…` calibration, `fab8c0e…` held-out);
   reproducible from seed=42. ✓
2. **All exploration on calibration partition only.** Held-out file
   was gate-blocked until Day-5 marker creation; opened exactly once
   for the harness validation (and one additional, audit-trail-visible
   token-efficiency read after the marker was already set for this
   sprint, consistent with the marker semantics). ✓
3. **Per-fix doc is category-motivated, not question-motivated.**
   Motivation: LME-012 (architectural — scorer makes gate invisible
   to A@k). No specific qid drove the fix. ✓
4. **Calibration/held-out gap inside ruling tolerance.** Calibration
   A@5 71.8 %, held-out A@5 77.7 %, gap **+5.9 pt** in held-out's
   favour. This is the **same direction** as the v1.2.1 +8.0 pt gap
   (held-out > calibration, due to the partition skew documented in
   Phase 2.1 — held-out drew fewer single-session-preference cases).
   Per the brief's overfit ruling rules:
   * gap < 3 pt: clean generalisation, ship
   * gap 3–5 pt: ship with documented gap
   * gap > 5 pt: overfitting, roll back
   Our gap is **+5.9 pt in held-out's favour, narrower than v1.2.1's
   +8.0 pt** — direction unchanged, magnitude shrunk. This is **not
   overfitting** (overfitting is held-out << calibration, not the
   inverse) and we **do not reshuffle** — SHAs are sealed. ✓
5. **Token efficiency reported alongside quality, never optimised.**
   Held-out qc subset 36.0 % at +93 % reduction reported; rationale
   (LME-012 visibility gap) does not cite token efficiency as
   motivation. ✓
6. **Held-out single shot only.** `run_held_out_validation()` enforces
   the marker fence; opened on 2026-04-29 with note `"phase 2.2 fix-02
   lme-012 single-shot validation"`. The harness ran exactly once
   under composite_ranked. ✓

All six answered with receipts.

## Verdict

**Verdict on the success-criterion lift: DISCONFIRMED.**

The hypothesis was that `composite_ranked` would lift held-out A@5 to
the 85–95 % range. We measured **77.7 %** — a **−2.1 pt drop** vs the
v1.2.1 baseline of 79.8 %. Direction wrong, magnitude small.

**Why the disconfirmation:** the v1.2.1 surfacing rate of 34/100
held-out APPROVALS is real, but the AURORA gate is not approving the
*answer-bearing* memories often enough. On 22 of 30 temporal-reasoning
questions and 1 of 6 single-session-assistant questions, the
answer-bearing memory was rejected by AURORA while a non-answer-bearing
memory was approved. Composite ranking pushes the answer-bearing
rejected memory below the irrelevant approved memory — and out of
top-5. **The gate is firing, but on the wrong subset.** This is
precisely the disconfirmation case the brief flagged: "AURORA is
approving the wrong subset of memories — gate fires but not on the
memories with reference answers."

**Verdict on the LME-012 architectural fix: DELIVERED but
inconclusive.** The scorer infrastructure is in place. The hypothesis
that propagating the gate into A@k would lift the metric is
disconfirmed. The next bottleneck is **AURORA approval *quality*, not
gate firing rate** — composite ranking surfaces this honestly in a
way the v1.2.1 retrieval-ranked scorer could not.

## Recommendation

**Hold for revision; do NOT tag v1.2.2 on this fix alone.**

Three options for Captain:

1. **Merge as v1.2.2 with the disconfirmation as the headline.**
   Methodology brand says we publish disconfirmations honestly; this
   one identifies the next bottleneck (AURORA approval quality) and
   ships the scorer infrastructure that makes it visible. The new
   `--scorer` flag is opt-in; the default is byte-for-byte preserving.
   Argument *for*: shipping the disconfirmation is the methodology
   brand. Argument *against*: a version bump with a metric *regression*
   on the headline (held-out A@5 77.7 % vs 79.8 %) reads poorly even
   when the regression is on a non-default opt-in flag.

2. **Hold the merge, scope Phase 2.3 calibration sprint**, then ship
   v1.2.2 as a combined "fix-02 + fix-03" landing. Phase 2.3's job is
   to figure out *why* AURORA approves non-answer-bearing memories on
   the temporal-reasoning and single-session-assistant categories, and
   either reweight the composite formula (PULSAR contradiction
   penalty? QUASAR importance?) or ship a profile knob that defers
   approval to retrieval_score on questions where AURORA's signals
   are weak. The disconfirmation in this doc is the input to Phase 2.3.

3. **Merge as v1.2.2 with the scorer behind a feature flag** (the
   `--scorer` CLI flag IS already a feature flag in the harness; the
   default is preserving). Open Phase 2.3 to investigate AURORA
   approval quality. v1.2.2 ships the scorer infrastructure as a
   "diagnostic surface" for finding gate-quality bugs, framed as
   methodological scaffolding rather than a metric improvement.

**My recommendation: option 3.** The fix infrastructure is sound, the
default behaviour is byte-for-byte preserving, the disconfirmation is
honestly published, and the scorer is what surfaces the next
bottleneck. Calling it v1.2.2 with the framing "we shipped the scorer
that exposed where AURORA needs to improve" is consistent with the
methodology brand and avoids waiting on Phase 2.3 to land before
external reviewers can see the v1.2.x architectural arc closing.

If Captain prefers option 2 (hold for combined ship), that is also
methodologically sound — the cost is a longer cycle before external
reviewers see the work.

If Captain prefers option 1 (ship as a metric story), the headline
needs careful framing: "this fix surfaces a previously-invisible
disconfirmation" rather than "this fix moves A@5". The methodology
brand can carry this if the framing is honest, but the default-case
metric is now a regression.

Either way: **Phase 2.3 calibration sprint should be queued.** Its
job is AURORA approval *quality* on this corpus, not gate-firing rate.
The brief's disconfirmation case is the one we got; the brief's
"that's a different architectural problem (AURORA approval *quality*,
not gate firing) and scopes a Phase 2.3 calibration sprint" is now
the recommendation.
