"""Decisions menu — keyboards, formatters, and DB query."""

import html as _html
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum

# ── Constants ──────────────────────────────────────────────────────────────

TYPE_EMOJI = {
    DecisionTypeEnum.CRITICAL:  "🚨",
    DecisionTypeEnum.NORMAL:    "✅",
    DecisionTypeEnum.INFO:      "ℹ️",
    DecisionTypeEnum.UNCERTAIN: "❓",
}
STATUS_EMOJI = {
    DecisionStatusEnum.PENDING:  "⏳",
    DecisionStatusEnum.APPROVED: "✔️",
    DecisionStatusEnum.REJECTED: "❌",
    DecisionStatusEnum.EXECUTED: "⚙️",
}
STATUS_LABEL = {
    DecisionStatusEnum.PENDING:  "ממתין",
    DecisionStatusEnum.APPROVED: "אושר",
    DecisionStatusEnum.REJECTED: "נדחה",
    DecisionStatusEnum.EXECUTED: "בוצע",
}

SHORTCUT_PRESETS: dict[str, dict] = {
    "recent":   {"owner": "all",  "type": None,       "status": None,      "date_days": 30, "title": "📋 החלטות אחרונות"},
    "critical": {"owner": "all",  "type": "critical",  "status": None,      "date_days": 0,  "title": "🚨 החלטות קריטיות"},
    "pending":  {"owner": "all",  "type": None,        "status": "pending", "date_days": 0,  "title": "⏳ החלטות ממתינות"},
    "recv":     {"owner": "recv", "type": None,        "status": None,      "date_days": 0,  "title": "📥 שקיבלתי"},
    "my":       {"owner": "my",   "type": None,        "status": None,      "date_days": 0,  "title": "📤 שהגשתי"},
}

_MENU_TEXT = "‏📋 <b>ההחלטות שלי</b>\n\nבחר תצוגה מהירה או סינון מותאם:"

# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕐 אחרונות",  callback_data="dm:recent:0"),
            InlineKeyboardButton("🚨 קריטיות",  callback_data="dm:critical:0"),
            InlineKeyboardButton("⏳ ממתינות",  callback_data="dm:pending:0"),
        ],
        [
            InlineKeyboardButton("📥 שקיבלתי", callback_data="dm:recv:0"),
            InlineKeyboardButton("📤 שהגשתי",  callback_data="dm:my:0"),
            InlineKeyboardButton("🔍 סינון",    callback_data="dm:custom"),
        ],
    ])


def get_menu_shortcut_keyboard() -> InlineKeyboardMarkup:
    """Single-button keyboard appended to process() confirmation messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 ההחלטות שלי", callback_data="dm:my:0"),
    ]])


def build_results_keyboard(shortcut: str, page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"dm:{shortcut}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="dm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"dm:{shortcut}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu")])
    return InlineKeyboardMarkup(rows)


def build_custom_filter_keyboard(state: dict) -> InlineKeyboardMarkup:
    def _btn(label: str, cd: str, active: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{label} ✓" if active else label, callback_data=cd)

    return InlineKeyboardMarkup([
        [
            _btn("הכל",      "dm_cf:o:all",  state["owner"] == "all"),
            _btn("שלי",      "dm_cf:o:my",   state["owner"] == "my"),
            _btn("שקיבלתי", "dm_cf:o:recv", state["owner"] == "recv"),
        ],
        [
            _btn("הכל",       "dm_cf:t:all",      state["type"] is None),
            _btn("🚨 קריטי",  "dm_cf:t:critical",  state["type"] == "critical"),
            _btn("✅ רגיל",   "dm_cf:t:normal",    state["type"] == "normal"),
            _btn("ℹ️ מידע",  "dm_cf:t:info",      state["type"] == "info"),
            _btn("❓ לא ודאי", "dm_cf:t:uncertain", state["type"] == "uncertain"),
        ],
        [
            _btn("הכל",      "dm_cf:s:all",      state["status"] is None),
            _btn("⏳ ממתין", "dm_cf:s:pending",  state["status"] == "pending"),
            _btn("✔️ אושר",  "dm_cf:s:approved", state["status"] == "approved"),
            _btn("❌ נדחה",  "dm_cf:s:rejected", state["status"] == "rejected"),
            _btn("⚙️ בוצע",  "dm_cf:s:executed", state["status"] == "executed"),
        ],
        [
            _btn("7 ימים", "dm_cf:d:7",  state["date_days"] == 7),
            _btn("30 יום",  "dm_cf:d:30", state["date_days"] == 30),
            _btn("הכל",    "dm_cf:d:0",  state["date_days"] == 0),
        ],
        [
            InlineKeyboardButton("🔍 הצג תוצאות", callback_data="dm_cf:show"),
            InlineKeyboardButton("🔙 תפריט",       callback_data="dm:menu"),
        ],
    ])


def build_custom_results_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"dm_cf:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="dm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"dm_cf:pg:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu")])
    return InlineKeyboardMarkup(rows)


# ── Formatters ─────────────────────────────────────────────────────────────

def format_result_line(d: Decision) -> str:
    t_emoji  = TYPE_EMOJI.get(d.type, "❓")
    s_emoji  = STATUS_EMOJI.get(d.status, "")
    s_label  = STATUS_LABEL.get(d.status, "")
    summary  = d.summary or ""
    if len(summary) > 40:
        summary = summary[:40] + "…"
    date_str = d.created_at.strftime("%d/%m") if d.created_at else ""
    date_part = f" · {date_str}" if date_str else ""
    return f"{t_emoji} <b>#{d.id}</b> — {_html.escape(summary)}  {s_emoji} {s_label}{date_part}"


def format_results_message(title: str, decisions: list, total: int, page: int) -> str:
    if not decisions:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו החלטות."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(decisions)
    lines  = [
        f"‏<b>{_html.escape(title)}</b> ({total})",
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>",
        "──────────────────",
    ]
    lines.extend(format_result_line(d) for d in decisions)
    return "\n".join(lines)


def build_custom_filter_message() -> str:
    return (
        "‏🔍 <b>סינון מותאם אישית</b>\n\n"
        "בחר פילטרים ולחץ הצג:\n"
        "──────────────────\n"
        "👤 <b>מקור</b> · 🏷️ <b>סוג</b> · 📌 <b>סטטוס</b> · 📅 <b>תקופה</b>"
    )


def get_menu_text() -> str:
    return _MENU_TEXT
