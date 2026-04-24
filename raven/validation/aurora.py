"""AURORA — Confidence gate engine.

Computes a composite score per retrieved memory entry using signals from
all upstream engines. Approves, conditionally approves, or refuses the
full response based on thresholds.

Hard rules:
  - If an entry is superseded and stale → REJECT (confidence 0.0)
  - If overall_confidence < REFUSE_THRESHOLD and no entries approved → REFUSE
  - If overall_confidence >= APPROVE_THRESHOLD → APPROVED
  - If APPROVE_THRESHOLD > overall_confidence >= CONDITIONAL_THRESHOLD → CONDITIONAL
  - Otherwise → REJECTED
"""
from __future__ import annotations

from raven.types import (
    AuroraInput,
    CausalEdge,
    Contradiction,
    MemoryEntry,
    PipelineTrace,
    RavenResponse,
    ScoredMemory,
)

APPROVE_THRESHOLD = 0.80
CONDITIONAL_THRESHOLD = 0.60
REFUSE_THRESHOLD = 0.30

# Base weights must sum to 1.0 so a non-conflicted entry with high recency
# and high importance can achieve >= APPROVE_THRESHOLD without any causal context.
# NOVA is applied as an additive bonus (up to +0.10) on top of the base.
WEIGHTS = {
    "eclipse": 0.25,   # recency/decay
    "quasar": 0.45,    # importance
    "pulsar": 0.30,    # conflict-free bonus
}
NOVA_BONUS_MAX = 0.10  # causal centrality bonus, additive


def _entry_composite(
    entry: MemoryEntry,
    decay: float,
    importance: float,
    conflicted: bool,
    causal_edges: list[CausalEdge],
) -> float:
    base = (
        decay * WEIGHTS["eclipse"]
        + importance * WEIGHTS["quasar"]
        + (0.0 if conflicted else 1.0) * WEIGHTS["pulsar"]
    )

    from raven.validation.nova import causal_centrality
    centrality = causal_centrality(entry.id, causal_edges)
    nova_bonus = centrality * NOVA_BONUS_MAX

    return min(1.0, base + nova_bonus)


def gate(inp: AuroraInput) -> tuple[list[ScoredMemory], list[ScoredMemory]]:
    """Return (approved, rejected) scored memory lists."""
    conflicted_ids = {
        c.entry_a.id for c in inp.contradictions
    } | {c.entry_b.id for c in inp.contradictions}

    approved: list[ScoredMemory] = []
    rejected: list[ScoredMemory] = []

    for i, entry in enumerate(inp.entries):
        decay = inp.decay_weights[i] if i < len(inp.decay_weights) else 0.5
        importance = inp.importance_scores[i] if i < len(inp.importance_scores) else 0.5
        conflicted = entry.id in conflicted_ids

        # Hard reject: stale / superseded
        if entry.id in inp.stale_ids:
            rejected.append(ScoredMemory(
                entry=entry,
                score=0.0,
                engine_scores={"stale": 0.0},
                rejection_reason="superseded by newer entry",
            ))
            continue

        composite = _entry_composite(entry, decay, importance, conflicted, inp.causal_edges)

        scored = ScoredMemory(
            entry=entry,
            score=composite,
            engine_scores={
                "eclipse": decay,
                "quasar": importance,
                "pulsar": 0.0 if conflicted else 1.0,
            },
        )

        if composite >= APPROVE_THRESHOLD:
            approved.append(scored)
        else:
            scored.rejection_reason = (
                "contradiction flagged" if conflicted
                else f"below threshold ({composite:.2f} < {APPROVE_THRESHOLD})"
            )
            rejected.append(scored)

    approved.sort(key=lambda s: s.score, reverse=True)
    return approved, rejected


def run_aurora(inp: AuroraInput, trace: PipelineTrace) -> RavenResponse:
    """Full AURORA gate. Returns complete RavenResponse."""
    from raven.types import RavenResponse  # avoid circular at module level

    approved, rejected = gate(inp)

    trace.aurora_approved = len(approved)
    trace.aurora_rejected = len(rejected)

    if approved:
        overall = sum(s.score for s in approved) / len(approved)
    elif rejected:
        overall = sum(s.score for s in rejected) / len(rejected)
    else:
        overall = 0.0

    if not approved and overall < REFUSE_THRESHOLD:
        status = "REFUSED"
    elif overall >= APPROVE_THRESHOLD and approved:
        status = "APPROVED"
    elif overall >= CONDITIONAL_THRESHOLD:
        status = "CONDITIONAL"
    else:
        status = "REJECTED"

    return RavenResponse(
        query=trace.notes[0] if trace.notes else "",
        status=status,  # type: ignore[arg-type]
        overall_confidence=overall,
        approved_memories=approved,
        flagged_contradictions=inp.contradictions,
        rejected_memories=rejected,
        pipeline_trace=trace,
    )
