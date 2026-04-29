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


# ── Capability 1.2 — decay & migration sub-commands ────────────────────────


@main.group()
def decay() -> None:
    """Inspect and manage class-aware decay policies (Capability 1.2)."""


@decay.command("list-policies")
@click.option("--json", "as_json", is_flag=True, help="Output JSON instead of a table.")
def decay_list_policies(as_json: bool) -> None:
    """List all currently registered DecayPolicy objects."""
    from raven.decay import list_decay_policies

    policies = list_decay_policies()
    if as_json:
        click.echo(json.dumps(
            [
                {
                    "name": p.name,
                    "applies_to_class": p.applies_to_class,
                    "half_life_seconds": p.half_life_seconds,
                    "floor_confidence": p.floor_confidence,
                }
                for p in policies
            ],
            indent=2,
        ))
        return
    _print(f"\n{G}* RAVEN{Z} - Registered DecayPolicies\n")
    _print(f"  {T}{'NAME':<18}{'CLASS':<18}{'HALF-LIFE':<18}FLOOR{Z}")
    _print(D + "-" * 70 + Z)
    for p in policies:
        hl = "no decay" if p.half_life_seconds is None else f"{p.half_life_seconds:.0f}s"
        _print(f"  {GR}{p.name:<18}{Z}{T}{p.applies_to_class:<18}{Z}{hl:<18}{p.floor_confidence}")
    _print("")


@decay.command("show-policy")
@click.argument("name")
def decay_show_policy(name: str) -> None:
    """Print one DecayPolicy by memory-class name."""
    from raven.decay import get_decay_policy

    try:
        p = get_decay_policy(name)
    except KeyError as exc:
        _print(f"{R}x{Z} {exc}")
        sys.exit(1)
    click.echo(json.dumps(
        {
            "name": p.name,
            "applies_to_class": p.applies_to_class,
            "half_life_seconds": p.half_life_seconds,
            "floor_confidence": p.floor_confidence,
        },
        indent=2,
    ))


@decay.command("register-policy")
@click.argument("name")
@click.argument("half_life_seconds", type=float)
@click.argument("floor", type=float)
@click.argument("memory_class")
def decay_register_policy(
    name: str, half_life_seconds: float, floor: float, memory_class: str
) -> None:
    """Register a custom DecayPolicy at runtime (process-local).

    Pass ``-1`` for HALF_LIFE_SECONDS to register a no-decay policy
    (mapped to None internally).
    """
    from raven.decay import register_decay_policy
    from raven.types import DecayPolicy

    hl = None if half_life_seconds < 0 else half_life_seconds
    policy = DecayPolicy(
        name=name,
        half_life_seconds=hl,
        floor_confidence=floor,
        applies_to_class=memory_class,
    )
    try:
        register_decay_policy(policy)
    except (TypeError, ValueError) as exc:
        _print(f"{R}x{Z} {exc}")
        sys.exit(1)
    _print(f"{GR}+{Z} Registered DecayPolicy {D}{name}{Z} for class {T}{memory_class}{Z}")


@main.group()
def migrate() -> None:
    """Run / inspect storage migrations (Capability 1.2)."""


@migrate.command("run")
@click.option("--db", default=_DEFAULT_DB)
def migrate_run(db: str) -> None:
    """Apply Capability 1.2 schema migration + heuristic backfill.

    Requires ``RAVEN_RUN_MIGRATIONS=1`` in the environment as a safety
    interlock - the v0 default is opt-in. (Phase 2 will flip the default.)
    """
    if os.environ.get("RAVEN_RUN_MIGRATIONS") != "1":
        _print(
            f"{R}x{Z} Refusing to migrate: set RAVEN_RUN_MIGRATIONS=1 to confirm.\n"
            f"  {D}This is the v0 opt-in safety interlock.{Z}"
        )
        sys.exit(2)
    if not os.path.exists(db):
        _print(f"{R}x{Z} No database at {db}")
        sys.exit(1)

    from raven.storage.migrations import run_migrations

    result = run_migrations(db)
    _print(f"\n{G}* RAVEN{Z} - Migration complete\n")
    _print(f"  {T}schema_changed:{Z}     {result.schema_changed}")
    _print(f"  {T}rows_total:{Z}         {result.rows_total}")
    _print(f"  {T}rows_classified:{Z}    {result.rows_classified}")
    _print(f"  {T}review_required:{Z}    {result.rows_review_required}")
    if result.by_class:
        _print(f"\n  {T}by class:{Z}")
        for cls, n in sorted(result.by_class.items()):
            _print(f"    {GR}{cls:<18}{Z} {n}")
    if result.review_sample:
        _print(f"\n  {T}review sample (first {len(result.review_sample)}):{Z}")
        for row in result.review_sample[:5]:
            _print(f"    {D}- {row['id']} [{row['assigned_class']} @ {row['confidence']}]: {row['text'][:80]}{Z}")
    _print("")


@migrate.command("review-queue")
@click.option("--db", default=_DEFAULT_DB)
@click.option("--limit", default=50, help="Max rows to show.")
def migrate_review_queue(db: str, limit: int) -> None:
    """List rows that the migration flagged ``review_required = 1``."""
    if not os.path.exists(db):
        _print(f"{R}x{Z} No database at {db}")
        sys.exit(1)
    from raven.storage.migrations import review_queue

    rows = review_queue(db, limit=limit)
    if not rows:
        _print(f"{GR}+{Z} Review queue empty")
        return
    _print(f"\n{G}* RAVEN{Z} - Review queue ({len(rows)} rows)\n")
    for row in rows:
        _print(f"  {T}[{row['memory_class']}]{Z} {row['id']}: {D}{(row['text'] or '')[:140]}{Z}")
    _print("")


if __name__ == "__main__":
    main()
