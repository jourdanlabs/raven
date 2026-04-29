"""DecayPolicy registry — process-global lookup keyed by memory class name.

The registry is intentionally simple: a single module-level dict guarded
behind a small functional API. Built-in policies in
``raven.decay.policies`` register themselves on import.

Concurrency: the registry is not lock-protected. Registration is expected
at module-load / startup time (or in tests). Calls during steady-state
recall are read-only.
"""
from __future__ import annotations

from raven.types import DecayPolicy

# Process-global singleton. Keyed by ``DecayPolicy.applies_to_class`` —
# i.e. the MemoryClass.name the policy applies to. Lookup by the same key
# is what ECLIPSE needs at recall time.
_REGISTRY: dict[str, DecayPolicy] = {}


def register_decay_policy(policy: DecayPolicy) -> None:
    """Register a DecayPolicy.

    Raises ``ValueError`` if a policy is already registered for the same
    memory class. Use ``unregister_decay_policy`` first if you want to
    replace an existing policy (typical only in tests).
    """
    if not isinstance(policy, DecayPolicy):
        raise TypeError(
            f"register_decay_policy expected DecayPolicy, got {type(policy).__name__}"
        )
    key = policy.applies_to_class
    if key in _REGISTRY:
        raise ValueError(
            f"DecayPolicy for memory_class={key!r} already registered "
            f"(existing={_REGISTRY[key].name!r}, new={policy.name!r}). "
            f"Call unregister_decay_policy({key!r}) first to replace."
        )
    _REGISTRY[key] = policy


def get_decay_policy(memory_class_name: str) -> DecayPolicy:
    """Return the DecayPolicy registered for ``memory_class_name``.

    Raises ``KeyError`` if no policy is registered for that class.
    """
    if memory_class_name not in _REGISTRY:
        raise KeyError(
            f"No DecayPolicy registered for memory_class={memory_class_name!r}. "
            f"Registered classes: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[memory_class_name]


def list_decay_policies() -> list[DecayPolicy]:
    """Return all registered DecayPolicy objects, sorted by class name."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def unregister_decay_policy(name: str) -> None:
    """Remove a DecayPolicy from the registry.

    ``name`` is the memory class name (i.e. ``policy.applies_to_class``).
    Raises ``KeyError`` if nothing is registered for that class. Intended
    for test cleanup; production code should not unregister built-ins.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Cannot unregister: no DecayPolicy for memory_class={name!r}"
        )
    del _REGISTRY[name]


def _clear_registry_for_tests() -> None:
    """Test-only helper. Wipes the registry. Call ``policies.register_builtins()``
    afterwards to restore the default state.
    """
    _REGISTRY.clear()
