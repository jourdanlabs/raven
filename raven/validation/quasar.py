"""QUASAR — Importance ranking engine.

Scores memories by importance using weighted signals:
  - Decision/milestone keyword presence (highest weight)
  - Recency (recent memories score higher)
  - Source authority (configurable weights per source type)
  - Causal centrality (entries at the center of causal chains score higher)
  - Explicit importance tag in metadata (★ or importance: high)
"""
from __future__ import annotations

import re
import time

from raven.types import CausalEdge, MemoryEntry

# Keyword → base importance score
DECISION_KEYWORDS: dict[str, float] = {
    "DECISION": 1.0, "decided": 0.90, "chose": 0.90, "committed": 0.90,
    "approved": 0.90, "ship": 0.88, "shipped": 0.88, "launched": 0.88,
    "deployed": 0.85, "completed": 0.85, "built": 0.82,
    "milestone": 0.80, "breakthrough": 0.80, "invented": 0.80,
    "created": 0.75, "founded": 0.80, "first": 0.72,
    "resolved": 0.70, "fixed": 0.70, "patched": 0.68,
    "bug": 0.60, "error": 0.58, "failed": 0.58,
}

IMPORTANCE_MARKERS: list[str] = ["★", "**important**", "priority:", "critical:"]

SOURCE_AUTHORITY: dict[str, float] = {
    "system": 0.90,
    "decision_log": 1.0,
    "user": 0.80,
    "agent": 0.75,
    "ingest": 0.65,
    "unknown": 0.60,
}

_SECONDS_PER_DAY = 86_400.0


def _recency_boost(entry: MemoryEntry, now: float | None = None) -> float:
    now = now or time.time()
    days_ago = (now - entry.timestamp) / _SECONDS_PER_DAY
    if days_ago < 1:
        return 0.15
    if days_ago < 7:
        return 0.10
    if days_ago < 30:
        return 0.05
    return 0.0


def _keyword_score(text: str) -> float:
    text_lower = text.lower()
    best = 0.50  # base
    for kw, score in DECISION_KEYWORDS.items():
        if re.search(rf"\b{re.escape(kw.lower())}\b", text_lower):
            best = max(best, score)
    return best


def _importance_marker_score(entry: MemoryEntry) -> float:
    text_lower = entry.text.lower()
    for marker in IMPORTANCE_MARKERS:
        if marker.lower() in text_lower:
            return 0.15
    # Check metadata
    if entry.metadata.get("importance") in ("high", "critical", "milestone"):
        return 0.15
    return 0.0


def _source_authority(entry: MemoryEntry) -> float:
    src = entry.source.lower()
    for key, val in SOURCE_AUTHORITY.items():
        if key in src:
            return val
    return SOURCE_AUTHORITY["unknown"]


def score_entry(
    entry: MemoryEntry,
    causal_edges: list[CausalEdge] | None = None,
    now: float | None = None,
) -> float:
    """Compute 0.0–1.0 importance score for a single entry."""
    base = _keyword_score(entry.text)
    base += _importance_marker_score(entry)
    base += _recency_boost(entry, now)
    base = min(base, 0.95)  # keyword ceiling

    # Authority modulates: multiply base by authority dampened toward 1.0
    authority = _source_authority(entry)
    base = base * (0.7 + 0.3 * authority)

    # Causal centrality bonus
    if causal_edges:
        from raven.validation.nova import causal_centrality
        centrality = causal_centrality(entry.id, causal_edges)
        base = min(1.0, base + centrality * 0.10)

    return min(1.0, max(0.0, base))


def rank_by_importance(
    entries: list[MemoryEntry],
    causal_edges: list[CausalEdge] | None = None,
    now: float | None = None,
) -> list[tuple[MemoryEntry, float]]:
    """Return (entry, importance_score) pairs, sorted descending."""
    scored = [(e, score_entry(e, causal_edges, now)) for e in entries]
    return sorted(scored, key=lambda x: x[1], reverse=True)
