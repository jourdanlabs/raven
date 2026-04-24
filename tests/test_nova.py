import time
import pytest
from raven.types import MemoryEntry
from raven.validation.nova import build_causal_graph, get_causal_chains, causal_centrality


def _entry(id: str, text: str, offset_secs: float = 0.0) -> MemoryEntry:
    return MemoryEntry(id=id, text=text, timestamp=time.time() - offset_secs)


class TestBuildCausalGraph:
    def test_no_causal_keywords(self):
        entries = [
            _entry("a", "The sky is blue today", 100),
            _entry("b", "The grass is green today", 50),
        ]
        edges = build_causal_graph(entries)
        assert edges == []

    def test_detects_causal_edge(self):
        entries = [
            _entry("cause", "The deployment pipeline was updated", 200),
            _entry("effect", "The deployment pipeline failed because the update was wrong", 100),
        ]
        edges = build_causal_graph(entries)
        assert len(edges) >= 1
        assert edges[0].from_id == "cause"
        assert edges[0].to_id == "effect"
        assert "because" in edges[0].keywords_matched

    def test_respects_chronological_order(self):
        # Effect comes BEFORE cause in time — should not produce edge
        entries = [
            _entry("future", "The build failed because the config changed", 50),
            _entry("past", "The config was changed in the pipeline", 200),
        ]
        edges = build_causal_graph(entries)
        # 'past' is older, 'future' is newer — edge should go past → future
        for e in edges:
            assert e.from_id == "past"
            assert e.to_id == "future"

    def test_weight_bounded(self):
        entries = [
            _entry("a", "project pipeline deployment system was configured", 200),
            _entry("b", "project pipeline deployment system failed therefore resulting in downtime", 100),
        ]
        edges = build_causal_graph(entries)
        for e in edges:
            assert 0.0 <= e.weight <= 1.0

    def test_empty_input(self):
        assert build_causal_graph([]) == []

    def test_single_entry(self):
        entries = [_entry("a", "something happened because of a prior event")]
        assert build_causal_graph(entries) == []


class TestGetCausalChains:
    def test_chain_length_gte_2(self):
        entries = [
            _entry("a", "The server was updated", 300),
            _entry("b", "The server update caused the cache to invalidate", 200),
            _entry("c", "The cache invalidation resulted in slower queries on the server", 100),
        ]
        chains = get_causal_chains(entries)
        # At least one chain of length 2+
        assert any(len(c) >= 2 for c in chains)


class TestCausalCentrality:
    def test_zero_with_no_edges(self):
        assert causal_centrality("a", []) == 0.0

    def test_central_node_scores_higher(self):
        from raven.types import CausalEdge
        edges = [
            CausalEdge("a", "b", "caused", 0.5),
            CausalEdge("b", "c", "caused", 0.5),
            CausalEdge("b", "d", "caused", 0.5),
        ]
        centrality_b = causal_centrality("b", edges)
        centrality_a = causal_centrality("a", edges)
        assert centrality_b >= centrality_a

    def test_bounded(self):
        from raven.types import CausalEdge
        edges = [CausalEdge("x", "y", "caused", 1.0) for _ in range(20)]
        assert 0.0 <= causal_centrality("x", edges) <= 1.0
