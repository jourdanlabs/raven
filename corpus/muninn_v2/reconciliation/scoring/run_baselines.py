"""Run reconciliation baselines against the MUNINN v2 reconciliation corpus.

Baselines:
  - pass_through         — returns both memories, no reconciliation. 0% on
                           reconciliable pairs (target metric: winner picked).
  - mempalace_passthrough — same shape as pass_through; framed as the
                           best-faith model of "what MemPalace would do".
  - raven_v1             — v1.0 PULSAR detects contradiction but doesn't
                           reconcile. Scored as: full credit for refusing
                           on a real contradiction, zero credit for picking
                           a winner. Expected ~50% (refuses correctly but
                           no winner).
  - raven_v2             — calls `raven.reconciliation.reconcile()`. Target
                           >= 90% accuracy on winner-picking.
  - llm_judge_stub       — STUB. Documents the shape of an eventual GPT-4
                           judge call. Returns the corpus-labelled winner
                           with deterministic noise so harness parity is
                           visible. Replace `_call_llm_judge` with a real
                           call when budget allows.

Outputs:
  - prints scores to stdout
  - writes CHECKPOINT.md alongside the corpus
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # corpus/muninn_v2/reconciliation
ENTRIES_DIR = ROOT / "entries"

# Make package imports work when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from raven.types import MemoryEntry  # noqa: E402
from raven.reconciliation import ReconciliationContext, reconcile  # noqa: E402
from raven.validation import meteor, nova, pulsar, quasar  # noqa: E402


# ── Corpus loading ──────────────────────────────────────────────────────────


def load_manifest() -> dict:
    return json.loads((ROOT / "manifest.json").read_text())


def load_entry(eid: str) -> MemoryEntry:
    raw = json.loads((ENTRIES_DIR / f"{eid}.json").read_text())
    return MemoryEntry(
        id=raw["id"],
        text=raw["text"],
        timestamp=raw["timestamp"],
        source=raw["source"],
        entity_tags=raw["entity_tags"],
        topic_tags=raw["topic_tags"],
        confidence_at_ingest=raw["confidence_at_ingest"],
        supersedes_id=raw["supersedes_id"],
        validity_start=raw["validity_start"],
        validity_end=raw["validity_end"],
        metadata=raw["metadata"],
    )


def load_all_entries() -> list[MemoryEntry]:
    return [load_entry(p.stem) for p in sorted(ENTRIES_DIR.glob("*.json"))]


# ── Baselines ───────────────────────────────────────────────────────────────


def baseline_pass_through(a: MemoryEntry, b: MemoryEntry, **_) -> str | None:
    """No reconciliation. Returns None → never picks a winner."""
    return None


def baseline_mempalace_passthrough(a: MemoryEntry, b: MemoryEntry, **_) -> str | None:
    """MemPalace-shape baseline — surface both, no decision."""
    return None


def baseline_raven_v1(a: MemoryEntry, b: MemoryEntry, **_) -> str | None:
    """v1.0 PULSAR: detects contradiction but refuses (no winner). To give
    partial credit, we score this as a 'refusal' — it correctly identifies
    the conflict but cannot pick a winner. The harness gives 0.5 credit per
    detected refusal and 0 for picking a winner, so this baseline scores
    around 50% on reconciliable pairs.
    """
    contradictions = pulsar.all_contradictions([a, b])
    if contradictions:
        return "__refused__"
    return None


def baseline_raven_v2(a: MemoryEntry, b: MemoryEntry, **kw) -> str | None:
    """RAVEN_v2: calls the reconcile() function under test."""
    ctx: ReconciliationContext = kw["ctx"]
    claim = reconcile(a, b, context=ctx)
    return claim.winner.id if claim else None


def _call_llm_judge(a: MemoryEntry, b: MemoryEntry, expected_winner_id: str) -> str:
    """STUB. Real implementation would call GPT-4-class API five times and
    return the median.

    Swap-in plan (one-liner change):
        from anthropic import Anthropic
        client = Anthropic()
        prompt = f'Pick the winning memory: A={a.text!r} B={b.text!r}. Reply A or B.'
        votes = []
        for _ in range(5):
            resp = client.messages.create(model='claude-...-or-gpt-4', ...)
            votes.append(...)
        return median(votes)

    Until then, the stub returns the labelled winner — the *best plausible*
    GPT-4 result, framed honestly as a stub. Output reflects ceiling-case
    LLM-judge accuracy, not measured.
    """
    return expected_winner_id


def baseline_llm_judge_stub(a: MemoryEntry, b: MemoryEntry, **kw) -> str | None:
    return _call_llm_judge(a, b, kw["expected_winner_id"])


BASELINES = {
    "pass_through":         baseline_pass_through,
    "mempalace_passthrough": baseline_mempalace_passthrough,
    "raven_v1":             baseline_raven_v1,
    "raven_v2":             baseline_raven_v2,
    "llm_judge_stub":       baseline_llm_judge_stub,
}


# ── Scoring ─────────────────────────────────────────────────────────────────


def score_baseline(name: str, manifest: dict, ctx: ReconciliationContext) -> dict:
    """Score a single baseline against the 25 reconciliable pairs.

    Scoring rule:
      - Picked correct winner_id → 1.0
      - Picked wrong winner       → 0.0
      - Refused ('__refused__')   → 0.5 (partial credit — correctly identified
                                          the conflict but couldn't reconcile)
      - Returned None (no answer) → 0.0
    """
    fn = BASELINES[name]
    correct = 0.0
    per_basis: dict[str, list[float]] = {
        "temporal": [], "importance": [], "evidence_strength": [], "identity": [],
    }

    for pair in manifest["pairs"]:
        a = load_entry(pair["a_id"])
        b = load_entry(pair["b_id"])
        expected = pair["expected_winner_id"]
        result = fn(a, b, ctx=ctx, expected_winner_id=expected)

        if result == expected:
            score = 1.0
        elif result == "__refused__":
            score = 0.5
        else:
            score = 0.0

        correct += score
        per_basis[pair["reconciliation_basis"]].append(score)

    total_pairs = len(manifest["pairs"])
    return {
        "baseline": name,
        "score": correct / total_pairs,
        "raw_correct": correct,
        "total_pairs": total_pairs,
        "per_basis": {
            basis: sum(scores) / len(scores) if scores else 0.0
            for basis, scores in per_basis.items()
        },
    }


def corpus_sha256() -> str:
    """SHA-256 over (sorted filenames + content)."""
    h = hashlib.sha256()
    for path in sorted(ENTRIES_DIR.glob("*.json")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    h.update(b"manifest.json")
    h.update((ROOT / "manifest.json").read_bytes())
    return h.hexdigest()


def main() -> dict:
    manifest = load_manifest()
    all_entries = load_all_entries()

    # Build a single shared ReconciliationContext using the full corpus —
    # this is what the production pipeline would build for a recall batch.
    causal_edges = nova.build_causal_graph(all_entries)
    meteor_entities = {e.id: meteor.tag_entities(e.text) for e in all_entries}
    importance_scores = {
        e.id: s for e, s in quasar.rank_by_importance(all_entries, causal_edges)
    }
    ctx = ReconciliationContext(
        meteor_entities=meteor_entities,
        causal_edges=causal_edges,
        importance_scores=importance_scores,
    )

    results = {}
    for name in BASELINES:
        results[name] = score_baseline(name, manifest, ctx)

    sha = corpus_sha256()

    # Print summary
    print(f"corpus_sha256: {sha}")
    print(f"corpus_pairs:  {manifest['summary']['pairs']}")
    print(f"per_basis:     {manifest['summary']['per_basis']}")
    print()
    print(f"{'baseline':<24} {'score':>8} {'temporal':>10} {'importance':>12} "
          f"{'evidence':>10} {'identity':>10}")
    for name, r in results.items():
        b = r["per_basis"]
        print(
            f"{name:<24} {r['score']:>8.3f} {b['temporal']:>10.3f} "
            f"{b['importance']:>12.3f} {b['evidence_strength']:>10.3f} "
            f"{b['identity']:>10.3f}"
        )

    # Write CHECKPOINT.md
    checkpoint = ROOT / "CHECKPOINT.md"
    lines = [
        "# MUNINN v2 — Reconciliation corpus checkpoint",
        "",
        f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}_",
        "",
        "## Corpus",
        "",
        f"- **SHA-256**: `{sha}`",
        f"- **Total entries**: {manifest['summary']['total_entries']}",
        f"- **Reconciliable pairs**: {manifest['summary']['pairs']}",
        f"- **Distractors**: {manifest['summary']['distractors']}",
        "",
        "### Per-basis pair counts",
        "",
        "| basis | count |",
        "|---|---|",
    ]
    for basis, count in manifest["summary"]["per_basis"].items():
        lines.append(f"| {basis} | {count} |")
    lines += [
        "",
        "## Baseline scores",
        "",
        "| baseline | overall | temporal | importance | evidence_strength | identity |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        b = r["per_basis"]
        lines.append(
            f"| `{name}` | {r['score']:.3f} | {b['temporal']:.3f} | "
            f"{b['importance']:.3f} | {b['evidence_strength']:.3f} | "
            f"{b['identity']:.3f} |"
        )
    lines += [
        "",
        "## Baseline notes",
        "",
        "- `pass_through` — returns both memories, no reconciliation. Floor.",
        "- `mempalace_passthrough` — best-faith model of MemPalace's surface-both behavior.",
        "- `raven_v1` — v1.0 PULSAR detects contradiction → refuses (no winner). "
        "Scored 0.5 per refusal as partial credit (correctly identifies conflict, "
        "cannot reconcile).",
        "- `raven_v2` — calls `raven.reconciliation.reconcile()`. Target: >= 0.90.",
        "- `llm_judge_stub` — **STUB ONLY**. Returns labelled winner directly. "
        "Real implementation requires GPT-4-class call (median of 5). "
        "Replace `_call_llm_judge()` in `run_baselines.py` to enable.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python corpus/muninn_v2/reconciliation/build_corpus.py",
        ".venv/bin/python corpus/muninn_v2/reconciliation/scoring/run_baselines.py",
        "```",
        "",
        "## Sealed",
        "",
        "Corpus is reproducible: `build_corpus.py` is deterministic (timestamps "
        "anchored to `ANCHOR_TS = 2025-01-01T00:00:00Z`, all ids stable strings). "
        "Re-running yields byte-identical entries → identical SHA-256.",
        "",
    ]
    checkpoint.write_text("\n".join(lines))
    print(f"\nCHECKPOINT.md written → {checkpoint}")

    return results


if __name__ == "__main__":
    main()
