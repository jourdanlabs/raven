"""MUNINN scoring harness — loads corpus, runs all baselines, outputs results.

Usage:
    python -m benchmarks.muninn.scoring.harness [--baseline NAME] [--output results.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from benchmarks.muninn.scoring.baselines import ALL_BASELINES
from benchmarks.muninn.scoring.metrics import aggregate, score_scenario, BenchmarkReport


_CORPUS_DIR = Path(__file__).parent.parent / "corpus"
# Fixed reference time matching the corpus anchor — makes scores reproducible
# regardless of when the benchmark is run.
_CORPUS_NOW = 1745481600.0   # 2026-04-24 00:00:00 UTC


def _verify_sha(corpus_path: Path, sha_path: Path) -> None:
    expected = sha_path.read_text().strip()
    actual = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"Corpus SHA-256 mismatch — corpus may have been tampered with.\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}"
        )


def load_corpus(verify: bool = True) -> tuple[dict[str, dict], list[dict]]:
    corpus_path = _CORPUS_DIR / "corpus.jsonl"
    sha_path = _CORPUS_DIR / "corpus.sha256"
    queries_path = _CORPUS_DIR / "queries.jsonl"

    if verify and sha_path.exists():
        _verify_sha(corpus_path, sha_path)

    entries_by_id: dict[str, dict] = {}
    for line in corpus_path.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            entries_by_id[e["id"]] = e

    queries = [
        json.loads(line)
        for line in queries_path.read_text().splitlines()
        if line.strip()
    ]
    return entries_by_id, queries


def run_baseline(
    baseline_name: str,
    entries_by_id: dict[str, dict],
    queries: list[dict],
    verbose: bool = False,
) -> BenchmarkReport:
    fn = ALL_BASELINES[baseline_name]
    scenario_results = []

    for q in queries:
        qid = q["query_id"]
        query_text = q["query_text"]
        hazard = q["hazard_mode"]
        entry_ids = q["entry_ids"]
        expected_status = q["expected_status"]
        expected_approved = set(q["expected_approved_ids"])

        raw = [entries_by_id[eid] for eid in entry_ids if eid in entries_by_id]
        try:
            # Pass corpus anchor so decay/recency are scored at the corpus reference time
            import inspect
            sig = inspect.signature(fn)
            result = fn(query_text, raw, _CORPUS_NOW) if "now" in sig.parameters else fn(query_text, raw)
        except Exception as exc:
            if verbose:
                print(f"  [WARN] {baseline_name} error on {qid}: {exc}", file=sys.stderr)
            result_status = "REFUSED"
            result_approved = set()
            result_rejected = set(entry_ids)
        else:
            result_status = result.status
            result_approved = result.approved_ids
            result_rejected = result.rejected_ids

        scenario_results.append(score_scenario(
            query_id=qid,
            hazard_mode=hazard,
            baseline=baseline_name,
            actual_status=result_status,
            actual_approved_ids=result_approved,
            expected_status=expected_status,
            expected_approved_ids=expected_approved,
        ))

    return aggregate(scenario_results, baseline_name)


def print_report(report: BenchmarkReport) -> None:
    w = 26
    print(f"\n{'─'*60}")
    print(f"  MUNINN — {report.baseline}")
    print(f"{'─'*60}")
    print(f"  {'Hazard mode':<{w}}  {'N':>4}  {'F1':>6}  {'Prec':>6}  {'Rec':>6}  {'Status%':>7}")
    print(f"  {'─'*w}  {'─'*4}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}")
    for h in report.per_hazard:
        print(
            f"  {h.hazard_mode:<{w}}  {h.n:>4}  "
            f"{h.mean_f1:>6.3f}  {h.mean_precision:>6.3f}  "
            f"{h.mean_recall:>6.3f}  {h.status_accuracy:>7.1%}"
        )
    print(f"  {'─'*w}  {'─'*4}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}")
    print(f"  {'OVERALL':<{w}}  {200:>4}  {report.overall_f1:>6.3f}  {'':>6}  {'':>6}  {report.overall_status_accuracy:>7.1%}")
    print()


def run_all(
    baseline_names: list[str] | None = None,
    output_path: str | None = None,
    verbose: bool = False,
) -> list[BenchmarkReport]:
    names = baseline_names or list(ALL_BASELINES.keys())
    entries_by_id, queries = load_corpus()

    print(f"\nMUNINN Benchmark  ·  {len(entries_by_id)} entries  ·  {len(queries)} queries")
    print(f"Baselines: {', '.join(names)}\n")

    reports = []
    for name in names:
        t0 = time.perf_counter()
        report = run_baseline(name, entries_by_id, queries, verbose=verbose)
        elapsed = time.perf_counter() - t0
        print(f"  {name:<26} done in {elapsed:.2f}s  (F1={report.overall_f1:.3f})")
        reports.append(report)

    for report in reports:
        print_report(report)

    if output_path:
        serialized = []
        for r in reports:
            serialized.append({
                "baseline": r.baseline,
                "overall_f1": r.overall_f1,
                "overall_status_accuracy": r.overall_status_accuracy,
                "per_hazard": [
                    {
                        "hazard_mode": h.hazard_mode,
                        "n": h.n,
                        "mean_f1": h.mean_f1,
                        "mean_precision": h.mean_precision,
                        "mean_recall": h.mean_recall,
                        "status_accuracy": h.status_accuracy,
                    }
                    for h in r.per_hazard
                ],
            })
        Path(output_path).write_text(json.dumps(serialized, indent=2))
        print(f"\n  Results saved → {output_path}")

    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MUNINN benchmark")
    parser.add_argument("--baseline", nargs="*", help="Baselines to run (default: all)")
    parser.add_argument("--output", help="Save results JSON to this path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    run_all(baseline_names=args.baseline, output_path=args.output, verbose=args.verbose)


if __name__ == "__main__":
    main()
