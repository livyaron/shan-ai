"""Decisions menu → "➕ הוספת החלטה" (2026-11-12). No database.

Pressing the button makes the user's NEXT message a decision: it goes straight
to the analysis preview, skipping the classifier and the keyword shortcuts.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import telegram_polling as tp
from app.services import telegram_state as ts
from app.services.decisions_menu_service import get_menu_keyboard


@pytest.fixture(autouse=True)
def _clean():
    ts._awaiting_new_decision.clear()
    ts._awaiting_decision_preview.clear()
    yield
    ts._awaiting_new_decision.clear()
    ts._awaiting_decision_preview.clear()


def test_the_menu_opens_with_the_add_button():
    first = get_menu_keyboard().inline_keyboard[0]
    assert len(first) == 1 and first[0].callback_data == "dm:add" and "הוספת החלטה" in first[0].text


def test_the_press_is_taken_once():
    ts.expect_new_decision(7, now=100.0)
    assert ts.take_new_decision(7, now=101.0) is True
    assert ts.take_new_decision(7, now=102.0) is False  # one press, one decision


def test_an_old_press_is_forgotten():
    ts.expect_new_decision(7, now=100.0)
    assert ts.take_new_decision(7, now=100.0 + ts.NEW_DECISION_WAIT_SECONDS + 1) is False
    assert 7 not in ts._awaiting_new_decision


def test_no_press_means_an_ordinary_message():
    assert ts.take_new_decision(7, now=1.0) is False


@pytest.mark.asyncio
async def test_dm_add_arms_the_wait_and_asks_for_the_text():
    query = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    await tp.telegram_bot._handle_decisions_menu(query, None, "dm:add", 7, SimpleNamespace(id=1))
    assert 7 in ts._awaiting_new_decision
    args, kwargs = query.message.reply_text.call_args
    assert "הוספת החלטה" in args[0] and args[0].startswith("\u200f")
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "dm:menu"  # ביטול


@pytest.mark.asyncio
async def test_preview_decision_shows_the_preview_and_waits_for_approval(monkeypatch):
    analyze = AsyncMock(return_value={"summary": "s"})
    monkeypatch.setattr(tp, "DecisionService", lambda session, app: SimpleNamespace(analyze_only=analyze))
    monkeypatch.setattr(tp, "_build_preview_text", lambda r: "PREVIEW")
    monkeypatch.setattr(tp, "_user_has_manager", lambda u: True)
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    user = SimpleNamespace(id=1)

    await tp.telegram_bot._preview_decision(update, None, user, 7, "מחליפים ספק מסדרים", [])

    analyze.assert_awaited_once()
    assert analyze.call_args.args[1] == "מחליפים ספק מסדרים"
    assert ts._awaiting_decision_preview[7]["text"] == "מחליפים ספק מסדרים"
    assert update.message.reply_text.call_args.args[0] == "PREVIEW"


def test_the_forced_decision_runs_before_shortcuts_and_classifier():
    """A decision that says "החלטות" or "זכור" must not open a menu or be saved as a fact."""
    src = inspect.getsource(tp.TelegramBot.handle_message) if hasattr(tp, "TelegramBot") \
        else inspect.getsource(type(tp.telegram_bot).handle_message)
    forced = src.index("take_new_decision(telegram_id")
    assert forced < src.index("extract_remember_content(text)")
    assert forced < src.index("_menu_shortcut(text)")
    assert forced < src.index("classify(text)")
    assert src.index("user.role == _RE.VIEWER") < forced  # a viewer never records a decision


def test_any_other_button_cancels_the_wait():
    src = inspect.getsource(type(tp.telegram_bot).handle_callback)
    assert "_awaiting_new_decision.pop(telegram_id, None)" in src
    assert src.index("_awaiting_new_decision.pop(telegram_id, None)") < src.index('data.startswith("dm:")')
