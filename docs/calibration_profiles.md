# Calibration Profiles

Phase 2.1 introduced **calibration profiles** to RAVEN — a small bundle
of calibration knobs (currently the AURORA approval threshold) that lets
a deployment swap calibration regimes without code changes.

## What's shipping

| profile     | aurora_threshold | when to use |
| ----------- | ---------------: | ----------- |
| `factual`   | 0.80             | Default. Tuned for fact-style memories (decision records, identity statements, structured assertions). Matches v1.0 cold-run behaviour byte-for-byte. |
| `chat_turn` | 0.65             | Conversational chat-turn corpora (LongMemEval-style: each retrieved unit is one chat turn from a multi-turn session). Lowers the AURORA gate so high-importance preference statements can be approved instead of universally REFUSED. |

Both profiles are registered automatically when ``raven.calibration`` is
imported. Inspect them at runtime:

```python
from raven.calibration import list_calibration_profiles, get_calibration_profile

for p in list_calibration_profiles():
    print(p.name, p.aurora_threshold)

chat_turn = get_calibration_profile("chat_turn")
print(chat_turn.description)
```

## Using a profile

Pass `calibration_profile` when constructing the pipeline:

```python
from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore

store = RAVENStore("memory.db")
pipeline = RAVENPipeline(store=store, calibration_profile="chat_turn")
```

The default is `"factual"`, so existing v1.0 callers see no behaviour
change.

If you pass `aurora_threshold=...` explicitly, your value wins — this
preserves backward compatibility with v1.0 callers that specified a
custom threshold:

```python
# Explicit threshold beats profile.
pipeline = RAVENPipeline(
    store=store, aurora_threshold=0.42, calibration_profile="chat_turn",
)
assert pipeline.aurora_threshold == 0.42
```

## Profile design

A profile is a frozen `CalibrationProfile`:

```python
@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    description: str
    aurora_threshold: float
    decay_overrides: dict[str, DecayPolicy] = field(default_factory=dict)
```

`decay_overrides` is reserved for Phase 2.2 (per-memory-class decay
policy overrides). Phase 2.1 ships every profile with an empty
`decay_overrides` because class-aware decay needs an upstream classifier
that is out of calibration scope.

## Adding a profile

Drop a YAML-shape file into `raven/calibration/profiles/<name>.yaml`:

```yaml
name: my_profile
description: |
  What this profile is calibrated for, and the rationale behind the
  numeric values. Be honest about what was *measured* vs *targeted*.
aurora_threshold: 0.70
```

`raven.calibration.load_builtin_profiles()` picks it up at import time.
There is intentionally no profile-creation API at runtime — calibration
should not be a per-request decision.

## Profile rationale documentation

Every profile's `description` field documents the reasoning behind its
numeric values. The `chat_turn` profile in particular cites
composite-formula arithmetic from the calibration loss profile rather
than benchmark-tuning. Per the Phase 2.1 brief: **calibration changes
are category-motivated, not question-motivated, and never motivated by
secondary metrics like token efficiency**.

## What profiles do NOT do

* They do NOT change AURORA's composite formula. Weights stay
  `eclipse=0.25, quasar=0.45, pulsar=0.30, nova_bonus≤0.10`.
* They do NOT change the per-engine validation logic.
* They do NOT change the storage schema or the public API surface.

A profile is a calibration knob, not an architectural lever. If you
need an architectural change, that's a separate sprint.

## Phase 2.1 result

The `chat_turn` profile shipped with the calibration system. Empirical
result on LongMemEval (held-out, single shot):

* A@5 = 79.8 % (vs. v1.0 cold-run 73.4 %; partition is 100-question
  random subset, see `benchmarks/longmemeval/v1.1_calibrated_results.md`)
* AURORA approval count: 2 / 100 (vs. 0 / 100 at default `factual`
  threshold)
* Approval-quality preserved: median composite on approvals = 0.676
  (above the 0.6 audit floor).
* Token efficiency: **disconfirmed** (full report at
  `benchmarks/longmemeval/token_efficiency.md`).

The honest summary: the calibration profile system is the durable Phase
2.1 deliverable. The chat_turn calibration value (0.65) is a one-knob
correction in the right direction; the magnitude is bounded by the
ECLIPSE-decay-vs-`time.time()` interaction documented as LME-010.
