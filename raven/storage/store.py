"""RAVENStore — SQLite-backed memory store with vector index.

Capability 1.2 added two columns to ``memories``: ``memory_class`` (the
decay-class label) and ``review_required`` (set by the migration when a
heuristic classification was low-confidence). Both have safe defaults so
v1.0 databases keep working — see ``raven.storage.migrations`` for the
opt-in upgrade path.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import uuid

from raven.storage.embeddings import EmbedderProtocol, TFIDFEmbedder, cosine_similarity
from raven.storage.schema import ALL_DDL
from raven.types import MemoryEntry

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


def _vec_to_blob(vec: list[float]) -> bytes:
    if _HAS_NUMPY:
        buf = io.BytesIO()
        import numpy as np
        np.save(buf, np.array(vec, dtype=np.float32))
        return buf.getvalue()
    # Fallback: JSON bytes
    return json.dumps(vec).encode()


def _blob_to_vec(blob: bytes) -> list[float]:
    if _HAS_NUMPY:
        import numpy as np
        buf = io.BytesIO(blob)
        return np.load(buf).tolist()
    return json.loads(blob.decode())


class RAVENStore:
    """Persistent RAVEN memory store backed by SQLite + in-process vector index."""

    def __init__(
        self,
        db_path: str = ":memory:",
        embedder: EmbedderProtocol | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedder: EmbedderProtocol = embedder or TFIDFEmbedder()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        for ddl in ALL_DDL:
            self._conn.execute(ddl)
        # Capability 1.2 — opt-in column migration for pre-existing v1.0
        # databases that don't yet have memory_class / review_required.
        # Idempotent: ensure_class_columns no-ops if columns already exist.
        if os.environ.get("RAVEN_RUN_MIGRATIONS") == "1":
            from raven.storage.migrations import ensure_class_columns
            ensure_class_columns(self._conn)
        self._conn.commit()

    # ── Write ──────────────────────────────────────────────────────────────────

    def ingest(self, entry: MemoryEntry) -> str:
        """Ingest a MemoryEntry. Returns its id."""
        entry_id = entry.id or str(uuid.uuid4())

        # Lightweight PULSAR pass: mark prior entries superseded when supersedes_id set
        if entry.supersedes_id:
            self._conn.execute(
                "UPDATE memories SET validity_end = ? WHERE id = ?",
                (entry.timestamp, entry.supersedes_id),
            )

        # Detect whether the connected DB has the Capability 1.2 columns.
        # We branch the INSERT to stay compatible with both pre- and
        # post-migration schemas without forcing all callers to migrate.
        has_class_columns = self._has_memory_class_column()

        if has_class_columns:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                  (id, text, timestamp, source, entity_tags, topic_tags,
                   confidence, supersedes_id, validity_start, validity_end,
                   metadata, memory_class, review_required)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry_id,
                    entry.text,
                    entry.timestamp,
                    entry.source,
                    json.dumps(entry.entity_tags),
                    json.dumps(entry.topic_tags),
                    entry.confidence_at_ingest,
                    entry.supersedes_id,
                    entry.validity_start or entry.timestamp,
                    entry.validity_end,
                    json.dumps(entry.metadata),
                    getattr(entry, "memory_class", "contextual") or "contextual",
                    int(bool(entry.metadata.get("review_required", False))),
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                  (id, text, timestamp, source, entity_tags, topic_tags,
                   confidence, supersedes_id, validity_start, validity_end, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry_id,
                    entry.text,
                    entry.timestamp,
                    entry.source,
                    json.dumps(entry.entity_tags),
                    json.dumps(entry.topic_tags),
                    entry.confidence_at_ingest,
                    entry.supersedes_id,
                    entry.validity_start or entry.timestamp,
                    entry.validity_end,
                    json.dumps(entry.metadata),
                ),
            )

        vec = self._embedder.encode(entry.text)
        self._conn.execute(
            "INSERT OR REPLACE INTO embeddings (memory_id, vector) VALUES (?,?)",
            (entry_id, _vec_to_blob(vec)),
        )
        self._conn.commit()
        return entry_id

    def ingest_batch(self, entries: list[MemoryEntry]) -> list[str]:
        return [self.ingest(e) for e in entries]

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, entry_id: str) -> MemoryEntry | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def all_entries(self, limit: int = 10_000) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    # ── Hybrid Search ──────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 10,
        semantic_weight: float = 0.5,
        keyword_weight: float = 0.25,
        temporal_weight: float = 0.15,
        entity_weight: float = 0.10,
        entity_tags: list[str] | None = None,
        now: float | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """
        Hybrid retrieval. Returns (entry, score) pairs sorted descending.

        Scoring formula:
            score = semantic * semantic_weight
                  + keyword * keyword_weight
                  + temporal * temporal_weight
                  + entity  * entity_weight
        """
        now = now or time.time()
        q_vec = self._embedder.encode(query)
        q_words = set(query.lower().split())

        rows = self._conn.execute(
            "SELECT m.*, e.vector FROM memories m JOIN embeddings e ON m.id = e.memory_id"
        ).fetchall()

        results: list[tuple[MemoryEntry, float]] = []

        for row in rows:
            entry = self._row_to_entry(row)
            vec = _blob_to_vec(row["vector"])

            # Semantic
            sem = cosine_similarity(q_vec, vec)

            # Keyword
            entry_words = set(entry.text.lower().split())
            kw = len(q_words & entry_words) / max(len(q_words), 1)

            # Temporal (more recent = higher score, exponential)
            import math
            days_ago = (now - entry.timestamp) / 86_400
            temp = math.pow(0.5, days_ago / 30.0)

            # Entity match
            ent = 0.0
            if entity_tags:
                et = set(t.lower() for t in entry.entity_tags)
                ent = len(set(t.lower() for t in entity_tags) & et) / max(len(entity_tags), 1)

            score = (
                sem * semantic_weight
                + kw * keyword_weight
                + temp * temporal_weight
                + ent * entity_weight
            )
            results.append((entry, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        # row.keys() tells us whether the Capability 1.2 columns are present;
        # databases predating the migration won't have them, in which case
        # MemoryEntry's default ("contextual") applies.
        keys = set(row.keys())
        memory_class = (
            row["memory_class"] if "memory_class" in keys and row["memory_class"]
            else "contextual"
        )
        return MemoryEntry(
            id=row["id"],
            text=row["text"],
            timestamp=row["timestamp"],
            source=row["source"],
            entity_tags=json.loads(row["entity_tags"]),
            topic_tags=json.loads(row["topic_tags"]),
            confidence_at_ingest=row["confidence"],
            supersedes_id=row["supersedes_id"],
            validity_start=row["validity_start"],
            validity_end=row["validity_end"],
            metadata=json.loads(row["metadata"]),
            memory_class=memory_class,
        )

    def _has_memory_class_column(self) -> bool:
        """True iff the connected SQLite DB has the Capability 1.2 columns.

        Cheap PRAGMA call — sub-millisecond on any realistic store.
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)")}
        return "memory_class" in cols and "review_required" in cols

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RAVENStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
