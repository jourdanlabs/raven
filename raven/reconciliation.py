"""Capability 1.1 — Contradiction reconciliation.

When PULSAR detects two memories in conflict, this module decides which one
wins and records WHY (the "reconciliation_basis"). Output is the typed
`ResolvedClaim` defined in `raven.types.phase1` — a frozen verdict with a
SHA-256 audit hash so downstream replay is deterministic.

Hierarchy (FIRST MATCH WINS):
  a. Identity precedence  (METEOR + class)   — identity claim beats contextual
  b. Temporal precedence  (ECLIPSE)          — newer-and-still-valid beats older
  c. Evidence strength    (NOVA)             — deeper causal chain wins
  d. Importance ranking   (QUASAR class rank) — class with higher default rank wins

Each rule that fires populates `evidence_chain` with the EvidenceNode(s) from
the engine that fired it, so a ResolvedClaim is fully auditable.

Notes / dependencies:
  - v1.0 `MemoryEntry` does NOT carry a `memory_class` field yet. Sub-agent B
    is adding it in capability 1.2. Until that lands, we infer the class from
    `topic_tags` / `metadata['memory_class']` via `derive_memory_class()` and
    fall back to "contextual". When 1.2 ships, swap in `entry.memory_class`
    where derive_memory_class() is called. Tracked in GAPS.md as GAPS-007.
  - METEOR's `tag_entities()` is the source of truth for entity overlap; we
    don't try to second-guess its alias resolution.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from raven.types import (
    CausalEdge,
    EvidenceNode,
    Memory,
    MemoryClass,
    ResolvedClaim,
)
from raven.validation import meteor


# ── Class-level importance ranking ──────────────────────────────────────────
#
# QUASAR scores per-memory; for class-level reconciliation we need a stable
# default ordering. Higher = wins on tie.
CLASS_RANK: dict[str, int] = {
    "identity": 5,
    "factual": 4,
    "preference": 3,
    "contextual": 2,
    "transactional": 1,
}

# Built-in class registry — used until Sub B's MemoryClass-on-MemoryEntry lands.
DEFAULT_MEMORY_CLASSES: dict[str, MemoryClass] = {
    "identity":      MemoryClass(name="identity",      decay_curve=None,             reconcilable=False),
    "factual":       MemoryClass(name="factual",       decay_curve="factual_long",   reconcilable=True),
    "preference":    MemoryClass(name="preference",    decay_curve="preference",     reconcilable=True),
    "contextual":    MemoryClass(name="contextual",    decay_curve="contextual",     reconcilable=True),
    "transactional": MemoryClass(name="transactional", decay_curve="transactional",  reconcilable=True),
}


# ── ReconciliationContext ───────────────────────────────────────────────────


@dataclass
class ReconciliationContext:
    """Engine outputs needed by the four-rule hierarchy.

    Built by the pipeline hook (`raven.pipeline.RAVENPipeline._build_reco_ctx`)
    from the existing METEOR / ECLIPSE / NOVA / QUASAR outputs. Capability code
    that calls `reconcile()` directly (e.g. unit tests, scoring harness) builds
    this dataclass itself.
    """

    # METEOR: canonical entity tags per memory id (output of tag_entities).
    meteor_entities: dict[str, list[str]] = field(default_factory=dict)
    # ECLIPSE: validity check — None means "use entry.validity_end as-is".
    now: Optional[float] = None
    # NOVA: full causal-graph edge list — used for chain-depth comparison.
    causal_edges: list[CausalEdge] = field(default_factory=list)
    # QUASAR: per-memory importance score (0.0..1.0). Available but not
    # required — the 4th hierarchy rule uses CLASS_RANK, not these scores.
    importance_scores: dict[str, float] = field(default_factory=dict)
    # Optional override: pre-computed class for a given memory id. If absent,
    # `derive_memory_class()` is used to infer.
    memory_class_override: dict[str, str] = field(default_factory=dict)


# ── Class derivation (heuristic until 1.2 lands MemoryEntry.memory_class) ──


def derive_memory_class(memory: Memory, override: Optional[str] = None) -> MemoryClass:
    """Infer MemoryClass for a memory.

    Order of resolution:
      1. explicit override (caller-provided, e.g. unit tests)
      2. memory.metadata["memory_class"] (corpus-tagged)
      3. memory.topic_tags overlap with class names
      4. fallback to "contextual"
    """
    if override and override in DEFAULT_MEMORY_CLASSES:
        return DEFAULT_MEMORY_CLASSES[override]

    md_class = memory.metadata.get("memory_class") if memory.metadata else None
    if isinstance(md_class, str) and md_class in DEFAULT_MEMORY_CLASSES:
        return DEFAULT_MEMORY_CLASSES[md_class]

    for tag in memory.topic_tags:
        if tag in DEFAULT_MEMORY_CLASSES:
            return DEFAULT_MEMORY_CLASSES[tag]

    return DEFAULT_MEMORY_CLASSES["contextual"]


# ── Rule helpers ────────────────────────────────────────────────────────────


def _entities_for(memory: Memory, ctx: ReconciliationContext) -> set[str]:
    """Canonical entity set for a memory. Prefers ctx, falls back to METEOR."""
    if memory.id in ctx.meteor_entities:
        return set(ctx.meteor_entities[memory.id])
    return set(meteor.tag_entities(memory.text))


def _is_well_grounded(memory: Memory, now: Optional[float]) -> bool:
    """ECLIPSE-style validity check. True if validity window covers `now`."""
    if memory.validity_end is None:
        return True
    if now is None:
        # No clock supplied — be conservative: treat as well-grounded only if
        # validity_end is in the future relative to entry.timestamp.
        return memory.validity_end > memory.timestamp
    return memory.validity_end > now


def _causal_depth(memory_id: str, edges: list[CausalEdge]) -> float:
    """Weighted depth of edges incident on this memory.

    Sum of `edge.weight` for every edge with from_id == memory_id or
    to_id == memory_id. Weighted (not raw count) so a tightly
    word-overlapping chain memory contributes more "evidence depth" than
    a thinly-overlapping one — this matters when both sides of a pair
    pick up at least one chain edge (NOVA's word-overlap heuristic is
    permissive). Identical depths still tie and skip to rule (d).
    """
    if not edges:
        return 0.0
    return sum(e.weight for e in edges if e.from_id == memory_id or e.to_id == memory_id)


# ── Hierarchy rules ─────────────────────────────────────────────────────────


def _rule_identity(
    a: Memory, b: Memory, ctx: ReconciliationContext
) -> Optional[tuple[Memory, Memory, list[EvidenceNode]]]:
    """Rule (a) — identity precedence.

    Fires when:
      - Both memories share at least one canonical entity (METEOR)
      - Exactly one of {a, b} is class 'identity', the other is non-identity
      - The identity memory's class is NOT reconcilable (identity wins outright)

    The non-reconcilable check matches the MemoryClass.reconcilable flag — if a
    future identity-class is marked reconcilable, this rule will not fire and
    the next rule gets a chance.
    """
    cls_a = derive_memory_class(a, ctx.memory_class_override.get(a.id))
    cls_b = derive_memory_class(b, ctx.memory_class_override.get(b.id))

    ent_a = _entities_for(a, ctx)
    ent_b = _entities_for(b, ctx)
    shared = ent_a & ent_b
    if not shared:
        return None

    if cls_a.name == "identity" and cls_b.name != "identity":
        identity, other = a, b
    elif cls_b.name == "identity" and cls_a.name != "identity":
        identity, other = b, a
    else:
        return None

    # Identity is non-reconcilable per default config — that's what makes it
    # "win" outright. If a deployment marks identity reconcilable, skip.
    identity_cls = derive_memory_class(identity, ctx.memory_class_override.get(identity.id))
    if identity_cls.reconcilable:
        return None

    evidence = [
        EvidenceNode(
            engine="meteor",
            finding=f"identity overlap on entities: {sorted(shared)}",
            score=1.0,
            timestamp=identity.timestamp,
        ),
        EvidenceNode(
            engine="pulsar",
            finding=(
                f"identity-class memory {identity.id!r} beats "
                f"{other.id!r} (class={derive_memory_class(other, ctx.memory_class_override.get(other.id)).name})"
            ),
            score=1.0,
            timestamp=identity.timestamp,
        ),
    ]
    return identity, other, evidence


def _rule_temporal(
    a: Memory, b: Memory, ctx: ReconciliationContext
) -> Optional[tuple[Memory, Memory, list[EvidenceNode]]]:
    """Rule (b) — temporal precedence.

    Both memories must be well-grounded (validity window covers ctx.now). If
    so, the newer timestamp wins. Equal timestamps return None so the next
    rule gets a chance.
    """
    if not _is_well_grounded(a, ctx.now) or not _is_well_grounded(b, ctx.now):
        return None
    if a.timestamp == b.timestamp:
        return None
    winner, loser = (a, b) if a.timestamp > b.timestamp else (b, a)
    delta_days = abs(a.timestamp - b.timestamp) / 86_400.0
    evidence = [
        EvidenceNode(
            engine="eclipse",
            finding=f"temporal: winner is {delta_days:.2f}d newer (and still valid)",
            score=min(1.0, delta_days / 30.0 + 0.5),
            timestamp=winner.timestamp,
        ),
    ]
    return winner, loser, evidence


def _rule_evidence_strength(
    a: Memory, b: Memory, ctx: ReconciliationContext
) -> Optional[tuple[Memory, Memory, list[EvidenceNode]]]:
    """Rule (c) — evidence strength.

    Compares NOVA causal-edge depth (in+out edges incident on each memory).
    Memory with the deeper chain wins. Tie returns None.
    """
    depth_a = _causal_depth(a.id, ctx.causal_edges)
    depth_b = _causal_depth(b.id, ctx.causal_edges)
    if depth_a == depth_b:
        return None
    winner, loser = (a, b) if depth_a > depth_b else (b, a)
    evidence = [
        EvidenceNode(
            engine="nova",
            finding=(
                f"causal depth: winner weighted-depth={max(depth_a, depth_b):.3f} "
                f"vs loser={min(depth_a, depth_b):.3f}"
            ),
            score=min(1.0, max(depth_a, depth_b) / 5.0),
            timestamp=winner.timestamp,
        ),
    ]
    return winner, loser, evidence


def _rule_importance(
    a: Memory, b: Memory, ctx: ReconciliationContext
) -> Optional[tuple[Memory, Memory, list[EvidenceNode]]]:
    """Rule (d) — importance ranking.

    Per-class default ranking (CLASS_RANK). Higher rank wins. Tie returns None
    (no rule fired, memories are not reconcilable on this basis).
    """
    cls_a = derive_memory_class(a, ctx.memory_class_override.get(a.id))
    cls_b = derive_memory_class(b, ctx.memory_class_override.get(b.id))
    rank_a = CLASS_RANK.get(cls_a.name, 0)
    rank_b = CLASS_RANK.get(cls_b.name, 0)
    if rank_a == rank_b:
        return None
    winner, loser = (a, b) if rank_a > rank_b else (b, a)
    win_cls = cls_a if rank_a > rank_b else cls_b
    lose_cls = cls_b if rank_a > rank_b else cls_a
    evidence = [
        EvidenceNode(
            engine="quasar",
            finding=(
                f"importance: class {win_cls.name!r} (rank {max(rank_a, rank_b)}) "
                f"beats {lose_cls.name!r} (rank {min(rank_a, rank_b)})"
            ),
            score=max(rank_a, rank_b) / max(CLASS_RANK.values()),
            timestamp=winner.timestamp,
        ),
    ]
    return winner, loser, evidence


# ── Audit hash ──────────────────────────────────────────────────────────────


def compute_audit_hash(
    winner_id: str,
    loser_id: str,
    basis: str,
    evidence_chain: list[EvidenceNode],
) -> str:
    """SHA-256 of the canonical reconciliation tuple.

    Spec from brief:
      SHA-256(winner.id + "|" + loser.id + "|" + basis + "|" + sorted(chain))
    where chain is sorted by (engine, finding) for stability.
    """
    sorted_chain = sorted(evidence_chain, key=lambda n: (n.engine, n.finding))
    chain_repr = "|".join(f"{n.engine}:{n.finding}" for n in sorted_chain)
    payload = f"{winner_id}|{loser_id}|{basis}|{chain_repr}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Public API ──────────────────────────────────────────────────────────────


_RULES = [
    ("identity",          _rule_identity),
    ("temporal",          _rule_temporal),
    ("evidence_strength", _rule_evidence_strength),
    ("importance",        _rule_importance),
]


def reconcile(
    memory_a: Memory,
    memory_b: Memory,
    *,
    context: ReconciliationContext,
) -> Optional[ResolvedClaim]:
    """Reconcile two contradictory memories. Returns None if not reconcilable.

    Edge cases:
      - same memory passed twice → None (nothing to reconcile)
      - either memory is None → ValueError
      - no rule fires (all four tied / inapplicable) → None
    """
    if memory_a is None or memory_b is None:
        raise ValueError("reconcile() requires two non-None Memory instances")
    if memory_a.id == memory_b.id:
        return None

    # Walk the hierarchy in order, FIRST MATCH WINS.
    for basis, rule in _RULES:
        result = rule(memory_a, memory_b, context)
        if result is None:
            continue
        winner, loser, evidence = result
        # Confidence in reconciliation = mean evidence score, floor 0.5
        if evidence:
            conf = sum(n.score for n in evidence) / len(evidence)
        else:
            conf = 0.5
        conf = max(0.5, min(1.0, conf))

        audit = compute_audit_hash(winner.id, loser.id, basis, evidence)

        return ResolvedClaim(
            winner=winner,
            loser=loser,
            reconciliation_basis=basis,  # type: ignore[arg-type]
            confidence=conf,
            evidence_chain=list(evidence),
            audit_hash=audit,
        )

    return None


__all__ = [
    "CLASS_RANK",
    "DEFAULT_MEMORY_CLASSES",
    "ReconciliationContext",
    "compute_audit_hash",
    "derive_memory_class",
    "reconcile",
]
