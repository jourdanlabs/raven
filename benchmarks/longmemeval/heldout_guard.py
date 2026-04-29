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


def run_held_out_validation(
    runner=None,
    *,
    profile: str | None = None,
    scorer: str | None = None,
    output: str | None = None,
    top_k: int = 20,
) -> dict:
    """Run a single held-out validation pass. **One-shot, Day 5 only.**

    Two call styles, both single-shot:

    1. **Custom runner (Phase 2.1 / fix-01 style).** Pass a positional
       ``runner`` callable that accepts ``list[LMEQuestion]`` and returns
       a JSON-serialisable dict. The function loads the held-out file
       once and hands the questions over. This is the original signature
       and remains supported byte-for-byte.

    2. **Profile + scorer convenience (Phase 2.2 fix-02 / LME-012).**
       Pass ``profile=``, ``scorer=``, optional ``output=`` and the
       guard composes the harness's ``run_all`` + ``aggregate`` +
       ``serialize_report`` chain internally. Equivalent to writing the
       runner inline; reduces the chance that a Day-5 reproducibility
       script trips over a typo.

    Both styles enforce the ``phase2.1_complete`` marker fence. Without
    the marker the function raises :class:`HeldOutAccessError` and never
    opens the held-out file. The marker check is the single
    architectural fence between the calibration sprint and the
    published number; removing or bypassing it invalidates the THEMIS
    framing test for this sprint.
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

    if runner is not None:
        if profile is not None or scorer is not None or output is not None:
            raise TypeError(
                "Pass either a positional `runner` OR the "
                "profile/scorer/output convenience kwargs, not both."
            )
        return runner(questions)

    # Convenience path. Defer the harness import to keep this module
    # cheap to import for the lock-status predicate path.
    if profile is None or scorer is None:
        raise TypeError(
            "run_held_out_validation requires either a positional `runner` "
            "or both `profile=` and `scorer=` keyword arguments."
        )
    import json
    import time
    from dataclasses import asdict

    from benchmarks.longmemeval.harness import (
        aggregate,
        run_all,
        serialize_report,
    )

    t0 = time.perf_counter()
    results = run_all(
        questions, top_k=top_k, calibration_profile=profile, scorer=scorer,
    )
    rep = aggregate(results)
    out = serialize_report(rep, results)
    out["wall_time_s"] = time.perf_counter() - t0
    out["config"] = {
        "top_k": top_k,
        "embedder": "TFIDFEmbedder",
        "raven_version": "1.2.x",
        "calibration_profile": profile,
        "scorer": scorer,
    }
    if output:
        Path(output).write_text(json.dumps(out, indent=2))
    return out


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
