"""Cookbook example: handling RefusalReason.type == 'identity_ambiguous'.

METEOR found multiple entity candidates and we can't tell who the user
meant. The recommended action is ``ask_user`` — show the candidates and
let them disambiguate.

Run with: python docs/_examples/04_identity_ambiguous.py
"""
from __future__ import annotations

import time

from raven.refusal import classify_refusal
from raven.types import AuroraInput, MemoryEntry, RefusalReason
from raven.validation.aurora import APPROVE_THRESHOLD


def handle_identity_ambiguous(reason: RefusalReason) -> str:
    """Build a 'did you mean?' prompt for the user."""
    candidates = [
        s.split("Candidate entities:", 1)[-1].strip()
        for s in reason.what_we_know
        if s.startswith("Candidate entities:")
    ]
    head = candidates[0] if candidates else "<no candidates>"
    return f"Did you mean one of: {head}?"


def main() -> None:
    e1 = MemoryEntry(
        id="a",
        text="Krillin won the match",
        timestamp=time.time(),
        entity_tags=["Krillin"],
    )
    e2 = MemoryEntry(
        id="b",
        text="18 won the match",
        timestamp=time.time(),
        entity_tags=["18"],
    )
    aurora_input = AuroraInput(
        entries=[e1, e2],
        decay_weights=[0.5, 0.5],
        importance_scores=[0.5, 0.5],
        contradictions=[],
        causal_edges=[],
        stale_ids=set(),
        meteor_entity_count=2,
    )
    reason = classify_refusal(
        query="who won the match",
        aurora_input=aurora_input,
        aurora_threshold=APPROVE_THRESHOLD,
    )
    assert reason.type == "identity_ambiguous"
    assert reason.recommended_action == "ask_user"
    msg = handle_identity_ambiguous(reason)
    assert "Krillin" in msg
    assert "18" in msg
    print(msg)


if __name__ == "__main__":
    main()
