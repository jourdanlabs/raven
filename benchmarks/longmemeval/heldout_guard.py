"""Architectural guard around the LongMemEval Phase 2.1 held-out partition.

Held-out methodology rule (from the Phase 2.1 brief): the held-out set is
read **once**, on Day 5, after every calibration decision is final. Any
earlier read contaminates the published number.

This module is the only place in the codebase that opens
``benchmarks/longmemeval/heldout.json`` for full-corpus reading. Two
mechanisms enforce the rule:

1. :func:`run_held_out_validation` requires a ``phase2.1_complete`` marker
   to exist on disk before it will read the held-out file. Without the
   marker it raises :class:`HeldOutAccessError`. The marker file is created
   by :func:`mark_phase_complete`, which a human operator runs only after
   the calibration sprint is sealed.

2. The held-out file's *path* is exposed only through this module
   (:data:`HELDOUT_PATH`); other code paths that try to read it directly
   are flagged in code review.

The guard does not (and cannot) prevent every imaginable contamination
pattern — a determined developer can always shell-cat the file. Its job is
to make accidental contamination loud and to make intentional
contamination an explicit choice that shows up in the audit trail.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from benchmarks.longmemeval.loader import LMEQuestion, load_questions

_HERE = Path(__file__).resolve().parent
HELDOUT_PATH = _HERE / "heldout.json"
CALIBRATION_PATH = _HERE / "calibration.json"
MARKER_PATH = _HERE / ".phase2.1_complete"


class HeldOutAccessError(RuntimeError):
    """Raised when the held-out partition is read before Day 5 unlock."""


def held_out_unlocked() -> bool:
    """True if :func:`mark_phase_complete` has been called.

    Cheap predicate intended for tests and audit dashboards. The file's
    presence — not its contents — is the signal.
    """
    return MARKER_PATH.exists()


def mark_phase_complete(note: str = "") -> Path:
    """Create the marker that unlocks the held-out partition.

    The marker stores a UTC timestamp and an optional human-supplied note
    describing the calibration profile being validated. The marker should
    be created exactly once per sprint, after Day 4 calibration is sealed.
    Re-creating the marker overwrites the timestamp; that is intentional
    so a re-validation after a documented overfit-rollback can be
    distinguished from the original Day 5 run by inspecting the file.
    """
    contents = [
        f"phase: 2.1",
        f"unlocked_at_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"note: {note}".rstrip(),
    ]
    MARKER_PATH.write_text("\n".join(contents) + "\n")
    return MARKER_PATH


def load_calibration_questions() -> list[LMEQuestion]:
    """Load the calibration partition. Always permitted."""
    if not CALIBRATION_PATH.exists():
        raise FileNotFoundError(
            f"Calibration partition missing at {CALIBRATION_PATH}. "
            f"Run `python -m benchmarks.longmemeval.split --write-doc` first."
        )
    return load_questions(CALIBRATION_PATH)


def _load_held_out_unguarded() -> list[LMEQuestion]:
    """Test-only escape hatch. Bypasses the marker check.

    Activated by the ``RAVEN_BYPASS_HELDOUT_GUARD=1`` environment variable
    so unit tests for the *guard itself* can read the file without
    polluting the marker. Production code MUST NOT set this variable.
    """
    if os.environ.get("RAVEN_BYPASS_HELDOUT_GUARD") != "1":
        raise HeldOutAccessError(
            "Unguarded held-out load attempted without the test-only bypass. "
            "Use run_held_out_validation() in production code."
        )
    return load_questions(HELDOUT_PATH)


def run_held_out_validation(runner) -> dict:
    """Run a single held-out validation pass. **One-shot, Day 5 only.**

    ``runner`` is any callable accepting ``list[LMEQuestion]`` and
    returning a JSON-serialisable dict (typically the harness's
    ``run_all`` + ``aggregate`` chain). The function:

    1. Verifies the ``phase2.1_complete`` marker is present.
       Without it, raises :class:`HeldOutAccessError` immediately and
       never opens the held-out file.
    2. Loads the held-out partition through :func:`load_questions`.
    3. Hands the questions to ``runner`` and returns whatever it returns.

    The marker check is the single architectural fence between the
    calibration sprint and the published number. Removing or bypassing it
    invalidates the THEMIS framing test for this sprint.
    """
    if not MARKER_PATH.exists():
        raise HeldOutAccessError(
            "Held-out partition is locked. Call mark_phase_complete() "
            "after Day 4 calibration is sealed; only then is "
            "run_held_out_validation() permitted to open the file."
        )
    if not HELDOUT_PATH.exists():
        raise FileNotFoundError(
            f"Held-out partition missing at {HELDOUT_PATH}. "
            f"Run `python -m benchmarks.longmemeval.split --write-doc` first."
        )
    questions = load_questions(HELDOUT_PATH)
    return runner(questions)


__all__ = [
    "CALIBRATION_PATH",
    "HELDOUT_PATH",
    "MARKER_PATH",
    "HeldOutAccessError",
    "held_out_unlocked",
    "load_calibration_questions",
    "mark_phase_complete",
    "run_held_out_validation",
]
