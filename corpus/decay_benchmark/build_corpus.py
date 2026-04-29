"""Build the Capability 1.2 DECAY benchmark corpus.

Produces 300 query JSON files = 5 memory classes × 6 horizons × 10 queries.

The five matrix classes are the *decaying* ones — factual_short, factual_long,
preference, transactional, contextual. The sixth built-in policy
(``identity``, no-decay) is a separate fixed-weight scenario set, written
to ``queries/identity/no_horizon/`` and not part of the 300-query matrix
per the brief. Identity tests are still scored — see
``scoring/run_baselines.py``.

Deterministic — re-running produces byte-identical files (modulo whitespace).

Output layout (relative to this file's parent):

    queries/{class}/{horizon}/q{NN}.json

Each JSON has the schema:

    {
      "id": "<class>__<horizon>__<NN>",
      "memory_class": "<class>",
      "horizon": "<horizon>",
      "horizon_seconds": <int>,
      "query": "<text>",
      "target_memory_id": "mem-<class>-<horizon>-<NN>",
      "memory_text": "<text>",
      "memory_confidence_at_ingest": 1.0,
      "expected_band": {"min": <float>, "max": <float>}
    }

The expected_band is the analytically-computed RAVEN_v2 weight (max(floor,
0.5 ** (age / half_life))) ± 0.02 — i.e. the pass/fail target for the
RAVEN_v2 baseline on this scenario. Tightening the band would require
floating-point identity, which we deliberately do NOT require so the
suite is portable across math implementations.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
QUERIES = ROOT / "queries"
SCORING = ROOT / "scoring"


# Mirrors raven.decay.policies (kept in sync manually — drift is caught by
# tests in tests/test_decay.py).
POLICIES = {
    "factual_short": {"half_life_seconds": 86_400.0, "floor_confidence": 0.10},
    "factual_long": {"half_life_seconds": 2_592_000.0, "floor_confidence": 0.20},
    "preference": {"half_life_seconds": 7_776_000.0, "floor_confidence": 0.30},
    "transactional": {"half_life_seconds": 14_400.0, "floor_confidence": 0.05},
    "contextual": {"half_life_seconds": 604_800.0, "floor_confidence": 0.15},
    "identity": {"half_life_seconds": None, "floor_confidence": 0.50},
}

HORIZONS = {
    "1h": 3600,
    "1d": 86_400,
    "1w": 604_800,
    "1m": 2_592_000,
    "1y": 31_536_000,
    "5y": 5 * 31_536_000,
}

# Query / memory templates per class. Each list is exactly 10 entries — one
# per horizon-slot — so the corpus stays evenly-balanced. Templates are
# parameterised with `{horizon}` for variety but the filename horizon stays
# deterministic.
TEMPLATES = {
    "factual_short": [
        ("What time is the standup?", "Standup is at 9:30am today."),
        ("Where is lunch tonight?", "Lunch is at 7pm at the corner cafe."),
        ("What is today's room?", "Today's meeting is in conference room B."),
        ("What is the current build version?", "Build 2026.04.27 is live."),
        ("Who is on call today?", "Alice is on call today."),
        ("What is the current ticket count?", "There are 12 open tickets right now."),
        ("What is today's deploy window?", "Deploy window is 2-3pm today."),
        ("What is the conference call number?", "Dial 555-0123 for today's call."),
        ("Which slide deck is current?", "Deck v17 is the live deck this week."),
        ("Who is the on-call engineer?", "Ben is on-call this rotation."),
    ],
    "factual_long": [
        ("Where is the Eiffel Tower?", "The Eiffel Tower is in Paris."),
        ("What is the company HQ?", "Headquarters is at 123 Main St."),
        ("Who founded the company?", "The company was founded by Carol in 2008."),
        ("What is the database engine?", "The primary database is PostgreSQL."),
        ("What is the project codename?", "The codename is RAVEN."),
        ("What language was the project written in?", "The project is written in Python."),
        ("What is the team's main repository?", "The main repo is github.com/jourdanlabs/raven."),
        ("Who is the technical lead?", "Dana is the technical lead."),
        ("What is the support email?", "Support email is help@example.com."),
        ("Which cloud provider is used?", "We deploy to AWS us-east-1."),
    ],
    "preference": [
        ("What coffee do I like?", "I prefer dark roast coffee, no sugar."),
        ("What is my favourite editor?", "My favourite editor is Vim."),
        ("Do I like chocolate?", "I love dark chocolate."),
        ("What music do I prefer?", "I prefer jazz when working."),
        ("Do I drink wine?", "I prefer red wine over white."),
        ("Which sport do I follow?", "My favourite sport is basketball."),
        ("Do I read fiction?", "I prefer non-fiction over fiction."),
        ("What kind of food do I like?", "I like spicy Thai food."),
        ("Which OS do I prefer?", "I prefer macOS for development."),
        ("Which terminal do I use?", "I prefer iTerm2 over the default Terminal."),
    ],
    "transactional": [
        ("What was paid last?", "Paid $50 to the contractor."),
        ("What was ordered?", "Ordered 3 boxes of supplies."),
        ("Who shipped a package?", "Shipped 2 packages to the warehouse."),
        ("What was delivered?", "Delivered 5 cases of paper."),
        ("Who was billed?", "Invoiced $1200 to client A."),
        ("Who was paid?", "Paid $800 to vendor B."),
        ("What was bought?", "Bought 4 monitors for the office."),
        ("Who returned an order?", "Returned 1 keyboard to the supplier."),
        ("What was transferred?", "Transferred $500 to checking."),
        ("What was received?", "Received 6 boxes of inventory."),
    ],
    "contextual": [
        ("Where am I working from today?", "Working from the SF office today."),
        ("What is the weather context?", "It's raining in the city."),
        ("Who am I meeting with?", "Meeting with the design team."),
        ("What is the current sprint?", "We're in sprint 42 this week."),
        ("Who joined the call?", "Jane joined the standup call."),
        ("What is the current focus?", "Focusing on the migration this week."),
        ("Who is travelling?", "The CTO is travelling this week."),
        ("Which room am I in?", "I'm in room 401 right now."),
        ("Which dashboard is open?", "I have the metrics dashboard open."),
        ("Which Slack channel is active?", "#incident-response is the active channel."),
    ],
    "identity": [
        ("Who is Alice?", "Alice is the CEO of Acme Corp."),
        ("Who is my wife?", "My wife is Sarah."),
        ("Who am I?", "I am Leland Jourdan."),
        ("Who is the founder?", "Carol is the founder of the company."),
        ("Who is the CTO?", "Bob is the CTO of Acme Corp."),
        ("Who is my doctor?", "My doctor is Dr. Patel."),
        ("Who is the VP of engineering?", "Eve is the VP of engineering."),
        ("Who is my mother?", "My mother is Marie."),
        ("Who is the chair?", "Frank is the board chair."),
        ("Who is the lead investor?", "Grace is our lead investor."),
    ],
}


def expected_weight(memory_class: str, horizon_seconds: int) -> float:
    p = POLICIES[memory_class]
    floor = p["floor_confidence"]
    hl = p["half_life_seconds"]
    if hl is None:
        return 1.0  # identity: no decay; weight is confidence_at_ingest = 1.0
    raw = math.pow(0.5, horizon_seconds / hl)
    return max(floor, raw)


DECAYING_CLASSES = (
    "factual_short",
    "factual_long",
    "preference",
    "transactional",
    "contextual",
)


def build() -> dict:
    """Generate every query JSON and return a manifest dict."""
    if QUERIES.exists():
        # Wipe so re-runs are deterministic even if the prior run left junk.
        for child in sorted(QUERIES.glob("**/*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
    QUERIES.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    # 5 decaying classes × 6 horizons × 10 queries = 300.
    for cls in DECAYING_CLASSES:
        templates = TEMPLATES[cls]
        assert len(templates) == 10, f"need 10 templates for {cls}"
        for horizon, h_seconds in HORIZONS.items():
            cls_dir = QUERIES / cls / horizon
            cls_dir.mkdir(parents=True, exist_ok=True)
            for i, (q_text, m_text) in enumerate(templates):
                qid = f"{cls}__{horizon}__{i:02d}"
                target_id = f"mem-{cls}-{horizon}-{i:02d}"
                w = expected_weight(cls, h_seconds)
                rec = {
                    "id": qid,
                    "memory_class": cls,
                    "horizon": horizon,
                    "horizon_seconds": h_seconds,
                    "query": q_text,
                    "target_memory_id": target_id,
                    "memory_text": m_text,
                    "memory_confidence_at_ingest": 1.0,
                    "expected_band": {
                        "min": round(max(0.0, w - 0.02), 6),
                        "max": round(min(1.0, w + 0.02), 6),
                    },
                }
                out_path = cls_dir / f"q{i:02d}.json"
                out_path.write_text(
                    json.dumps(rec, indent=2, sort_keys=True) + "\n"
                )
                manifest.append(rec)

    # Identity scenarios: 10 queries, no horizon split (no decay anyway).
    # Stored separately so they don't inflate the 300-query matrix count.
    id_dir = QUERIES / "identity" / "no_horizon"
    id_dir.mkdir(parents=True, exist_ok=True)
    for i, (q_text, m_text) in enumerate(TEMPLATES["identity"]):
        qid = f"identity__no_horizon__{i:02d}"
        rec = {
            "id": qid,
            "memory_class": "identity",
            "horizon": "no_horizon",
            "horizon_seconds": 0,
            "query": q_text,
            "target_memory_id": f"mem-identity-{i:02d}",
            "memory_text": m_text,
            "memory_confidence_at_ingest": 1.0,
            # Identity = no decay; weight stays at confidence_at_ingest = 1.0.
            "expected_band": {"min": 0.98, "max": 1.0},
        }
        (id_dir / f"q{i:02d}.json").write_text(
            json.dumps(rec, indent=2, sort_keys=True) + "\n"
        )
        manifest.append(rec)

    # Deterministic checksum of the corpus directory tree (filenames + bytes).
    h = hashlib.sha256()
    for path in sorted(QUERIES.glob("**/*")):
        if path.is_file():
            h.update(str(path.relative_to(QUERIES)).encode())
            h.update(b"\0")
            h.update(path.read_bytes())
    digest = h.hexdigest()
    return {"count": len(manifest), "sha256": digest, "manifest": manifest}


def main() -> None:
    summary = build()
    matrix_count = sum(
        1 for r in summary["manifest"] if r["memory_class"] != "identity"
    )
    identity_count = sum(
        1 for r in summary["manifest"] if r["memory_class"] == "identity"
    )
    print(json.dumps(
        {
            "matrix_count": matrix_count,
            "identity_count": identity_count,
            "total": summary["count"],
            "sha256": summary["sha256"],
        },
        indent=2,
    ))
    # Write a CHECKPOINT.md alongside the corpus.
    cp = ROOT / "CHECKPOINT.md"
    cp.write_text(
        "# DECAY benchmark — checkpoint\n\n"
        f"- matrix queries: {matrix_count} "
        "(5 decaying classes × 6 horizons × 10 queries)\n"
        f"- identity queries: {identity_count} (no-decay outlier scenarios)\n"
        f"- total: {summary['count']}\n"
        f"- horizons: 1h, 1d, 1w, 1m, 1y, 5y\n"
        f"- sha256 (corpus dir): `{summary['sha256']}`\n\n"
        "Regenerate: `python corpus/decay_benchmark/build_corpus.py`\n"
    )


if __name__ == "__main__":
    main()
