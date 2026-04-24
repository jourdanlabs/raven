"""MUNINN baselines — six memory retrieval/validation strategies for comparison.

1. raw_passthrough   — return all entries as approved, no filtering
2. recency_filter    — top-K by timestamp only
3. simulated_zep     — basic dedup + recency (approximates Zep graph-memory behavior)
4. simulated_mem0    — keyword importance + recency threshold (approximates Mem0 behavior)
5. raven_retrieval_only — TF-IDF retrieval score only, skip validation pipeline (ablation)
6. raven_full        — complete RAVEN validation pipeline (METEOR→NOVA→ECLIPSE→PULSAR→QUASAR→AURORA)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from raven.types import MemoryEntry


@dataclass
class BaselineResult:
    status: str   # "APPROVED" | "CONDITIONAL" | "REJECTED" | "REFUSED"
    approved_ids: set[str]
    rejected_ids: set[str]
    meta: dict = None  # optional debug info


def _to_mem_entries(raw: list[dict]) -> list[MemoryEntry]:
    return [
        MemoryEntry(
            id=e["id"],
            text=e["text"],
            timestamp=e["timestamp"],
            source=e.get("source", "unknown"),
            entity_tags=e.get("entity_tags", []),
            topic_tags=e.get("topic_tags", []),
            confidence_at_ingest=e.get("confidence_at_ingest", 0.9),
            supersedes_id=e.get("supersedes_id"),
            validity_start=e.get("validity_start", e["timestamp"]),
            validity_end=e.get("validity_end"),
            metadata=e.get("metadata", {}),
        )
        for e in raw
    ]


def raw_passthrough(query: str, raw_entries: list[dict]) -> BaselineResult:
    """Approve everything. Simulates a system with no validation."""
    ids = {e["id"] for e in raw_entries}
    status = "APPROVED" if ids else "REFUSED"
    return BaselineResult(status=status, approved_ids=ids, rejected_ids=set())


def recency_filter(query: str, raw_entries: list[dict], top_k: int = 3) -> BaselineResult:
    """Approve the top-K most recent entries. Ignores staleness, contradiction, importance."""
    if not raw_entries:
        return BaselineResult(status="REFUSED", approved_ids=set(), rejected_ids=set())
    sorted_entries = sorted(raw_entries, key=lambda e: e["timestamp"], reverse=True)
    approved = {e["id"] for e in sorted_entries[:top_k]}
    rejected = {e["id"] for e in sorted_entries[top_k:]}
    return BaselineResult(status="APPROVED", approved_ids=approved, rejected_ids=rejected)


def _word_set(text: str, min_len: int = 4) -> set[str]:
    return {w for w in re.sub(r"[^\w\s]", "", text.lower()).split() if len(w) >= min_len}


def simulated_zep(query: str, raw_entries: list[dict]) -> BaselineResult:
    """Approximate Zep: deduplicate near-identical entries, return rest sorted by recency.

    Zep uses a knowledge graph to dedup and link memories. We approximate this by
    removing entries whose text shares >60% word overlap with a more recent entry.
    No contradiction detection, no importance scoring, no staleness check.
    """
    if not raw_entries:
        return BaselineResult(status="REFUSED", approved_ids=set(), rejected_ids=set())

    sorted_entries = sorted(raw_entries, key=lambda e: e["timestamp"], reverse=True)
    seen_word_sets: list[set[str]] = []
    approved, rejected = set(), set()

    for entry in sorted_entries:
        words = _word_set(entry["text"])
        is_dup = False
        for seen in seen_word_sets:
            if not seen or not words:
                continue
            overlap = len(words & seen) / len(words | seen)
            if overlap > 0.60:
                is_dup = True
                break
        if is_dup:
            rejected.add(entry["id"])
        else:
            approved.add(entry["id"])
            seen_word_sets.append(words)

    return BaselineResult(
        status="APPROVED" if approved else "REFUSED",
        approved_ids=approved,
        rejected_ids=rejected,
    )


_MEM0_KEYWORDS = {
    "critical", "important", "priority", "urgent", "decision", "approved",
    "deployed", "completed", "milestone", "shipped", "resolved", "fixed",
    "launched", "failed", "breach", "outage", "cve", "patch",
}

def simulated_mem0(query: str, raw_entries: list[dict], threshold: float = 0.45) -> BaselineResult:
    """Approximate Mem0: keyword importance + recency score, threshold-based gate.

    Mem0 uses LLM-extracted importance scores and recency. We approximate with:
    keyword presence score + normalised recency (no contradiction detection, no staleness).
    """
    if not raw_entries:
        return BaselineResult(status="REFUSED", approved_ids=set(), rejected_ids=set())

    now = time.time()
    approved, rejected = set(), set()

    # Normalise timestamps for recency score
    timestamps = [e["timestamp"] for e in raw_entries]
    ts_min, ts_max = min(timestamps), max(timestamps)
    ts_range = max(ts_max - ts_min, 1.0)

    for entry in raw_entries:
        words = set(entry["text"].lower().split())
        kw_hits = len(words & _MEM0_KEYWORDS)
        kw_score = min(1.0, kw_hits / 3.0)
        recency = (entry["timestamp"] - ts_min) / ts_range
        score = 0.6 * kw_score + 0.4 * recency
        if score >= threshold:
            approved.add(entry["id"])
        else:
            rejected.add(entry["id"])

    return BaselineResult(
        status="APPROVED" if approved else "REFUSED",
        approved_ids=approved,
        rejected_ids=rejected,
    )


def raven_retrieval_only(query: str, raw_entries: list[dict], top_k: int = 3) -> BaselineResult:
    """Ablation: TF-IDF cosine similarity to query only — no validation pipeline.

    This isolates the retrieval contribution from the full pipeline. Entries are ranked
    by cosine similarity to the query; top-K are approved with no staleness/contradiction check.
    """
    if not raw_entries:
        return BaselineResult(status="REFUSED", approved_ids=set(), rejected_ids=set())

    from raven.storage.embeddings import TFIDFEmbedder, cosine_similarity
    embedder = TFIDFEmbedder()

    q_vec = embedder.encode(query)
    scored = []
    for entry in raw_entries:
        e_vec = embedder.encode(entry["text"])
        sim = cosine_similarity(q_vec, e_vec)
        scored.append((entry["id"], sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    approved = {eid for eid, _ in scored[:top_k]}
    rejected = {eid for eid, _ in scored[top_k:]}

    return BaselineResult(
        status="APPROVED" if approved else "REFUSED",
        approved_ids=approved,
        rejected_ids=rejected,
    )


def raven_full(query: str, raw_entries: list[dict], now: float | None = None) -> BaselineResult:
    """Full RAVEN validation pipeline: NOVA→ECLIPSE→PULSAR→QUASAR→AURORA."""
    from raven.validation import eclipse, nova, pulsar, quasar, aurora
    from raven.types import AuroraInput, PipelineTrace

    if not raw_entries:
        return BaselineResult(status="REFUSED", approved_ids=set(), rejected_ids=set())

    entries = _to_mem_entries(raw_entries)
    now = now or time.time()

    causal_edges = nova.build_causal_graph(entries)
    decayed = eclipse.apply_decay(entries, now=now)
    decay_weights = [w for _, w in decayed]
    stale_ids = eclipse.find_superseded(entries)
    stale_ids |= {e.id for e in entries if eclipse.is_stale(e, now)}
    contradictions = pulsar.all_contradictions(entries)
    ranked = quasar.rank_by_importance(entries, causal_edges, now)
    importance_map = {e.id: s for e, s in ranked}
    importance_scores = [importance_map.get(e.id, 0.5) for e in entries]

    inp = AuroraInput(
        entries=entries,
        decay_weights=decay_weights,
        importance_scores=importance_scores,
        contradictions=contradictions,
        causal_edges=causal_edges,
        stale_ids=stale_ids,
    )
    trace = PipelineTrace(notes=[query])
    response = aurora.run_aurora(inp, trace)

    return BaselineResult(
        status=response.status,
        approved_ids={sm.entry.id for sm in response.approved_memories},
        rejected_ids={sm.entry.id for sm in response.rejected_memories},
        meta={"aurora_approved": trace.aurora_approved, "aurora_rejected": trace.aurora_rejected},
    )


ALL_BASELINES = {
    "raw_passthrough": raw_passthrough,
    "recency_filter": recency_filter,
    "simulated_zep": simulated_zep,
    "simulated_mem0": simulated_mem0,
    "raven_retrieval_only": raven_retrieval_only,
    "raven_full": raven_full,
}
