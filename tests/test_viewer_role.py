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


def test_keyboard_for_operational_has_two_buttons():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    user = MagicMock()
    user.role = RoleEnum.PROJECT_MANAGER
    kb = _keyboard_for_user(user)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 2


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
