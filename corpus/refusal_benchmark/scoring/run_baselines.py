"""Refusal benchmark — run baselines and report metrics.

Baselines:
  - **RAVEN_v1** — degenerate baseline that refuses with
    ``insufficient_evidence`` for every query. Models the v1.0 surface
    where every refusal collapsed to a single status.
  - **LLM-judge** — STUB. The plug point for a future LLM-driven
    classifier. The stub randomly assigns one of the five types using a
    deterministic seed and prints a notice that this baseline is not
    real. Structured this way so a future implementer can swap in real
    LLM calls without changing the harness contract.
  - **RAVEN_v2** — the new ``classify_refusal`` from
    :mod:`raven.refusal`. The harness drives it through
    :class:`RAVENPipeline.recall_v2` for each corpus row, ingesting any
    ``setup`` memories first.

Metrics:
  - **Refusal accuracy**         — fraction where the model returned a
    refusal at all (RAVEN should refuse every row in this corpus, since
    every row is labeled with the correct refusal type).
  - **Refusal precision**        — of the refusals returned, fraction
    where the predicted type equals the gold label.
  - **Refusal recall per type**  — of the gold rows of type X, the
    fraction the model classified as X.

Usage:
  python corpus/refusal_benchmark/scoring/run_baselines.py \
      [--out corpus/refusal_benchmark/scoring/results.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REFUSAL_TYPES = (
    "insufficient_evidence",
    "conflicting_evidence_unresolvable",
    "staleness_threshold_exceeded",
    "identity_ambiguous",
    "scope_violation",
)

CORPUS_PATH = Path(__file__).parent.parent / "queries.jsonl"
DEFAULT_RESULTS = Path(__file__).parent / "results.json"


# ── Corpus loader ──────────────────────────────────────────────────────────


def load_corpus(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ── Baselines ──────────────────────────────────────────────────────────────


def baseline_raven_v1(rows: list[dict]) -> list[dict]:
    """v1.0 collapsed every refusal into a single bucket. Model that here
    by predicting ``insufficient_evidence`` for every row.
    """
    out = []
    for r in rows:
        out.append({
            "query_id": r["query_id"],
            "gold": r["label"],
            "predicted": "insufficient_evidence",
            "refused": True,
        })
    return out


def baseline_llm_judge(rows: list[dict], seed: int = 1729) -> list[dict]:
    """STUB. No real LLM is called. We emit a deterministic random
    prediction across the five types so downstream metrics code is
    exercised end-to-end. To wire a real LLM judge, replace this body
    with a call that sends each row's ``query`` and ``setup`` to a
    chat-completion endpoint and parses the response into one of the
    five types.

    The notice below is printed exactly once so reviewers don't mistake
    the stub output for a real comparison.
    """
    print(
        "[LLM-judge] STUB baseline — deterministic random predictions. "
        "Replace this body with a real LLM call before publishing scores."
    )
    rng = random.Random(seed)
    out = []
    for r in rows:
        out.append({
            "query_id": r["query_id"],
            "gold": r["label"],
            "predicted": rng.choice(REFUSAL_TYPES),
            "refused": True,
        })
    return out


def baseline_raven_v2(rows: list[dict]) -> list[dict]:
    """Run the new classifier through the full :meth:`recall_v2` path.

    For each row we spin up a fresh in-memory store, ingest any
    ``setup`` memories, then call ``recall_v2``. The store is reset per
    row so memories from one row don't leak into another. Latency is
    not optimized — this is a correctness baseline.
    """
    # Local imports so the harness can be used without raven installed
    # in unit-test contexts.
    from raven.pipeline import RAVENPipeline
    from raven.storage.store import RAVENStore
    from raven.types import MemoryEntry

    out = []
    for r in rows:
        with tempfile.TemporaryDirectory() as tmp:
            store = RAVENStore(db_path=f"{tmp}/raven.db")
            pipeline = RAVENPipeline(store)
            for s in r.get("setup", []):
                pipeline.ingest(MemoryEntry(
                    id=s["id"],
                    text=s["text"],
                    timestamp=s["timestamp"],
                    entity_tags=s.get("entity_tags", []),
                ))
            verdict = pipeline.recall_v2(
                r["query"],
                scope_allowlist=r.get("scope_allowlist"),
            )
            refused = verdict.decision == "refuse"
            predicted = (
                verdict.refusal_reason.type
                if verdict.refusal_reason is not None else None
            )
            out.append({
                "query_id": r["query_id"],
                "gold": r["label"],
                "predicted": predicted,
                "refused": refused,
            })
    return out


# ── Metrics ────────────────────────────────────────────────────────────────


def _per_type_recall(predictions: list[dict]) -> dict[str, float]:
    by_type_total: Counter = Counter()
    by_type_correct: Counter = Counter()
    for p in predictions:
        by_type_total[p["gold"]] += 1
        if p["refused"] and p["predicted"] == p["gold"]:
            by_type_correct[p["gold"]] += 1
    return {
        t: (by_type_correct[t] / by_type_total[t]) if by_type_total[t] else 0.0
        for t in REFUSAL_TYPES
    }


def score(predictions: list[dict]) -> dict:
    n = len(predictions)
    refused = sum(1 for p in predictions if p["refused"])
    correct = sum(
        1 for p in predictions if p["refused"] and p["predicted"] == p["gold"]
    )
    accuracy = refused / n if n else 0.0
    precision = correct / refused if refused else 0.0
    recall = _per_type_recall(predictions)
    return {
        "n": n,
        "refused": refused,
        "correct": correct,
        "refusal_accuracy": accuracy,
        "refusal_precision": precision,
        "refusal_recall_per_type": recall,
    }


# ── Driver ─────────────────────────────────────────────────────────────────


def run() -> dict:
    rows = load_corpus(CORPUS_PATH)
    sha = hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()
    by_baseline: dict[str, dict] = {}
    for name, fn in [
        ("RAVEN_v1", baseline_raven_v1),
        ("LLM-judge", baseline_llm_judge),
        ("RAVEN_v2", baseline_raven_v2),
    ]:
        t0 = time.perf_counter()
        preds = fn(rows)
        dt = time.perf_counter() - t0
        result = score(preds)
        result["latency_seconds"] = dt
        by_baseline[name] = result

    return {
        "corpus_path": str(CORPUS_PATH),
        "corpus_sha256": sha,
        "n_queries": len(rows),
        "results": by_baseline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()

    results = run()
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True))

    print(f"\nCorpus SHA-256: {results['corpus_sha256']}")
    print(f"Queries:        {results['n_queries']}")
    print()
    for name, r in results["results"].items():
        print(f"== {name} ==")
        print(f"  refusal_accuracy:  {r['refusal_accuracy']:.3f}")
        print(f"  refusal_precision: {r['refusal_precision']:.3f}")
        print(f"  recall_per_type:")
        for t, v in r["refusal_recall_per_type"].items():
            print(f"    {t:<38} {v:.3f}")
        print(f"  latency_seconds:   {r['latency_seconds']:.2f}")
        print()
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
