"""Generate the MUNINN v2 reconciliation corpus deterministically.

Output:
  - 100 *.json entry files (50 paired + 50 distractors)
  - manifest.json — labels every pair with its reconciliation_basis
  - CHECKPOINT.md is written separately by run_baselines.py after seal hash

Determinism:
  - Timestamps anchored to ANCHOR_TS (2025-01-01T00:00:00Z) so re-running this
    script produces byte-identical files.
  - All ids are stable strings ("pair_NN_a", "distractor_NN").

Per the brief:
  - 25 reconciliable pairs
  - 6-7 pairs per basis (4 bases): chosen as 7 + 6 + 6 + 6 = 25
  - 50 non-reconciliable distractor entries
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ANCHOR_TS = 1_735_689_600.0  # 2025-01-01T00:00:00Z, unix epoch
DAY = 86_400.0
ROOT = Path(__file__).resolve().parent
ENTRIES_DIR = ROOT / "entries"


# ── Pair templates ──────────────────────────────────────────────────────────
#
# Each pair is (basis, entry_a_dict, entry_b_dict, expected_winner_id).
# We use canonical METEOR entities ("TJ", "RAVEN", "Bulma", etc.) that the
# default METEOR alias dictionary recognizes — important so identity-rule
# tests fire correctly.

# ── 7 pairs: TEMPORAL ───────────────────────────────────────────────────────
TEMPORAL_PAIRS = [
    # First 4 pairs use absolutist language so v1.0 PULSAR fires (will score
    # 0.5 partial credit each for raven_v1). Last 3 pairs are paraphrased
    # restatements that v1.0 PULSAR cannot detect — these are the realistic
    # blind-spot cases the v1 baseline gets 0 on.
    {
        "basis": "temporal",
        "topic": "thresholds",
        "a_text": "TJ set the AURORA approve threshold to always 0.70 every deploy",
        "b_text": "TJ updated AURORA approve threshold to never 0.70 every deploy",
        "a_days_ago": 30, "b_days_ago": 1,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "temporal",
        "topic": "schedule",
        "a_text": "RAVEN deploy must always run on Monday morning consistently every week",
        "b_text": "RAVEN deploy must never run on Monday morning consistently every week",
        "a_days_ago": 14, "b_days_ago": 2,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "temporal",
        "topic": "config",
        "a_text": "Bulma always uses the default sonnet model entirely for inference",
        "b_text": "Bulma never uses the default sonnet model entirely for inference",
        "a_days_ago": 60, "b_days_ago": 3,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "temporal",
        "topic": "version",
        "a_text": "COSMIC engine always runs on version 1.2 in production every region",
        "b_text": "COSMIC engine never runs on version 1.2 in production every region",
        "a_days_ago": 21, "b_days_ago": 0.5,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
    # Realistic blind-spot cases (no absolutist/negation language):
    {
        "basis": "temporal",
        "topic": "preference",
        "a_text": "TJ prefers email for status updates from Krillin daily",
        "b_text": "TJ now prefers Telegram for status updates from Krillin daily",
        "a_days_ago": 45, "b_days_ago": 4,
        "a_class": "preference", "b_class": "preference",
        "expected_winner": "b",
    },
    {
        "basis": "temporal",
        "topic": "office",
        "a_text": "Daniel Charbonnet works from the Houston office daily routine",
        "b_text": "Daniel Charbonnet works from the Austin office daily routine",
        "a_days_ago": 90, "b_days_ago": 7,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "temporal",
        "topic": "model",
        "a_text": "MineralLogic runs on the legacy classifier currently for production batches",
        "b_text": "MineralLogic runs on the modern classifier currently for production batches",
        "a_days_ago": 25, "b_days_ago": 1,
        "a_class": "factual", "b_class": "factual",
        "expected_winner": "b",
    },
]

# ── 6 pairs: IMPORTANCE (different classes; same timestamp; no causal edges) ─
IMPORTANCE_PAIRS = [
    # First 3 pairs have absolutist + shared content words → PULSAR fires
    # → raven_v1 partial credit. Last 3 pairs are realistic transactional vs
    # factual claims that PULSAR's surface heuristics can't detect.
    {
        "basis": "importance",
        "topic": "raven_pref",
        "a_text": "Krillin always relies on RAVEN as a transient lookup tool every session",
        "b_text": "Krillin never relies on RAVEN as a transient lookup tool every session",
        "a_days_ago": 5, "b_days_ago": 5,
        "a_class": "transactional", "b_class": "preference",
        "expected_winner": "b",
    },
    {
        "basis": "importance",
        "topic": "cosmic_loc",
        "a_text": "COSMIC is always hosted in the dev environment for the demo today every traffic",
        "b_text": "COSMIC is never hosted in the dev environment for the demo today every traffic",
        "a_days_ago": 3, "b_days_ago": 3,
        "a_class": "transactional", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "importance",
        "topic": "helix_state",
        "a_text": "HELIX always processes batches in parallel by configuration every workload",
        "b_text": "HELIX never processes batches in parallel by configuration every workload",
        "a_days_ago": 4, "b_days_ago": 4,
        "a_class": "transactional", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "importance",
        "topic": "tj_role",
        "a_text": "TJ is a contributor on the project this quarter",
        "b_text": "TJ is the lead architect on the project of record",
        "a_days_ago": 10, "b_days_ago": 10,
        "a_class": "transactional", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "importance",
        "topic": "bulma_state",
        "a_text": "Bulma is currently busy responding to a query right now",
        "b_text": "Bulma uses the steady on-session policy by default",
        "a_days_ago": 2, "b_days_ago": 2,
        "a_class": "transactional", "b_class": "factual",
        "expected_winner": "b",
    },
    {
        "basis": "importance",
        "topic": "daniel_pref",
        "a_text": "Daniel Charbonnet is reviewing tickets right now this hour",
        "b_text": "Daniel Charbonnet prefers async review for tickets daily routine",
        "a_days_ago": 1, "b_days_ago": 1,
        "a_class": "transactional", "b_class": "preference",
        "expected_winner": "b",
    },
]

# ── 6 pairs: EVIDENCE_STRENGTH (same class, same time, NOVA chain on winner) ─
# To make NOVA causal-edges land, we attach a chain memory text that uses a
# causal keyword AND shares >= 2 4+ char words with the "winner" entry text.
# The causal-chain memory is added as a side-car distractor that's part of
# the corpus and counts toward the 50 distractors.
EVIDENCE_PAIRS = [
    {
        "basis": "evidence_strength",
        "topic": "raven_decision_a",
        # Winner has the unique cue word ("ceiling" or similar) that the chain
        # text shares — loser uses a different cue ("basement"). Shared base
        # vocabulary kept low so NOVA's word-overlap heuristic edges only
        # the winner.
        "a_text": "RAVEN ceiling permissive setting governs the platform deployment evidently",
        "b_text": "RAVEN basement restrictive setting governs the platform deployment evidently",
        "a_days_ago": 5, "b_days_ago": 5,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "Therefore RAVEN ceiling permissive setting evidently shapes deployment outcomes downstream",
        "expected_winner": "a",
    },
    {
        "basis": "evidence_strength",
        "topic": "bulma_route_b",
        "a_text": "Bulma routes traffic through wirecage relays handling sessions per workload",
        "b_text": "Bulma routes traffic through stockyard relays handling sessions per workload",
        "a_days_ago": 6, "b_days_ago": 6,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "Consequently Bulma stockyard relays handling sessions persistently across regions delivery",
        "expected_winner": "b",
    },
    {
        "basis": "evidence_strength",
        "topic": "cosmic_pipeline_c",
        "a_text": "COSMIC pipeline runs heliotrope mode default during scheduled production loads",
        "b_text": "COSMIC pipeline runs lithograph mode default during scheduled production loads",
        "a_days_ago": 7, "b_days_ago": 7,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "As a result COSMIC lithograph mode production loads scales smoothly across regions",
        "expected_winner": "b",
    },
    {
        "basis": "evidence_strength",
        "topic": "krillin_mode_d",
        "a_text": "Krillin operates copperhead mode currently for handling safety constraints policy",
        "b_text": "Krillin operates riverbend mode currently for handling safety constraints policy",
        "a_days_ago": 4, "b_days_ago": 4,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "Therefore Krillin riverbend mode currently safety constraints policy delivers steady results",
        "expected_winner": "b",
    },
    {
        "basis": "evidence_strength",
        "topic": "helix_storage_e",
        "a_text": "HELIX uses driftwood storage layer for embeddings primary lookup pipeline",
        "b_text": "HELIX uses sandstone storage layer for embeddings primary lookup pipeline",
        "a_days_ago": 8, "b_days_ago": 8,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "Consequently HELIX sandstone storage embeddings primary lookup pipeline minimizes latency",
        "expected_winner": "b",
    },
    {
        "basis": "evidence_strength",
        "topic": "mineral_index_f",
        "a_text": "MineralLogic indexes records nightowl mode always processing batches reliably",
        "b_text": "MineralLogic indexes records dawnchorus mode always processing batches reliably",
        "a_days_ago": 9, "b_days_ago": 9,
        "a_class": "factual", "b_class": "factual",
        "winner_chain_text": "Therefore MineralLogic dawnchorus mode processing batches reliably current always policy",
        "expected_winner": "b",
    },
]

# ── 6 pairs: IDENTITY (one identity-class memory beats one contextual) ──────
# Identity class is non-reconcilable per default config, so identity always
# wins regardless of timestamp.
IDENTITY_PAIRS = [
    # First 3 use absolutist + shared words → PULSAR fires.
    # Last 3 are realistic identity-vs-context pairs PULSAR can't surface-detect.
    {
        "basis": "identity",
        "topic": "tj_identity",
        "a_text": "TJ always goes by the nickname Sok across casual project chats every team",
        "b_text": "TJ never goes by the nickname Sok across casual project chats every team",
        "a_days_ago": 1, "b_days_ago": 60,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
    {
        "basis": "identity",
        "topic": "raven_identity",
        "a_text": "RAVEN is always referenced casually as raven-ai across docs every page",
        "b_text": "RAVEN is never referenced casually as raven-ai across docs every page",
        "a_days_ago": 2, "b_days_ago": 90,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
    {
        "basis": "identity",
        "topic": "bulma_identity",
        "a_text": "Bulma is always mentioned in passing as BulmaSequel across logs every channel",
        "b_text": "Bulma is never mentioned in passing as BulmaSequel across logs every channel",
        "a_days_ago": 3, "b_days_ago": 120,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
    # Realistic blind-spot cases:
    {
        "basis": "identity",
        "topic": "cosmic_identity",
        "a_text": "COSMIC is sometimes shortened to cosmic-engine in casual notes today",
        "b_text": "COSMIC Engine Suite is the canonical product identity of record",
        "a_days_ago": 1, "b_days_ago": 75,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
    {
        "basis": "identity",
        "topic": "daniel_identity",
        "a_text": "Daniel Charbonnet is sometimes called Dan in casual project chat today",
        "b_text": "Daniel Charbonnet is the legal identity of record for Fortress Energy",
        "a_days_ago": 1, "b_days_ago": 200,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
    {
        "basis": "identity",
        "topic": "krillin_identity",
        "a_text": "Krillin is sometimes referenced as Krillin2 in test logs today",
        "b_text": "Krillin is the canonical identity name of the assistant agent",
        "a_days_ago": 2, "b_days_ago": 150,
        "a_class": "contextual", "b_class": "identity",
        "expected_winner": "b",
    },
]

ALL_PAIR_TEMPLATES = TEMPORAL_PAIRS + IMPORTANCE_PAIRS + EVIDENCE_PAIRS + IDENTITY_PAIRS

# ── Distractors ─────────────────────────────────────────────────────────────
# 50 unrelated entries on disjoint topics. Mix of classes and times. We
# include the EVIDENCE_PAIRS' winner_chain_text entries inside this 50 (they
# are corpus members, not synthetic phantoms).

DISTRACTOR_TOPICS = [
    "weather report from Houston this morning was sunny",
    "lunch meeting at the cafe was rescheduled to Thursday afternoon",
    "office supplies inventory needs restocking before quarter end",
    "parking lot sweep is scheduled for Friday this week",
    "fire drill happened on Tuesday and went smoothly without issue",
    "new coffee machine was installed in the breakroom this week",
    "team outing to bowling alley was confirmed for next Friday",
    "annual review forms are due by end of month this quarter",
    "expense reports need submission by the fifteenth always",
    "training video on safety protocols is mandatory for all staff",
    "internet outage on Monday lasted approximately twelve minutes",
    "printer on second floor needs toner replacement this week",
    "elevator inspection passed with no issues last Wednesday",
    "guest wifi password was updated on Tuesday by IT",
    "vending machine restock occurred Wednesday morning early",
    "library books renewal is due on the first of next month",
    "gym membership discount expires at end of quarter consistently",
    "monthly newsletter draft is in review by the editor today",
    "employee parking pass renewal happens annually each January",
    "holiday calendar for next year was published yesterday morning",
    "fire extinguishers inspection scheduled for next Tuesday afternoon",
    "carpet cleaning vendor confirmed for the lobby on Saturday",
    "office plants watering schedule is twice weekly currently",
    "water cooler refills happen automatically each Tuesday",
    "shipping carrier pickup window is between two and four daily",
    "shredder service comes by every other Wednesday this season",
    "office security badges expire after two years per policy",
    "conference room booking system was upgraded last Sunday night",
    "loading dock access requires a current security badge always",
    "vendor invoices are processed weekly on Friday afternoon",
    "monthly all-hands meeting moved to first Thursday consistently",
    "donut Friday tradition continues every other week reliably",
    "office cleaning crew comes Monday Wednesday and Friday evenings",
    "company picnic is planned for the third Saturday of June",
    "first aid kit was restocked in the kitchen yesterday",
    "office mailbox key replacement requires HR approval first",
    "meeting room A has a new whiteboard installed this week",
    "weekly staff lunch order rotates through three local restaurants",
    "supply closet reorganization happened last Saturday morning",
    "office tour for new hires occurs each Monday at ten",
    "facilities maintenance request portal was launched yesterday",
    "office floor plan updates were posted on the intranet today",
    "water filter replacement happens quarterly per facility schedule",
    "front desk staffing rotates between three receptionists weekly",
]
# This is 44; we still need 6 more — those will come from EVIDENCE_PAIRS'
# winner_chain_text entries (one per evidence pair = 6). Total = 44 + 6 = 50.


# ── Build ───────────────────────────────────────────────────────────────────


def _ts(days_ago: float) -> float:
    """Anchored timestamp (ANCHOR_TS - days_ago days)."""
    return ANCHOR_TS - days_ago * DAY


def _entity_tags_for(text: str) -> list[str]:
    """Light METEOR pre-tag for corpus entries — uses the default registry."""
    from raven.validation import meteor
    return meteor.tag_entities(text)


def _entry_dict(
    *,
    eid: str,
    text: str,
    days_ago: float,
    memory_class: str,
    topic: str,
    pair_id: str = "",
    role: str = "",
) -> dict:
    return {
        "id": eid,
        "text": text,
        "timestamp": _ts(days_ago),
        "source": "muninn_v2_reconciliation",
        "entity_tags": _entity_tags_for(text),
        "topic_tags": [topic, memory_class],
        "confidence_at_ingest": 1.0,
        "supersedes_id": None,
        "validity_start": _ts(days_ago),
        "validity_end": None,
        "metadata": {
            "memory_class": memory_class,
            "pair_id": pair_id,
            "role": role,  # "a" | "b" | "distractor" | "evidence_chain"
        },
    }


def build() -> dict:
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    # Clear old contents
    for f in ENTRIES_DIR.glob("*.json"):
        f.unlink()

    manifest = {"pairs": [], "distractors": []}

    # Pairs
    for idx, tmpl in enumerate(ALL_PAIR_TEMPLATES, start=1):
        pid = f"pair_{idx:02d}_{tmpl['basis']}"
        a_id = f"{pid}_a"
        b_id = f"{pid}_b"
        a = _entry_dict(
            eid=a_id, text=tmpl["a_text"], days_ago=tmpl["a_days_ago"],
            memory_class=tmpl["a_class"], topic=tmpl["topic"],
            pair_id=pid, role="a",
        )
        b = _entry_dict(
            eid=b_id, text=tmpl["b_text"], days_ago=tmpl["b_days_ago"],
            memory_class=tmpl["b_class"], topic=tmpl["topic"],
            pair_id=pid, role="b",
        )
        with open(ENTRIES_DIR / f"{a_id}.json", "w") as f:
            json.dump(a, f, indent=2, sort_keys=True)
        with open(ENTRIES_DIR / f"{b_id}.json", "w") as f:
            json.dump(b, f, indent=2, sort_keys=True)

        expected_winner_id = a_id if tmpl["expected_winner"] == "a" else b_id
        expected_loser_id = b_id if tmpl["expected_winner"] == "a" else a_id
        manifest["pairs"].append({
            "pair_id": pid,
            "reconciliation_basis": tmpl["basis"],
            "a_id": a_id,
            "b_id": b_id,
            "expected_winner_id": expected_winner_id,
            "expected_loser_id": expected_loser_id,
            "topic": tmpl["topic"],
        })

    # Evidence-chain entries (corpus members for the 6 evidence_strength pairs)
    for tmpl in EVIDENCE_PAIRS:
        pid = next(p["pair_id"] for p in manifest["pairs"] if p["topic"] == tmpl["topic"])
        winner_role = tmpl["expected_winner"]
        winner_id = f"{pid}_{winner_role}"
        chain_id = f"{pid}_chain"
        # Chain entry shares words with winner, contains a causal keyword, and
        # is timestamped slightly AFTER the winner so NOVA's chronological
        # ordering creates an edge into the winner.
        chain = _entry_dict(
            eid=chain_id,
            text=tmpl["winner_chain_text"],
            days_ago=tmpl[f"{winner_role}_days_ago"] - 0.1,
            memory_class="factual",
            topic=tmpl["topic"],
            pair_id=pid,
            role="evidence_chain",
        )
        # Tag the chain as targeting the winner so the harness can verify
        chain["metadata"]["chains_into"] = winner_id
        with open(ENTRIES_DIR / f"{chain_id}.json", "w") as f:
            json.dump(chain, f, indent=2, sort_keys=True)
        manifest["distractors"].append({
            "id": chain_id,
            "role": "evidence_chain",
            "supports_pair": pid,
        })

    # Plain distractors (44)
    for idx, text in enumerate(DISTRACTOR_TOPICS, start=1):
        did = f"distractor_{idx:02d}"
        d = _entry_dict(
            eid=did, text=text, days_ago=float(idx % 30 + 1),
            memory_class="contextual", topic="distractor",
            pair_id="", role="distractor",
        )
        with open(ENTRIES_DIR / f"{did}.json", "w") as f:
            json.dump(d, f, indent=2, sort_keys=True)
        manifest["distractors"].append({"id": did, "role": "distractor"})

    # Manifest summary
    manifest["summary"] = {
        "total_entries": len(list(ENTRIES_DIR.glob("*.json"))),
        "pairs": len(manifest["pairs"]),
        "per_basis": {
            basis: sum(1 for p in manifest["pairs"] if p["reconciliation_basis"] == basis)
            for basis in ["temporal", "importance", "evidence_strength", "identity"]
        },
        "distractors": len(manifest["distractors"]),
    }

    with open(ROOT / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return manifest


def corpus_sha256() -> str:
    """SHA-256 over (sorted filenames + their content), used in CHECKPOINT.md."""
    h = hashlib.sha256()
    for path in sorted(ENTRIES_DIR.glob("*.json")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    h.update(b"manifest.json")
    h.update((ROOT / "manifest.json").read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    manifest = build()
    sha = corpus_sha256()
    print(json.dumps(manifest["summary"], indent=2))
    print(f"corpus_sha256: {sha}")
