"""Storage migrations for Capability 1.2.

Migration model: idempotent, opt-in. The orchestration entry point is
``run_migrations(db_path)``, which:

  1. Detects whether ``memories.memory_class`` already exists; if so,
     the schema-change phase is a no-op.
  2. Otherwise applies ``001_add_memory_class.sql`` to add the new
     columns + indexes.
  3. Backfills ``memory_class`` for every existing row using the heuristic
     classifier in :func:`classify_text`. Rows whose heuristic confidence
     falls below ``REVIEW_THRESHOLD`` get ``review_required = 1``.
  4. Returns a :class:`MigrationResult` with counts and a sample of the
     review queue.

The opt-in env flag ``RAVEN_RUN_MIGRATIONS=1`` controls whether
:class:`raven.storage.store.RAVENStore` runs the schema-change phase
automatically on construction. The CLI's ``raven migrate run`` always
performs the migration (the env flag is checked there too, with a clear
error if missing — see ``cli/raven.py``).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Confidence below this gets marked review_required = 1 in the migration.
REVIEW_THRESHOLD: float = 0.6

_MIGRATIONS_DIR = Path(__file__).parent
_001_SQL = _MIGRATIONS_DIR / "001_add_memory_class.sql"


# ── Heuristic classifier ───────────────────────────────────────────────────


_IDENTITY_PATTERNS = [
    re.compile(r"\b([A-Z][a-zA-Z]+)\s+(?:is|was|am|are)\s+(?:a|an|the)?\s*[A-Za-z]"),
    re.compile(r"\bmy\s+(?:name|wife|husband|father|mother|son|daughter)\s+is\b", re.I),
    re.compile(r"\bI\s+am\s+[A-Z]"),
]

_PREFERENCE_TOKENS = (
    "prefer",
    "favorite",
    "favourite",
    "like ",
    "love ",
    "hate ",
    "dislike",
    "would rather",
)

# very loose transactional shape: a number, a verb-ish token, an entity-ish
# capitalised word. Examples: "paid $50 to Alice", "shipped 3 boxes Tuesday".
_TRANSACTIONAL_RE = re.compile(
    r"\b(?:\$?\d+(?:[.,]\d+)?)\s+\w+\s+[A-Z][a-z]+",
)
_TRANSACTIONAL_VERBS = (
    "paid",
    "bought",
    "sold",
    "ordered",
    "shipped",
    "delivered",
    "transferred",
    "received",
    "invoiced",
    "billed",
)

_TIME_ANCHOR_RE = re.compile(
    r"\b(?:today|yesterday|tomorrow|tonight|this\s+(?:morning|afternoon|evening|week|month)"
    r"|next\s+(?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|last\s+(?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|in\s+\d+\s+(?:minutes?|hours?|days?)"
    r"|\d{1,2}:\d{2}\s*(?:am|pm)?)\b",
    re.I,
)

# Loose "descriptive without time anchor" check: presence of "is", "are",
# "has", "have" + absence of any time anchor → factual_long.
_DESCRIPTIVE_RE = re.compile(r"\b(?:is|are|has|have|was|were)\b", re.I)


def classify_text(
    text: str,
    entity_tags: Iterable[str] | None = None,
) -> tuple[str, float]:
    """Heuristically classify a memory's text into a memory class.

    Returns ``(class_name, confidence)``. ``confidence`` is bounded
    [0.0, 1.0]. Rows whose confidence falls below ``REVIEW_THRESHOLD``
    are flagged ``review_required`` by ``run_migrations``.

    Order of checks reflects spec priority:
      1. Identity (rule fires hardest if a person/org tag + identity pattern)
      2. Preference (clear lexical signal)
      3. Transactional (number + verb + entity shape)
      4. Time-anchored → factual_short
      5. Descriptive without temporal markers → factual_long
      6. Fallback → contextual
    """
    if not text:
        return "contextual", 0.30

    tags = list(entity_tags or [])
    txt = text.strip()

    # 1. Identity
    has_person_tag = any(t and t[:1].isupper() for t in tags)
    if has_person_tag and any(p.search(txt) for p in _IDENTITY_PATTERNS):
        return "identity", 0.85
    if any(p.search(txt) for p in _IDENTITY_PATTERNS[1:]):
        # "I am X" / "my wife is X" — strong even without entity tag
        return "identity", 0.75

    # 2. Preference
    txt_lower = txt.lower()
    if any(tok in txt_lower for tok in _PREFERENCE_TOKENS):
        return "preference", 0.80

    # 3. Transactional
    if _TRANSACTIONAL_RE.search(txt) and any(
        v in txt_lower for v in _TRANSACTIONAL_VERBS
    ):
        return "transactional", 0.75
    if any(v in txt_lower for v in _TRANSACTIONAL_VERBS) and any(
        ch.isdigit() for ch in txt
    ):
        return "transactional", 0.65

    # 4. Time-anchored
    if _TIME_ANCHOR_RE.search(txt):
        return "factual_short", 0.70

    # 5. Descriptive without temporal markers
    if _DESCRIPTIVE_RE.search(txt) and not _TIME_ANCHOR_RE.search(txt):
        return "factual_long", 0.55  # below threshold → review_required

    # 6. Fallback
    return "contextual", 0.40


# ── Schema operations ──────────────────────────────────────────────────────


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def ensure_class_columns(conn: sqlite3.Connection) -> bool:
    """Idempotently add ``memory_class`` and ``review_required`` columns.

    Returns ``True`` if columns were added, ``False`` if they already
    existed. Does not commit — caller controls the transaction boundary.
    """
    if _has_column(conn, "memories", "memory_class") and _has_column(
        conn, "memories", "review_required"
    ):
        return False

    if not _has_column(conn, "memories", "memory_class"):
        conn.execute(
            "ALTER TABLE memories ADD COLUMN memory_class TEXT NOT NULL "
            "DEFAULT 'contextual'"
        )
    if not _has_column(conn, "memories", "review_required"):
        conn.execute(
            "ALTER TABLE memories ADD COLUMN review_required INTEGER NOT NULL "
            "DEFAULT 0"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_memory_class "
        "ON memories (memory_class)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_review_required "
        "ON memories (review_required) WHERE review_required = 1"
    )
    return True


# ── Backfill / orchestration ───────────────────────────────────────────────


@dataclass
class MigrationResult:
    """Summary of a migration run."""

    rows_total: int = 0
    rows_classified: int = 0  # rows whose class changed from default
    rows_review_required: int = 0
    schema_changed: bool = False
    review_sample: list[dict] = field(default_factory=list)  # up to 20 rows
    by_class: dict[str, int] = field(default_factory=dict)


def _backfill(conn: sqlite3.Connection) -> MigrationResult:
    """Backfill memory_class + review_required for every row in memories.

    Idempotent: rows already classified to a non-contextual class with
    ``review_required = 0`` are left alone. Only rows where memory_class
    is the default (``'contextual'``) AND review_required is 0 are re-evaluated
    (so a manual reclassification persists across re-runs).
    """
    import json

    result = MigrationResult()
    cur = conn.execute(
        "SELECT id, text, entity_tags, memory_class, review_required FROM memories"
    )
    rows = cur.fetchall()
    result.rows_total = len(rows)

    for row in rows:
        rid = row[0]
        text = row[1]
        try:
            tags = json.loads(row[2]) if row[2] else []
        except (TypeError, ValueError):
            tags = []
        current_class = row[3]
        current_review = row[4]

        # Don't re-classify a row that the operator has already touched.
        if current_class != "contextual" and current_review == 0:
            result.by_class[current_class] = result.by_class.get(current_class, 0) + 1
            continue

        new_class, conf = classify_text(text, entity_tags=tags)
        review = 1 if conf < REVIEW_THRESHOLD else 0
        conn.execute(
            "UPDATE memories SET memory_class = ?, review_required = ? WHERE id = ?",
            (new_class, review, rid),
        )
        result.by_class[new_class] = result.by_class.get(new_class, 0) + 1
        if new_class != "contextual":
            result.rows_classified += 1
        if review:
            result.rows_review_required += 1
            if len(result.review_sample) < 20:
                result.review_sample.append(
                    {
                        "id": rid,
                        "text": (text or "")[:160],
                        "assigned_class": new_class,
                        "confidence": round(conf, 3),
                    }
                )
    return result


def run_migrations(db_path: str) -> MigrationResult:
    """Apply Capability 1.2 schema migration + heuristic backfill.

    Idempotent: running twice is a no-op the second time (schema_changed
    will be False and rows already classified are skipped). Caller is
    responsible for setting ``RAVEN_RUN_MIGRATIONS=1`` if they want this
    behaviour to fire automatically on store construction.
    """
    conn = sqlite3.connect(db_path)
    try:
        schema_changed = ensure_class_columns(conn)
        result = _backfill(conn)
        result.schema_changed = schema_changed
        conn.commit()
        return result
    finally:
        conn.close()


def review_queue(db_path: str, limit: int = 100) -> list[dict]:
    """Return rows flagged ``review_required = 1`` for the operator to inspect."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Older DBs may not have the column at all — return [] instead of erroring.
        if not _has_column(conn, "memories", "review_required"):
            return []
        cur = conn.execute(
            "SELECT id, text, memory_class FROM memories "
            "WHERE review_required = 1 LIMIT ?",
            (limit,),
        )
        return [
            {"id": r["id"], "text": r["text"], "memory_class": r["memory_class"]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


__all__ = [
    "MigrationResult",
    "REVIEW_THRESHOLD",
    "classify_text",
    "ensure_class_columns",
    "run_migrations",
    "review_queue",
]
