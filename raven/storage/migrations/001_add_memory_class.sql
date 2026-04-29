-- Capability 1.2 — add memory_class + review_required to memories.
-- This file is applied via raven.storage.migrations.run_migrations(); the
-- ALTER TABLE statements are guarded in Python because SQLite's
-- "ALTER TABLE ADD COLUMN" raises if the column already exists, and
-- versions before 3.35 don't support "IF NOT EXISTS" for ADD COLUMN.

ALTER TABLE memories ADD COLUMN memory_class TEXT NOT NULL DEFAULT 'contextual';
ALTER TABLE memories ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0;

-- Index on memory_class so class-aware queries (e.g. "all identity rows")
-- don't scan the full table.
CREATE INDEX IF NOT EXISTS idx_memories_memory_class ON memories (memory_class);

-- Partial index for the review queue — sparse by design.
CREATE INDEX IF NOT EXISTS idx_memories_review_required
    ON memories (review_required) WHERE review_required = 1;
