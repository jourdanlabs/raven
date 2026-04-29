"""AURORA — Confidence gate engine.

Computes a composite score per retrieved memory entry using signals from
all upstream engines. Approves, conditionally approves, or refuses the
full response based on thresholds.

Hard rules:
  - If an entry is superseded and stale -> REJECT (confidence 0.0)
  - If overall_confidence < REFUSE_THRESHOLD and no entries approved -> REFUSE
  - If overall_confidence >= APPROVE_THRESHOLD -> APPROVED
  - If APPROVE_THRESHOLD > overall_confidence >= CONDITIONAL_THRESHOLD -> CONDITIONAL
  - Otherwise -> REJECTED

Capability 1.3 adds :func:`validate_aurora_v2` — an optional companion to
:func:`run_aurora` that returns an :class:`AuroraVerdict` with a typed
:class:`RefusalReason` on refusal. The v1.0 entrypoint is unchanged.
"""
from __future__ import annotations

import hashlib

from raven.types import (
    AuroraInput,
    AuroraVerdict,
    CausalEdge,
    Contradiction,
    MemoryEntry,
    PipelineTrace,
    RavenResponse,
    RefusalReason,
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


def gate(
    inp: AuroraInput,
    *,
    approve_threshold: float = APPROVE_THRESHOLD,
) -> tuple[list[ScoredMemory], list[ScoredMemory]]:
    """Return (approved, rejected) scored memory lists.

    The ``approve_threshold`` parameter (Phase 2.1) lets a pipeline
    configured with a calibration profile override the module-level
    default without monkey-patching. The default value preserves v1.0
    behaviour exactly — every existing caller keeps its semantics.
    """
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

        if composite >= approve_threshold:
            approved.append(scored)
        else:
            scored.rejection_reason = (
                "contradiction flagged" if conflicted
                else f"below threshold ({composite:.2f} < {approve_threshold})"
            )
            rejected.append(scored)

    approved.sort(key=lambda s: s.score, reverse=True)
    return approved, rejected


def run_aurora(
    inp: AuroraInput,
    trace: PipelineTrace,
    *,
    approve_threshold: float = APPROVE_THRESHOLD,
    conditional_threshold: float = CONDITIONAL_THRESHOLD,
    refuse_threshold: float = REFUSE_THRESHOLD,
) -> RavenResponse:
    """Full AURORA gate. Returns complete RavenResponse.

    Phase 2.1 calibration: thresholds may be overridden per pipeline
    instance via the ``calibration_profile`` argument to
    :class:`RAVENPipeline`. Default values preserve v1.0 behaviour.
    """
    from raven.types import RavenResponse  # avoid circular at module level

    approved, rejected = gate(inp, approve_threshold=approve_threshold)

    trace.aurora_approved = len(approved)
    trace.aurora_rejected = len(rejected)

    if approved:
        overall = sum(s.score for s in approved) / len(approved)
    elif rejected:
        overall = sum(s.score for s in rejected) / len(rejected)
    else:
        overall = 0.0

    if not approved and overall < refuse_threshold:
        status = "REFUSED"
    elif overall >= approve_threshold and approved:
        status = "APPROVED"
    elif overall >= conditional_threshold:
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


# -- Phase 1 capability path: validate_aurora_v2 -> AuroraVerdict ------------


def _verdict_audit_hash(
    decision: str,
    confidence: float,
    refusal_reason: RefusalReason | None,
    contributing_engines: list[str],
) -> str:
    """SHA-256 over (decision, confidence rounded to 6 dp, refusal hash if
    any, sorted contributing_engines).

    Resolved-claims and decay-applied are not yet wired into the v2 path
    (Sub A and Sub B own those surfaces); when they land we extend this
    hash to include their audit_hashes and policy names per the
    AuroraVerdict docstring.
    """
    h = hashlib.sha256()
    h.update(decision.encode("utf-8"))
    h.update(b"\x1f")
    h.update(f"{confidence:.6f}".encode("utf-8"))
    h.update(b"\x1f")
    h.update((refusal_reason.audit_hash if refusal_reason else "").encode("utf-8"))
    h.update(b"\x1f")
    for engine in sorted(contributing_engines):
        h.update(engine.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def validate_aurora_v2(
    aurora_input: AuroraInput,
    *,
    threshold: float = APPROVE_THRESHOLD,
    scope_allowlist: list[str] | None = None,
    query: str = "",
    resolved_claim_count: int = 0,
    contributing_engines: list[str] | None = None,
) -> AuroraVerdict:
    """Phase 1 capability path. Returns an :class:`AuroraVerdict`.

    Co-exists with :func:`run_aurora` — the v1.0 entrypoint is unchanged
    and continues to return :class:`RavenResponse`. New callers that want
    typed refusal reasons consume :class:`AuroraVerdict` from this
    function.

    Decision rule mirrors v1.0:
      - At least one approved entry whose composite >= ``threshold`` =>
        ``decision="approve"``.
      - Otherwise => ``decision="refuse"`` with a typed
        :class:`RefusalReason` produced by
        :func:`raven.refusal.classify_refusal`.

    The ``confidence`` field on the verdict is the mean composite score of
    approved entries (matching v1.0 ``overall_confidence``) on approve, or
    the mean composite of rejected entries (matching v1.0 fallback) on
    refuse.

    ``contributing_engines`` is recorded on the verdict so downstream
    audit consumers can see which engines ran. Pipeline callers populate
    this from :class:`PipelineTrace`. Direct callers can pass an explicit
    list.
    """
    # Local import to avoid circular dependency at module load time.
    from raven.refusal import classify_refusal

    approved, rejected = gate(aurora_input)

    if approved:
        confidence = sum(s.score for s in approved) / len(approved)
    elif rejected:
        confidence = sum(s.score for s in rejected) / len(rejected)
    else:
        confidence = 0.0

    engines = list(contributing_engines) if contributing_engines else []

    if approved and confidence >= threshold:
        audit = _verdict_audit_hash("approve", confidence, None, engines)
        return AuroraVerdict(
            decision="approve",
            confidence=confidence,
            refusal_reason=None,
            resolved_claims=[],
            decay_applied=[],
            audit_hash=audit,
            contributing_engines=engines,
        )

    refusal = classify_refusal(
        query=query,
        aurora_input=aurora_input,
        aurora_threshold=threshold,
        scope_allowlist=scope_allowlist,
        resolved_claim_count=resolved_claim_count,
    )
    audit = _verdict_audit_hash("refuse", confidence, refusal, engines)
    return AuroraVerdict(
        decision="refuse",
        confidence=confidence,
        refusal_reason=refusal,
        resolved_claims=[],
        decay_applied=[],
        audit_hash=audit,
        contributing_engines=engines,
    )
