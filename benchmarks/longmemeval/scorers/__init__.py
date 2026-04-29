"""LongMemEval scorer registry.

Two scorer variants are shipped:

* ``retrieval_ranked`` (default) — the v1.0 → v1.2.1 published behaviour.
  Ranks ``(approved + rejected)`` by ``retrieval_score`` descending.
  AURORA's gate is invisible to A@k under this scorer.
* ``composite_ranked`` (Phase 2.2 fix-02 / LME-012) — the architectural
  fix. Ranks the union by ``retrieval_score × aurora_weight`` where the
  weight is 1.0 for approved memories and 0.0 for rejected. The
  substring matcher used to compute A@k is identical between the two
  scorers; only the ranking is different.

The harness selects the scorer via ``--scorer=<name>``. The ``rank``
function below is the only piece that varies between scorers; all the
substring scoring and report aggregation live in ``retrieval_ranked``
and are re-exported by ``composite_ranked``.
"""
from __future__ import annotations

from typing import Callable

from benchmarks.longmemeval.scorers import composite_ranked, retrieval_ranked

# Public API: scorer name -> rank function.
RANKERS: dict[str, Callable] = {
    "retrieval_ranked": retrieval_ranked.rank_memories,
    "composite_ranked": composite_ranked.rank_memories,
}

DEFAULT_SCORER = "retrieval_ranked"


def get_ranker(name: str) -> Callable:
    """Return the rank function for ``name``.

    Raises ``ValueError`` for unknown names so a typo on the harness
    CLI fails loudly rather than silently falling back to the default.
    """
    if name not in RANKERS:
        raise ValueError(
            f"Unknown scorer {name!r}; expected one of "
            f"{sorted(RANKERS.keys())}"
        )
    return RANKERS[name]


__all__ = [
    "DEFAULT_SCORER",
    "RANKERS",
    "composite_ranked",
    "get_ranker",
    "retrieval_ranked",
]
