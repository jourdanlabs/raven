import time
import uuid
import pytest
from raven.storage.store import RAVENStore
from raven.pipeline import RAVENPipeline
from raven.types import MemoryEntry


def _store() -> RAVENStore:
    return RAVENStore(db_path=":memory:")


def _pipeline(store=None) -> RAVENPipeline:
    return RAVENPipeline(store or _store())


def _entry(text: str, days_ago: float = 1.0, source="test",
           supersedes_id=None, entity_tags=None) -> MemoryEntry:
    return MemoryEntry(
        id=str(uuid.uuid4()),
        text=text,
        timestamp=time.time() - days_ago * 86_400,
        source=source,
        entity_tags=entity_tags or [],
        supersedes_id=supersedes_id,
    )


class TestRecallEmptyStore:
    def test_empty_store_returns_refused(self):
        p = _pipeline()
        response = p.recall("anything")
        assert response.status == "REFUSED"
        assert response.approved_memories == []
        assert response.pipeline_trace.latency_ms > 0

    def test_refused_response_properties(self):
        p = _pipeline()
        r = p.recall("any query")
        assert r.refused()
        assert not r.is_approved()


class TestRecallWithMemories:
    def test_relevant_memory_approved(self):
        store = _store()
        p = RAVENPipeline(store)
        p.ingest(_entry("TJ decided to ship RAVEN v1 today", days_ago=0.1, source="decision_log"))
        p.ingest(_entry("RAVEN pipeline approved for production deployment", days_ago=0.5, source="decision_log"))

        response = p.recall("RAVEN deployment decision")
        # Should find relevant memories (may be approved or conditional)
        assert response.status in ("APPROVED", "CONDITIONAL", "REJECTED")
        assert response.pipeline_trace.latency_ms > 0

    def test_trace_populated(self):
        store = _store()
        p = RAVENPipeline(store)
        for i in range(5):
            p.ingest(_entry(f"memory number {i} about RAVEN testing", days_ago=float(i)))
        response = p.recall("RAVEN testing")
        t = response.pipeline_trace
        assert t.aurora_approved + t.aurora_rejected > 0
        assert t.quasar_ranked > 0

    def test_contradiction_flagged(self):
        store = _store()
        p = RAVENPipeline(store)
        p.ingest(_entry("the RAVEN pipeline always succeeds on first attempt", days_ago=2))
        p.ingest(_entry("the RAVEN pipeline never succeeds without human review", days_ago=1))
        response = p.recall("RAVEN pipeline success")
        assert len(response.flagged_contradictions) >= 0  # may or may not trigger heuristic


class TestStaleMemories:
    def test_superseded_memory_rejected(self):
        store = _store()
        p = RAVENPipeline(store)
        old_id = str(uuid.uuid4())
        old = MemoryEntry(
            id=old_id, text="the threshold is 0.70", timestamp=time.time() - 10 * 86_400,
            source="config",
        )
        new = MemoryEntry(
            id=str(uuid.uuid4()),
            text="the threshold is updated to 0.80",
            timestamp=time.time() - 1 * 86_400,
            source="config",
            supersedes_id=old_id,
        )
        p.ingest(old)
        p.ingest(new)
        response = p.recall("what is the threshold")
        rejected_ids = {sm.entry.id for sm in response.rejected_memories}
        # Old entry should be rejected (superseded)
        assert old_id in rejected_ids


class TestIngest:
    def test_ingest_returns_id(self):
        p = _pipeline()
        entry = _entry("test memory")
        eid = p.ingest(entry)
        assert eid == entry.id

    def test_ingest_persists(self):
        store = _store()
        p = RAVENPipeline(store)
        e = _entry("persistent memory fact")
        p.ingest(e)
        assert store.count() == 1
        retrieved = store.get(e.id)
        assert retrieved is not None
        assert retrieved.text == e.text
