"""Cookbook example: handling RefusalReason.type == 'insufficient_evidence'.

Surface RAVEN's gap to the user and ask for the missing context. This is
the most common refusal type — RAVEN looked, found nothing strong, and
wants the caller to either reformulate the query or supply a hint.

Run with: python docs/_examples/01_insufficient_evidence.py
"""
from __future__ import annotations

import tempfile

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.types import AuroraVerdict, RefusalReason


def handle_insufficient_evidence(verdict: AuroraVerdict) -> str:
    """Render a user-facing message that surfaces the gap honestly."""
    assert verdict.refusal_reason is not None
    r: RefusalReason = verdict.refusal_reason
    lines = ["I don't have enough to answer that confidently."]
    lines.append("Here's what I do know:")
    for s in r.what_we_know:
        lines.append(f"  - {s}")
    lines.append("Here's what I'd need:")
    for s in r.what_we_dont:
        lines.append(f"  - {s}")
    lines.append(f"Suggested next step: {r.recommended_action}")
    return "\n".join(lines)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = RAVENStore(db_path=f"{tmp}/raven.db")
        pipeline = RAVENPipeline(store)
        verdict = pipeline.recall_v2("what is the meaning of life")

        assert verdict.decision == "refuse"
        assert verdict.refusal_reason is not None
        assert verdict.refusal_reason.type == "insufficient_evidence"
        message = handle_insufficient_evidence(verdict)
        # The message must surface what we know AND what we don't.
        assert "I do know" in message
        assert "I'd need" in message
        print(message)


if __name__ == "__main__":
    main()
