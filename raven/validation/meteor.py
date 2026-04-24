"""METEOR — Entity normalization engine.

Resolves aliases to canonical forms. Uses exact match first, then
Levenshtein distance for fuzzy matching within a configurable threshold.
"""
from __future__ import annotations

# Canonical entity registry: canonical name → list of known aliases.
# Extend at runtime via METEORConfig.extra_aliases.
DEFAULT_ALIASES: dict[str, list[str]] = {
    "TJ": ["Leland", "Leland Jourdan", "Leland Jourdan II", "Sokpyeon", "TJ Jourdan"],
    "18": ["Android 18", "Android18", "android18", "18sequel"],
    "Bulma": ["BulmaSequel", "BulmaSequelBot"],
    "Krillin": ["krillin", "Krillin2", "KrillinSequel"],
    "Chris Lynch": ["Lynch", "C. Lynch"],
    "Charles Jourdan": ["Charles", "Cousin Charles", "c.andre.jourdan"],
    "Daniel Charbonnet": ["Daniel", "Dan", "Charbonnet", "Fortress Energy"],
    "Raven": ["Raven Lenore", "Raven Lenore Jourdan"],
    "COSMIC": ["COSMIC Engine", "COSMIC Suite", "cosmic-engine-suite"],
    "MineralLogic": ["MineralScope", "MineralScopeTX"],
    "HELIX": ["HELIX-FIT", "helixfit"],
    "OMNIS KEY": ["omnis-key", "omniskey", "OMNIS"],
    "MemPalace": ["mempalace", "Milla Jovovich", "Ben Sigman"],
    "RAVEN": ["raven-ai", "raven_ai"],
}


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[lb]


class METEORConfig:
    def __init__(
        self,
        fuzzy_threshold: int = 2,
        extra_aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        aliases = dict(DEFAULT_ALIASES)
        if extra_aliases:
            for canon, als in extra_aliases.items():
                aliases.setdefault(canon, []).extend(als)
        # Build flat lookup: lowercased alias/canonical → canonical
        self._lookup: dict[str, str] = {}
        for canon, als in aliases.items():
            self._lookup[canon.lower()] = canon
            for a in als:
                self._lookup[a.lower()] = canon

    def resolve(self, name: str) -> str:
        """Return canonical form of name, or name itself if unknown."""
        lower = name.lower().strip()
        if lower in self._lookup:
            return self._lookup[lower]
        # Fuzzy fallback
        best, best_dist = name, self.fuzzy_threshold + 1
        for alias, canon in self._lookup.items():
            d = _levenshtein(lower, alias)
            if d < best_dist:
                best_dist = d
                best = canon
        return best if best_dist <= self.fuzzy_threshold else name

    def tag_entities(self, text: str) -> list[str]:
        """Return list of canonical entity names found in text."""
        found: list[str] = []
        text_lower = text.lower()
        for alias, canon in self._lookup.items():
            if alias in text_lower and canon not in found:
                found.append(canon)
        return found

    def normalize_text(self, text: str) -> str:
        """Replace known aliases in text with canonical names."""
        result = text
        # Longest aliases first to avoid partial clobbers
        for alias in sorted(self._lookup, key=len, reverse=True):
            canon = self._lookup[alias]
            import re
            result = re.sub(re.escape(alias), canon, result, flags=re.IGNORECASE)
        return result


_default = METEORConfig()


def tag_entities(text: str, config: METEORConfig | None = None) -> list[str]:
    return (config or _default).tag_entities(text)


def resolve_entity(name: str, config: METEORConfig | None = None) -> str:
    return (config or _default).resolve(name)


def normalize_text(text: str, config: METEORConfig | None = None) -> str:
    return (config or _default).normalize_text(text)
