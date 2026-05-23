from app.models import RoleEnum
from app.services.decision_service import SUPERIOR_ROLE


def test_viewer_role_exists():
    assert RoleEnum.VIEWER == "viewer"


def test_viewer_not_in_superior_hierarchy():
    assert RoleEnum.VIEWER not in SUPERIOR_ROLE


def test_viewer_in_dashboard_role_labels():
    from app.routers.dashboard import ROLE_LABELS
    assert "viewer" in ROLE_LABELS
    assert ROLE_LABELS["viewer"] == "צופה"


def test_keyboard_for_viewer_has_one_button():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    kb = _keyboard_for_user(viewer)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 1
    assert "פרוייקטים" in buttons[0].text


def test_keyboard_for_operational_has_report_button():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    user = MagicMock()
    user.role = RoleEnum.PROJECT_MANAGER
    kb = _keyboard_for_user(user)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 3  # פרוייקטים, החלטות, דוח שלי
    assert any("דוח שלי" in b.text for b in buttons)


import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_handle_decisions_blocks_viewer():
    from app.services.telegram_polling import TelegramPollingBot, _VIEWER_DECISIONS_BLOCKED
    from app.models import RoleEnum

    bot = TelegramPollingBot()

    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    viewer.id = 1

    update = MagicMock()
    update.effective_user.id = 999
    update.message = AsyncMock()

    context = MagicMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=viewer)
        mock_sm.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)
        await bot.handle_decisions(update, context)

    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "🔒" in call_text


@pytest.mark.asyncio
async def test_viewer_projects_keyword_triggers_menu():
    """Viewer typing פרוייקטים should call reply_text (projects menu)."""
    from app.services.telegram_polling import TelegramPollingBot
    from app.models import RoleEnum

    bot = TelegramPollingBot()
    bot.application = MagicMock()

    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    viewer.id = 1

    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = 42
    update.message = AsyncMock()
    update.message.text = "פרוייקטים"

    context = MagicMock()
    context.bot = AsyncMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=5)
        mock_sm.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)

        await bot._handle_viewer_message(update, context, viewer, "פרוייקטים")

    assert update.message.reply_text.called
