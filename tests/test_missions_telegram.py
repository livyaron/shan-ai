"""Regression tests for the operations-room Telegram callback path.

Would have caught the RoleEnum NameError that killed every om:* button on deploy.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import RoleEnum


def _mock_session_maker(mock_sm, session):
    mock_sm.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)


def _make_query(data):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    return query


def _make_callback_update(data, telegram_id=999):
    update = MagicMock()
    update.effective_user.id = telegram_id
    update.callback_query = _make_query(data)
    return update


@pytest.mark.asyncio
async def test_om_new_starts_wizard_for_manager():
    """Tapping ➕ must open step 1/5 of the walkthrough (regression: NameError)."""
    from app.services.telegram_polling import TelegramPollingBot
    from app.services.telegram_state import _missions_create_state

    bot = TelegramPollingBot()
    manager = MagicMock()
    manager.role = RoleEnum.PROJECT_MANAGER
    manager.id = 7

    update = _make_callback_update("om:new", telegram_id=999)
    context = MagicMock()
    context.bot = AsyncMock()

    _missions_create_state.pop(999, None)
    try:
        with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
            session = AsyncMock()
            session.scalar = AsyncMock(return_value=manager)
            _mock_session_maker(mock_sm, session)
            await bot.handle_callback(update, context)

        query = update.callback_query
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        assert "שלב 1/5" in text and "כותרת" in text
        kb = query.edit_message_text.call_args[1]["reply_markup"]
        cds = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "om:c:abort" in cds
        assert _missions_create_state.get(999) == {"step": "title"}
    finally:
        _missions_create_state.pop(999, None)


@pytest.mark.asyncio
async def test_om_callback_blocks_viewer():
    from app.services.telegram_polling import TelegramPollingBot
    from app.services.telegram_state import _missions_create_state

    bot = TelegramPollingBot()
    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    viewer.id = 3

    update = _make_callback_update("om:new", telegram_id=555)
    context = MagicMock()
    context.bot = AsyncMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=viewer)
        _mock_session_maker(mock_sm, session)
        await bot.handle_callback(update, context)

    context.bot.send_message.assert_called_once()
    assert "🔒" in context.bot.send_message.call_args[1]["text"]
    update.callback_query.edit_message_text.assert_not_called()
    assert 555 not in _missions_create_state


@pytest.mark.asyncio
async def test_wizard_button_steps_advance_state():
    """Quadrant → owner → due → confirm, driven purely by buttons."""
    from app.services.telegram_polling import TelegramPollingBot
    from app.services.telegram_state import _missions_create_state

    bot = TelegramPollingBot()
    manager = MagicMock()
    manager.role = RoleEnum.PROJECT_MANAGER
    manager.id = 7
    manager.username = "דני"

    tid = 777
    context = MagicMock()
    context.bot = AsyncMock()
    _missions_create_state[tid] = {"step": "quadrant", "title": "משימת בדיקה"}
    try:
        # quadrant pick → owner step (opens a session to list assignable users)
        query = _make_query("om:c:qd:do")
        with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
            session = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all.return_value = []
            session.scalars = AsyncMock(return_value=scalars_result)
            _mock_session_maker(mock_sm, session)
            await bot._handle_missions_menu(query, context, "om:c:qd:do", tid, manager)
        state = _missions_create_state[tid]
        assert state["quadrant"] == "do" and state["step"] == "owner"
        kb = query.edit_message_text.call_args[1]["reply_markup"]
        cds = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "om:c:own:me" in cds  # "👤 אני" default owner button

        # owner pick (me) → due step
        query = _make_query("om:c:own:me")
        await bot._handle_missions_menu(query, context, "om:c:own:me", tid, manager)
        state = _missions_create_state[tid]
        assert state["owner_id"] == 7 and state["step"] == "due"
        kb = query.edit_message_text.call_args[1]["reply_markup"]
        cds = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "om:c:due:tomorrow" in cds

        # due quick-pick → confirm step
        query = _make_query("om:c:due:tomorrow")
        await bot._handle_missions_menu(query, context, "om:c:due:tomorrow", tid, manager)
        state = _missions_create_state[tid]
        assert state["step"] == "confirm" and state["due_date"] is not None
        kb = query.edit_message_text.call_args[1]["reply_markup"]
        cds = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "om:c:save" in cds and "om:c:abort" in cds
    finally:
        _missions_create_state.pop(tid, None)


def test_war_room_poster_assets_exist():
    from app.services.telegram_polling import _WAR_ROOM_POSTERS
    names = {p.name for p in _WAR_ROOM_POSTERS}
    assert names == {
        "poster_bunker.jpg", "poster_keepcalm.jpg",
        "poster_wecandoit.jpg", "poster_radar.jpg",
    }
    for p in _WAR_ROOM_POSTERS:
        assert 0 < p.stat().st_size < 400_000


@pytest.mark.asyncio
async def test_missions_entry_sends_poster_then_menu():
    from app.services.telegram_polling import TelegramPollingBot, _poster_file_id_cache
    from app.services import telegram_polling as tp

    bot = TelegramPollingBot()
    manager = MagicMock()
    manager.role = RoleEnum.PROJECT_MANAGER
    manager.id = 7

    update = MagicMock()
    update.effective_user.id = 888
    update.message = AsyncMock()
    context = MagicMock()

    _poster_file_id_cache.clear()
    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=manager)
        # get_board_counts runs two queries: execute (grouped counts) + scalar (overdue)
        exec_result = MagicMock()
        exec_result.all.return_value = []
        session.execute = AsyncMock(return_value=exec_result)
        session.scalar = AsyncMock(side_effect=[manager, 0])
        _mock_session_maker(mock_sm, session)
        await bot.handle_missions(update, context)

    update.message.reply_photo.assert_called_once()
    update.message.reply_text.assert_called_once()  # menu still sent after poster
    _poster_file_id_cache.clear()  # don't leak mock file_ids into other tests


@pytest.mark.asyncio
async def test_poster_failure_does_not_block_menu():
    from app.services.telegram_polling import TelegramPollingBot, _poster_file_id_cache

    bot = TelegramPollingBot()
    manager = MagicMock()
    manager.role = RoleEnum.PROJECT_MANAGER
    manager.id = 7

    update = MagicMock()
    update.effective_user.id = 889
    update.message = AsyncMock()
    update.message.reply_photo = AsyncMock(side_effect=Exception("boom"))
    context = MagicMock()

    _poster_file_id_cache.clear()
    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        session = AsyncMock()
        exec_result = MagicMock()
        exec_result.all.return_value = []
        session.execute = AsyncMock(return_value=exec_result)
        session.scalar = AsyncMock(side_effect=[manager, 0])
        _mock_session_maker(mock_sm, session)
        await bot.handle_missions(update, context)

    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_missions_command_blocks_viewer():
    """Regression: /missions raised NameError before the RoleEnum import fix."""
    from app.services.telegram_polling import TelegramPollingBot

    bot = TelegramPollingBot()
    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    viewer.id = 3

    update = MagicMock()
    update.effective_user.id = 111
    update.message = AsyncMock()
    context = MagicMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=viewer)
        _mock_session_maker(mock_sm, session)
        await bot.handle_missions(update, context)

    update.message.reply_text.assert_called_once()
    assert "🔒" in update.message.reply_text.call_args[0][0]
