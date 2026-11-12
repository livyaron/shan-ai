"""The AI's recommended action on a new decision can be rewritten by the user
before it is recorded (2026-11-12). No database.
"""
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import telegram_polling as tp
from app.services import telegram_state as ts


@pytest.fixture(autouse=True)
def _clean():
    ts._awaiting_action_edit.clear()
    yield
    ts._awaiting_action_edit.clear()


def test_the_preview_offers_to_change_the_action():
    rows = tp._decision_preview_keyboard().inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["dec_prev_y:0", "dec_prev_n:0"]
    assert rows[1][0].callback_data == "dec_prev_e:0" and "פעולה מומלצת" in rows[1][0].text


def test_the_users_action_replaces_the_ais_and_the_original_is_kept():
    ai = {"type": "NORMAL", "summary": "s", "recommended_action": "להזמין ספק חדש", "requires_approval": True}
    edited = ts.apply_action_edit(ai, "  לדחות את ההזמנה בחודש  ")
    assert edited["recommended_action"] == "לדחות את ההזמנה בחודש"
    assert edited["ai_recommended_action"] == "להזמין ספק חדש" and edited["action_edited"] is True
    assert edited["summary"] == "s" and edited["requires_approval"] is True     # nothing else moves
    assert ai["recommended_action"] == "להזמין ספק חדש"                          # the input is not mutated
    again = ts.apply_action_edit(edited, "גרסה שנייה")
    assert again["ai_recommended_action"] == "להזמין ספק חדש"                   # still the AI's, not the first edit


def test_the_preview_says_when_the_action_is_the_users():
    ai = {"type": "NORMAL", "summary": "s", "recommended_action": "<b>x</b>"}
    assert "נערכה על ידך" not in tp._build_preview_text(ai)
    text = tp._build_preview_text(ts.apply_action_edit(ai, "פעולה <שלי>"))
    assert "פעולה מומלצת (נערכה על ידך):" in text and "פעולה &lt;שלי&gt;" in text


def test_the_wait_is_taken_once_and_expires():
    ts.expect_action_edit(7, now=100.0)
    assert ts.take_action_edit(7, now=101.0) is True
    assert ts.take_action_edit(7, now=102.0) is False
    ts.expect_action_edit(7, now=100.0)
    assert ts.take_action_edit(7, now=100.0 + ts.NEW_DECISION_WAIT_SECONDS + 1) is False


def test_the_handler_wiring():
    handler = inspect.getsource(type(tp.telegram_bot).handle_message)
    edit = handler.index("take_action_edit(telegram_id")
    assert edit < handler.index("take_new_decision(telegram_id")
    assert edit < handler.index("_menu = _menu_shortcut(text)")
    assert handler.index("user.role == _RE.VIEWER") < edit
    callback = inspect.getsource(type(tp.telegram_bot).handle_callback)
    assert 'if action == "dec_prev_e":' in callback
    assert "_awaiting_action_edit.pop(telegram_id, None)" in callback   # any other button cancels
    assert 'if action == "dec_prev_k":' in callback and '"dec_prev_k:0"' in callback   # keep the AI's wording
    # the edit keeps the preview: only approve/dismiss pop it
    block = callback[callback.index('if action == "dec_prev_e":'):callback.index('if action in ("dec_prev_y", "dec_prev_n")')]
    assert "_awaiting_decision_preview.pop" not in block


@pytest.mark.asyncio
async def test_the_edited_action_is_what_gets_recorded(monkeypatch):
    """process() records pre_result as given — so the edited dict is the decision."""
    from app.services.decision_service import DecisionService
    src = inspect.getsource(DecisionService.process)
    assert "result = pre_result" in src


def test_the_web_form_sends_the_edited_action():
    html = Path("app/templates/ask.html").read_text(encoding="utf-8")
    assert '<textarea id="action-${uid}"' in html
    assert "form.append('recommended_action', editedAction || a.recommended_action || '');" in html
