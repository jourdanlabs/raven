import time
import pytest
from raven.types import MemoryEntry
from raven.validation.eclipse import (
    decay_weight, is_stale, recency_tier, apply_decay,
    sort_by_recency, find_superseded, DEFAULT_HALF_LIFE_DAYS,
)


def _entry(id: str, days_ago: float = 0.0, supersedes_id=None, validity_end=None) -> MemoryEntry:
    ts = time.time() - days_ago * 86_400
    return MemoryEntry(
        id=id, text="test", timestamp=ts,
        supersedes_id=supersedes_id, validity_end=validity_end,
    )


class TestDecayWeight:
    def test_fresh_is_near_one(self):
        e = _entry("a", days_ago=0)
        w = decay_weight(e, half_life_days=30)
        assert w > 0.99

    def test_at_half_life_is_half(self):
        e = _entry("a", days_ago=30)
        w = decay_weight(e, half_life_days=30)
        assert abs(w - 0.5) < 0.01

    def test_old_memory_low_weight(self):
        e = _entry("a", days_ago=120)
        w = decay_weight(e, half_life_days=30)
        assert w < 0.10

    def test_weight_bounded_0_to_1(self):
        for days in [0, 1, 7, 30, 90, 365]:
            e = _entry("a", days_ago=days)
            w = decay_weight(e)
            assert 0.0 <= w <= 1.0


class TestIsStale:
    def test_no_validity_end_not_stale(self):
        e = _entry("a")
        assert not is_stale(e)

    def test_past_validity_end_is_stale(self):
        e = _entry("a", validity_end=time.time() - 3600)
        assert is_stale(e)

    def test_future_validity_end_not_stale(self):
        e = _entry("a", validity_end=time.time() + 3600)
        assert not is_stale(e)


class TestRecencyTier:
    def test_today(self):
        e = _entry("a", days_ago=0.1)
        assert recency_tier(e) == "today"

    def test_this_week(self):
        e = _entry("a", days_ago=3)
        assert recency_tier(e) == "this_week"

    def test_this_month(self):
        e = _entry("a", days_ago=15)
        assert recency_tier(e) == "this_month"

    def test_older(self):
        e = _entry("a", days_ago=400)
        assert recency_tier(e) == "older"


class TestApplyDecay:
    def test_returns_parallel_pairs(self):
        entries = [_entry(str(i), days_ago=i * 10) for i in range(5)]
        pairs = apply_decay(entries)
        assert len(pairs) == 5
        for e, w in pairs:
            assert isinstance(e, MemoryEntry)
            assert 0.0 <= w <= 1.0

    def test_recent_higher_weight(self):
        fresh = _entry("fresh", days_ago=1)
        old = _entry("old", days_ago=60)
        pairs = {e.id: w for e, w in apply_decay([fresh, old])}
        assert pairs["fresh"] > pairs["old"]


class TestSortByRecency:
    def test_most_recent_first(self):
        entries = [_entry(str(i), days_ago=i * 10) for i in range(5)]
        sorted_entries = sort_by_recency(entries)
        timestamps = [e.timestamp for e in sorted_entries]
        assert timestamps == sorted(timestamps, reverse=True)


class TestFindSuperseded:
    def test_finds_superseded_ids(self):
        entries = [
            _entry("old"),
            _entry("new", supersedes_id="old"),
        ]
        superseded = find_superseded(entries)
        assert "old" in superseded
        assert "new" not in superseded

    def test_empty(self):
        assert find_superseded([]) == set()
