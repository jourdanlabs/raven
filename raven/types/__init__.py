"""Public type surface for RAVEN.

v1.0 types live in `raven.types.v1`; Phase 1 capability types live in
`raven.types.phase1`. This module re-exports both so existing callers
that do `from raven.types import MemoryEntry` keep working unchanged.
"""

# v1.0 types — preserved exactly. Existing callers keep their imports.
from .v1 import (
    AuroraInput,
    CausalEdge,
    Contradiction,
    MemoryEntry,
    PipelineTrace,
    RavenResponse,
    ScoredMemory,
)

# Phase 1 capability types — new in v1.1. Consumed by sub-agents A/B/C
# during the Phase 1 capability sprint. AuroraVerdict is a superset of
# v1.0 RavenResponse, not a replacement.
from .phase1 import (
    AuroraVerdict,
    DecayPolicy,
    EvidenceNode,
    Memory,
    MemoryClass,
    RefusalReason,
    ResolvedClaim,
)

__all__ = [
    # v1.0 surface
    "AuroraInput",
    "CausalEdge",
    "Contradiction",
    "MemoryEntry",
    "PipelineTrace",
    "RavenResponse",
    "ScoredMemory",
    # Phase 1 surface
    "AuroraVerdict",
    "DecayPolicy",
    "EvidenceNode",
    "Memory",
    "MemoryClass",
    "RefusalReason",
    "ResolvedClaim",
]
