"""ECLIPSE — Temporal decay engine.

Weights memories by recency using exponential decay. Configurable half-life.
Entries with explicit validity_end set that lies in the past are marked stale.

Capability 1.2 extension
------------------------
``apply_class_aware_decay`` is the new class-aware path. It looks up the
``DecayPolicy`` registered for each entry's ``memory_class``, applies that
policy's half-life curve, and floors the result at the policy's
``floor_confidence``. The v1.0 functions (``decay_weight``, ``apply_decay``,
``is_stale``, ``recency_tier``, ``sort_by_recency``, ``find_superseded``)
are unchanged — existing callers keep their behaviour.
"""
from __future__ import annotations

import math
import time

from raven.types import DecayPolicy, MemoryEntry

# Importing raven.decay registers the built-in policies. Done at module
# load so callers using class-aware decay don't have to remember to
# bootstrap the registry themselves.
from raven import decay as _decay  # noqa: F401  (side-effect import)
from raven.decay.registry import get_decay_policy

_SECONDS_PER_DAY = 86_400.0
DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_MEMORY_CLASS = "contextual"


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


# ── Capability 1.2 — class-aware decay ─────────────────────────────────────


def _resolve_memory_class(entry: MemoryEntry) -> str:
    """Return the memory class name for ``entry``.

    Resolution order:
      1. Attribute ``entry.memory_class`` (the new MemoryEntry field).
      2. ``entry.metadata['memory_class']`` (transitional / migration path
         used by older corpora that pre-date the v1 schema extension).
      3. ``DEFAULT_MEMORY_CLASS`` (``"contextual"``).
    """
    cls = getattr(entry, "memory_class", None)
    if cls:
        return cls
    if isinstance(getattr(entry, "metadata", None), dict):
        meta_cls = entry.metadata.get("memory_class")
        if meta_cls:
            return meta_cls
    return DEFAULT_MEMORY_CLASS


def class_aware_weight(
    entry: MemoryEntry,
    policy: DecayPolicy,
    now: float,
) -> float:
    """Compute the policy-aware decay weight for one entry.

    Curve:
        weight = max(floor, confidence_at_ingest * 0.5 ** (age / half_life))

    Special cases:
      - ``policy.half_life_seconds is None`` → no decay; weight is the
        entry's confidence at ingest, never below the floor.
      - ``age_seconds < 0`` (entry timestamped in the future) → treated as
        weight = 1.0, then floored (no-op since floor < 1.0 by spec). This
        protects against system-clock drift; the unit test
        ``TestNegativeAge`` codifies the contract.
    """
    base = float(entry.confidence_at_ingest)
    floor = float(policy.floor_confidence)

    if policy.half_life_seconds is None:
        # No-decay path: identity policy. Floor still applies as a lower
        # bound on the entry's effective confidence.
        return max(floor, base)

    age_seconds = now - float(entry.timestamp)
    if age_seconds <= 0.0:
        # Future-dated or just-ingested entry: weight = 1.0 (then floored,
        # which is a no-op since floor < 1.0 by spec).
        return max(floor, 1.0)

    half_life = float(policy.half_life_seconds)
    if half_life <= 0.0:
        # Pathological policy — fall back to floor so we never emit NaN.
        return floor

    raw = math.pow(0.5, age_seconds / half_life)
    weighted = base * raw
    return max(floor, weighted)


def apply_class_aware_decay(
    entries: list[MemoryEntry],
    now: float,
) -> list[tuple[MemoryEntry, float, DecayPolicy]]:
    """Class-aware decay path for Capability 1.2.

    For each entry, look up the ``DecayPolicy`` registered for its
    ``memory_class`` (falling back to the ``contextual`` policy if the
    entry has no class set or the class is unknown), apply the policy's
    decay curve to the entry's confidence at ingest, floor the result at
    ``DecayPolicy.floor_confidence``, and return the triple

        (entry, weighted_confidence, applied_policy)

    in the same order as ``entries``. Pure function: same inputs always
    yield identical outputs.
    """
    out: list[tuple[MemoryEntry, float, DecayPolicy]] = []
    for entry in entries:
        class_name = _resolve_memory_class(entry)
        try:
            policy = get_decay_policy(class_name)
        except KeyError:
            # Unknown class → fall back to contextual. ECLIPSE must always
            # return a verdict for every entry.
            policy = get_decay_policy(DEFAULT_MEMORY_CLASS)
        weight = class_aware_weight(entry, policy, now)
        out.append((entry, weight, policy))
    return out
