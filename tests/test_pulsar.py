import time
import pytest
from raven.types import MemoryEntry
from raven.validation.pulsar import (
    detect_contradictions, detect_stale_contradictions,
    all_contradictions, _absolutist_in, _has_negation,
)


def _entry(id: str, text: str, days_ago: float = 0.0, supersedes_id=None) -> MemoryEntry:
    ts = time.time() - days_ago * 86_400
    return MemoryEntry(id=id, text=text, timestamp=ts, supersedes_id=supersedes_id)


class TestDetectContradictions:
    def test_no_contradictions_unrelated(self):
        entries = [
            _entry("a", "The project is on track for delivery"),
            _entry("b", "The weather in Houston is warm"),
        ]
        assert detect_contradictions(entries) == []

    def test_absolutist_conflict(self):
        entries = [
            _entry("a", "The pipeline always passes on first run"),
            _entry("b", "The pipeline never passes without manual review"),
        ]
        conflicts = detect_contradictions(entries)
        assert len(conflicts) >= 1
        conflict = conflicts[0]
        assert conflict.contradiction_type == "absolutist"
        assert conflict.entry_a.id in ("a", "b")
        assert 0.0 < conflict.confidence <= 1.0

    def test_predicate_negation(self):
        entries = [
            _entry("a", "RAVEN pipeline is deterministic and stable"),
            _entry("b", "RAVEN pipeline is not deterministic under concurrency"),
        ]
        conflicts = detect_contradictions(entries)
        assert any(c.contradiction_type == "predicate" for c in conflicts)

    def test_time_window_respected(self):
        # Entries more than 90 days apart should not be flagged
        entries = [
            _entry("a", "The system always succeeds", days_ago=100),
            _entry("b", "The system never succeeds in production", days_ago=1),
        ]
        conflicts = detect_contradictions(entries)
        assert conflicts == []

    def test_empty_list(self):
        assert detect_contradictions([]) == []

    def test_single_entry(self):
        assert detect_contradictions([_entry("a", "foo")]) == []


class TestDetectStaleContradictions:
    def test_supersede_detected(self):
        entries = [
            _entry("old", "The threshold is 0.70"),
            _entry("new", "The threshold is now 0.80", supersedes_id="old"),
        ]
        conflicts = detect_stale_contradictions(entries)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.contradiction_type == "temporal"
        assert c.confidence == 1.0

    def test_no_supersede(self):
        entries = [
            _entry("a", "fact a"),
            _entry("b", "fact b"),
        ]
        assert detect_stale_contradictions(entries) == []

    def test_supersedes_missing_id_silently_skipped(self):
        entries = [_entry("new", "update", supersedes_id="ghost")]
        # 'ghost' not in entries — should not crash
        conflicts = detect_stale_contradictions(entries)
        assert conflicts == []


class TestAllContradictions:
    def test_combines_both(self):
        entries = [
            _entry("old", "always reliable", days_ago=1),
            _entry("new", "never reliable", days_ago=0, supersedes_id="old"),
        ]
        conflicts = all_contradictions(entries)
        types = {c.contradiction_type for c in conflicts}
        assert "temporal" in types  # from stale


class TestHelpers:
    def test_absolutist_detection(self):
        assert "always" in _absolutist_in("it always works")
        assert _absolutist_in("it sometimes works") == []

    def test_negation_detection(self):
        assert _has_negation("the system is not ready")
        assert not _has_negation("the system is ready")
