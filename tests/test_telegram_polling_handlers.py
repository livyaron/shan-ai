"""Tests for telegram_polling — keyboards, preview text, registration flow,
and handle_message role gating. The bot is the primary user interface and was
almost entirely untested."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from app.models import Message, RoleEnum, User
from app.services.telegram_polling import (
    TelegramPollingBot,
    _build_preview_text,
    _cause_keyboard,
    _feedback_keyboard,
    _mgr_approval_keyboard,
    _user_has_manager,
    _viewer_reply_keyboard,
)


def _make_update(telegram_id=901234501, text="שלום", args=None):
    update = MagicMock()
    update.effective_user.id = telegram_id
    update.effective_user.to_dict.return_value = {"id": telegram_id, "first_name": "Test"}
    update.effective_chat.id = telegram_id
    update.message.text = text
    update.message.message_id = 1
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = args or []
    context.bot.send_chat_action = AsyncMock()
    return update, context


def _replies(update) -> str:
    return "\n".join(
        str(c.args[0]) if c.args else str(c.kwargs.get("text", ""))
        for c in update.message.reply_text.call_args_list
    )


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_build_preview_text_maps_type_and_approval():
    text = _build_preview_text({
        "type": "critical",
        "summary": "תקלה בשנאי",
        "recommended_action": "לנתק מיידית",
        "requires_approval": True,
    })
    assert "קריטי" in text
    assert "תקלה בשנאי" in text
    assert "לנתק מיידית" in text
    assert "<b>דורש אישור:</b> כן" in text


def test_build_preview_text_escapes_html_and_defaults():
    text = _build_preview_text({"summary": "<script>alert(1)</script>"})
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "<b>פעולה מומלצת:</b> —" in text
    assert "<b>דורש אישור:</b> לא" in text


def test_feedback_keyboard_callback_data_carries_log_id():
    kb = _feedback_keyboard(42)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["lfb_up:42", "lfb_dn:42"]


def test_cause_keyboard_has_all_causes_plus_skip():
    kb = _cause_keyboard(7)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lfc:7:WRONG_PROJECT" in data
    assert "lfc:7:MISSING_DATA" in data
    assert "lfc:7:HALLUCINATION" in data
    assert data[-1] == "lfc:7:SKIP"


def test_mgr_approval_keyboard_yes_no():
    kb = _mgr_approval_keyboard()
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["mgr_yes:0", "mgr_no:0"]


def test_user_has_manager_hierarchy():
    pm = MagicMock()
    pm.role = RoleEnum.PROJECT_MANAGER
    assert _user_has_manager(pm) is True
    top = MagicMock()
    top.role = RoleEnum.DIVISION_MANAGER
    assert _user_has_manager(top) is False


def test_viewer_keyboard_projects_only():
    kb = _viewer_reply_keyboard()
    texts = [b.text for row in kb.keyboard for b in row]
    assert len(texts) == 1
    assert "פרוייקטים" in texts[0]


# ── Registration flow (_do_register) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_register_unknown_code(db_session):
    bot = TelegramPollingBot()
    update, _ = _make_update()
    await bot._do_register(update, update.effective_user.id, "NOPE99")
    assert "קוד הרשמה לא נמצא" in _replies(update)


@pytest.mark.asyncio
async def test_do_register_success_links_telegram_id(db_session):
    user = User(username="reg_target", role=RoleEnum.PROJECT_MANAGER,
                registration_code="AB12CD", telegram_id=None)
    db_session.add(user)
    await db_session.commit()

    bot = TelegramPollingBot()
    update, _ = _make_update(telegram_id=901234777)
    await bot._do_register(update, 901234777, "AB12CD")

    await db_session.refresh(user)
    assert user.telegram_id == 901234777
    assert user.registration_code is None
    assert "ההרשמה הצליחה" in _replies(update)


@pytest.mark.asyncio
async def test_do_register_code_bound_to_other_account(db_session):
    db_session.add(User(username="other_owner", role=RoleEnum.PROJECT_MANAGER,
                        registration_code="ZZ99XX", telegram_id=901230001))
    await db_session.commit()

    bot = TelegramPollingBot()
    update, _ = _make_update(telegram_id=901230002)
    await bot._do_register(update, 901230002, "ZZ99XX")
    assert "כבר נוצמד לחשבון אחר" in _replies(update)


@pytest.mark.asyncio
async def test_do_register_already_linked_confirms(db_session):
    db_session.add(User(username="linked_user", role=RoleEnum.PROJECT_MANAGER,
                        registration_code="QQ11WW", telegram_id=901230003))
    await db_session.commit()

    bot = TelegramPollingBot()
    update, _ = _make_update(telegram_id=901230003)
    await bot._do_register(update, 901230003, "QQ11WW")
    assert "כבר רשום במערכת" in _replies(update)


@pytest.mark.asyncio
async def test_do_register_merges_roleless_placeholder(db_session):
    """Messages sent before registration must move to the real user, and the
    auto-created placeholder must be deleted."""
    placeholder = User(username="ph_user", role=None, telegram_id=901230004)
    real = User(username="real_user", role=RoleEnum.PROJECT_MANAGER,
                registration_code="MM33NN", telegram_id=None)
    db_session.add_all([placeholder, real])
    await db_session.commit()
    db_session.add(Message(user_id=placeholder.id, content="הודעה מוקדמת",
                           telegram_message_id=5))
    await db_session.commit()
    placeholder_id, real_id = placeholder.id, real.id

    bot = TelegramPollingBot()
    update, _ = _make_update(telegram_id=901230004)
    await bot._do_register(update, 901230004, "MM33NN")

    db_session.expire_all()  # bypass identity map — rows changed in another session
    assert await db_session.scalar(
        select(User).where(User.id == placeholder_id)) is None
    msg = await db_session.scalar(select(Message).where(Message.content == "הודעה מוקדמת"))
    assert msg.user_id == real_id
    linked = await db_session.scalar(select(User).where(User.id == real_id))
    assert linked.telegram_id == 901230004


# ── handle_message role gating ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_message_roleless_user_redirected(db_session):
    bot = TelegramPollingBot()
    update, context = _make_update(telegram_id=901230010, text="שאלה כלשהי")
    await bot.handle_message(update, context)
    assert "ממתין לאישור" in _replies(update)
    # Message is still stored for later merge
    msg = await db_session.scalar(select(Message).where(Message.content == "שאלה כלשהי"))
    assert msg is not None


@pytest.mark.asyncio
async def test_handle_message_viewer_routed_to_viewer_pipeline(db_session):
    db_session.add(User(username="viewer_route", role=RoleEnum.VIEWER,
                        telegram_id=901230011))
    await db_session.commit()

    bot = TelegramPollingBot()
    bot._handle_viewer_message = AsyncMock()
    update, context = _make_update(telegram_id=901230011, text="מה שלב פרויקט חולה?")
    await bot.handle_message(update, context)
    bot._handle_viewer_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_start_welcomes_new_user(db_session):
    bot = TelegramPollingBot()
    update, context = _make_update(telegram_id=901230012)
    await bot.handle_start(update, context)
    reply = _replies(update)
    assert "ברוך הבא" in reply
    assert "/register" in reply
