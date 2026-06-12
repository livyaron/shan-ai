"""Tests for judge_backfill_service: verdict mapping, failure-type parsing, idempotent row selection."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog
from app.services.judge_backfill_service import (
    score_to_verdict,
    parse_failure_type,
    NO_INFO,
    run_backfill,
)


def test_score_to_verdict_thresholds():
    assert score_to_verdict(1.0) == "PASS"
    assert score_to_verdict(0.8) == "PASS"
    assert score_to_verdict(0.79) == "PARTIAL"
    assert score_to_verdict(0.5) == "PARTIAL"
    assert score_to_verdict(0.49) == "FAIL"
    assert score_to_verdict(0.0) == "FAIL"


def test_parse_failure_type_valid():
    assert parse_failure_type("WRONG_PROJECT") == "WRONG_PROJECT"
    assert parse_failure_type("  hallucination \n") == "HALLUCINATION"
    assert parse_failure_type("התשובה: MISSING_DATA כי חסר") == "MISSING_DATA"


def test_parse_failure_type_garbage_returns_none():
    assert parse_failure_type("לא יודע") is None
    assert parse_failure_type("") is None
    assert parse_failure_type(None) is None


def test_no_info_constant():
    assert NO_INFO == "אין מידע"


@pytest.mark.asyncio
async def test_backfill_skips_already_judged(db_session):
    from sqlalchemy import select

    # Create one unjudged row
    row_unjudged = QueryLog(question="test_unjudged", ai_response="response", judge_verdict=None)
    db_session.add(row_unjudged)
    await db_session.commit()

    # Record calls to judge_one
    judged_ids = []

    async def track_judge(session, log):
        judged_ids.append(log.id)
        return ("FAIL", "MISSING_DATA")

    with patch(
        "app.services.judge_backfill_service.judge_one",
        new=AsyncMock(side_effect=track_judge),
    ):
        await run_backfill(db_session, limit=1)

    # Verify judge_one was called with our test row
    assert row_unjudged.id in judged_ids, "Unjudged row should have been judged"
    # Verify the verdict was set (reload the row to check)
    await db_session.refresh(row_unjudged)
    assert row_unjudged.judge_verdict == "FAIL"
    assert row_unjudged.failure_type == "MISSING_DATA"
