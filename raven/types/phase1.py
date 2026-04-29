"""Phase 1 capability types.

Shared by all three Phase 1 sub-agents (1.1 reconciliation, 1.2 decay,
1.3 refusal). These types are the contract surface — modifying them
during the sprint requires Captain coordination per the brief's
Coordination Protocol.

Design rules:
- Every type is `frozen=True` — verdicts, claims, and policies are
  immutable values, never mutated after construction.
- AuroraVerdict is a SUPERSET of v1.0 RavenResponse, not a replacement.
  Existing callers that consume RavenResponse keep working. AuroraVerdict
  is consumed by NEW capability paths.
- audit_hash on every verdict / claim / refusal is the SHA-256 of the
  decision inputs — required for chain-of-custody replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .v1 import MemoryEntry


# Backward-compat alias — brief uses "Memory", v1.0 calls it MemoryEntry.
# Both names resolve to the same type so capability code can use either.
Memory = MemoryEntry


# ── Capability 1.1 — Contradiction Reconciliation ───────────────────────────


@dataclass(frozen=True)
class EvidenceNode:
    """Atomic evidence unit in a reconciliation chain.

    One node per engine that contributed to the reconciliation decision.
    Sub-agent A populates these from PULSAR / ECLIPSE / NOVA / QUASAR /
    METEOR outputs while building a ResolvedClaim.
    """

    engine: Literal["meteor", "nova", "eclipse", "pulsar", "quasar", "aurora"]
    finding: str  # short human-readable string ("temporal: B is 47d newer")
    score: float  # 0.0 .. 1.0 — engine's confidence in this evidence
    timestamp: float  # unix epoch — when this evidence was registered


@dataclass(frozen=True)
class MemoryClass:
    """Classification of a memory for decay and reconciliation purposes.

    `name` is one of: 'factual' | 'preference' | 'transactional' | 'contextual' | 'identity'.
    `decay_curve` references a registered DecayPolicy by name, or None if no decay applies.
    `reconcilable` controls whether memories of this class can be reconciled when contradictory
    (e.g., identity claims should usually NOT be silently reconciled — flag instead).
    """

    name: str
    decay_curve: Optional[str]
    reconcilable: bool


@dataclass(frozen=True)
class ResolvedClaim:
    """Output of contradiction reconciliation — Capability 1.1's primary verdict surface.

    `winner` and `loser` are the two memories that were in contradiction;
    `winner` is the one PULSAR + reconciliation hierarchy selected as authoritative.
    `reconciliation_basis` records WHICH rule fired (temporal, evidence_strength, etc.)
    so the decision is auditable and reviewable.
    """

    winner: Memory
    loser: Memory
    reconciliation_basis: Literal[
        "temporal", "importance", "evidence_strength", "identity"
    ]
    confidence: float  # 0.0 .. 1.0 — confidence in the reconciliation, NOT in the winner
    evidence_chain: list[EvidenceNode] = field(default_factory=list)
    audit_hash: str = ""  # SHA-256 of (winner.id, loser.id, basis, evidence_chain)


# ── Capability 1.2 — Decay-Aware Recall ─────────────────────────────────────


@dataclass(frozen=True)
class DecayPolicy:
    """Decay curve for a memory class — Capability 1.2's policy surface.

    `half_life_seconds` is the time at which confidence is halved. None means
    no decay (e.g., identity claims should never decay).
    `floor_confidence` is the value below which decay can never push the memory's
    effective confidence — protects very-old-but-foundational facts.
    `applies_to_class` is the MemoryClass.name this policy is registered for.

    Built-in policies (Capability 1.2 sub-agent registers these on init):
      - factual_short  : half_life=1d,    floor=0.10
      - factual_long   : half_life=30d,   floor=0.20
      - preference     : half_life=90d,   floor=0.30
      - transactional  : half_life=4h,    floor=0.05
      - contextual     : half_life=7d,    floor=0.15
      - identity       : half_life=None,  floor=0.50  (no decay)
    """

    name: str
    half_life_seconds: Optional[float]
    floor_confidence: float
    applies_to_class: str


# ── Capability 1.3 — Structured Refusal ─────────────────────────────────────


@dataclass(frozen=True)
class RefusalReason:
    """Structured refusal output — Capability 1.3's primary surface.

    Replaces the v1.0 free-form refusal-as-status pattern with a typed reason
    that downstream agents can route on. Each `type` corresponds to a distinct
    failure mode in the validation pipeline; recommended_action tells the calling
    agent what to do about it.
    """

    type: Literal[
        "insufficient_evidence",
        "conflicting_evidence_unresolvable",
        "staleness_threshold_exceeded",
        "identity_ambiguous",
        "scope_violation",
    ]
    confidence: float  # 0.0 .. 1.0 — RAVEN's confidence in the refusal classification itself
    what_we_know: list[str] = field(default_factory=list)  # human-readable summary
    what_we_dont: list[str] = field(default_factory=list)  # human-readable gap list
    recommended_action: Literal[
        "ask_user", "request_context", "surface_uncertainty", "escalate"
    ] = "ask_user"
    audit_hash: str = ""  # SHA-256 of (type, confidence, what_we_know, what_we_dont)


# ── Updated AURORA verdict — superset of v1.0 output ────────────────────────


@dataclass(frozen=True)
class AuroraVerdict:
    """AURORA's final output for the Phase 1 capability path.

    SUPERSET of v1.0 — existing callers using RavenResponse continue to work
    unchanged. NEW callers that need refusal reasons, resolved claims, or
    decay attribution use AuroraVerdict.

    Construction rules:
      - decision="refuse" REQUIRES refusal_reason populated.
      - decision="approve" REQUIRES refusal_reason is None.
      - resolved_claims is populated whenever PULSAR's reconciliation fired
        for this verdict (may be empty even on approve).
      - decay_applied lists every DecayPolicy whose curve was applied to any
        candidate memory feeding this verdict.
      - audit_hash is the SHA-256 over (decision, confidence, refusal_reason
        audit_hash if any, sorted resolved_claims audit_hashes, sorted
        decay_applied policy names, sorted contributing_engines).
    """

    decision: Literal["approve", "refuse"]
    confidence: float
    refusal_reason: Optional[RefusalReason] = None
    resolved_claims: list[ResolvedClaim] = field(default_factory=list)
    decay_applied: list[DecayPolicy] = field(default_factory=list)
    audit_hash: str = ""
    contributing_engines: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Cheap sanity invariants. Keeps capability sub-agents from constructing
        # contradictory verdicts.
        if self.decision == "refuse" and self.refusal_reason is None:
            raise ValueError("AuroraVerdict.decision='refuse' requires refusal_reason")
        if self.decision == "approve" and self.refusal_reason is not None:
            raise ValueError(
                "AuroraVerdict.decision='approve' must have refusal_reason=None"
            )


__all__ = [
    "Memory",
    "EvidenceNode",
    "MemoryClass",
    "ResolvedClaim",
    "DecayPolicy",
    "RefusalReason",
    "AuroraVerdict",
]
