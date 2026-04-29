"""Cookbook example: handling RefusalReason.type == 'conflicting_evidence_unresolvable'.

When PULSAR finds contradictions reconciliation can't resolve, surface
both sides instead of picking one. The recommended action is
``surface_uncertainty`` — show the user the conflict and let them choose.

Run with: python docs/_examples/02_conflicting_evidence.py
"""
from __future__ import annotations

from raven.refusal import classify_refusal
from raven.types import AuroraInput, Contradiction, MemoryEntry, RefusalReason
from raven.validation.aurora import APPROVE_THRESHOLD


def handle_conflicting_evidence(reason: RefusalReason) -> str:
    """Render a user-facing message that lays out both sides of the conflict."""
    lines = ["I have conflicting evidence and can't pick a winner safely."]
    lines.append("What I see:")
    for s in reason.what_we_know:
        lines.append(f"  - {s}")
    lines.append(f"Recommended next step: {reason.recommended_action}")
    return "\n".join(lines)


def main() -> None:
    import time

    e1 = MemoryEntry(id="a", text="The server always returns 200", timestamp=time.time())
    e2 = MemoryEntry(id="b", text="The server never returns 200", timestamp=time.time())
    contradiction = Contradiction(e1, e2, "absolutist", "always vs never on returns 200", 0.9)

    aurora_input = AuroraInput(
        entries=[e1, e2],
        decay_weights=[0.5, 0.5],
        importance_scores=[0.5, 0.5],
        contradictions=[contradiction],
        causal_edges=[],
        stale_ids=set(),
        meteor_entity_count=0,
    )
    reason = classify_refusal(
        query="does the server return 200",
        aurora_input=aurora_input,
        aurora_threshold=APPROVE_THRESHOLD,
    )
    assert reason.type == "conflicting_evidence_unresolvable"
    assert reason.recommended_action == "surface_uncertainty"

    message = handle_conflicting_evidence(reason)
    assert "conflicting" in message.lower()
    print(message)


if __name__ == "__main__":
    main()
