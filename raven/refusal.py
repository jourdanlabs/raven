"""Capability 1.3 — Structured Refusal classifier.

When the RAVEN pipeline decides it cannot answer a query, the v1.0 surface
collapsed every failure mode into ``status="REFUSED"``. That single channel
hides the difference between "I have no evidence", "I have contradictory
evidence", "everything I have is too stale", "I can't tell which entity you
mean", and "this query is outside my permitted scope" — five very different
conditions that downstream agents would respond to in five very different
ways.

This module produces a typed :class:`raven.types.RefusalReason` from the
engine outputs the pipeline already collected. It is the *only* place in the
codebase where the priority order of the five refusal types is decided, so
that priority lives in one auditable function (:func:`classify_refusal`).

Hard rules:
  - The classifier is pure. Same inputs ⇒ same RefusalReason ⇒ same
    audit_hash. Determinism is required for chain-of-custody replay and is
    enforced by the test suite.
  - The classifier never decides *whether* to refuse — that's AURORA's
    job. It only assigns a typed reason to a refusal AURORA already
    committed to.
  - All branches populate ``what_we_know`` (3-5 short strings),
    ``what_we_dont`` (3-5 short strings), and ``recommended_action``.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Literal

from raven.types import (
    AuroraInput,
    Contradiction,
    MemoryEntry,
    RefusalReason,
)

# Default ECLIPSE floor for "all candidates decayed below floor" detection.
# Mirrors the lowest floor across phase1.DecayPolicy built-ins (transactional=0.05).
DEFAULT_DECAY_FLOOR = 0.05

# Confidence we report on the refusal *classification itself*. These are
# calibrated so users can route on confidence: scope_violation is structural
# (we either match the allowlist or we don't) so it's near-certain;
# insufficient_evidence is a fallback so it's the lowest.
_REFUSAL_CONFIDENCE = {
    "scope_violation": 0.99,
    "identity_ambiguous": 0.85,
    "conflicting_evidence_unresolvable": 0.90,
    "staleness_threshold_exceeded": 0.85,
    "insufficient_evidence": 0.70,
}


_RecommendedAction = Literal[
    "ask_user", "request_context", "surface_uncertainty", "escalate"
]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, alpha + digits only. Used by scope check."""
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t]


def _audit_hash(
    type_: str,
    query: str,
    what_we_know: list[str],
    what_we_dont: list[str],
) -> str:
    """SHA-256 of (type, query, sorted what_we_know, sorted what_we_dont).

    Sorting the two lists makes the hash invariant under list-order
    reshuffling so callers that present the strings in a different order
    don't break replay equality.
    """
    h = hashlib.sha256()
    h.update(type_.encode("utf-8"))
    h.update(b"\x1f")
    h.update(query.encode("utf-8"))
    h.update(b"\x1f")
    for s in sorted(what_we_know):
        h.update(s.encode("utf-8"))
        h.update(b"\x1e")
    h.update(b"\x1f")
    for s in sorted(what_we_dont):
        h.update(s.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def _entity_candidates(aurora_input: AuroraInput) -> list[str]:
    """Distinct canonical entity tags across the candidate memories.

    METEOR populates ``entry.entity_tags`` with canonical names. The pipeline
    also reports ``meteor_entity_count`` for the count seen in *retrieved*
    text. Both signals feed the identity-ambiguous check.
    """
    seen: list[str] = []
    for entry in aurora_input.entries:
        for tag in entry.entity_tags:
            if tag not in seen:
                seen.append(tag)
    return seen


def _all_below_floor(weights: Iterable[float], floor: float) -> bool:
    weights = list(weights)
    if not weights:
        return False
    return all(w < floor for w in weights)


def _has_unresolved_contradictions(
    contradictions: list[Contradiction],
    resolved_claim_count: int,
) -> bool:
    """Phase 1.1 (Sub A) emits ResolvedClaim per pair PULSAR reconciled.

    Until Sub A is wired through (and even after), any contradiction that
    has *not* been paired with a ResolvedClaim is treated as unresolved.
    Conservative: if PULSAR found N conflicts and Sub A produced M < N
    resolutions, the leftover N-M are unresolved.
    """
    return len(contradictions) > resolved_claim_count


# ── Per-type builders ────────────────────────────────────────────────────────


def _build_scope_violation(
    query: str,
    offending_tokens: list[str],
    scope_allowlist: list[str],
) -> RefusalReason:
    what_we_know = [
        f"Allowed scope tokens: {', '.join(sorted(scope_allowlist)[:5])}",
        f"Query length: {len(query)} chars",
        "Refusal is structural (allowlist mismatch), not evidence-based",
    ]
    what_we_dont = [
        f"Whether the user has authorization for: {', '.join(offending_tokens[:3])}",
        "Whether the allowlist is complete for this query type",
        "Whether this query should escalate to a human policy reviewer",
    ]
    type_ = "scope_violation"
    return RefusalReason(
        type=type_,
        confidence=_REFUSAL_CONFIDENCE[type_],
        what_we_know=what_we_know,
        what_we_dont=what_we_dont,
        recommended_action="escalate",
        audit_hash=_audit_hash(type_, query, what_we_know, what_we_dont),
    )


def _build_identity_ambiguous(
    query: str,
    candidates: list[str],
) -> RefusalReason:
    head = candidates[:3] if candidates else ["<no canonical entities>"]
    what_we_know = [
        f"Candidate entities: {', '.join(head)}",
        f"Total distinct candidates: {len(candidates)}",
        "Retrieval returned multiple plausible entity matches",
    ]
    what_we_dont = [
        "Which of the candidates the user meant",
        "Whether any of the candidates is the correct authoritative entity",
        "Whether a disambiguating attribute (date, role, project) was omitted",
    ]
    type_ = "identity_ambiguous"
    return RefusalReason(
        type=type_,
        confidence=_REFUSAL_CONFIDENCE[type_],
        what_we_know=what_we_know,
        what_we_dont=what_we_dont,
        recommended_action="ask_user",
        audit_hash=_audit_hash(type_, query, what_we_know, what_we_dont),
    )


def _build_conflicting_evidence(
    query: str,
    contradictions: list[Contradiction],
) -> RefusalReason:
    sample = contradictions[0] if contradictions else None
    what_we_know = [
        f"Contradictions detected: {len(contradictions)}",
        (
            f"Top conflict type: {sample.contradiction_type}"
            if sample
            else "Conflict signal present"
        ),
        (
            f"Top conflict: {sample.description[:90]}"
            if sample
            else "Reconciliation hierarchy did not select a winner"
        ),
    ]
    what_we_dont = [
        "Which conflicting memory should win without a tiebreaker",
        "Whether the source of the contradiction is data error or genuine change",
        "Whether the user prefers the older or newer version",
    ]
    type_ = "conflicting_evidence_unresolvable"
    return RefusalReason(
        type=type_,
        confidence=_REFUSAL_CONFIDENCE[type_],
        what_we_know=what_we_know,
        what_we_dont=what_we_dont,
        recommended_action="surface_uncertainty",
        audit_hash=_audit_hash(type_, query, what_we_know, what_we_dont),
    )


def _build_staleness(
    query: str,
    weights: list[float],
    floor: float,
) -> RefusalReason:
    max_weight = max(weights) if weights else 0.0
    what_we_know = [
        f"Candidate count: {len(weights)}",
        f"Highest decay weight: {max_weight:.3f}",
        f"ECLIPSE floor: {floor:.3f} — every candidate fell below it",
    ]
    what_we_dont = [
        "Whether fresher evidence exists outside the current store",
        "Whether the user wants the stale-but-best-available answer anyway",
        "Whether re-ingest from upstream would supply newer entries",
    ]
    type_ = "staleness_threshold_exceeded"
    return RefusalReason(
        type=type_,
        confidence=_REFUSAL_CONFIDENCE[type_],
        what_we_know=what_we_know,
        what_we_dont=what_we_dont,
        recommended_action="request_context",
        audit_hash=_audit_hash(type_, query, what_we_know, what_we_dont),
    )


def _build_insufficient_evidence(
    query: str,
    aurora_input: AuroraInput,
    aurora_threshold: float,
) -> RefusalReason:
    n = len(aurora_input.entries)
    max_decay = max(aurora_input.decay_weights, default=0.0)
    max_importance = max(aurora_input.importance_scores, default=0.0)
    what_we_know = [
        f"Candidate memories considered: {n}",
        f"Best decay weight: {max_decay:.3f}",
        f"Best importance score: {max_importance:.3f}",
        f"AURORA approval threshold: {aurora_threshold:.2f}",
    ]
    what_we_dont = [
        "Whether more relevant memories exist outside the retrieval top-k",
        "Whether reformulating the query would surface stronger evidence",
        "Whether the user has access to context not yet ingested into RAVEN",
    ]
    type_ = "insufficient_evidence"
    return RefusalReason(
        type=type_,
        confidence=_REFUSAL_CONFIDENCE[type_],
        what_we_know=what_we_know,
        what_we_dont=what_we_dont,
        recommended_action="ask_user",
        audit_hash=_audit_hash(type_, query, what_we_know, what_we_dont),
    )


# ── Scope check ─────────────────────────────────────────────────────────────


def _scope_violation_tokens(
    query: str,
    scope_allowlist: list[str] | None,
) -> list[str]:
    """Return tokens in the query that are outside the allowlist.

    Allowlist semantics: each allowlist entry is treated as a *prefix or
    substring* match against the lowercased token. This is intentionally
    permissive — operators define what's in scope by topic prefixes
    ("billing", "engineering"), not exhaustive vocabulary lists.

    A query is scope-violating if it contains at least one alphanumeric
    token of length >= 3 that does not match any allowlist entry. Short
    tokens ("is", "of", numbers) are ignored to avoid false positives.

    When ``scope_allowlist`` is None, scope is unrestricted and this
    returns ``[]`` — the scope branch is skipped entirely.
    """
    if scope_allowlist is None:
        return []
    if not scope_allowlist:
        # Empty list = nothing in scope. Every content token violates.
        return [t for t in _tokenize(query) if len(t) >= 3]

    allowed = [a.lower() for a in scope_allowlist]
    offending: list[str] = []
    for tok in _tokenize(query):
        if len(tok) < 3:
            continue
        if any(a in tok or tok in a for a in allowed):
            continue
        offending.append(tok)
    return offending


# ── Public entrypoint ───────────────────────────────────────────────────────


def classify_refusal(
    *,
    query: str,
    aurora_input: AuroraInput,
    aurora_threshold: float,
    scope_allowlist: list[str] | None = None,
    resolved_claim_count: int = 0,
    decay_floor: float = DEFAULT_DECAY_FLOOR,
) -> RefusalReason:
    """Classify why RAVEN is refusing.

    Called only when the pipeline has already decided to refuse. Examines the
    AuroraInput (engine outputs the pipeline collected) and the original
    query, returning a typed :class:`RefusalReason` with audit hash.

    Priority order — first match wins:

    1. ``scope_violation`` — the query has content tokens outside
       ``scope_allowlist`` (only when an allowlist is supplied).
    2. ``identity_ambiguous`` — METEOR surfaced multiple distinct entity
       candidates and no winner was selected. Trip threshold:
       ``meteor_entity_count >= 2`` and at least 2 distinct ``entity_tags``
       across the candidate memories.
    3. ``conflicting_evidence_unresolvable`` — PULSAR found contradictions
       and the reconciliation count (Sub A's :class:`ResolvedClaim`) is
       less than the contradiction count. Until Sub A is fully wired, any
       contradiction with no resolution counts as unresolved.
    4. ``staleness_threshold_exceeded`` — every candidate's ECLIPSE-decayed
       weight fell below ``decay_floor``.
    5. ``insufficient_evidence`` — fallback when no other category fits.

    The ordering is significant. Scope first because it's a structural
    boundary that supersedes evidence reasoning (we should not look at
    out-of-scope evidence). Identity second because if we cannot tell
    *who* the query is about, downstream conflict / staleness signals are
    not yet meaningful. Conflict third because unresolved contradictions
    actively poison answers. Staleness fourth because a stale-but-coherent
    answer is preferable to nothing in some user contexts. Insufficient
    last as a default.

    Parameters
    ----------
    query
        The original user query string.
    aurora_input
        Engine outputs the pipeline assembled before AURORA gating.
    aurora_threshold
        AURORA's approval threshold. Reported in ``what_we_know`` for the
        insufficient-evidence branch.
    scope_allowlist
        Optional list of allowed scope tokens (substring-matched against
        query tokens). ``None`` disables the scope check entirely;
        ``[]`` means "nothing in scope".
    resolved_claim_count
        Count of :class:`ResolvedClaim` instances Sub A produced for the
        contradictions present. When unknown / not wired, leave at 0.
    decay_floor
        ECLIPSE decay floor below which a candidate is considered too
        stale to use. Defaults to the lowest built-in policy floor.
    """
    # 1) Scope
    offending = _scope_violation_tokens(query, scope_allowlist)
    if offending:
        return _build_scope_violation(
            query=query,
            offending_tokens=offending,
            scope_allowlist=scope_allowlist or [],
        )

    # 2) Identity ambiguity
    candidates = _entity_candidates(aurora_input)
    if (
        aurora_input.meteor_entity_count >= 2
        and len(candidates) >= 2
    ):
        return _build_identity_ambiguous(query=query, candidates=candidates)

    # 3) Unresolved contradictions
    if aurora_input.contradictions and _has_unresolved_contradictions(
        aurora_input.contradictions, resolved_claim_count
    ):
        return _build_conflicting_evidence(
            query=query, contradictions=aurora_input.contradictions
        )

    # 4) Staleness
    if aurora_input.entries and _all_below_floor(
        aurora_input.decay_weights, decay_floor
    ):
        return _build_staleness(
            query=query,
            weights=list(aurora_input.decay_weights),
            floor=decay_floor,
        )

    # 5) Fallback
    return _build_insufficient_evidence(
        query=query,
        aurora_input=aurora_input,
        aurora_threshold=aurora_threshold,
    )


__all__ = [
    "DEFAULT_DECAY_FLOOR",
    "classify_refusal",
]
