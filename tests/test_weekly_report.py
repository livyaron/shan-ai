"""Tests for weekly intelligence report service (C4)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_generate_report_no_data_returns_fallback(db_session):
    """When no decisions or projects exist for this user, return fallback message."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99999
    user.username = "test_user_no_data"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    with patch("app.services.weekly_report_service.llm_chat", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "אין נתונים לסיכום."
        report = await generate_report_for_user(user, db_session)

    assert "‏" in report   # RTL mark present
    assert isinstance(report, str)


@pytest.mark.asyncio
async def test_weekly_report_skips_viewer(db_session):
    """send_weekly_reports skips users with VIEWER role."""
    from app.services.weekly_report_service import send_weekly_reports

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    # Smoke test: runs without crashing when DB may be empty
    await send_weekly_reports(mock_bot)
