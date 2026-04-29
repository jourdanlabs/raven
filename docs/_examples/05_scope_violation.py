"""Cookbook example: handling RefusalReason.type == 'scope_violation'.

The query is structurally outside RAVEN's permitted operational scope.
The recommended action is ``escalate`` — route to a human policy reviewer
or a different system. RAVEN should NOT consult evidence for queries it
isn't authorized to answer.

Run with: python docs/_examples/05_scope_violation.py
"""
from __future__ import annotations

import tempfile

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.types import AuroraVerdict


def escalate(verdict: AuroraVerdict, ticket_queue: list) -> dict:
    """Append an escalation ticket; never expose RAVEN's internals to the user."""
    assert verdict.refusal_reason is not None
    r = verdict.refusal_reason
    ticket = {
        "type": r.type,
        "audit_hash": r.audit_hash,
        "what_we_know": list(r.what_we_know),
        "recommended_action": r.recommended_action,
    }
    ticket_queue.append(ticket)
    return ticket


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = RAVENStore(db_path=f"{tmp}/raven.db")
        pipeline = RAVENPipeline(store)
        # Operator has restricted this RAVEN instance to billing topics.
        verdict = pipeline.recall_v2(
            "tell me about quantum cryptography",
            scope_allowlist=["billing", "invoice"],
        )
        assert verdict.decision == "refuse"
        assert verdict.refusal_reason is not None
        assert verdict.refusal_reason.type == "scope_violation"
        assert verdict.refusal_reason.recommended_action == "escalate"

        queue: list = []
        ticket = escalate(verdict, queue)
        assert ticket["type"] == "scope_violation"
        assert len(queue) == 1
        print(ticket)


if __name__ == "__main__":
    main()
