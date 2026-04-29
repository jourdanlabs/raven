"""Tests for the Phase 2.1 LongMemEval corpus split + held-out guard.

These tests guard the methodology fence: a regression here would let the
held-out partition leak into calibration runs, which invalidates any
published number from the sprint.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from benchmarks.longmemeval import heldout_guard
from benchmarks.longmemeval.heldout_guard import (
    HELDOUT_PATH,
    HeldOutAccessError,
    held_out_unlocked,
    mark_phase_complete,
    run_held_out_validation,
)
from benchmarks.longmemeval.split import (
    DEFAULT_SEED,
    SplitResult,
    render_split_doc,
    split_corpus,
)


@pytest.fixture
def tiny_corpus(tmp_path) -> Path:
    """Build a 10-instance fake corpus with the LongMemEval JSON shape.

    Real LongMemEval entries have nested haystacks; the split logic only
    needs ``question_id`` + the rest as opaque, so a stub is sufficient.
    Using a stub keeps the test fast and independent of the on-disk
    upstream file.
    """
    items = []
    for i in range(10):
        items.append({
            "question_id": f"q_{i:02d}",
            "question_type": "single-session-user",
            "question": f"q text {i}",
            "answer": f"a {i}",
            "question_date": "2024/01/01 (Mon) 00:00",
            "haystack_dates": ["2024/01/01 (Mon) 00:00"],
            "haystack_session_ids": [f"s_{i}"],
            "haystack_sessions": [[{"role": "user", "content": "hi", "has_answer": True}]],
            "answer_session_ids": [f"s_{i}"],
        })
    src = tmp_path / "src.json"
    src.write_text(json.dumps(items))
    return src


# ── split determinism ──────────────────────────────────────────────────────


class TestSplitDeterminism:
    def test_same_seed_same_shas(self, tiny_corpus, tmp_path):
        """Re-running the split on the same source must produce identical SHAs."""
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        a = split_corpus(source_path=tiny_corpus, out_dir=out_a, seed=DEFAULT_SEED)
        b = split_corpus(source_path=tiny_corpus, out_dir=out_b, seed=DEFAULT_SEED)
        assert a.calibration_sha256 == b.calibration_sha256
        assert a.heldout_sha256 == b.heldout_sha256

    def test_different_seed_different_shas(self, tiny_corpus, tmp_path):
        a = split_corpus(source_path=tiny_corpus, out_dir=tmp_path / "a", seed=42)
        b = split_corpus(source_path=tiny_corpus, out_dir=tmp_path / "b", seed=7)
        assert a.calibration_sha256 != b.calibration_sha256

    def test_partition_size(self, tiny_corpus, tmp_path):
        r = split_corpus(
            source_path=tiny_corpus, out_dir=tmp_path,
            seed=DEFAULT_SEED, heldout_fraction=0.20,
        )
        assert r.calibration_count == 8
        assert r.heldout_count == 2

    def test_partitions_disjoint_and_complete(self, tiny_corpus, tmp_path):
        r = split_corpus(source_path=tiny_corpus, out_dir=tmp_path, seed=DEFAULT_SEED)
        cal_qids = {x["question_id"] for x in json.loads(r.calibration_path.read_text())}
        hel_qids = {x["question_id"] for x in json.loads(r.heldout_path.read_text())}
        all_qids = {x["question_id"] for x in json.loads(tiny_corpus.read_text())}
        assert cal_qids.isdisjoint(hel_qids)
        assert cal_qids | hel_qids == all_qids

    def test_canonical_json_shape(self, tiny_corpus, tmp_path):
        """Written JSON must be a list of objects (so loader.load_questions works)."""
        r = split_corpus(source_path=tiny_corpus, out_dir=tmp_path, seed=DEFAULT_SEED)
        cal = json.loads(r.calibration_path.read_text())
        assert isinstance(cal, list) and all(isinstance(x, dict) for x in cal)


class TestSplitDocRender:
    def test_doc_contains_shas(self, tiny_corpus, tmp_path):
        r = split_corpus(source_path=tiny_corpus, out_dir=tmp_path, seed=DEFAULT_SEED)
        doc = render_split_doc(r)
        assert r.calibration_sha256 in doc
        assert r.heldout_sha256 in doc
        assert r.source_sha256 in doc
        assert "Phase 2.1" in doc


# ── held-out guard ──────────────────────────────────────────────────────────


class TestHeldOutGuard:
    """The Day 5 fence. These tests enforce the methodology rule that the
    held-out partition is read at most once, after the calibration sprint
    seals."""

    def _stub_partition_files(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Drop in stub calibration/heldout files at the module-level paths
        and return paths so the test can clean up afterwards."""
        items = [
            {
                "question_id": "q_test",
                "question_type": "single-session-user",
                "question": "q",
                "answer": "a",
                "question_date": "2024/01/01 (Mon) 00:00",
                "haystack_dates": ["2024/01/01 (Mon) 00:00"],
                "haystack_session_ids": ["s_test"],
                "haystack_sessions": [[{"role": "user", "content": "hi"}]],
                "answer_session_ids": ["s_test"],
            }
        ]
        return items, heldout_guard.CALIBRATION_PATH, heldout_guard.HELDOUT_PATH

    def test_run_held_out_validation_blocks_without_marker(self, tmp_path, monkeypatch):
        """Without the marker, the guard must raise BEFORE opening the file."""
        # Point the marker to a path we control
        sentinel_marker = tmp_path / ".phase2.1_complete"
        monkeypatch.setattr(heldout_guard, "MARKER_PATH", sentinel_marker)
        assert not sentinel_marker.exists()

        with pytest.raises(HeldOutAccessError):
            run_held_out_validation(lambda qs: {"n": len(qs)})

    def test_run_held_out_validation_allows_with_marker(self, tmp_path, monkeypatch):
        """With the marker present and a valid partition file, guard passes."""
        sentinel_marker = tmp_path / ".phase2.1_complete"
        sentinel_marker.write_text("phase: 2.1\n")
        monkeypatch.setattr(heldout_guard, "MARKER_PATH", sentinel_marker)

        # If a real heldout.json exists, use it; else write a stub at the
        # module's HELDOUT_PATH (back it up first).
        items, _, heldout_path = self._stub_partition_files(tmp_path)
        backup = None
        if heldout_path.exists():
            backup = heldout_path.read_bytes()
        heldout_path.write_text(json.dumps(items))
        try:
            result = run_held_out_validation(lambda qs: {"n": len(qs)})
            assert result == {"n": 1}
        finally:
            if backup is not None:
                heldout_path.write_bytes(backup)
            else:
                heldout_path.unlink(missing_ok=True)

    def test_held_out_unlocked_predicate(self, tmp_path, monkeypatch):
        sentinel_marker = tmp_path / ".phase2.1_complete"
        monkeypatch.setattr(heldout_guard, "MARKER_PATH", sentinel_marker)
        assert not held_out_unlocked()
        sentinel_marker.write_text("ok")
        assert held_out_unlocked()

    def test_mark_phase_complete_writes_marker(self, tmp_path, monkeypatch):
        sentinel_marker = tmp_path / ".phase2.1_complete"
        monkeypatch.setattr(heldout_guard, "MARKER_PATH", sentinel_marker)
        path = mark_phase_complete(note="day5 final run")
        assert path.exists()
        contents = path.read_text()
        assert "phase: 2.1" in contents
        assert "day5 final run" in contents
