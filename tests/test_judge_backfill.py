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
    from sqlalchemy import delete

    # Clean existing unjudged rows to isolate this test
    await db_session.execute(delete(QueryLog).where(QueryLog.judge_verdict.is_(None)))
    await db_session.commit()

    db_session.add_all([
        QueryLog(question="ש1", ai_response="ת1", judge_verdict="PASS"),
        QueryLog(question="ש2", ai_response="ת2", judge_verdict=None),
    ])
    await db_session.commit()

    with patch(
        "app.services.judge_backfill_service.judge_one",
        new=AsyncMock(return_value=("FAIL", "MISSING_DATA")),
    ) as mocked:
        stats = await run_backfill(db_session, limit=50)

    assert mocked.await_count == 1
    assert stats["judged"] == 1
