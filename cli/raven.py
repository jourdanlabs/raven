#!/usr/bin/env python3
"""RAVEN CLI — AI memory built for trust."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import click

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

from raven.pipeline import RAVENPipeline
from raven.storage.store import RAVENStore
from raven.types import MemoryEntry

_DEFAULT_DB = os.path.expanduser("~/.raven/raven.db")
console = Console() if _RICH else None

G = "\x1b[38;5;220m\x1b[1m"
T = "\x1b[38;5;51m"
D = "\x1b[2m"
R = "\x1b[38;5;196m"
GR = "\x1b[38;5;46m"
Z = "\x1b[0m"


def _print(msg: str) -> None:
    click.echo(msg)


def _get_pipeline(db: str) -> RAVENPipeline:
    os.makedirs(os.path.dirname(db), exist_ok=True)
    store = RAVENStore(db_path=db)
    return RAVENPipeline(store)


@click.group()
@click.version_option("1.0.0", prog_name="RAVEN")
def main() -> None:
    """RAVEN — AI memory built for trust."""


@main.command()
@click.argument("query")
@click.option("--db", default=_DEFAULT_DB, help="Path to RAVEN database.")
@click.option("--top", default=5, help="Max approved memories to show.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def recall(query: str, db: str, top: int, as_json: bool) -> None:
    """Query RAVEN memory and run the full validation pipeline."""
    pipeline = _get_pipeline(db)
    response = pipeline.recall(query)

    if as_json:
        click.echo(json.dumps({
            "query": query,
            "status": response.status,
            "confidence": round(response.overall_confidence, 4),
            "approved": [
                {"id": s.entry.id, "text": s.entry.text[:200], "score": round(s.score, 4)}
                for s in response.approved_memories[:top]
            ],
            "conflicts": len(response.flagged_contradictions),
            "rejected": len(response.rejected_memories),
        }, indent=2))
        return

    pct = f"{response.overall_confidence * 100:.1f}%"
    if response.status == "APPROVED":
        status_str = f"{GR}✅ APPROVED{Z}"
    elif response.status == "CONDITIONAL":
        status_str = f"{G}⚠️  CONDITIONAL{Z}"
    elif response.status == "REFUSED":
        status_str = f"{R}🚫 REFUSED{Z}"
    else:
        status_str = f"{R}❌ REJECTED{Z}"

    _print(f"\n{G}◆ RAVEN{Z} — Query: {T}\"{query}\"{Z}\n")
    _print(f"{status_str} {D}({pct}){Z}")
    _print(D + "─" * 50 + Z)

    if response.approved_memories:
        _print(f"\n{G}APPROVED ({len(response.approved_memories)}){Z}")
        for sm in response.approved_memories[:top]:
            preview = sm.entry.text[:120].replace("\n", " ").strip()
            tail = "..." if len(sm.entry.text) > 120 else ""
            date_str = time.strftime("%Y-%m-%d", time.localtime(sm.entry.timestamp))
            _print(f"  {T}◆{Z} {preview}{tail}")
            _print(f"    {D}{date_str} · score: {sm.score:.2f} · src: {sm.entry.source}{Z}")

    if response.flagged_contradictions:
        _print(f"\n{G}CONFLICTS ({len(response.flagged_contradictions)}){Z}")
        for c in response.flagged_contradictions[:3]:
            _print(f"  {R}✕{Z} {c.description}")

    if response.refused():
        _print(f"\n{R}REFUSED{Z} — no trustworthy answer found for this query.")

    t = response.pipeline_trace
    _print(
        f"\n{D}METEOR:{t.meteor_entities}e  NOVA:{t.nova_edges}edges  "
        f"PULSAR:{t.pulsar_conflicts}conflicts  QUASAR:{t.quasar_ranked}ranked  "
        f"AURORA:{t.aurora_approved}/{t.aurora_approved + t.aurora_rejected} approved  "
        f"{t.latency_ms:.0f}ms{Z}\n"
    )


@main.command()
@click.option("--db", default=_DEFAULT_DB)
def status(db: str) -> None:
    """Show RAVEN memory store stats."""
    pipeline = _get_pipeline(db)
    n = pipeline.store.count()
    _print(f"\n{G}◆ RAVEN{Z} — Memory Status")
    _print(f"  {T}Database:{Z} {db}")
    _print(f"  {T}Total memories:{Z} {G}{n:,}{Z}\n")


@main.command()
@click.argument("text")
@click.option("--db", default=_DEFAULT_DB)
@click.option("--source", default="cli")
@click.option("--supersedes", default=None, help="ID of entry this supersedes.")
def remember(text: str, db: str, source: str, supersedes: str | None) -> None:
    """Store a new memory."""
    pipeline = _get_pipeline(db)
    from raven.validation.meteor import tag_entities
    entry = MemoryEntry(
        id=str(uuid.uuid4()),
        text=text,
        timestamp=time.time(),
        source=source,
        entity_tags=tag_entities(text),
        supersedes_id=supersedes,
    )
    entry_id = pipeline.ingest(entry)
    _print(f"{GR}✓{Z} Stored memory {D}{entry_id}{Z}")


@main.command()
@click.argument("jsonl_file", type=click.Path(exists=True))
@click.option("--db", default=_DEFAULT_DB)
def ingest(jsonl_file: str, db: str) -> None:
    """Bulk ingest memories from a JSONL file (one MemoryEntry-compatible JSON per line)."""
    pipeline = _get_pipeline(db)
    count = 0
    with open(jsonl_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            entry = MemoryEntry(
                id=obj.get("id") or str(uuid.uuid4()),
                text=obj["text"],
                timestamp=obj.get("timestamp", time.time()),
                source=obj.get("source", "import"),
                entity_tags=obj.get("entity_tags", []),
                topic_tags=obj.get("topic_tags", []),
                confidence_at_ingest=obj.get("confidence", 1.0),
                supersedes_id=obj.get("supersedes_id"),
                metadata=obj.get("metadata", {}),
            )
            pipeline.ingest(entry)
            count += 1
    _print(f"{GR}✓{Z} Ingested {count:,} memories from {jsonl_file}")


if __name__ == "__main__":
    main()
