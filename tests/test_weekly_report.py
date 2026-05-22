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
async def test_weekly_report_skips_viewer():
    """send_weekly_reports does not send to VIEWER users."""
    from app.services.weekly_report_service import send_weekly_reports
    from app.models import User, RoleEnum

    viewer = MagicMock(spec=User)
    viewer.telegram_id = 7777777777
    viewer.role = RoleEnum.VIEWER
    viewer.id = 88888
    viewer.username = "viewer_test"

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [viewer]

    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    with patch("app.database.async_session_maker", return_value=mock_session):
        await send_weekly_reports(mock_bot)

    # VIEWER user must not receive a report
    mock_bot.send_message.assert_not_called()
