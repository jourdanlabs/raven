import time
import pytest
from raven.types import MemoryEntry
from raven.validation.quasar import score_entry, rank_by_importance


def _entry(id: str, text: str, days_ago: float = 0.0, source: str = "unknown", metadata=None) -> MemoryEntry:
    ts = time.time() - days_ago * 86_400
    return MemoryEntry(id=id, text=text, timestamp=ts, source=source, metadata=metadata or {})


class TestScoreEntry:
    def test_base_score_in_range(self):
        e = _entry("a", "some random fact")
        s = score_entry(e)
        assert 0.0 <= s <= 1.0

    def test_decision_keyword_boosts_score(self):
        boring = _entry("a", "the server is running")
        decisive = _entry("b", "decided to ship RAVEN v1 today")
        assert score_entry(decisive) > score_entry(boring)

    def test_recent_entry_higher_than_old(self):
        fresh = _entry("a", "the threshold is 0.80", days_ago=0.1)
        old = _entry("b", "the threshold is 0.80", days_ago=90)
        assert score_entry(fresh) > score_entry(old)

    def test_decision_log_source_boosts(self):
        generic = _entry("a", "approved the deployment", source="unknown")
        authoritative = _entry("b", "approved the deployment", source="decision_log")
        assert score_entry(authoritative) >= score_entry(generic)

    def test_importance_marker_boosts(self):
        plain = _entry("a", "update the config file")
        starred = _entry("b", "★ update the config file — CRITICAL")
        assert score_entry(starred) > score_entry(plain)

    def test_importance_metadata_boosts(self):
        plain = _entry("a", "update config")
        tagged = _entry("b", "update config", metadata={"importance": "critical"})
        assert score_entry(tagged) >= score_entry(plain)

    def test_score_ceiling_1(self):
        e = _entry("a", "DECISION ★ decided deployed shipped launched approved", days_ago=0)
        assert score_entry(e) <= 1.0


class TestRankByImportance:
    def test_sorted_descending(self):
        entries = [
            _entry("low", "general chat about the project", days_ago=5),
            _entry("high", "decided to launch RAVEN publicly this week", days_ago=0),
            _entry("mid", "fixed a bug in the pipeline", days_ago=2),
        ]
        ranked = rank_by_importance(entries)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_returns_all_entries(self):
        entries = [_entry(str(i), f"fact {i}") for i in range(10)]
        ranked = rank_by_importance(entries)
        assert len(ranked) == 10

    def test_empty_input(self):
        assert rank_by_importance([]) == []

    def test_causal_centrality_bonus_applied(self):
        from raven.types import CausalEdge
        entries = [
            _entry("hub", "central deployment event", days_ago=2),
            _entry("leaf", "minor note", days_ago=1),
        ]
        edges = [CausalEdge("hub", "leaf", "caused", 0.8)]
        ranked_with = rank_by_importance(entries, causal_edges=edges)
        ranked_without = rank_by_importance(entries)
        # Hub should score higher or equal with causal context
        score_with = {e.id: s for e, s in ranked_with}
        score_without = {e.id: s for e, s in ranked_without}
        assert score_with["hub"] >= score_without["hub"]
