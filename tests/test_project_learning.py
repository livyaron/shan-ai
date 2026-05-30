"""Tests for project_learning_service."""
import pytest
from datetime import date, datetime, timedelta
from app.models import ProjectSnapshot
from app.services.project_learning_service import compute_risk_score


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
    assert result["score"] == 0
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
