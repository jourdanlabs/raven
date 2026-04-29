# Phase 2.1 — LongMemEval calibration / held-out split

**Status:** Sealed. SHAs below are the receipt for the THEMIS framing test.
**Sprint:** RAVEN Phase 2.1 (chat-turn calibration + token-efficiency).
**Created:** Day 1 of the calibration sprint, before any calibration change.

## Method

- Upstream source: `/tmp/longmemeval_data/longmemeval_oracle.json`
- Upstream SHA-256: `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`
- RNG: `random.Random(seed=42).shuffle(indices)`
- Held-out fraction: 20%
- Total instances: 500

Procedure: shuffle the index list with the seeded RNG, slice off the trailing `heldout_fraction` as the held-out set, restore source order within each partition. Both partitions are written as canonical JSON (sorted keys, no whitespace, UTF-8) so their SHA-256 digests are stable across Python runtimes.

## Receipts

| partition   | path                                              |   N | SHA-256 |
| ----------- | ------------------------------------------------- | --: | ------- |
| calibration | `/Users/sokpyeon/projects/raven/benchmarks/longmemeval/calibration.json` | 400 | `d6fc0b788c509d89cd2af0ecf71e6b3337f591be87005abfc5bdbdfda47c8eb0` |
| held-out    | `/Users/sokpyeon/projects/raven/benchmarks/longmemeval/heldout.json` | 100 | `fab8c0edbc761424f57fce2f632010729644535cbae9936deb7dff199dee3c1c` |

## Held-out access discipline

- The held-out partition is read by exactly one function in the
  codebase: `benchmarks.longmemeval.heldout_guard.run_held_out_validation()`.
- That function asserts a `phase2.1_complete` marker file exists before
  opening the held-out file. The marker is created at the end of Day 4
  by `benchmarks.longmemeval.heldout_guard.mark_phase_complete()`.
- Any other code path that imports `heldout.json` directly is a
  methodology violation and the THEMIS framing test fails.

## Reproducing the split

```bash
cd /Users/sokpyeon/projects/raven
.venv/bin/python -m benchmarks.longmemeval.split
```

Re-running the command writes the same bytes (the SHAs above are the
expected output). If they don't match, the upstream file changed; pin
the source SHA above and investigate before continuing the sprint.
