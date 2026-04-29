"""Built-in DecayPolicy definitions for Capability 1.2.

Importing this module registers the six built-in policies into the
process-global registry exposed by ``raven.decay.registry``. The policy
constants are ALSO exported as ``BUILTIN_POLICIES`` for direct access
(useful in tests and for documenting the spec without touching the
registry).

Spec (per Phase 1 brief, Capability 1.2):

    factual_short  : half_life = 1 day      = 86_400 s,    floor = 0.10
    factual_long   : half_life = 30 days    = 2_592_000 s, floor = 0.20
    preference     : half_life = 90 days    = 7_776_000 s, floor = 0.30
    transactional  : half_life = 4 hours    = 14_400 s,    floor = 0.05
    contextual     : half_life = 7 days     = 604_800 s,   floor = 0.15
    identity       : half_life = None  (no decay),         floor = 0.50
"""
from __future__ import annotations

from raven.types import DecayPolicy

from .registry import _REGISTRY, register_decay_policy

# ── Built-in policy constants ──────────────────────────────────────────────

FACTUAL_SHORT = DecayPolicy(
    name="factual_short",
    half_life_seconds=86_400.0,
    floor_confidence=0.10,
    applies_to_class="factual_short",
)

FACTUAL_LONG = DecayPolicy(
    name="factual_long",
    half_life_seconds=2_592_000.0,  # 30 days
    floor_confidence=0.20,
    applies_to_class="factual_long",
)

PREFERENCE = DecayPolicy(
    name="preference",
    half_life_seconds=7_776_000.0,  # 90 days
    floor_confidence=0.30,
    applies_to_class="preference",
)

TRANSACTIONAL = DecayPolicy(
    name="transactional",
    half_life_seconds=14_400.0,  # 4 hours
    floor_confidence=0.05,
    applies_to_class="transactional",
)

CONTEXTUAL = DecayPolicy(
    name="contextual",
    half_life_seconds=604_800.0,  # 7 days
    floor_confidence=0.15,
    applies_to_class="contextual",
)

IDENTITY = DecayPolicy(
    name="identity",
    half_life_seconds=None,  # no decay
    floor_confidence=0.50,
    applies_to_class="identity",
)

BUILTIN_POLICIES: list[DecayPolicy] = [
    FACTUAL_SHORT,
    FACTUAL_LONG,
    PREFERENCE,
    TRANSACTIONAL,
    CONTEXTUAL,
    IDENTITY,
]

# Convenience lookup for the scoring harness and any caller that wants a
# policy by name without touching the registry.
POLICIES_BY_NAME: dict[str, DecayPolicy] = {p.name: p for p in BUILTIN_POLICIES}


def register_builtins() -> None:
    """Register all built-in policies. Idempotent — safe to call multiple times."""
    for p in BUILTIN_POLICIES:
        if p.applies_to_class in _REGISTRY:
            continue
        register_decay_policy(p)


# Side-effect on import: register the six built-ins so callers that just
# do ``from raven import decay`` (or ``from raven.decay import ...``) get
# a populated registry without a second step.
register_builtins()
