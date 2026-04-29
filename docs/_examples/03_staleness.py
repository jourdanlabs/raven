"""Cookbook example: handling RefusalReason.type == 'staleness_threshold_exceeded'.

Every candidate memory decayed below the ECLIPSE floor. The recommended
action is ``request_context`` — ask the user to supply fresh information
or trigger a re-ingest from upstream.

Run with: python docs/_examples/03_staleness.py
"""
from __future__ import annotations

import time

from raven.refusal import classify_refusal
from raven.types import AuroraInput, MemoryEntry, RefusalReason
from raven.validation.aurora import APPROVE_THRESHOLD


def handle_staleness(reason: RefusalReason) -> dict:
    """Render a routing decision: ask the user for fresh context."""
    return {
        "action": reason.recommended_action,
        "user_message": "Everything I have on this is too stale to trust.",
        "what_we_know": list(reason.what_we_know),
        "what_we_dont": list(reason.what_we_dont),
    }


def main() -> None:
    e = MemoryEntry(
        id="old",
        text="server was rebooted last quarter",
        timestamp=time.time() - 200 * 86_400,
    )
    aurora_input = AuroraInput(
        entries=[e],
        decay_weights=[0.001],  # well below default floor 0.05
        importance_scores=[0.5],
        contradictions=[],
        causal_edges=[],
        stale_ids=set(),
        meteor_entity_count=0,
    )
    reason = classify_refusal(
        query="server reboot status",
        aurora_input=aurora_input,
        aurora_threshold=APPROVE_THRESHOLD,
    )
    assert reason.type == "staleness_threshold_exceeded"
    decision = handle_staleness(reason)
    assert decision["action"] == "request_context"
    print(decision)


if __name__ == "__main__":
    main()
