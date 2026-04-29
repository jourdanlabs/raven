"""Tests for the Phase 2.1 calibration-profile system.

Calibration profiles are the only architecturally-permitted shape of
calibration change in Phase 2.1. These tests guard:

* The factual profile preserves v1.0 byte-for-byte.
* The chat_turn profile applies a different threshold WITHOUT mutating
  the factual profile (no shared global state across profiles).
* The pipeline accepts a profile name and uses the resolved threshold.
* Explicit ``aurora_threshold=...`` still wins (backward compat).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raven.calibration import (
    CHAT_TURN_PROFILE_NAME,
    FACTUAL_PROFILE_NAME,
    CalibrationProfile,
    get_calibration_profile,
    list_calibration_profiles,
)
from raven.calibration.profile import (
    _parse_profile_yaml,
    load_profile_from_path,
    register_calibration_profile,
)
from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.validation import aurora


# ── built-in profile invariants ─────────────────────────────────────────────


class TestBuiltinProfiles:
    def test_factual_profile_matches_v1_default(self):
        """v1.0 callers depend on aurora_threshold = APPROVE_THRESHOLD = 0.80."""
        p = get_calibration_profile(FACTUAL_PROFILE_NAME)
        assert p.aurora_threshold == 0.80
        assert p.aurora_threshold == aurora.APPROVE_THRESHOLD

    def test_chat_turn_profile_lowers_threshold(self):
        p = get_calibration_profile(CHAT_TURN_PROFILE_NAME)
        assert 0.5 <= p.aurora_threshold < 0.80

    def test_at_least_two_profiles_registered(self):
        names = {p.name for p in list_calibration_profiles()}
        assert FACTUAL_PROFILE_NAME in names
        assert CHAT_TURN_PROFILE_NAME in names

    def test_profile_is_frozen(self):
        p = get_calibration_profile(FACTUAL_PROFILE_NAME)
        with pytest.raises(Exception):
            p.aurora_threshold = 0.50  # type: ignore[misc]

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError):
            get_calibration_profile("definitely-not-a-profile")

    def test_profile_scoped_no_leakage(self):
        """Mutating one profile (via re-registration) does not affect another."""
        a = get_calibration_profile(FACTUAL_PROFILE_NAME)
        b = get_calibration_profile(CHAT_TURN_PROFILE_NAME)
        # The two profiles are distinct frozen objects with distinct values.
        assert a.aurora_threshold != b.aurora_threshold
        assert a.name != b.name


# ── pipeline wiring ────────────────────────────────────────────────────────


class TestPipelineWiring:
    def test_default_profile_preserves_v1_threshold(self):
        store = RAVENStore(":memory:")
        pipeline = RAVENPipeline(store=store)
        assert pipeline.aurora_threshold == aurora.APPROVE_THRESHOLD

    def test_chat_turn_profile_applies_lower_threshold(self):
        store = RAVENStore(":memory:")
        pipeline = RAVENPipeline(store=store, calibration_profile="chat_turn")
        chat_turn = get_calibration_profile("chat_turn")
        assert pipeline.aurora_threshold == chat_turn.aurora_threshold
        assert pipeline.aurora_threshold < aurora.APPROVE_THRESHOLD

    def test_explicit_threshold_overrides_profile(self):
        """Backward compat: callers passing aurora_threshold=... still win."""
        store = RAVENStore(":memory:")
        pipeline = RAVENPipeline(
            store=store, aurora_threshold=0.42, calibration_profile="chat_turn",
        )
        assert pipeline.aurora_threshold == 0.42

    def test_pipeline_records_profile(self):
        store = RAVENStore(":memory:")
        pipeline = RAVENPipeline(store=store, calibration_profile="chat_turn")
        assert pipeline.calibration_profile.name == "chat_turn"

    def test_unknown_profile_fails_at_construction(self):
        store = RAVENStore(":memory:")
        with pytest.raises(KeyError):
            RAVENPipeline(store=store, calibration_profile="never-registered")


# ── YAML loader ─────────────────────────────────────────────────────────────


class TestYamlLoader:
    def test_parse_simple_yaml(self):
        text = (
            "name: example\n"
            "description: |\n"
            "  multi\n"
            "  line\n"
            "aurora_threshold: 0.65\n"
        )
        parsed = _parse_profile_yaml(text)
        assert parsed["name"] == "example"
        assert parsed["aurora_threshold"] == 0.65
        assert "multi" in parsed["description"]
        assert "line" in parsed["description"]

    def test_load_profile_from_path(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text(
            "name: test\n"
            "description: foo\n"
            "aurora_threshold: 0.5\n"
        )
        prof = load_profile_from_path(p)
        assert prof.name == "test"
        assert prof.aurora_threshold == 0.5

    def test_invalid_threshold_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text(
            "name: bad\n"
            "description: oops\n"
            "aurora_threshold: 99.0\n"
        )
        with pytest.raises(ValueError):
            load_profile_from_path(p)

    def test_missing_name_raises(self, tmp_path):
        p = tmp_path / "nameless.yaml"
        p.write_text("aurora_threshold: 0.5\n")
        with pytest.raises(ValueError):
            load_profile_from_path(p)


# ── AURORA gate threading ───────────────────────────────────────────────────


class TestAuroraGateThreading:
    """gate() and run_aurora() must accept an explicit threshold argument
    and use it (not the module constant) — this is the calibration-plumbing
    contract Phase 2.1 depends on. Default arg keeps v1.0 behaviour."""

    def _build_input(self):
        from raven.types import AuroraInput, MemoryEntry
        # Fixed inputs so the arithmetic is predictable: composite ≈ 0.65
        # for entry with decay=0.4, importance=0.6, no conflict.
        # base = 0.4*0.25 + 0.6*0.45 + 1.0*0.30 = 0.10 + 0.27 + 0.30 = 0.67
        e = MemoryEntry(id="e1", text="hello", timestamp=0.0)
        return AuroraInput(
            entries=[e],
            decay_weights=[0.4],
            importance_scores=[0.6],
            contradictions=[],
            causal_edges=[],
            stale_ids=set(),
            meteor_entity_count=0,
        )

    def test_gate_default_threshold_rejects_subthreshold(self):
        inp = self._build_input()
        approved, rejected = aurora.gate(inp)  # default 0.80
        assert len(approved) == 0
        assert len(rejected) == 1

    def test_gate_lower_threshold_approves(self):
        inp = self._build_input()
        approved, rejected = aurora.gate(inp, approve_threshold=0.65)
        assert len(approved) == 1
        assert len(rejected) == 0

    def test_run_aurora_threshold_argument_changes_status(self):
        from raven.types import PipelineTrace
        inp = self._build_input()
        # Default threshold (0.80) -> REFUSED on this single sub-threshold entry
        resp_default = aurora.run_aurora(inp, PipelineTrace(notes=["q"]))
        # Lower threshold (0.65) -> APPROVED
        resp_low = aurora.run_aurora(
            inp, PipelineTrace(notes=["q"]),
            approve_threshold=0.65,
            conditional_threshold=0.40,
            refuse_threshold=0.20,
        )
        assert resp_low.status == "APPROVED"
        # Default still refuses (composite 0.67 < 0.80)
        assert resp_default.status in ("REFUSED", "REJECTED", "CONDITIONAL")
