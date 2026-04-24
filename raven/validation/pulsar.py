"""PULSAR — Contradiction detection engine.

Detects conflicts between memory entries. Three detection strategies:
1. Absolutist claim conflict — two entries share subject but use incompatible absolutes
2. Predicate negation — "X is Y" vs "X is not Y" on same subject
3. Temporal staleness conflict — an entry superseded by a newer one on same topic
"""
from __future__ import annotations

import re
from raven.types import Contradiction, MemoryEntry

# Words that signal an absolute claim
ABSOLUTIST_WORDS: list[str] = [
    "never", "always", "definitely", "certainly", "absolutely",
    "impossible", "must", "guaranteed", "certain", "completely",
    "entirely", "every", "all", "none", "nobody", "nothing",
]

# Negation markers
NEGATIONS: list[str] = ["not", "no", "never", "isn't", "aren't", "wasn't",
                         "weren't", "doesn't", "don't", "didn't", "cannot", "can't"]

# Window within which contradictions are considered active (90 days in seconds)
_CONTRADICTION_WINDOW_SECS = 90 * 86_400


def _extract_subject(text: str) -> str:
    """Crude subject extraction: first proper noun or first noun phrase."""
    # Take first capitalized token that isn't sentence-start
    tokens = text.split()
    for i, tok in enumerate(tokens):
        clean = re.sub(r"[^\w]", "", tok)
        if i > 0 and clean and clean[0].isupper():
            return clean.lower()
    return tokens[0].lower() if tokens else ""


def _absolutist_in(text: str) -> list[str]:
    lower = text.lower()
    return [w for w in ABSOLUTIST_WORDS if re.search(rf"\b{w}\b", lower)]


def _has_negation(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{n}\b", lower) for n in NEGATIONS)


def _shared_content_words(a: str, b: str, min_len: int = 4) -> list[str]:
    wa = set(re.sub(r"[^\w\s]", "", a.lower()).split())
    wb = set(re.sub(r"[^\w\s]", "", b.lower()).split())
    return [w for w in wa & wb if len(w) >= min_len]


def detect_contradictions(entries: list[MemoryEntry]) -> list[Contradiction]:
    conflicts: list[Contradiction] = []
    n = len(entries)

    for i in range(n):
        for j in range(i + 1, n):
            a, b = entries[i], entries[j]

            # Skip if too far apart in time
            if abs(a.timestamp - b.timestamp) > _CONTRADICTION_WINDOW_SECS:
                continue

            shared = _shared_content_words(a.text, b.text)
            if len(shared) < 2:
                continue

            # Strategy 1: absolutist conflict — both claim absolutes on shared content
            abs_a = _absolutist_in(a.text)
            abs_b = _absolutist_in(b.text)
            if abs_a and abs_b and set(abs_a) != set(abs_b):
                conflicts.append(Contradiction(
                    entry_a=a,
                    entry_b=b,
                    contradiction_type="absolutist",
                    description=(
                        f'"{abs_a[0]}" vs "{abs_b[0]}" on shared context: '
                        f'{", ".join(shared[:3])}'
                    ),
                    confidence=0.75,
                ))
                continue

            # Strategy 2: predicate negation — one affirms, other negates
            neg_a = _has_negation(a.text)
            neg_b = _has_negation(b.text)
            if neg_a != neg_b and len(shared) >= 3:
                conflicts.append(Contradiction(
                    entry_a=a,
                    entry_b=b,
                    contradiction_type="predicate",
                    description=(
                        f"Potential predicate negation on: "
                        f'{", ".join(shared[:3])}'
                    ),
                    confidence=0.65,
                ))

    return conflicts


def detect_stale_contradictions(entries: list[MemoryEntry]) -> list[Contradiction]:
    """Entries marked as superseding a prior entry create a temporal conflict."""
    conflicts: list[Contradiction] = []
    id_map = {e.id: e for e in entries}

    for entry in entries:
        if entry.supersedes_id and entry.supersedes_id in id_map:
            prior = id_map[entry.supersedes_id]
            conflicts.append(Contradiction(
                entry_a=prior,
                entry_b=entry,
                contradiction_type="temporal",
                description=(
                    f"Entry {entry.id} supersedes {prior.id}: "
                    f'"{prior.text[:60]}..." → "{entry.text[:60]}..."'
                ),
                confidence=1.0,
            ))

    return conflicts


def all_contradictions(entries: list[MemoryEntry]) -> list[Contradiction]:
    return detect_contradictions(entries) + detect_stale_contradictions(entries)
