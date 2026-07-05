"""Tests for project_learning_service."""
import pytest
from datetime import date, datetime, timedelta
from app.models import ProjectSnapshot
from app.services.project_learning_service import (
    compute_risk_score, is_presumed_completed, _stage_intervals,
    MISSING_RISKS_PTS, MISSING_TO_HANDLE_PTS, MISSING_WEEKLY_PTS,
)


def test_project_snapshot_has_required_columns():
    cols = {c.key for c in ProjectSnapshot.__table__.columns}
    assert "project_id" in cols
    assert "snapshot_date" in cols
    assert "risk_score" in cols
    assert "days_overdue" in cols
    assert "stage" in cols
    assert "estimated_finish_date" in cols
    assert "dev_plan_date" in cols
    assert "risks" in cols
    assert "to_handle" in cols
    assert "weekly_report_brief" in cols
    assert "is_active" in cols


def test_project_snapshot_unique_constraint():
    """unique constraint must be on (project_id, snapshot_date)."""
    ucs = [str(uc) for uc in ProjectSnapshot.__table_args__]
    assert any("project_id" in u and "snapshot_date" in u for u in ucs)


def test_no_dates_gives_zero_schedule_signals():
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=None,
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=datetime.utcnow(),
        prior_finish_dates=[],
        today=date(2026, 5, 30),
    )
    # Schedule signals are zero; only missing-data penalties remain
    assert result["breakdown"]["velocity"] == 0
    assert result["breakdown"]["overdue"] == 0
    assert result["breakdown"]["buffer"] == 0
    assert result["score"] == MISSING_RISKS_PTS + MISSING_TO_HANDLE_PTS + MISSING_WEEKLY_PTS
    assert result["reliable"] is True


def test_overdue_project_scores_high():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=60),
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=datetime.utcnow(),
        today=today,
    )
    assert result["score"] >= 30
    assert result["breakdown"]["overdue"] > 0


def test_severe_keywords_add_points():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="תכנון",
        estimated_finish_date=today + timedelta(days=90),
        dev_plan_date=None,
        risks="הפרויקט תקוע מול חח״י לא אישרה המשך",
        to_handle=None,
        last_updated=datetime.utcnow(),
        today=today,
    )
    assert result["breakdown"]["keywords"] >= 6  # תקוע=3 + חח״י לא אישרה=3


def test_stage_multiplier_biutz_raises_score():
    today = date(2026, 5, 30)
    base = compute_risk_score(
        stage="תכנון",
        estimated_finish_date=today - timedelta(days=14),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow(), today=today,
    )
    high = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=14),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow(), today=today,
    )
    assert high["score"] > base["score"]


def test_stale_project_sets_unreliable():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today + timedelta(days=60),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow() - timedelta(days=25),
        today=today,
    )
    assert result["reliable"] is False


def test_score_capped_at_100():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=200),
        dev_plan_date=today - timedelta(days=300),
        risks="תקוע מעוכב חסם הקפאה ביטול אין תקציב חריגה ללא היתר חח״י לא אישרה",
        to_handle="\n".join(f"פריט {i}" for i in range(20)),
        last_updated=datetime.utcnow() - timedelta(days=30),
        today=today,
    )
    assert result["score"] <= 100


# ── Presumed-completed ("not closed in systems") ─────────────────────────────

def test_is_presumed_completed_siyum_past_finish():
    today = date(2026, 5, 30)
    assert is_presumed_completed("סיום", today - timedelta(days=30), today) is True


def test_is_presumed_completed_whitespace_stage():
    today = date(2026, 5, 30)
    assert is_presumed_completed("סיום ", today - timedelta(days=30), today) is True


def test_is_presumed_completed_future_finish_is_false():
    today = date(2026, 5, 30)
    assert is_presumed_completed("סיום", today + timedelta(days=30), today) is False


def test_is_presumed_completed_no_finish_date_is_false():
    assert is_presumed_completed("סיום", None, date(2026, 5, 30)) is False


def test_is_presumed_completed_other_stage_is_false():
    today = date(2026, 5, 30)
    assert is_presumed_completed("השלמות", today - timedelta(days=30), today) is False
    assert is_presumed_completed("ביצוע", today - timedelta(days=30), today) is False
    assert is_presumed_completed(None, today - timedelta(days=30), today) is False


def test_presumed_completed_skips_missing_data_penalties():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="סיום",
        estimated_finish_date=today - timedelta(days=90),
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=None,
        today=today,
    )
    assert result["presumed_completed"] is True
    # Only the overdue signal (× stage multiplier) remains — no +25 missing penalties
    assert result["score"] == int(result["breakdown"]["overdue"] * 0.7)


def test_same_project_in_bitzua_gets_penalties():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=90),
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=None,
        today=today,
    )
    assert result["presumed_completed"] is False
    expected_missing = MISSING_RISKS_PTS + MISSING_TO_HANDLE_PTS + MISSING_WEEKLY_PTS
    assert result["score"] == int(result["breakdown"]["overdue"] * 1.3) + expected_missing


# ── Stage-duration intervals ──────────────────────────────────────────────────

def test_stage_intervals_empty_and_single_run():
    assert _stage_intervals([]) == []
    d = date(2026, 1, 1)
    history = [(d + timedelta(days=7 * i), "תכנון") for i in range(5)]
    assert _stage_intervals(history) == []  # single run is left-censored + open


def test_stage_intervals_one_transition_excluded():
    d = date(2026, 1, 1)
    history = [
        (d, "תכנון"), (d + timedelta(days=7), "תכנון"),
        (d + timedelta(days=14), "ביצוע"), (d + timedelta(days=21), "ביצוע"),
    ]
    # first run left-censored, second run still open → no completed intervals
    assert _stage_intervals(history) == []


def test_stage_intervals_completed_middle_run():
    d = date(2026, 1, 1)
    history = [
        (d, "תכנון"),
        (d + timedelta(days=7), "ביצוע"),
        (d + timedelta(days=14), "ביצוע"),
        (d + timedelta(days=28), "ביצוע"),
        (d + timedelta(days=35), "השלמות"),
    ]
    assert _stage_intervals(history) == [("ביצוע", 21)]


def test_stage_intervals_skips_blank_stages():
    d = date(2026, 1, 1)
    history = [
        (d, "תכנון"),
        (d + timedelta(days=7), None),
        (d + timedelta(days=14), "ביצוע"),
        (d + timedelta(days=21), "ביצוע"),
        (d + timedelta(days=28), "השלמות"),
    ]
    assert _stage_intervals(history) == [("ביצוע", 7)]


from app.services.project_learning_service import predict_next_score


def test_predict_returns_none_with_fewer_than_3_scores():
    assert predict_next_score([]) is None
    assert predict_next_score([50]) is None
    assert predict_next_score([40, 50]) is None


def test_predict_rising_trend():
    scores = [20, 30, 40, 50, 60, 70, 75, 80]
    pred = predict_next_score(scores)
    assert pred is not None
    assert pred > 80


def test_predict_falling_trend():
    scores = [80, 70, 60, 50, 40, 30, 20, 15]
    pred = predict_next_score(scores)
    assert pred is not None
    assert pred < 15


def test_predict_clamped_0_100():
    assert predict_next_score([95, 98, 99, 100, 100, 100, 100, 100]) <= 100
    assert predict_next_score([5, 3, 2, 1, 1, 1, 1, 1]) >= 0


def test_predict_needs_only_3_scores():
    pred = predict_next_score([30, 50, 70])
    assert pred is not None
    assert pred > 70


import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.project_learning_service import save_snapshot
from app.models import Project


@pytest.mark.asyncio
async def test_save_snapshot_executes_upsert():
    proj = MagicMock(spec=Project)
    proj.id = 1
    proj.stage = "ביצוע"
    proj.estimated_finish_date = date(2026, 4, 1)
    proj.dev_plan_date = date(2026, 3, 1)
    proj.risks = "תקוע"
    proj.to_handle = "פריט אחד\nפריט שניים"
    proj.weekly_report_brief = "עדכון"
    proj.is_active = True
    proj.last_updated = datetime.utcnow()

    session = AsyncMock()
    # scalars().all() for prior snapshots query
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute = AsyncMock(return_value=mock_result)
    session.scalar = AsyncMock(return_value=None)

    await save_snapshot(proj, session)

    assert session.execute.called
