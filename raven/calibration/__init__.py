"""Calibration profiles for RAVEN.

A calibration profile is a small bundle of values — AURORA approval
threshold, optional per-class decay-policy overrides — that lets a
RAVEN deployment swap calibration regimes without code changes.

The v1.0 *factual* profile is preserved as the default
(:data:`FACTUAL_PROFILE_NAME`); Phase 2.1 ships *chat_turn* alongside it
for conversational corpora. Both profiles register in
:mod:`raven.calibration.registry`; new profiles drop in by adding a
``raven/calibration/profiles/<name>.yaml`` file plus an entry in
:func:`load_builtin_profiles`.

The profile API is intentionally narrow:

* :func:`get_calibration_profile` returns a frozen
  :class:`CalibrationProfile` by name.
* :func:`RAVENPipeline(calibration_profile=…)` accepts the profile name
  and applies the threshold / decay overrides at construction time.

Anything that requires more than threshold + decay overrides is
**architectural** and out of scope for this calibration sprint.
"""
from __future__ import annotations

from .profile import (
    CHAT_TURN_PROFILE_NAME,
    FACTUAL_PROFILE_NAME,
    CalibrationProfile,
    get_calibration_profile,
    list_calibration_profiles,
    load_builtin_profiles,
)

# Side-effect on import: register the built-in profiles so callers that
# just do `from raven.calibration import get_calibration_profile` get a
# populated registry without a second step.
load_builtin_profiles()

__all__ = [
    "CHAT_TURN_PROFILE_NAME",
    "FACTUAL_PROFILE_NAME",
    "CalibrationProfile",
    "get_calibration_profile",
    "list_calibration_profiles",
    "load_builtin_profiles",
]
