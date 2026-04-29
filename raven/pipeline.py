"""RAVEN pipeline orchestrator.

Flow: retrieve -> METEOR -> NOVA -> ECLIPSE -> PULSAR -> QUASAR -> AURORA

Every response carries a full pipeline trace for auditability.

Capability 1.3 adds :meth:`RAVENPipeline.recall_v2` — an additive companion
to :meth:`recall` that routes the AURORA stage through
:func:`raven.validation.aurora.validate_aurora_v2`, returning an
:class:`AuroraVerdict` with a typed :class:`RefusalReason` on refusal.
The v1.0 :meth:`recall` flow is unchanged.
"""
from __future__ import annotations

import time

from raven.storage.store import RAVENStore
from raven.types import (
    AuroraInput,
    AuroraVerdict,
    MemoryEntry,
    PipelineTrace,
    RavenResponse,
    RefusalReason,
)
# AuroraInput is defined in raven.types and imported there
from raven.validation import (
    aurora,
    eclipse,
    meteor,
    nova,
    pulsar,
    quasar,
)


class RAVENPipeline:
    def __init__(
        self,
        store: RAVENStore,
        top_k: int = 20,
        aurora_threshold: float | None = None,
        half_life_days: float = eclipse.DEFAULT_HALF_LIFE_DAYS,
        meteor_config: meteor.METEORConfig | None = None,
        calibration_profile: str = "factual",
    ) -> None:
        # Phase 2.1: calibration profiles supply default knob values.
        # ``aurora_threshold`` (when supplied) still wins so existing
        # callers that pass an explicit value see their behaviour
        # preserved bit-for-bit.
        from raven.calibration import get_calibration_profile

        profile = get_calibration_profile(calibration_profile)
        self.store = store
        self.top_k = top_k
        self.aurora_threshold = (
            aurora_threshold
            if aurora_threshold is not None
            else profile.aurora_threshold
        )
        self.half_life_days = half_life_days
        self._meteor = meteor_config or meteor.METEORConfig()
        self.calibration_profile = profile

    def recall(
        self,
        query: str,
        *,
        now: float | None = None,
    ) -> RavenResponse:
        """Run the full RAVEN recall pipeline against ``query``.

        Parameters
        ----------
        query
            The user's query string.
        now
            **Corpus-relative reference time** (Unix epoch seconds) used by
            ECLIPSE for the decay computation and by QUASAR for any
            time-aware importance signals.

            When ``None`` (the default) RAVEN uses ``time.time()`` — this
            preserves v1.0 / v1.1 behaviour bit-for-bit.

            When supplied, RAVEN computes decay relative to ``now`` instead
            of wall-clock. This is useful in two distinct scenarios:

            1. **Benchmark fairness.** Corpora whose ingest timestamps are
               systematically older than the recall machine's clock (e.g.
               LongMemEval haystacks dated 2023-2024 replayed in 2026)
               otherwise see decay weights pinned at ~1e-7, which collapses
               the AURORA composite below any threshold that preserves
               approval quality. Passing ``now=question_timestamp`` makes
               decay reflect the corpus's own time origin.
            2. **Production historical backfill.** A caller restoring
               archived chat data into a fresh RAVEN store can pass
               ``now=session_end_time`` so decay reflects the session's
               relative age, not the wall-clock gap to the backfill run.

            This parameter is **fairness, not target-fixing**: the same
            arithmetic applies to any deployment whose ingest timestamps
            are older than recall time. See
            ``docs/phase2.2/fixes/01_now_override.md`` for the full
            motivation + benchmark receipts.
        """
        t_start = time.perf_counter()

        trace = PipelineTrace(notes=[query])

        # -- Retrieve --
        # Tag entities in the query for entity-weighted retrieval
        query_entities = self._meteor.tag_entities(query)
        raw_results = self.store.search(query, top_k=self.top_k, entity_tags=query_entities)

        if not raw_results:
            trace.latency_ms = (time.perf_counter() - t_start) * 1000
            return RavenResponse(
                query=query,
                status="REFUSED",
                overall_confidence=0.0,
                approved_memories=[],
                flagged_contradictions=[],
                rejected_memories=[],
                pipeline_trace=trace,
            )

        entries = [e for e, _ in raw_results]
        retrieval_scores = {e.id: s for e, s in raw_results}

        # -- METEOR --
        entities = self._meteor.tag_entities(" ".join(e.text for e in entries))
        trace.meteor_entities = len(set(entities))

        # -- NOVA --
        causal_edges = nova.build_causal_graph(entries)
        trace.nova_edges = len(causal_edges)

        # -- ECLIPSE --
        # ``now`` is corpus-relative when the caller supplies it; otherwise
        # we fall back to wall-clock for v1.0 / v1.1 byte-for-byte
        # compatibility.
        now = now if now is not None else time.time()
        decayed = eclipse.apply_decay(entries, self.half_life_days, now)
        decay_weights = [w for _, w in decayed]
        stale_ids = eclipse.find_superseded(entries)
        # Also mark validity_end-expired entries as stale
        stale_ids |= {e.id for e in entries if eclipse.is_stale(e, now)}
        trace.eclipse_applied = len(entries)

        # -- PULSAR --
        contradictions = pulsar.all_contradictions(entries)
        trace.pulsar_conflicts = len(contradictions)

        # -- QUASAR --
        ranked = quasar.rank_by_importance(entries, causal_edges, now)
        importance_scores_map = {e.id: s for e, s in ranked}
        # Align importance scores to original entries order
        importance_scores = [importance_scores_map.get(e.id, 0.5) for e in entries]
        trace.quasar_ranked = len(ranked)

        # -- AURORA --
        inp = AuroraInput(
            entries=entries,
            decay_weights=decay_weights,
            importance_scores=importance_scores,
            contradictions=contradictions,
            causal_edges=causal_edges,
            stale_ids=stale_ids,
            meteor_entity_count=trace.meteor_entities,
        )
        response = aurora.run_aurora(
            inp, trace, approve_threshold=self.aurora_threshold,
        )

        # Backfill retrieval scores onto scored memories
        for sm in response.approved_memories + response.rejected_memories:
            sm.retrieval_score = retrieval_scores.get(sm.entry.id, 0.0)

        trace.latency_ms = (time.perf_counter() - t_start) * 1000
        return response

    def ingest(self, entry: MemoryEntry) -> str:
        """Convenience passthrough to the store."""
        return self.store.ingest(entry)

    # ── Capability 1.1 — reconciliation hook ────────────────────────────────
    #
    # Additive only. The main `recall()` flow above is unchanged. This hook
    # is invoked by AURORA-aware callers (Sub C wires it through AuroraVerdict
    # in capability 1.3) to turn PULSAR's contradictions into typed
    # ResolvedClaim verdicts.
    #
    # The hook is also used directly by the scoring harness in
    # `corpus/muninn_v2/reconciliation/scoring/run_baselines.py`.

    def reconcile_contradictions(
        self,
        entries: list[MemoryEntry],
        *,
        now: float | None = None,
    ) -> list:
        """Detect contradictions in `entries`, reconcile each pair, return
        the list of ResolvedClaim verdicts (None entries dropped — only
        successful reconciliations returned).
        """
        # Local imports to avoid circular at module load.
        from raven.reconciliation import ReconciliationContext, reconcile

        if not entries:
            return []

        # Build context once for this batch
        causal_edges = nova.build_causal_graph(entries)
        meteor_entities = {
            e.id: self._meteor.tag_entities(e.text) for e in entries
        }
        # importance_scores are advisory — class rank is what wins rule (d),
        # but we expose per-memory scores for harness diagnostics.
        importance_scores = {
            e.id: s for e, s in quasar.rank_by_importance(entries, causal_edges, now)
        }

        ctx = ReconciliationContext(
            meteor_entities=meteor_entities,
            now=now,
            causal_edges=causal_edges,
            importance_scores=importance_scores,
        )

        resolved: list = []
        for a, b, _ctype in pulsar.reconcilable_pairs(entries):
            claim = reconcile(a, b, context=ctx)
            if claim is not None:
                resolved.append(claim)
        return resolved

    # -- Capability 1.3 — structured-refusal recall path -----------------------
    #
    # Additive only. The main `recall()` flow above is unchanged. This new
    # method runs the same engine flow but routes the AURORA stage through
    # `validate_aurora_v2`, returning an AuroraVerdict with a typed
    # RefusalReason on refusal.
    #
    # Callers that want v1.0 RavenResponse keep using `recall()`.

    def recall_v2(
        self,
        query: str,
        scope_allowlist: list[str] | None = None,
        *,
        now: float | None = None,
    ) -> AuroraVerdict:
        """Phase 1 capability pipeline. Same flow as :meth:`recall` but
        routes the final stage through
        :func:`raven.validation.aurora.validate_aurora_v2` to produce an
        :class:`AuroraVerdict` with a typed :class:`RefusalReason` on
        refusal.

        The ``scope_allowlist`` parameter, when supplied, lets the caller
        declare which topic tokens are in scope for this RAVEN instance.
        Queries containing content tokens outside that allowlist are
        refused with ``type="scope_violation"`` *before* any retrieval
        happens — RAVEN should not consult evidence for queries it is
        not authorized to answer.

        The ``now`` parameter is the same corpus-relative reference time
        described on :meth:`recall`. Default ``None`` preserves v1.0 /
        v1.1 / capability-1.3 behaviour bit-for-bit.
        """
        t_start = time.perf_counter()
        trace = PipelineTrace(notes=[query])

        # Lazy imports to avoid circular at module load time.
        from raven.refusal import _scope_violation_tokens
        from raven.validation.aurora import validate_aurora_v2

        # Scope check fires before retrieval. We construct an empty
        # AuroraInput so the classifier still has a typed object.
        if scope_allowlist is not None:
            offending = _scope_violation_tokens(query, scope_allowlist)
            if offending:
                empty_input = AuroraInput(
                    entries=[],
                    decay_weights=[],
                    importance_scores=[],
                    contradictions=[],
                    causal_edges=[],
                    stale_ids=set(),
                    meteor_entity_count=0,
                )
                trace.latency_ms = (time.perf_counter() - t_start) * 1000
                return validate_aurora_v2(
                    empty_input,
                    threshold=self.aurora_threshold,
                    scope_allowlist=scope_allowlist,
                    query=query,
                    contributing_engines=[],
                )

        query_entities = self._meteor.tag_entities(query)
        raw_results = self.store.search(
            query, top_k=self.top_k, entity_tags=query_entities
        )

        if not raw_results:
            empty_input = AuroraInput(
                entries=[],
                decay_weights=[],
                importance_scores=[],
                contradictions=[],
                causal_edges=[],
                stale_ids=set(),
                meteor_entity_count=0,
            )
            trace.latency_ms = (time.perf_counter() - t_start) * 1000
            return validate_aurora_v2(
                empty_input,
                threshold=self.aurora_threshold,
                scope_allowlist=scope_allowlist,
                query=query,
                contributing_engines=[],
            )

        entries = [e for e, _ in raw_results]

        # METEOR
        entities = self._meteor.tag_entities(" ".join(e.text for e in entries))
        trace.meteor_entities = len(set(entities))

        # NOVA
        causal_edges = nova.build_causal_graph(entries)
        trace.nova_edges = len(causal_edges)

        # ECLIPSE — corpus-relative time when the caller supplies ``now``.
        now = now if now is not None else time.time()
        decayed = eclipse.apply_decay(entries, self.half_life_days, now)
        decay_weights = [w for _, w in decayed]
        stale_ids = eclipse.find_superseded(entries)
        stale_ids |= {e.id for e in entries if eclipse.is_stale(e, now)}
        trace.eclipse_applied = len(entries)

        # PULSAR
        contradictions = pulsar.all_contradictions(entries)
        trace.pulsar_conflicts = len(contradictions)

        # QUASAR
        ranked = quasar.rank_by_importance(entries, causal_edges, now)
        importance_scores_map = {e.id: s for e, s in ranked}
        importance_scores = [
            importance_scores_map.get(e.id, 0.5) for e in entries
        ]
        trace.quasar_ranked = len(ranked)

        # AURORA v2 — typed verdict
        inp = AuroraInput(
            entries=entries,
            decay_weights=decay_weights,
            importance_scores=importance_scores,
            contradictions=contradictions,
            causal_edges=causal_edges,
            stale_ids=stale_ids,
            meteor_entity_count=trace.meteor_entities,
        )

        # contributing_engines reflects what actually ran
        # (non-zero contributions from each upstream signal).
        contributing: list[str] = []
        if trace.meteor_entities:
            contributing.append("meteor")
        if trace.nova_edges:
            contributing.append("nova")
        if trace.eclipse_applied:
            contributing.append("eclipse")
        if trace.pulsar_conflicts:
            contributing.append("pulsar")
        if trace.quasar_ranked:
            contributing.append("quasar")
        contributing.append("aurora")

        verdict = validate_aurora_v2(
            inp,
            threshold=self.aurora_threshold,
            scope_allowlist=scope_allowlist,
            query=query,
            contributing_engines=contributing,
        )

        trace.latency_ms = (time.perf_counter() - t_start) * 1000
        return verdict
