"""Tests for project_report_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.project_report_service import gather_report_data
from app.models import RoleEnum


@pytest.mark.asyncio
async def test_gather_report_data_returns_required_keys():
    user = MagicMock()
    user.id = 1
    user.username = "test"
    user.role = RoleEnum.DIVISION_MANAGER

    session = AsyncMock()
    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar.return_value = None
    mock_one_result = MagicMock()
    mock_one_result.one.return_value = (0, 0, 0)
    session.execute = AsyncMock(side_effect=[mock_scalar_result, mock_one_result])

    with patch("app.services.project_report_service.get_overview_stats") as mock_ov, \
         patch("app.services.project_report_service.get_risk_table") as mock_rt:
        mock_ov.return_value = {
            "totals": {"active": 10, "delayed": 2, "at_risk": 1, "entering_next_week": 0},
            "type_counts": {},
            "delay_trend": [],
            "stage_distribution": {},
        }
        mock_rt.return_value = []

        result = await gather_report_data(user, session)

    assert "executive_summary" in result
    assert "portfolio_health" in result
    assert "risk_register" in result
    assert "meta" in result
    assert result["meta"]["username"] == "test"


@pytest.mark.asyncio
async def test_gather_report_data_limits_risk_register():
    user = MagicMock()
    user.id = 1
    user.username = "x"
    user.role = RoleEnum.DIVISION_MANAGER

    session = AsyncMock()
    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar.return_value = None
    mock_one_result = MagicMock()
    mock_one_result.one.return_value = (0, 0, 0)
    session.execute = AsyncMock(side_effect=[mock_scalar_result, mock_one_result])

    big_risk_table = [{"project_id": i, "name": f"p{i}", "risk_score": 90 - i, "main_reason": ""} for i in range(20)]

    with patch("app.services.project_report_service.get_overview_stats") as mock_ov, \
         patch("app.services.project_report_service.get_risk_table") as mock_rt:
        mock_ov.return_value = {"totals": {}, "type_counts": {}, "delay_trend": [], "stage_distribution": {}}
        mock_rt.return_value = big_risk_table

        result = await gather_report_data(user, session)

    assert len(result["risk_register"]) <= 10


import json
from app.services.project_report_service import generate_report_html


@pytest.mark.asyncio
async def test_generate_report_html_returns_html_string():
    sample_data = {
        "meta": {"generated_at": "30/05/2026 12:00", "username": "test", "role": "division_manager"},
        "executive_summary": {
            "total_active": 10, "total_delayed": 2, "total_at_risk": 1,
            "entering_next_week": 0, "avg_risk_score": 45,
            "rag_by_type": {"הקמה": "RED", "הרחבה": "GREEN"},
            "decisions_30d": 8, "critical_pending": 1, "approval_rate_pct": 75,
        },
        "portfolio_health": {"type_counts": {}, "delay_trend": [], "stage_distribution": {}},
        "risk_register": [{"name": "פרויקט א", "identifier": "P001", "type": "הקמה",
                           "stage": "ביצוע", "risk_score": 85, "main_reason": "איחור"}],
        "action_items": [{"item": "טיפול בפרויקט א", "owner": "מנהל", "priority": "HIGH", "main_reason": ""}],
    }

    with patch("app.services.project_report_service.llm_chat") as mock_llm:
        mock_llm.return_value = json.dumps({
            "executive_narrative": "המצב הכולל דורש תשומת לב.",
            "portfolio_narrative": "פרויקטי הקמה מובילים בסיכון.",
            "risk_narrative": "פרויקט א נמצא בסיכון גבוה.",
            "action_narrative": "יש לטפל בפרויקט א בדחיפות.",
        })
        html = await generate_report_html(sample_data)

    assert html.startswith("<!DOCTYPE html")
    assert "דוח פרויקטים" in html
    assert "המצב הכולל" in html
    assert "פרויקט א" in html
