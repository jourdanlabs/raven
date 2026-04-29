# Phase 2.2 Fix 01 — `now_override` on `RAVENPipeline.recall()`

**Status:** Methodology gates all green. Direction-confirmed on RAVEN
APPROVED rate (3 → 117 on calibration, 2 → 34 on held-out) and
quality-controlled token-efficiency subset (22.0 % → 37.8 %).
**A@5 success criterion: DISCONFIRMED on the harness substring metric**
— see "Verdict and recommended next work" for the honest read.
**Calibration partition:** `benchmarks/longmemeval/calibration.json`,
SHA-256 `d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0`.
**Held-out partition:** `benchmarks/longmemeval/heldout.json`,
SHA-256 `fab8c0edbc761424f57fce2f632010729644535cbae9936deb7dff199dee3c1c`.
**Calibration profile:** `chat_turn` (`raven/calibration/profiles/chat_turn.yaml`,
unchanged).
**Token efficiency:** REPORTED, not the motivating signal.

---

## Category of failure addressed

**Architectural blocker LME-010 — ECLIPSE decay vs `time.time()` collapses
chat-turn AURORA composite below any approval-quality-preserving threshold.**

LongMemEval haystack timestamps are 2023-2024; recall fires in 2026. With
the default 30-day half-life,
`decay = 0.5 ^ ((now - ingest_ts) / 30 days)` is approximately `1e-7` for
every entry. Composite then reduces to roughly
`importance × 0.45 + 0.30`, with empirical max **0.46** and median
**0.21** across a 30-question calibration sub-sample. **No threshold
≥ 0.5** (the lowest value preserving the audit ≥0.6 approval-quality
floor) can produce a meaningful approval rate at this composite
distribution. The Phase 2.1 chat_turn profile lowered the threshold to
0.65 and recovered 3/400 approvals — direction-correct, magnitude
insufficient, exactly as documented in the Phase 2.1 attribution doc.

This is **not** a question-level failure. It is a category-wide
arithmetic collapse driven by the haystack-vs-recall-clock skew, and it
applies uniformly to **every** chat-turn entry from the LongMemEval
distribution (and to any production deployment whose ingest timestamps
are systematically older than the recall machine's clock).

The fix is fairness, not target-fixing: callers who have a meaningful
"now" relative to the corpus pass it; callers who don't keep the v1.0 /
v1.1 wall-clock semantics. Default is `None`, so no existing caller's
behaviour changes.

## # of calibration-set questions in that category

All 400 calibration questions are affected by the LME-010 regime; the
chat_turn AURORA gate was producing only 3 approvals across the entire
partition pre-fix. Post-fix it produces **117 approvals**, a 39× lift in
the surfacing rate (one of the two metrics — alongside quality-controlled
token reduction — that `chat_turn` was supposed to move).

## Pre-fix score on those questions

`profile=chat_turn`, calibration set, **before** `now_override` (the
Phase 2.1 baseline as published in `v1.1_calibrated_results.md`):

| metric                                     | value |
| ------------------------------------------ | ----: |
| A@5 (answerable, N=378)                    | 71.8% |
| sR@5                                       | 95.2% |
| RAVEN APPROVED (status)                    | 3 / 400 |
| RAVEN-surfaced tokens (approved-set total) | 3,165 |
| Quality-controlled subset (RAVEN ≥ pt A@5) | 122 / 400 (30.5%) |
| AURORA composite, max measured             | 0.46  |
| AURORA composite, median measured          | 0.21  |

## Post-fix score on those questions

`profile=chat_turn`, calibration set, **with** `now_override`:

| metric                                     | value |
| ------------------------------------------ | ----: |
| A@5 (answerable, N=378)                    | 71.8% (unchanged) |
| sR@5                                       | 95.2% (unchanged) |
| RAVEN APPROVED (status)                    | **117 / 400** (+114) |
| RAVEN-surfaced tokens (approved-set total) | **66,569** (+63,404) |
| Quality-controlled subset (RAVEN ≥ pt A@5) | **151 / 400 (37.8%)** (+7.3 pts) |
| Approval-quality (median composite, N=30 sample) | **0.663** (≥0.6 floor satisfied) |

A@5 doesn't move because the harness ranks `(approved + rejected)` by
`retrieval_score` for substring scoring; the AURORA gate doesn't reorder
the top-5. The gate only changes what RAVEN **surfaces** for downstream
LLM consumption — i.e. the token-efficiency picture. That picture moves:
the quality-controlled subset (the only headline THEMIS allows) grows
from 30.5 % to 37.8 % of the corpus.

## Pre/post score on questions OUTSIDE the chat-turn regime (regression check)

The fix is opt-in (`now=None` → wall-clock, exactly as before). The
factual profile baseline is preserved bit-for-bit:

`profile=factual`, calibration set:

| metric                          | pre-fix | post-fix |
| ------------------------------- | ------: | -------: |
| A@5                             | 71.8%   | 71.8%    |
| sR@5                            | 95.2%   | 95.2%    |
| RAVEN APPROVED                  | 15/400  | 15/400   |
| latency p50                     | 77.1 ms | 77.1 ms  |

The factual profile passes its `now=None` and consequently sees
identical numbers. **No regression** on factual.

The MUNINN-style unit-test suite (the existing `tests/` corpora that
defend the v1.0 / v1.1 / capability-1.x semantics) passes 322/322 with
the fix in place — 316 baseline tests + 6 new tests defending the
override behaviour. See "Tests" section below.

## Held-out (single shot, Day-5 fence)

Marker created Day-5 with note `"phase 2.2 fix-01 single-shot validation"`
via `mark_phase_complete()`; held-out file opened exactly once via
`run_held_out_validation()`.

`profile=chat_turn`, held-out set (N=100):

| metric                            | held-out |
| --------------------------------- | -------: |
| **A@5 (answerable, N=94)**        | **79.8%** |
| A@10 (answerable)                 | 88.3%    |
| sR@5                              | 93.5%    |
| abstention accuracy (3/6)         | 50.0%    |
| RAVEN APPROVED (status)           | **34 / 100** |
| latency p50 / p95 / p99           | 90.2 / 137.7 / 157.9 ms |

### Per-type breakdown (held-out)

| question_type              |   N | A@5    | RAVEN APPROVED |
| -------------------------- | --: | ------ | -------------- |
| knowledge-update           |  17 | 93.3%  | 4 / 17         |
| multi-session              |  28 | 68.0%  | 9 / 28         |
| single-session-assistant   |   6 | 100.0% | 5 / 6          |
| single-session-preference  |   3 | 33.3%  | 1 / 3          |
| single-session-user        |  16 | 93.8%  | 7 / 16         |
| temporal-reasoning         |  30 | 75.9%  | 8 / 30         |

## Calibration vs held-out gap (overfitting check)

| metric             | calibration (N=400) | held-out (N=100) | gap |
| ------------------ | ------------------: | ---------------: | --: |
| A@5                | 71.8%               | 79.8%            | **+8.0 pts** |
| sR@5               | 95.2%               | 93.5%            | −1.7 pts |
| RAVEN APPROVED %   | 29.3%               | 34.0%            | +4.7 pts |
| Quality-controlled subset (token-eff) | 37.8% | n/a (token-eff requires guard bypass) | n/a |

The held-out result is **8.0 points BETTER** than the calibration set on
A@5. This is **identical to the Phase 2.1 partition skew** (held-out's
80/20 random shuffle drew fewer single-session-preference cases —
3 of 30 in held-out vs 27 of 30 in calibration). It is not an
overfitting signal: overfitting is held-out << calibration, not the
inverse. Per the brief's overfit ruling rules:

* gap < 3 pts: clean generalisation, ship
* gap 3-5 pts: ship with documented gap
* gap > 5 pts: overfitting, roll back

Our gap (+8.0 pts in held-out's favour, identical to Phase 2.1) is **not
overfitting**. We **do not reshuffle** — the SHAs are sealed and
re-running would invalidate the THEMIS framing test for both Phase 2.1
and Phase 2.2.

## Token efficiency (REPORTED, not optimised)

| metric                                              | pre (Phase 2.1) | post (this fix) |
| --------------------------------------------------- | --------------: | --------------: |
| total RAVEN-surfaced tokens (cal set)               | 3,165           | **66,569**      |
| total passthrough tokens                            | 1,892,489       | 1,892,489       |
| naïve median token reduction                        | 100.0%          | 100.0%          |
| naïve A@5 delta (RAVEN − passthrough)               | −69.5 pts       | **−61.5 pts**   |
| quality-controlled subset size                      | 122 (30.5%)     | **151 (37.8%)** |
| quality-controlled median reduction                 | +100.0%         | +100.0%         |

The quality-controlled subset growing 30.5 % → 37.8 % is the chat-turn
wedge finally producing a real signal. The naïve A@5 delta improving by
+8.0 pts (less harm) is a real reduction in the gate's blast radius
even though substring A@5 is unchanged in the harness's
ranking-by-retrieval-score scoring.

**Token efficiency was not used to motivate the fix.** The motivation in
this doc is the LME-010 architectural blocker (decay arithmetic). The
numbers above are the brief-mandated report alongside quality, never as
a target. Per Phase 2.1's published verdict, the chat-turn token
efficiency wedge was disconfirmed at v1.1; this fix moves that
disconfirmation toward "mixed" but does NOT make a token-efficiency
claim the headline.

## Approval-quality check

Median AURORA composite on chat_turn approvals = **0.663** (sample of
30 approvals across the first 50 calibration questions). All 30
approvals score ≥ 0.65 (= chat_turn threshold by construction); the
≥ 0.6 audit floor is satisfied. We do **not** trade approval quality
for surfacing rate.

## Determinism check

`PYTHONHASHSEED=0` runs of the calibration chat_turn harness produce
byte-identical `per_question` output (modulo `latency_ms`):

```
run 1: c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea
run 2: c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea
```

Per-run SHAs (sha256 of `per_question` minus `latency_ms`):

* `cal_factual_phase22.json`     : `8fb968bbfb8635a8df23640d1cdf703364b79459b8ff9c410d6b2c99eaf4e279`
* `cal_chatturn_phase22.json`    : `c0bf8c9fbec1b5bc22ef2b1c3bd39e97c07187920bf63368bac35ae7be7080ea`
* `heldout_chatturn_phase22.json`: `52afdc3ebfb31e54f7c9e7cff07708c4a64ae3fdac560d2457aeec0cee4ec30c`

## Surgery surface

* `raven/pipeline.py:65-117` — `recall()` gains `now: float | None = None`
  kwarg-only parameter; line 142 replaces `now = time.time()` with
  `now = now if now is not None else time.time()`.
* `raven/pipeline.py:225-240` — `recall_v2()` gains the same parameter;
  line 335 receives the same fallback shim.
* `benchmarks/longmemeval/harness.py:101-110` — `run_one()` passes
  `now=q.question_timestamp` (or `None` if the corpus shipped no parseable
  date).
* `benchmarks/longmemeval/token_efficiency.py:184-192` — `measure_one()`
  passes the same `now_override` so token-efficiency runs see the same
  fair-decay regime as the harness.

Calibration profiles are unchanged. ECLIPSE engine is unchanged
(`apply_decay` already accepted `now`). AURORA is unchanged.

## Tests

Six new tests in `tests/test_pipeline.py`:

1. `test_default_uses_wall_clock_when_now_none` — `recall()` with
   `now=None` produces decay weights bracketed by `time.time()` measured
   immediately before/after the call. Regression guard for v1.0 / v1.1
   compatibility.
2. `test_now_override_changes_decay_for_old_timestamps` — 700-day-old
   entry, `now=ingest_ts` → weight ≈ 1.0; `now=None` → weight ≈ 1e-7.
3. `test_now_override_identical_for_recent_timestamps` — recent (12-hour-old)
   entries see identical pipeline `status` between the two paths.
4. `test_now_override_signature_v2` — `recall_v2(now=...)` accepts the
   kwarg without raising.
5. `test_old_entry_composite_above_threshold_with_now_override` —
   AURORA composite on a 700-day-old chat-turn entry crosses 0.5
   (the chat_turn floor) when `now=ingest_ts`.
6. `test_old_entry_composite_below_threshold_without_now_override` —
   the same entry without override stays in the LME-010 regime
   (composite shifts by > 0.2 pts between the two paths).

Full suite: **322 passing** (316 baseline + 6 new). 0 failures, 0
warnings.

## SHA of the calibration-set partition

`d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0`
(see `benchmarks/longmemeval/phase2.1_split.md` for the full receipt).
The held-out partition was opened **once**, on 2026-04-29, via the Day-5
marker fence (`benchmarks/longmemeval/heldout_guard.py`).

## Reproducibility

```bash
cd /Users/sokpyeon/projects/raven

# Calibration set — exploration runs, factual baseline:
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --data benchmarks/longmemeval/calibration.json --profile factual \
    --output /tmp/cal_factual_phase22.json

# Calibration set — chat_turn with now_override:
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.harness \
    --data benchmarks/longmemeval/calibration.json --profile chat_turn \
    --output /tmp/cal_chatturn_phase22.json

# Token efficiency (calibration, both profiles):
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition calibration --profile factual \
    --output /tmp/tok_eff_factual_phase22.json
PYTHONHASHSEED=0 .venv/bin/python -m benchmarks.longmemeval.token_efficiency \
    --partition calibration --profile chat_turn \
    --output /tmp/tok_eff_chatturn_phase22.json

# Held-out (Day-5 fence) — SINGLE SHOT after marker:
PYTHONHASHSEED=0 .venv/bin/python -c "
import json, time
from benchmarks.longmemeval.heldout_guard import (
    mark_phase_complete, run_held_out_validation,
)
from benchmarks.longmemeval.harness import run_all, aggregate, serialize_report
mark_phase_complete(note='phase 2.2 fix-01 single-shot validation')
def runner(qs):
    t0 = time.perf_counter()
    results = run_all(qs, top_k=20, calibration_profile='chat_turn')
    rep = aggregate(results)
    out = serialize_report(rep, results)
    out['wall_time_s'] = time.perf_counter() - t0
    return out
out = run_held_out_validation(runner)
json.dump(out, open('/tmp/heldout_chatturn_phase22.json','w'), indent=2)
"
```

---

## THEMIS framing test (carry-over from Phase 2.1)

1. **Corpus split sealed before calibration.** Same SHAs as Phase 2.1
   (`d6fc0b7…` calibration, `fab8c0e…` held-out); reproducible from
   seed=42 with the same source file.  ✓
2. **All exploration on calibration partition only.** Held-out file was
   gate-blocked until Day-5 marker creation; opened exactly once.  ✓
3. **Per-fix doc is category-motivated, not question-motivated.**
   Motivation: LME-010 (architectural decay-vs-clock skew). No specific
   qid drove the fix.  ✓
4. **No reshuffle on overfit.** +8.0 pt held-out delta is the same
   partition skew Phase 2.1 documented; not overfitting; not
   reshuffling.  ✓
5. **Token efficiency reported alongside quality, never optimized.**
   Numbers reported in this doc; rationale (LME-010 arithmetic) does
   not cite token-efficiency as motivation.  ✓
6. **Held-out single shot only.** `run_held_out_validation()` enforces
   the marker fence; opened once on 2026-04-29.  ✓

All six answered with receipts.

## Verdict and recommended next work

**Verdict on the success-criterion lift:** **DISCONFIRMED on A@5.**

The brief required ≥ 84.8 % held-out A@5 (+5 pts over Phase 2.1's
79.8 %). We measured **79.8 % held-out A@5** — identical to Phase 2.1.
The reason is structural: the LongMemEval harness ranks
`(approved + rejected)` by `retrieval_score` for substring scoring, so
the AURORA gate's job (filtering for surfacing) does not move the
substring-A@5 metric in this scorer.

**Verdict on the LME-010 root-cause unblocking: CONFIRMED.**

* RAVEN APPROVED count: 3/400 → **117/400** on calibration (+114),
  2/100 → **34/100** on held-out (+32).
* Quality-controlled token-efficiency subset: **30.5 % → 37.8 %**
  (+7.3 pts) — the metric the chat_turn profile was actually
  responsible for moving.
* Approval-quality floor preserved (median composite 0.663 ≥ 0.6).
* No regression on the factual profile or the existing test suite
  (322/322 pass).
* Methodology gates all green (determinism, single-shot held-out,
  no reshuffle, category-motivated, factual unchanged).

**Honest publish call:** ship the fix as a methodological correction —
benchmark fairness for any deployment whose ingest timestamps are
older than recall time, not a benchmark-target hack. The chat-turn
wedge narrative remains **mixed** (token efficiency moved by 7.3 pts;
A@5 unchanged in this scorer). The brief's "if held-out lift +5 pts AND
qc subset >50%" condition is partially met (qc subset 37.8 % is a real
move but doesn't cross 50 %); the wedge stays "mixed", not "back on the
table".

### Recommended Phase 2.2 follow-on

* **fix-02 (small):** add a `--now` flag to the harness CLI so a
  reviewer running the bench from the command line sees the fair-decay
  regime by default. Right now it's wired through the Python API
  (`run_all` reads `q.question_timestamp` per-question); the CLI
  accidentally inherits the right behaviour because it just calls
  `run_all`. A `--now wall|corpus|<ts>` flag would make the choice
  explicit and auditable. Small, low-risk, doc-only-ish.
* **fix-03 (architectural):** the AURORA gate's surfacing rate is
  unblocked but `harness.py` still ranks by `retrieval_score`, so A@5
  cannot move. A separate scorer that ranks by composite (approved
  first, then rejected) would let the chat-turn profile finally move
  A@5 — but this would change what we publish as the headline number,
  which is a methodology decision, not a code decision. Document the
  tradeoff in the v1.2 narrative; don't change the scorer
  unilaterally.
* **fix-04 (already known):** per-class decay overrides need an
  upstream `MemoryEntry.memory_class` classifier. That's the
  architectural change LME-010 originally identified as out of Phase
  2.1 scope. Phase 2.3+.

The methodology IS the work: we asked one well-formed question
(does fair decay change anything?), measured it cleanly, and got a
disconfirmed lift on A@5 alongside a real lift on the wedge metric.
That is a publishable result either way.
