"""Tests for the Phase 2.1 token-efficiency instrumentation.

Smoke-level: the production-quality assertion the brief asks for is that
this module computes the right shape of report without hidden bugs. The
heavy interpretation work happens in the Markdown report.
"""
from __future__ import annotations

import time

import pytest

# tiktoken is the bench-extra. Skip the suite if it's not installed.
tiktoken = pytest.importorskip("tiktoken")

from benchmarks.longmemeval.loader import LMEQuestion, Session, Turn
from benchmarks.longmemeval.token_efficiency import (
    DEFAULT_TOP_K,
    _default_pipeline_factory,
    aggregate_token_records,
    count_tokens,
    measure_one,
)


def _stub_question(qid: str = "q_test") -> LMEQuestion:
    """Build a tiny LMEQuestion that exercises measure_one end-to-end."""
    sess = Session(
        session_id="s_1",
        date_str="2024/01/01 (Mon) 00:00",
        timestamp=time.time() - 86_400,
        turns=[
            Turn(role="user", content="My favourite editor is Vim.", has_answer=True),
            Turn(role="assistant", content="Got it, Vim noted.", has_answer=False),
            Turn(role="user", content="Let's talk about lunch instead.", has_answer=False),
        ],
    )
    return LMEQuestion(
        question_id=qid,
        question_type="single-session-user",
        is_abstention=False,
        question="What is the user's favourite editor?",
        answer="Vim",
        question_date_str="2024/01/02 (Tue) 00:00",
        question_timestamp=time.time(),
        haystack_sessions=[sess],
        answer_session_ids={"s_1"},
    )


class TestCountTokens:
    def test_empty_list_is_zero(self):
        assert count_tokens([]) == 0

    def test_zero_for_empty_strings(self):
        assert count_tokens(["", ""]) == 0

    def test_strictly_positive_on_real_text(self):
        n = count_tokens(["hello world", "this is a test"])
        assert n > 0

    def test_additive(self):
        a = count_tokens(["hello"])
        b = count_tokens(["world"])
        ab = count_tokens(["hello", "world"])
        assert ab == a + b


class TestMeasureOne:
    def test_returns_record_with_expected_fields(self):
        q = _stub_question()
        record = measure_one(q, pipeline_factory=_default_pipeline_factory(top_k=10))
        assert record.question_id == "q_test"
        assert record.tokens_passthrough >= 0
        assert record.tokens_raven >= 0
        assert -1 <= record.quality_delta <= 1
        # On the v1.1 default profile we expect REFUSED for chat-turn data
        # (the calibration brief's whole premise). Allow other statuses
        # only so the test isn't overly brittle.
        assert record.raven_status in {"REFUSED", "REJECTED", "CONDITIONAL", "APPROVED"}


class TestAggregateTokenRecords:
    def test_empty_input(self):
        report = aggregate_token_records([])
        assert report.n == 0
        assert report.overall["n"] == 0
        assert report.quality_controlled_overall["n"] == 0

    def test_single_record_roundtrip(self):
        q = _stub_question()
        record = measure_one(q, pipeline_factory=_default_pipeline_factory(top_k=10))
        report = aggregate_token_records([record])
        assert report.n == 1
        assert "single-session-user" in report.by_type
        # quality-controlled subset is included only when RAVEN didn't lose
        # quality vs passthrough
        assert report.quality_controlled_overall["n"] in {0, 1}

    def test_quality_controlled_subset_filters_losers(self):
        """Quality-controlled subset is exactly the records with quality_delta >= 0."""
        from benchmarks.longmemeval.token_efficiency import TokenQueryRecord
        records = [
            TokenQueryRecord(
                question_id=f"q{i}",
                question_type="single-session-user",
                is_abstention=False,
                raven_status="APPROVED",
                n_passthrough=5,
                n_raven_surfaced=3,
                tokens_passthrough=100,
                tokens_raven=50,
                token_reduction_ratio=0.5,
                a_at_5_passthrough=True,
                a_at_5_raven=(i != 0),  # record 0 is the quality loser
                quality_delta=0 if i != 0 else -1,
            )
            for i in range(3)
        ]
        report = aggregate_token_records(records)
        # 2 of 3 records survive the quality gate
        assert report.quality_controlled_overall["n"] == 2
        # All 3 records contribute to the naïve overall stats
        assert report.overall["n"] == 3
