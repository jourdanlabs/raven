"""Phase 2.1 LongMemEval corpus split.

Deterministically partitions the upstream ``longmemeval_oracle.json``
distribution into a calibration set (80 %, used for any tuning during the
Phase 2.1 sprint) and a held-out set (20 %, locked until the single Day 5
validation shot).

The split is shuffle-with-seed (Python ``random.Random(42).shuffle``) so a
peer auditing the methodology can reproduce both partitions byte-for-byte
from the same upstream file. Both partition files are content-addressed by
SHA-256 and the digests are recorded in
``benchmarks/longmemeval/phase2.1_split.md``.

Architectural enforcement of the held-out lock
----------------------------------------------
The held-out file lives at a separate path. The only function in the
codebase permitted to read it at full size is
:func:`run_held_out_validation`, which asserts a marker file exists before
opening it. This is a BARRIER, not a courtesy: the marker is created at the
end of Day 4 once the calibration profile is final, and any earlier read
raises :class:`HeldOutAccessError`. See
``benchmarks/longmemeval/heldout_guard.py`` for the marker-file mechanism.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from benchmarks.longmemeval.loader import find_dataset_path

DEFAULT_SEED = 42
DEFAULT_HELDOUT_FRACTION = 0.20

DEFAULT_OUT_DIR = Path(__file__).resolve().parent
CALIBRATION_FILENAME = "calibration.json"
HELDOUT_FILENAME = "heldout.json"


@dataclass(frozen=True)
class SplitResult:
    """Outcome of a corpus split. All fields are content-addressed receipts.

    ``source_sha256``
        SHA-256 of the upstream ``longmemeval_oracle.json`` bytes the split
        was performed against. Pinned in the audit doc so the calibration
        partition can only be reproduced from the same source.
    ``calibration_sha256`` / ``heldout_sha256``
        SHA-256 of the *written* partition files (canonical JSON encoding).
        These are the receipts the THEMIS framing test cites.
    """

    source_path: Path
    source_sha256: str
    calibration_path: Path
    calibration_sha256: str
    calibration_count: int
    heldout_path: Path
    heldout_sha256: str
    heldout_count: int
    seed: int
    heldout_fraction: float


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canonical_json(obj) -> bytes:
    """Encode ``obj`` to canonical JSON bytes for hashing.

    Sorting keys + fixed separators makes the digest stable across Python
    runtimes; ``ensure_ascii=False`` preserves the upstream non-ASCII
    content (e.g. accented characters) byte-for-byte.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def split_corpus(
    *,
    source_path: Path | str | None = None,
    out_dir: Path | str | None = None,
    seed: int = DEFAULT_SEED,
    heldout_fraction: float = DEFAULT_HELDOUT_FRACTION,
) -> SplitResult:
    """Deterministically split the upstream LongMemEval JSON file.

    Reads ``source_path`` (or auto-discovers via ``find_dataset_path``),
    shuffles with ``random.Random(seed)``, slices off the trailing
    ``heldout_fraction`` as the held-out set, and writes
    ``calibration.json`` + ``heldout.json`` into ``out_dir`` (defaults to
    the LongMemEval benchmark directory).

    The shuffle-then-slice pattern (rather than per-question Bernoulli
    sampling) gives an exact 400/100 split for the canonical 500-instance
    distribution and keeps the partitions reproducible across Python minor
    versions where the underlying RNG implementation is stable.
    """
    src = Path(source_path) if source_path else find_dataset_path()
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = src.read_bytes()
    source_sha = _sha256_bytes(raw_bytes)
    items = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(items, list):
        raise ValueError(
            f"Expected upstream LongMemEval JSON to be a list, got "
            f"{type(items).__name__}"
        )

    n_total = len(items)
    n_heldout = int(round(n_total * heldout_fraction))
    n_calibration = n_total - n_heldout

    # Shuffle a *copy* of the list of indices, not the items themselves.
    # This keeps the underlying item dicts untouched (so any nested
    # references stay valid) and makes the partition deterministic by
    # qid order, not by item identity.
    indices = list(range(n_total))
    rng = random.Random(seed)
    rng.shuffle(indices)

    calibration_idx = sorted(indices[:n_calibration])
    heldout_idx = sorted(indices[n_calibration:])

    calibration_items = [items[i] for i in calibration_idx]
    heldout_items = [items[i] for i in heldout_idx]

    calibration_bytes = _canonical_json(calibration_items)
    heldout_bytes = _canonical_json(heldout_items)

    calibration_path = out_dir / CALIBRATION_FILENAME
    heldout_path = out_dir / HELDOUT_FILENAME

    calibration_path.write_bytes(calibration_bytes)
    heldout_path.write_bytes(heldout_bytes)

    return SplitResult(
        source_path=src,
        source_sha256=source_sha,
        calibration_path=calibration_path,
        calibration_sha256=_sha256_bytes(calibration_bytes),
        calibration_count=len(calibration_items),
        heldout_path=heldout_path,
        heldout_sha256=_sha256_bytes(heldout_bytes),
        heldout_count=len(heldout_items),
        seed=seed,
        heldout_fraction=heldout_fraction,
    )


def render_split_doc(result: SplitResult) -> str:
    """Render the canonical Markdown audit doc for a split."""
    lines = [
        "# Phase 2.1 — LongMemEval calibration / held-out split",
        "",
        "**Status:** Sealed. SHAs below are the receipt for the THEMIS framing test.",
        "**Sprint:** RAVEN Phase 2.1 (chat-turn calibration + token-efficiency).",
        "**Created:** Day 1 of the calibration sprint, before any calibration change.",
        "",
        "## Method",
        "",
        f"- Upstream source: `{result.source_path}`",
        f"- Upstream SHA-256: `{result.source_sha256}`",
        f"- RNG: `random.Random(seed={result.seed}).shuffle(indices)`",
        f"- Held-out fraction: {result.heldout_fraction:.0%}",
        f"- Total instances: {result.calibration_count + result.heldout_count}",
        "",
        "Procedure: shuffle the index list with the seeded RNG, slice off the"
        " trailing `heldout_fraction` as the held-out set, restore source order"
        " within each partition. Both partitions are written as canonical JSON"
        " (sorted keys, no whitespace, UTF-8) so their SHA-256 digests are"
        " stable across Python runtimes.",
        "",
        "## Receipts",
        "",
        "| partition   | path                                              |   N | SHA-256 |",
        "| ----------- | ------------------------------------------------- | --: | ------- |",
        f"| calibration | `{result.calibration_path}` | {result.calibration_count:>3} | `{result.calibration_sha256}` |",
        f"| held-out    | `{result.heldout_path}` | {result.heldout_count:>3} | `{result.heldout_sha256}` |",
        "",
        "## Held-out access discipline",
        "",
        "- The held-out partition is read by exactly one function in the",
        "  codebase: `benchmarks.longmemeval.heldout_guard.run_held_out_validation()`.",
        "- That function asserts a `phase2.1_complete` marker file exists before",
        "  opening the held-out file. The marker is created at the end of Day 4",
        "  by `benchmarks.longmemeval.heldout_guard.mark_phase_complete()`.",
        "- Any other code path that imports `heldout.json` directly is a",
        "  methodology violation and the THEMIS framing test fails.",
        "",
        "## Reproducing the split",
        "",
        "```bash",
        "cd /Users/sokpyeon/projects/raven",
        ".venv/bin/python -m benchmarks.longmemeval.split",
        "```",
        "",
        "Re-running the command writes the same bytes (the SHAs above are the",
        "expected output). If they don't match, the upstream file changed; pin",
        "the source SHA above and investigate before continuing the sprint.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically split LongMemEval into calibration + held-out"
    )
    parser.add_argument("--source", type=str, default=None)
    parser.add_argument("--out-dir", type=str, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--heldout-fraction", type=float, default=DEFAULT_HELDOUT_FRACTION)
    parser.add_argument(
        "--write-doc", action="store_true",
        help="Also write phase2.1_split.md alongside the partitions",
    )
    args = parser.parse_args()

    result = split_corpus(
        source_path=args.source,
        out_dir=args.out_dir,
        seed=args.seed,
        heldout_fraction=args.heldout_fraction,
    )

    print(f"source     : {result.source_path}")
    print(f"source sha : {result.source_sha256}")
    print(f"calibration: {result.calibration_path}  N={result.calibration_count}")
    print(f"  sha256   : {result.calibration_sha256}")
    print(f"held-out   : {result.heldout_path}  N={result.heldout_count}")
    print(f"  sha256   : {result.heldout_sha256}")

    if args.write_doc:
        doc_path = (Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR) / "phase2.1_split.md"
        doc_path.write_text(render_split_doc(result))
        print(f"wrote {doc_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
