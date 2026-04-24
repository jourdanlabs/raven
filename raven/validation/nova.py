"""NOVA — Causal chain construction engine.

Detects causal relationships between memory entries using keyword
markers and entity/word overlap. Returns a directed graph of edges
(from_id → to_id) representing "A caused B" relationships.
"""
from __future__ import annotations

from raven.types import CausalEdge, MemoryEntry

CAUSAL_KEYWORDS: list[str] = [
    "because", "therefore", "thus", "consequently",
    "caused", "led to", "resulted in", "triggered", "started",
    "after that", "following", "due to", "as a result",
    "this led", "which caused", "which resulted", "so that",
    "in response to", "prompted by",
]

# Minimum word overlap length to consider two entries related
_MIN_OVERLAP_LEN = 4
_MIN_OVERLAP_COUNT = 2


def build_causal_graph(entries: list[MemoryEntry]) -> list[CausalEdge]:
    """Return causal edges ordered chronologically."""
    edges: list[CausalEdge] = []
    sorted_entries = sorted(entries, key=lambda e: e.timestamp)

    for i, current in enumerate(sorted_entries):
        text_lower = current.text.lower()
        matched_kws = [kw for kw in CAUSAL_KEYWORDS if kw in text_lower]
        if not matched_kws:
            continue

        current_words = set(
            w for w in text_lower.split() if len(w) >= _MIN_OVERLAP_LEN
        )

        for prior in sorted_entries[:i]:
            prior_words = set(
                w for w in prior.text.lower().split() if len(w) >= _MIN_OVERLAP_LEN
            )
            overlap = current_words & prior_words

            # Require both entity overlap and causal keyword in current entry
            if len(overlap) >= _MIN_OVERLAP_COUNT:
                weight = min(1.0, len(overlap) / 8.0)
                edges.append(
                    CausalEdge(
                        from_id=prior.id,
                        to_id=current.id,
                        relation="caused",
                        weight=weight,
                        keywords_matched=list(matched_kws),
                    )
                )

    return edges


def get_causal_chains(entries: list[MemoryEntry]) -> list[list[str]]:
    """Return all causal chains (as lists of entry IDs) of length >= 2."""
    edges = build_causal_graph(entries)
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e.from_id, []).append(e.to_id)

    chains: list[list[str]] = []
    visited: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        path.append(node)
        neighbors = adj.get(node, [])
        if not neighbors:
            if len(path) >= 2:
                chains.append(list(path))
        else:
            for nb in neighbors:
                if nb not in visited:
                    visited.add(nb)
                    dfs(nb, path)
                    visited.discard(nb)
        path.pop()

    for root in adj:
        if root not in visited:
            dfs(root, [])

    return chains


def causal_centrality(entry_id: str, edges: list[CausalEdge]) -> float:
    """How central is this entry in the causal graph? (0.0–1.0)"""
    if not edges:
        return 0.0
    involved = sum(1 for e in edges if e.from_id == entry_id or e.to_id == entry_id)
    return min(1.0, involved / max(len(edges), 1))
