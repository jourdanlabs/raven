"""RAVEN storage schema — SQLite-backed, structured for validation-downstream use."""
from __future__ import annotations

CREATE_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    timestamp       REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT 'unknown',
    entity_tags     TEXT NOT NULL DEFAULT '[]',      -- JSON array
    topic_tags      TEXT NOT NULL DEFAULT '[]',      -- JSON array
    confidence      REAL NOT NULL DEFAULT 1.0,
    supersedes_id   TEXT,
    validity_start  REAL NOT NULL,
    validity_end    REAL,
    metadata        TEXT NOT NULL DEFAULT '{}'       -- JSON object
);
"""

CREATE_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS embeddings (
    memory_id   TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    vector      BLOB NOT NULL    -- numpy float32 array serialised with numpy.save
);
"""

CREATE_IDX_TIMESTAMP = """
CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories (timestamp DESC);
"""

CREATE_IDX_SUPERSEDES = """
CREATE INDEX IF NOT EXISTS idx_memories_supersedes ON memories (supersedes_id)
WHERE supersedes_id IS NOT NULL;
"""

ALL_DDL: list[str] = [
    CREATE_MEMORIES,
    CREATE_EMBEDDINGS,
    CREATE_IDX_TIMESTAMP,
    CREATE_IDX_SUPERSEDES,
]
