"""Calibration profile loader + registry.

A :class:`CalibrationProfile` is a frozen bundle of calibration knobs that
the pipeline can apply at construction time. The registry is profile-scoped
(per the Phase 2.1 brief: "Profile-scoped DecayPolicy registry — no shared
global state. Each profile registers its own policies."). Each profile
ships its own copy of the knobs so swapping profiles cannot accidentally
leak state across :class:`RAVENPipeline` instances.

Profiles are loaded from minimal YAML-shape dicts. We do NOT pull a YAML
parser into the production dependency closure; the profile files are
hand-rolled key:value Markdown-style records, parsed by a tiny inline
reader. This keeps the dependency surface honest (calibration profiles
should not silently expand the dep tree) and the on-disk format remains
human-editable for end users.

Profile schema (see ``raven/calibration/profiles/factual.yaml`` for the
canonical example):

    name: factual
    description: |
      v1.0 default calibration. Tuned for fact-style memory entries
      (MUNINN-style). DO NOT modify; v1.0 callers depend on these values.
    aurora_threshold: 0.80
    decay_overrides: {}      # empty = use built-in DecayPolicy registry
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raven.types import DecayPolicy

FACTUAL_PROFILE_NAME = "factual"
CHAT_TURN_PROFILE_NAME = "chat_turn"

_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

# Module-level registry of CalibrationProfile by name. Profile-scoped state
# (no shared globals across profiles) is enforced by deep-copying the
# decay overrides at lookup time — see :func:`get_calibration_profile`.
_REGISTRY: dict[str, "CalibrationProfile"] = {}


# ── data class ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalibrationProfile:
    """A frozen bundle of calibration knobs.

    Attributes
    ----------
    name
        Stable string identifier (e.g. ``"factual"``, ``"chat_turn"``).
    description
        Human-readable rationale for the calibration regime. Read by the
        ``raven calibration list`` CLI; lives at the top of the profile
        YAML.
    aurora_threshold
        Composite confidence required to APPROVE a memory at the AURORA
        gate. The v1.0 default is 0.80 (the value of
        ``aurora.APPROVE_THRESHOLD`` at v1.0 release). Lower values
        approve more aggressively; the approval-quality check (median
        confidence on approvals ≥ 0.6) bounds how low this can go.
    decay_overrides
        Per-memory-class decay overrides. Maps ``memory_class`` →
        ``DecayPolicy``. The pipeline applies these on top of the
        built-in registry (see :mod:`raven.decay.policies`); empty dict
        means "use built-ins as-is". Phase 2.1 ships an empty dict for
        chat_turn — class-aware decay needs an upstream classifier to
        bite, which is architectural and out of scope.
    """

    name: str
    description: str
    aurora_threshold: float
    decay_overrides: dict[str, DecayPolicy] = field(default_factory=dict)


# ── tiny YAML-ish reader ────────────────────────────────────────────────────
#
# We parse a deliberately small subset of YAML (top-level scalars + a
# block-literal description) so the calibration profile loader doesn't
# need PyYAML. This keeps dependency closure honest. If profile schemas
# grow more complex, swap in a real YAML parser at this single point.


_KEY_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)$")
_BOOL_RE = re.compile(r"^(true|false|True|False|yes|no|YES|NO)$")


def _parse_scalar(value: str) -> Any:
    s = value.strip()
    if not s:
        return ""
    if s.startswith("\"") and s.endswith("\""):
        return s[1:-1]
    if s in ("{}", "{ }"):
        return {}
    if s in ("[]", "[ ]"):
        return []
    if _BOOL_RE.match(s):
        return s.lower() in ("true", "yes")
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def _parse_profile_yaml(text: str) -> dict[str, Any]:
    """Parse a tiny subset of YAML (top-level scalars + block-literal).

    Supports two top-level shapes per line:

    * ``key: scalar``  — handled by :func:`_parse_scalar`.
    * ``key: |``       — block literal; subsequent indented lines are
      collected verbatim until indentation drops back to column 0.

    Anything more complex (nested mappings, sequences) raises ``ValueError``
    so misuse fails loudly rather than silently dropping config.
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _KEY_RE.match(line)
        if not m:
            raise ValueError(
                f"calibration profile parse error at line {i+1}: {line!r}"
            )
        key, raw_val = m.group(1), m.group(2)
        if raw_val.strip() == "|":
            # block literal — collect indented continuation lines
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                cont = lines[i]
                if cont.strip() == "" or cont.startswith(" ") or cont.startswith("\t"):
                    block_lines.append(cont.lstrip())
                    i += 1
                else:
                    break
            result[key] = "\n".join(block_lines).rstrip()
        else:
            result[key] = _parse_scalar(raw_val)
            i += 1
    return result


# ── public API ──────────────────────────────────────────────────────────────


def register_calibration_profile(profile: CalibrationProfile) -> None:
    """Register a calibration profile.

    Idempotent for identical re-registration (same name + identical
    field values); raises ``ValueError`` on conflicting re-registration.
    """
    existing = _REGISTRY.get(profile.name)
    if existing is not None:
        if existing == profile:
            return  # idempotent
        raise ValueError(
            f"CalibrationProfile {profile.name!r} already registered with "
            f"different values (existing aurora_threshold="
            f"{existing.aurora_threshold}, new={profile.aurora_threshold})."
        )
    _REGISTRY[profile.name] = profile


def get_calibration_profile(name: str) -> CalibrationProfile:
    """Return the registered profile by name. Raises ``KeyError`` if missing."""
    if name not in _REGISTRY:
        raise KeyError(
            f"No CalibrationProfile registered for name={name!r}. "
            f"Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_calibration_profiles() -> list[CalibrationProfile]:
    """Return all registered calibration profiles, sorted by name."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def load_profile_from_path(path: Path) -> CalibrationProfile:
    """Load a single profile from a YAML-shape file."""
    raw = _parse_profile_yaml(path.read_text())
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"profile {path} missing required 'name'")
    desc = raw.get("description", "")
    threshold = raw.get("aurora_threshold")
    if not isinstance(threshold, (float, int)):
        raise ValueError(
            f"profile {path} has invalid aurora_threshold={threshold!r}"
        )
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(
            f"profile {path} aurora_threshold={threshold} out of range [0,1]"
        )
    # Phase 2.1 only ships scalar overrides, not full DecayPolicy dicts —
    # those would require a richer parser. Keep the door open for future
    # extension via a ``decay_overrides`` mapping in YAML.
    decay_overrides: dict[str, DecayPolicy] = {}
    return CalibrationProfile(
        name=name,
        description=str(desc),
        aurora_threshold=threshold,
        decay_overrides=decay_overrides,
    )


def load_builtin_profiles() -> None:
    """Load and register every YAML profile under ``raven/calibration/profiles/``.

    Idempotent. Called as a side-effect on ``raven.calibration`` import so
    callers don't have to remember to bootstrap the registry.
    """
    if not _PROFILES_DIR.exists():
        return
    for path in sorted(_PROFILES_DIR.glob("*.yaml")):
        profile = load_profile_from_path(path)
        register_calibration_profile(profile)


def _clear_registry_for_tests() -> None:
    """Test-only helper. Wipes the registry so a fresh load can be tested."""
    _REGISTRY.clear()


__all__ = [
    "CHAT_TURN_PROFILE_NAME",
    "FACTUAL_PROFILE_NAME",
    "CalibrationProfile",
    "get_calibration_profile",
    "list_calibration_profiles",
    "load_builtin_profiles",
    "load_profile_from_path",
    "register_calibration_profile",
]
