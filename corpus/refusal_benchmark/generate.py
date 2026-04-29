"""Generate the refusal benchmark corpus deterministically.

Outputs `corpus/refusal_benchmark/queries.jsonl` and
`corpus/refusal_benchmark/CHECKPOINT.md` with the SHA-256 of the JSONL.

Each query is labeled with:
  - ``query_id``           — stable id (label_##)
  - ``label``              — the *correct* RefusalReason.type RAVEN should
                             produce on this query
  - ``query``              — the query string
  - ``scope_allowlist``    — optional, for scope_violation queries
  - ``setup``              — optional, list of MemoryEntry seeds the harness
                             ingests before running the query
  - ``notes``              — short rationale
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# Generation is fully deterministic — no random calls. We encode each
# scenario template explicitly so the harness can replay the same corpus
# across runs and so a third party can audit the labels.

OUT_DIR = Path(__file__).parent
QUERIES_PATH = OUT_DIR / "queries.jsonl"
CHECKPOINT_PATH = OUT_DIR / "CHECKPOINT.md"

# Anchor a fixed timestamp so generation is reproducible across calendar dates.
# Real-time deltas (e.g., "200 days ago") are encoded as offsets from this anchor.
ANCHOR_TS = 1_730_000_000.0  # 2024-10-27 (deterministic)


# ── Insufficient-evidence (40) ──────────────────────────────────────────────
#
# Empty store + question on a topic RAVEN has no evidence for. The store has
# zero or near-zero relevant memories. RAVEN must produce
# 'insufficient_evidence' as the refusal type.

INSUFFICIENT_QUERIES = [
    "what is the meaning of life",
    "who first climbed mount everest",
    "what's the speed of light in a vacuum",
    "list the periodic table elements alphabetically",
    "what time is dinner tonight",
    "where did i leave my keys this morning",
    "what color was my first car",
    "who was the third roman emperor",
    "what is the GDP of liechtenstein in 2026",
    "name three rare earth metals",
    "what is the airspeed of an unladen swallow",
    "how many sonnets did shakespeare write",
    "describe the process of photosynthesis",
    "what's the capital of estonia",
    "name the founding members of the band rush",
    "what year was the eiffel tower built",
    "what is the boiling point of mercury",
    "who painted the persistence of memory",
    "what's the mass of a hydrogen atom",
    "name the seven wonders of the ancient world",
    "what is the half-life of carbon-14",
    "describe how a transformer neural network works",
    "what is the population of antarctica",
    "name three breeds of welsh sheep",
    "what is the etymology of the word melancholy",
    "what is the deepest known underwater trench",
    "name a famous brutalist architect",
    "what was the first commercial transistor",
    "what year did the apollo missions end",
    "list the prime numbers under one hundred",
    "what is the chemical symbol for tungsten",
    "describe the maillard reaction",
    "what is the cube root of 27",
    "name the first three lunar missions",
    "what is the average rainfall in seattle",
    "who composed the four seasons concertos",
    "what year did saturn get rings",
    "describe the doppler effect briefly",
    "what is the longest word in english",
    "name three breeds of horse from spain",
]

# ── Conflicting evidence (40) ───────────────────────────────────────────────
#
# Setup planted contradictions. The store contains absolutist or negation
# pairs that PULSAR will detect, and the contradictions remain unresolved.

CONFLICT_TEMPLATES = [
    ("the deploy script always succeeds on monday", "the deploy script never succeeds on monday",
     "does the deploy script succeed on monday"),
    ("the api always returns 200 for valid auth", "the api never returns 200 for valid auth",
     "does the api return 200 for valid auth"),
    ("backups always complete by midnight", "backups never complete by midnight",
     "do backups complete by midnight"),
    ("the cron job always fires at 3am", "the cron job never fires at 3am",
     "does the cron job fire at 3am"),
    ("our staging cluster is always healthy", "our staging cluster is never healthy",
     "is staging healthy"),
    ("alice always reviews release notes", "alice never reviews release notes",
     "does alice review release notes"),
    ("the database always replicates within 5s", "the database never replicates within 5s",
     "does the database replicate within 5s"),
    ("we always settle vendor invoices within thirty days", "we never settle vendor invoices within thirty days",
     "do we settle vendor invoices within thirty days"),
    ("the meeting always starts on time", "the meeting never starts on time",
     "does the meeting start on time"),
    ("incidents always page the on-call", "incidents never page the on-call",
     "do incidents page the on-call"),
    ("the build always passes on first try", "the build never passes on first try",
     "does the build pass on first try"),
    ("we always recycle paper", "we never recycle paper",
     "do we recycle paper"),
    ("the office always opens at 8am", "the office never opens at 8am",
     "does the office open at 8am"),
    ("invoices always require two signatures", "invoices never require two signatures",
     "do invoices require two signatures"),
    ("the printer always works on weekends", "the printer never works on weekends",
     "does the printer work on weekends"),
    ("legal always reviews contracts in 48h", "legal never reviews contracts in 48h",
     "does legal review contracts in 48h"),
    ("the cdn always caches images", "the cdn never caches images",
     "does the cdn cache images"),
    ("our ssl certs always renew automatically", "our ssl certs never renew automatically",
     "do ssl certs renew automatically"),
    ("the test suite always finishes in 10 min", "the test suite never finishes in 10 min",
     "does the test suite finish in 10 min"),
    ("metrics always flush every minute", "metrics never flush every minute",
     "do metrics flush every minute"),
    ("the auth service always uses jwt", "the auth service never uses jwt",
     "does the auth service use jwt"),
    ("we always tag releases with semver", "we never tag releases with semver",
     "do we tag releases with semver"),
    ("the queue always drains within 1h", "the queue never drains within 1h",
     "does the queue drain within 1h"),
    ("the etl pipeline always runs at noon", "the etl pipeline never runs at noon",
     "does the etl pipeline run at noon"),
    ("our api always supports oauth", "our api never supports oauth",
     "does our api support oauth"),
    ("the sales team always uses salesforce", "the sales team never uses salesforce",
     "does sales use salesforce"),
    ("the wiki always links to runbooks", "the wiki never links to runbooks",
     "does the wiki link to runbooks"),
    ("morning standup always lasts 15 min", "morning standup never lasts 15 min",
     "does standup last 15 min"),
    ("the on-call always carries a laptop", "the on-call never carries a laptop",
     "does on-call carry a laptop"),
    ("budget reviews always happen quarterly", "budget reviews never happen quarterly",
     "do budget reviews happen quarterly"),
    ("vendors always invoice via email", "vendors never invoice via email",
     "do vendors invoice via email"),
    ("our redis always uses tls", "our redis never uses tls",
     "does our redis use tls"),
    ("the kanban board always reflects reality", "the kanban board never reflects reality",
     "does the kanban board reflect reality"),
    ("hr always sends offer letters by friday", "hr never sends offer letters by friday",
     "does hr send offer letters by friday"),
    ("the changelog always includes contributors", "the changelog never includes contributors",
     "does the changelog include contributors"),
    ("incident retros always happen within 7 days", "incident retros never happen within 7 days",
     "do retros happen within 7 days"),
    ("the design system always ships with tokens", "the design system never ships with tokens",
     "does the design system ship with tokens"),
    ("our backups always live in s3", "our backups never live in s3",
     "do backups live in s3"),
    ("billing always rolls up monthly", "billing never rolls up monthly",
     "does billing roll up monthly"),
    ("the dashboard always loads under 2s", "the dashboard never loads under 2s",
     "does the dashboard load under 2s"),
]


# ── Staleness (40) ──────────────────────────────────────────────────────────
#
# Setup plants memories with timestamps far in the past so ECLIPSE decay
# pushes every weight below the floor.

STALENESS_TOPICS = [
    "release notes for v0.1",
    "old api keys for the legacy service",
    "the original 2014 product roadmap",
    "deprecated cdn config",
    "windows xp deployment guide",
    "perl 5.10 build instructions",
    "old aws region us-east-1 outage notes",
    "original founders' meeting minutes from 2010",
    "first prototype hardware spec",
    "early 2015 marketing brief",
    "obsolete mongodb 2.x migration plan",
    "the discontinued mobile app changelog",
    "phased-out billing provider integration",
    "old jenkins job definitions",
    "long-retired internal blog system",
    "early java 6 build steps",
    "deprecated python 2.7 setup",
    "ancient bug tracker config",
    "old jira workflow from 2013",
    "removed feature flag config",
    "obsolete chef cookbook v1",
    "early kanban tooling notes",
    "retired sso provider settings",
    "old company-wide email banner copy",
    "discontinued chatops bot config",
    "first version of the company handbook",
    "old gradle 2.x build script",
    "long-removed analytics tracking ids",
    "old crm field mappings",
    "the original css framework decisions",
    "early monorepo split plan",
    "discontinued sandbox environment notes",
    "deprecated docker swarm config",
    "old jenkins pipeline syntax",
    "obsolete s3 bucket policy template",
    "early dns provider migration steps",
    "old vpn client install guide",
    "the long-retired internal forum",
    "ancient legacy soap api endpoints",
    "old node.js 4.x runtime guide",
]


# ── Identity ambiguous (40) ────────────────────────────────────────────────
#
# Setup plants memories where multiple distinct canonical entities appear
# and the query doesn't disambiguate between them.

IDENTITY_PAIRS = [
    (("Krillin", "Krillin won the match"),  ("18", "18 won the match"),
     "who won the match"),
    (("Bulma", "Bulma shipped v1"),         ("18", "18 shipped v1"),
     "who shipped v1"),
    (("RAVEN", "RAVEN flagged it"),         ("COSMIC", "COSMIC flagged it"),
     "who flagged it"),
    (("TJ", "TJ approved it"),              ("Charles Jourdan", "Charles Jourdan approved it"),
     "who approved it"),
    (("Daniel Charbonnet", "Daniel paid"),  ("Chris Lynch", "Chris Lynch paid"),
     "who paid"),
    (("MineralLogic", "MineralLogic launched"), ("HELIX", "HELIX launched"),
     "what launched"),
    (("Krillin", "Krillin closed the ticket"), ("Bulma", "Bulma closed the ticket"),
     "who closed the ticket"),
    (("18", "18 reviewed the doc"),         ("Krillin", "Krillin reviewed the doc"),
     "who reviewed the doc"),
    (("MemPalace", "MemPalace deployed"),   ("OMNIS KEY", "OMNIS KEY deployed"),
     "what deployed"),
    (("Bulma", "Bulma owns the runbook"),   ("Raven", "Raven owns the runbook"),
     "who owns the runbook"),
    (("TJ", "TJ left a comment"),           ("Daniel Charbonnet", "Daniel left a comment"),
     "who left a comment"),
    (("Charles Jourdan", "Charles signed"), ("Chris Lynch", "Chris Lynch signed"),
     "who signed"),
    (("RAVEN", "RAVEN raised an alert"),    ("MineralLogic", "MineralLogic raised an alert"),
     "what raised an alert"),
    (("HELIX", "HELIX completed"),          ("OMNIS KEY", "OMNIS KEY completed"),
     "what completed"),
    (("Krillin", "Krillin started"),        ("18", "18 started"),
     "who started"),
    (("Bulma", "Bulma replied"),            ("Krillin", "Krillin replied"),
     "who replied"),
    (("MemPalace", "MemPalace shipped"),    ("RAVEN", "RAVEN shipped"),
     "what shipped"),
    (("18", "18 logged in"),                ("Bulma", "Bulma logged in"),
     "who logged in"),
    (("TJ", "TJ called the meeting"),       ("Chris Lynch", "Chris Lynch called the meeting"),
     "who called the meeting"),
    (("Daniel Charbonnet", "Daniel filed"), ("Charles Jourdan", "Charles filed"),
     "who filed"),
    (("Krillin", "Krillin tagged the build"), ("18", "18 tagged the build"),
     "who tagged the build"),
    (("Bulma", "Bulma documented"),         ("Krillin", "Krillin documented"),
     "who documented"),
    (("MineralLogic", "MineralLogic upgraded"), ("HELIX", "HELIX upgraded"),
     "what upgraded"),
    (("RAVEN", "RAVEN proposed"),           ("COSMIC", "COSMIC proposed"),
     "what proposed"),
    (("18", "18 emailed"),                  ("Bulma", "Bulma emailed"),
     "who emailed"),
    (("TJ", "TJ scheduled"),                ("Daniel Charbonnet", "Daniel scheduled"),
     "who scheduled"),
    (("Chris Lynch", "Chris Lynch published"), ("Charles Jourdan", "Charles published"),
     "who published"),
    (("Krillin", "Krillin pinged"),         ("Bulma", "Bulma pinged"),
     "who pinged"),
    (("HELIX", "HELIX delivered"),          ("OMNIS KEY", "OMNIS KEY delivered"),
     "what delivered"),
    (("18", "18 wrote"),                    ("Krillin", "Krillin wrote"),
     "who wrote"),
    (("Bulma", "Bulma fixed"),              ("18", "18 fixed"),
     "who fixed"),
    (("RAVEN", "RAVEN reviewed"),           ("MemPalace", "MemPalace reviewed"),
     "what reviewed"),
    (("TJ", "TJ acknowledged"),             ("Chris Lynch", "Chris Lynch acknowledged"),
     "who acknowledged"),
    (("Daniel Charbonnet", "Daniel forwarded"), ("Charles Jourdan", "Charles forwarded"),
     "who forwarded"),
    (("Krillin", "Krillin merged"),         ("Bulma", "Bulma merged"),
     "who merged"),
    (("18", "18 archived"),                 ("Bulma", "Bulma archived"),
     "who archived"),
    (("MineralLogic", "MineralLogic released"), ("RAVEN", "RAVEN released"),
     "what released"),
    (("HELIX", "HELIX deferred"),           ("COSMIC", "COSMIC deferred"),
     "what deferred"),
    (("TJ", "TJ proposed"),                 ("Charles Jourdan", "Charles proposed"),
     "who proposed"),
    (("Chris Lynch", "Chris Lynch closed"), ("Daniel Charbonnet", "Daniel closed"),
     "who closed"),
]


# ── Scope violation (40) ────────────────────────────────────────────────────
#
# A `scope_allowlist` is supplied; the query contains tokens outside it.

SCOPE_ALLOWLIST = ["billing", "invoice", "payment"]
SCOPE_QUERIES = [
    "tell me about quantum cryptography",
    "what is the best recipe for paella",
    "describe the rules of cricket",
    "who won the world cup in 2018",
    "explain the theory of plate tectonics",
    "what year did world war one end",
    "list the planets of the solar system",
    "describe the boiling point of nitrogen",
    "name famous renaissance painters",
    "what is the wikipedia page for emu",
    "explain the rules of go",
    "what mountain is taller than k2",
    "describe the migratory pattern of monarch butterflies",
    "list nobel laureates in physics 2010",
    "what is the wavelength of red light",
    "name three breeds of arctic dog",
    "explain the formation of basalt",
    "describe the ottoman empire's rise",
    "what is the chemical formula for caffeine",
    "list the works of shakespeare",
    "name three impressionist painters",
    "what is the population of mongolia",
    "describe the process of fermentation",
    "what is the deepest cave in europe",
    "name three composers from austria",
    "describe the structure of dna",
    "list the colors of the rainbow",
    "what year did rome fall",
    "describe the migration of wildebeest",
    "what is the latitude of reykjavik",
    "describe the hubble space telescope",
    "name three breeds of horse",
    "what is the chemical formula for water",
    "list the wonders of the modern world",
    "describe the great barrier reef",
    "what year did the cold war end",
    "list the works of mozart",
    "describe the formation of glaciers",
    "what is the speed of sound at sea level",
    "name three famous chess grandmasters",
]


# ── Build ──────────────────────────────────────────────────────────────────


def _entry(id: str, text: str, days_ago: float = 1.0, entity_tags=None):
    return {
        "id": id,
        "text": text,
        "timestamp": ANCHOR_TS - days_ago * 86_400,
        "entity_tags": entity_tags or [],
    }


def _build_insufficient(records: list[dict]) -> None:
    for i, q in enumerate(INSUFFICIENT_QUERIES, start=1):
        records.append({
            "query_id": f"insufficient_{i:02d}",
            "label": "insufficient_evidence",
            "query": q,
            "scope_allowlist": None,
            "setup": [],
            "notes": "empty store, generic question",
        })


def _build_conflict(records: list[dict]) -> None:
    for i, (text_a, text_b, query) in enumerate(CONFLICT_TEMPLATES, start=1):
        setup = [
            _entry(f"conflict_{i:02d}_a", text_a, days_ago=1.0),
            _entry(f"conflict_{i:02d}_b", text_b, days_ago=1.0),
        ]
        records.append({
            "query_id": f"conflict_{i:02d}",
            "label": "conflicting_evidence_unresolvable",
            "query": query,
            "scope_allowlist": None,
            "setup": setup,
            "notes": "absolutist contradiction; PULSAR detects, no reconciliation",
        })


def _build_staleness(records: list[dict]) -> None:
    for i, topic in enumerate(STALENESS_TOPICS, start=1):
        # 400 days old; default half-life 30d -> weight ~ 0.5^(400/30) ~ 0.0001
        setup = [_entry(f"stale_{i:02d}_a", topic, days_ago=400.0)]
        records.append({
            "query_id": f"staleness_{i:02d}",
            "label": "staleness_threshold_exceeded",
            "query": topic,
            "scope_allowlist": None,
            "setup": setup,
            "notes": "single very old memory; ECLIPSE decays below floor",
        })


def _build_identity(records: list[dict]) -> None:
    for i, ((tag1, text1), (tag2, text2), query) in enumerate(IDENTITY_PAIRS, start=1):
        setup = [
            _entry(f"identity_{i:02d}_a", text1, days_ago=1.0, entity_tags=[tag1]),
            _entry(f"identity_{i:02d}_b", text2, days_ago=1.0, entity_tags=[tag2]),
        ]
        records.append({
            "query_id": f"identity_{i:02d}",
            "label": "identity_ambiguous",
            "query": query,
            "scope_allowlist": None,
            "setup": setup,
            "notes": "two distinct entity tags, ambiguous query",
        })


def _build_scope(records: list[dict]) -> None:
    for i, q in enumerate(SCOPE_QUERIES, start=1):
        records.append({
            "query_id": f"scope_{i:02d}",
            "label": "scope_violation",
            "query": q,
            "scope_allowlist": list(SCOPE_ALLOWLIST),
            "setup": [],
            "notes": "out-of-scope query against billing-only allowlist",
        })


def build_corpus() -> list[dict]:
    records: list[dict] = []
    _build_insufficient(records)
    _build_conflict(records)
    _build_staleness(records)
    _build_identity(records)
    _build_scope(records)
    return records


def write_corpus(records: list[dict]) -> tuple[Path, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUERIES_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            # Force key-sorted JSON to keep the SHA-256 stable across runs.
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
    sha = hashlib.sha256(QUERIES_PATH.read_bytes()).hexdigest()

    by_label: dict[str, int] = {}
    for r in records:
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1

    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        f.write("# Refusal Benchmark CHECKPOINT\n\n")
        f.write(f"Generated by: `corpus/refusal_benchmark/generate.py`\n\n")
        f.write(f"Total queries: {len(records)}\n\n")
        f.write("Per-label counts:\n")
        for k in sorted(by_label):
            f.write(f"  - {k}: {by_label[k]}\n")
        f.write("\n")
        f.write(f"Anchor timestamp: {ANCHOR_TS} (deterministic)\n\n")
        f.write(f"queries.jsonl SHA-256:\n  {sha}\n")
    return QUERIES_PATH, sha


def main() -> None:
    recs = build_corpus()
    path, sha = write_corpus(recs)
    print(f"Wrote {len(recs)} queries to {path}")
    print(f"SHA-256: {sha}")


if __name__ == "__main__":
    main()
