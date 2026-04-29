from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class MemoryEntry:
    id: str
    text: str
    timestamp: float                          # unix epoch seconds
    source: str = "unknown"
    entity_tags: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)
    confidence_at_ingest: float = 1.0
    supersedes_id: Optional[str] = None       # id of entry this one replaces
    validity_start: float = 0.0               # defaults to timestamp at ingest
    validity_end: Optional[float] = None      # None = currently valid
    metadata: dict = field(default_factory=dict)
    # Capability 1.2 — class-aware decay. Backward-compat default keeps every
    # existing v1.0 caller working (uninitialised → contextual). One of:
    # "factual_short" | "factual_long" | "preference" | "transactional"
    # | "contextual" | "identity" | a custom registered class name.
    memory_class: str = "contextual"

    def __post_init__(self) -> None:
        if self.validity_start == 0.0:
            self.validity_start = self.timestamp


@dataclass
class ScoredMemory:
    entry: MemoryEntry
    score: float                              # 0.0–1.0 composite AURORA score
    engine_scores: dict[str, float] = field(default_factory=dict)
    retrieval_score: float = 0.0
    rejection_reason: Optional[str] = None


@dataclass
class Contradiction:
    entry_a: MemoryEntry
    entry_b: MemoryEntry
    contradiction_type: Literal["predicate", "temporal", "entity_mismatch", "absolutist"]
    description: str
    confidence: float                         # 0.0–1.0


@dataclass
class CausalEdge:
    from_id: str
    to_id: str
    relation: str
    weight: float
    keywords_matched: list[str] = field(default_factory=list)


@dataclass
class PipelineTrace:
    meteor_entities: int = 0
    nova_edges: int = 0
    eclipse_applied: int = 0
    pulsar_conflicts: int = 0
    quasar_ranked: int = 0
    aurora_approved: int = 0
    aurora_rejected: int = 0
    latency_ms: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class AuroraInput:
    entries: list[MemoryEntry]
    decay_weights: list[float]
    importance_scores: list[float]
    contradictions: list[Contradiction]
    causal_edges: list[CausalEdge]
    stale_ids: set[str]
    meteor_entity_count: int = 0


@dataclass
class RavenResponse:
    query: str
    status: Literal["APPROVED", "CONDITIONAL", "REJECTED", "REFUSED"]
    overall_confidence: float
    approved_memories: list[ScoredMemory]
    flagged_contradictions: list[Contradiction]
    rejected_memories: list[ScoredMemory]
    pipeline_trace: PipelineTrace

    def is_approved(self) -> bool:
        return self.status in ("APPROVED", "CONDITIONAL")

    def refused(self) -> bool:
        return self.status == "REFUSED"

    def top(self, n: int = 5) -> list[ScoredMemory]:
        return self.approved_memories[:n]
