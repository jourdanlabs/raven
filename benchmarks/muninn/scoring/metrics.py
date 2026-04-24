"""MUNINN scoring metrics — per-hazard precision, recall, F1, status accuracy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScenarioResult:
    query_id: str
    hazard_mode: str
    baseline: str
    status_match: bool
    precision: float   # approved ∩ expected / approved  (1.0 if both empty)
    recall: float      # approved ∩ expected / expected  (1.0 if expected empty)
    f1: float


@dataclass
class HazardSummary:
    hazard_mode: str
    baseline: str
    n: int
    status_accuracy: float
    mean_precision: float
    mean_recall: float
    mean_f1: float


@dataclass
class BenchmarkReport:
    baseline: str
    per_hazard: list[HazardSummary]
    overall_f1: float
    overall_status_accuracy: float
    scenario_results: list[ScenarioResult] = field(default_factory=list)


def score_scenario(
    query_id: str,
    hazard_mode: str,
    baseline: str,
    actual_status: str,
    actual_approved_ids: set[str],
    expected_status: str,
    expected_approved_ids: set[str],
) -> ScenarioResult:
    status_match = actual_status == expected_status

    if not actual_approved_ids and not expected_approved_ids:
        precision = recall = f1 = 1.0
    elif not actual_approved_ids:
        precision = 1.0
        recall = 0.0
        f1 = 0.0
    elif not expected_approved_ids:
        precision = 0.0
        recall = 1.0
        f1 = 0.0
    else:
        tp = len(actual_approved_ids & expected_approved_ids)
        precision = tp / len(actual_approved_ids)
        recall = tp / len(expected_approved_ids)
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return ScenarioResult(
        query_id=query_id,
        hazard_mode=hazard_mode,
        baseline=baseline,
        status_match=status_match,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def aggregate(results: list[ScenarioResult], baseline: str) -> BenchmarkReport:
    from collections import defaultdict
    by_hazard: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        by_hazard[r.hazard_mode].append(r)

    per_hazard = []
    for hazard, hazard_results in sorted(by_hazard.items()):
        n = len(hazard_results)
        per_hazard.append(HazardSummary(
            hazard_mode=hazard,
            baseline=baseline,
            n=n,
            status_accuracy=sum(r.status_match for r in hazard_results) / n,
            mean_precision=sum(r.precision for r in hazard_results) / n,
            mean_recall=sum(r.recall for r in hazard_results) / n,
            mean_f1=sum(r.f1 for r in hazard_results) / n,
        ))

    n_total = len(results)
    return BenchmarkReport(
        baseline=baseline,
        per_hazard=per_hazard,
        overall_f1=sum(r.f1 for r in results) / n_total if n_total else 0.0,
        overall_status_accuracy=sum(r.status_match for r in results) / n_total if n_total else 0.0,
        scenario_results=results,
    )
