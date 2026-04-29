"""raven.decay — Decay-aware recall (Capability 1.2).

Public surface:

    from raven.decay import (
        DecayPolicy,
        register_decay_policy,
        get_decay_policy,
        list_decay_policies,
        unregister_decay_policy,
        BUILTIN_POLICIES,
    )

Built-in policies are registered on import (see ``raven.decay.policies``).
The class-aware ECLIPSE entry point lives at
``raven.validation.eclipse.apply_class_aware_decay``.
"""
from __future__ import annotations

from raven.types import DecayPolicy

# Importing policies has the side effect of registering the built-ins.
from .policies import BUILTIN_POLICIES  # noqa: F401  (re-exported)
from .registry import (
    get_decay_policy,
    list_decay_policies,
    register_decay_policy,
    unregister_decay_policy,
)

__all__ = [
    "DecayPolicy",
    "BUILTIN_POLICIES",
    "register_decay_policy",
    "get_decay_policy",
    "list_decay_policies",
    "unregister_decay_policy",
]
