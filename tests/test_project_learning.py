"""Tests for project_learning_service."""
import pytest
from app.models import ProjectSnapshot


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
