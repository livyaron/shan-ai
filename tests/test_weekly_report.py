"""Tests for weekly report v2 — ReportHistory model, service API, cron skip."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_report_history_model_importable():
    from app.models import ReportHistory
    row = ReportHistory(user_id=1, sections={"prologue": "hi"}, sent_via="telegram")
    assert row.user_id == 1
    assert row.sections["prologue"] == "hi"
    assert row.raw_data is None  # default


# ── Task 2 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_report_returns_sections_dict(db_session):
    """generate_report_for_user returns a dict with 5 keys."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99991
    user.username = "test_gen"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"פתיח","decisions":"החלטות",'
        '"projects":"פרויקטים","summary":"סיכום","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert "prologue" in sections
    assert "decisions" in sections
    assert "projects" in sections
    assert "summary" in sections
    assert "delta" in sections


@pytest.mark.asyncio
async def test_generate_report_saves_history_row(db_session):
    """generate_report_for_user persists a ReportHistory row."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum, ReportHistory
    from sqlalchemy import select

    user = MagicMock(spec=User)
    user.id = 99992
    user.username = "test_save"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"p","decisions":"d","projects":"pr","summary":"s","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        await generate_report_for_user(user, db_session, triggered_by_id=1, sent_via="dashboard")

    row = await db_session.scalar(
        select(ReportHistory).where(ReportHistory.user_id == 99992)
    )
    assert row is not None
    assert row.sent_via == "dashboard"
    assert row.sections["prologue"] == "p"


@pytest.mark.asyncio
async def test_generate_report_fallback_on_llm_error(db_session):
    """When LLM raises, sections has a non-empty prologue and others are None."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99993
    user.username = "test_fallback"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, side_effect=Exception("timeout")):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert sections["prologue"]  # non-empty fallback message


@pytest.mark.asyncio
async def test_send_report_sends_non_empty_sections():
    """send_report_to_user calls bot.send_message once per non-null section."""
    from app.services.weekly_report_service import send_report_to_user

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    sections = {
        "prologue":  "פתיח",
        "decisions": "החלטות",
        "projects":  "פרויקטים",
        "summary":   "סיכום",
        "delta":     None,  # null → no message
    }
    await send_report_to_user(mock_bot, 12345, sections)
    assert mock_bot.send_message.call_count == 4  # delta skipped


@pytest.mark.asyncio
async def test_cron_skips_viewer(db_session):
    """send_weekly_reports_cron does not send to VIEWER users."""
    from app.services.weekly_report_service import send_weekly_reports_cron
    from app.models import User, RoleEnum

    viewer = MagicMock(spec=User)
    viewer.telegram_id = 7777777001
    viewer.role = RoleEnum.VIEWER
    viewer.id = 88801
    viewer.username = "viewer_skip"

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
        await send_weekly_reports_cron(mock_bot)

    mock_bot.send_message.assert_not_called()
