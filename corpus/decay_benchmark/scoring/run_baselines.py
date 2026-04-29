"""DECAY benchmark — baseline runner.

Runs four scoring strategies against every corpus query and reports per-class
+ per-horizon accuracy. The pass/fail criterion is "did the baseline's
weighted confidence land inside the corpus's `expected_band`?".

Baselines:
  no_decay        — every memory weight = 1.0 (oracle for fresh, terrible for stale).
  uniform_decay   — single 30-day half-life for everything (the v1.0 ECLIPSE behaviour).
  raven_v2        — class-aware via raven.validation.eclipse.apply_class_aware_decay.
  mempalace_stub  — STUB: hardcoded 0.5 for everything (acknowledges baseline gap).
  llm_judge_stub  — STUB: random in [floor, 1.0]; documents the LLM-judge gap.

Usage:
    python corpus/decay_benchmark/scoring/run_baselines.py
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from pathlib import Path

# Make `raven.*` importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from raven.decay.policies import POLICIES_BY_NAME  # noqa: E402  (added below)
from raven.types import MemoryEntry  # noqa: E402
from raven.validation.eclipse import apply_class_aware_decay  # noqa: E402

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "queries"


def load_corpus() -> list[dict]:
    """Read every query JSON in the corpus."""
    out: list[dict] = []
    for p in sorted(CORPUS_ROOT.glob("**/q*.json")):
        out.append(json.loads(p.read_text()))
    return out


# ── Baselines ──────────────────────────────────────────────────────────────


def baseline_no_decay(rec: dict) -> float:
    """Pretend every memory is fresh."""
    return rec["memory_confidence_at_ingest"]


def baseline_uniform_decay(rec: dict) -> float:
    """Single 30-day half-life — what v1.0 ECLIPSE does."""
    age = rec["horizon_seconds"]
    half_life = 30 * 86_400.0
    raw = math.pow(0.5, age / half_life)
    return rec["memory_confidence_at_ingest"] * raw


def baseline_raven_v2(rec: dict) -> float:
    """Class-aware decay — the new RAVEN_v2 behaviour."""
    now = 1_700_000_000.0
    e = MemoryEntry(
        id=rec["target_memory_id"],
        text=rec["memory_text"],
        timestamp=now - rec["horizon_seconds"],
        confidence_at_ingest=rec["memory_confidence_at_ingest"],
        memory_class=rec["memory_class"],
    )
    out = apply_class_aware_decay([e], now)
    return out[0][1]


def baseline_mempalace_stub(rec: dict) -> float:
    """STUB. MemPalace integration would replace this. Returns 0.5 always."""
    return 0.5


def baseline_llm_judge_stub(rec: dict) -> float:
    """STUB. An LLM-judge baseline would prompt a model with the corpus rec
    and parse a confidence. Approximated here as random ~ U[floor, 1.0].
    Deterministic seed so the harness is reproducible.
    """
    seed = int.from_bytes(rec["id"].encode()[:8], "big") % (2**31 - 1)
    rng = random.Random(seed)
    floor = POLICIES_BY_NAME[rec["memory_class"]].floor_confidence
    return rng.uniform(floor, 1.0)


BASELINES = {
    "no_decay": baseline_no_decay,
    "uniform_decay": baseline_uniform_decay,
    "raven_v2": baseline_raven_v2,
    "mempalace_stub": baseline_mempalace_stub,
    "llm_judge_stub": baseline_llm_judge_stub,
}


# ── Scoring ────────────────────────────────────────────────────────────────


def in_band(weight: float, band: dict) -> bool:
    return band["min"] <= weight <= band["max"]


def score(corpus: list[dict]) -> dict:
    by_baseline: dict[str, dict] = {}
    for name, fn in BASELINES.items():
        per_class: dict[str, dict[str, list[bool]]] = {}
        per_horizon: dict[str, list[bool]] = {}
        all_hits: list[bool] = []
        for rec in corpus:
            try:
                w = fn(rec)
            except Exception as exc:  # never let a baseline crash the run
                print(f"  WARN: {name} crashed on {rec['id']}: {exc}")
                w = 0.0
            hit = in_band(w, rec["expected_band"])
            all_hits.append(hit)
            per_class.setdefault(rec["memory_class"], {}).setdefault(
                rec["horizon"], []
            ).append(hit)
            per_horizon.setdefault(rec["horizon"], []).append(hit)
        by_baseline[name] = {
            "overall_accuracy": sum(all_hits) / len(all_hits),
            "by_class": {
                cls: {
                    h: round(sum(v) / len(v), 4)
                    for h, v in horizons.items()
                }
                for cls, horizons in per_class.items()
            },
            "by_horizon": {
                h: round(sum(v) / len(v), 4)
                for h, v in per_horizon.items()
            },
            "n": len(all_hits),
        }
    return by_baseline


def main() -> None:
    corpus = load_corpus()
    if not corpus:
        print("ERROR: no corpus found at", CORPUS_ROOT)
        sys.exit(1)
    t0 = time.perf_counter()
    results = score(corpus)
    dt = (time.perf_counter() - t0) * 1000
    print(json.dumps(
        {
            "corpus_size": len(corpus),
            "elapsed_ms": round(dt, 1),
            "results": results,
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
