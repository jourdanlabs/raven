"""ECLIPSE — Temporal decay engine.

Weights memories by recency using exponential decay. Configurable half-life.
Entries with explicit validity_end set that lies in the past are marked stale.
"""
from __future__ import annotations

import math
import time

from raven.types import MemoryEntry

_SECONDS_PER_DAY = 86_400.0
DEFAULT_HALF_LIFE_DAYS = 30.0


def decay_weight(
    entry: MemoryEntry,
    now: float | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Exponential decay weight: 1.0 at ingest, 0.5 at half-life, approaching 0."""
    now = now or time.time()
    days_ago = (now - entry.timestamp) / _SECONDS_PER_DAY
    return math.pow(0.5, days_ago / half_life_days)


def is_stale(entry: MemoryEntry, now: float | None = None) -> bool:
    """True if the entry has a validity_end in the past, or has been superseded."""
    now = now or time.time()
    if entry.validity_end is not None and entry.validity_end < now:
        return True
    return False


def recency_tier(entry: MemoryEntry, now: float | None = None) -> str:
    """Human-readable recency bucket."""
    now = now or time.time()
    days_ago = (now - entry.timestamp) / _SECONDS_PER_DAY
    if days_ago < 1:
        return "today"
    if days_ago < 7:
        return "this_week"
    if days_ago < 30:
        return "this_month"
    if days_ago < 90:
        return "this_quarter"
    if days_ago < 365:
        return "this_year"
    return "older"


def apply_decay(
    entries: list[MemoryEntry],
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    now: float | None = None,
) -> list[tuple[MemoryEntry, float]]:
    """Return (entry, decay_weight) pairs for all entries."""
    now = now or time.time()
    return [(e, decay_weight(e, now, half_life_days)) for e in entries]


def sort_by_recency(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    return sorted(entries, key=lambda e: e.timestamp, reverse=True)


def find_superseded(entries: list[MemoryEntry]) -> set[str]:
    """Return IDs of entries that have been superseded by a newer entry."""
    superseded: set[str] = set()
    for e in entries:
        if e.supersedes_id:
            superseded.add(e.supersedes_id)
    return superseded


# ── Capability 1.1 extension — well-grounded check for reconciliation ───────
#
# Additive only. Used by reconcile() rule (b).


def well_grounded(entry: MemoryEntry, now: float | None = None) -> bool:
    """True if the entry is currently within its validity window (not stale).

    Distinct from `is_stale()` which only checks validity_end. `well_grounded`
    is the inverse: validity_end is None (open) OR validity_end > now.
    """
    return not is_stale(entry, now)
