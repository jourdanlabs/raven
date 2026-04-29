"""LongMemEval dataset loader.

Reads the official `longmemeval_oracle.json` distribution from a local cache
path. The dataset is ~15 MB so we do NOT vendor it into the repo. By default
the loader looks for `LONGMEMEVAL_DATA` env var or a list of conventional
cache locations.

Source:
    https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
    File: longmemeval_oracle.json (500 instances, oracle-retrieval haystacks)

Schema (per instance):
    question_id          str   (suffix `_abs` => abstention question)
    question_type        str   one of:
                                - single-session-user
                                - single-session-assistant
                                - single-session-preference
                                - multi-session
                                - knowledge-update
                                - temporal-reasoning
    question             str   the user query
    answer               str   gold answer (or unanswerability explanation)
    question_date        str   "YYYY/MM/DD (Day) HH:MM"
    haystack_dates       list[str]
    haystack_session_ids list[str]
    haystack_sessions    list[list[{role, content, has_answer?}]]
    answer_session_ids   list[str]   evidence sessions
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


_DEFAULT_CACHE_PATHS = [
    "/tmp/longmemeval_data/longmemeval_oracle.json",
    str(Path.home() / ".cache" / "longmemeval" / "longmemeval_oracle.json"),
]


@dataclass
class Turn:
    role: str
    content: str
    has_answer: bool = False


@dataclass
class Session:
    session_id: str
    date_str: str
    timestamp: float          # unix epoch seconds, parsed from date_str
    turns: list[Turn] = field(default_factory=list)


@dataclass
class LMEQuestion:
    question_id: str
    question_type: str
    is_abstention: bool
    question: str
    answer: str
    question_date_str: str
    question_timestamp: float
    haystack_sessions: list[Session]
    answer_session_ids: set[str]


def _parse_date(date_str: str) -> float:
    """Parse a LongMemEval date like '2023/04/10 (Mon) 17:50' to unix seconds.

    Returns 0.0 if the format is unparseable (we just skip temporal weighting).
    """
    if not date_str:
        return 0.0
    try:
        # Strip the (Day) annotation
        cleaned = date_str
        if "(" in cleaned and ")" in cleaned:
            before, _, rest = cleaned.partition("(")
            _, _, after = rest.partition(")")
            cleaned = (before + after).strip()
            cleaned = " ".join(cleaned.split())  # collapse double spaces
        return datetime.strptime(cleaned, "%Y/%m/%d %H:%M").timestamp()
    except Exception:
        return 0.0


def find_dataset_path() -> Path:
    """Return the path to longmemeval_oracle.json or raise FileNotFoundError."""
    env_path = os.environ.get("LONGMEMEVAL_DATA")
    candidates = [env_path] if env_path else []
    candidates.extend(_DEFAULT_CACHE_PATHS)
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.exists():
            return p
    raise FileNotFoundError(
        "longmemeval_oracle.json not found. Download with:\n"
        "  mkdir -p /tmp/longmemeval_data && cd /tmp/longmemeval_data && \\\n"
        "  curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json\n"
        "Or set LONGMEMEVAL_DATA=/path/to/longmemeval_oracle.json"
    )


def load_questions(path: Path | str | None = None) -> list[LMEQuestion]:
    """Load LongMemEval questions from disk."""
    p = Path(path) if path else find_dataset_path()
    with p.open() as f:
        raw = json.load(f)

    out: list[LMEQuestion] = []
    for item in raw:
        sessions: list[Session] = []
        ids = item["haystack_session_ids"]
        dates = item["haystack_dates"]
        sess_lists = item["haystack_sessions"]
        for sid, d, turns_raw in zip(ids, dates, sess_lists):
            turns = [
                Turn(
                    role=t["role"],
                    content=t["content"],
                    has_answer=bool(t.get("has_answer", False)),
                )
                for t in turns_raw
            ]
            sessions.append(Session(
                session_id=sid,
                date_str=d,
                timestamp=_parse_date(d),
                turns=turns,
            ))
        out.append(LMEQuestion(
            question_id=item["question_id"],
            question_type=item["question_type"],
            is_abstention=item["question_id"].endswith("_abs"),
            question=str(item["question"]),
            # Some gold answers are ints (counting questions like "how many"); coerce to str.
            answer=str(item["answer"]),
            question_date_str=item.get("question_date", ""),
            question_timestamp=_parse_date(item.get("question_date", "")),
            haystack_sessions=sessions,
            answer_session_ids=set(item.get("answer_session_ids", []) or []),
        ))
    return out
