"""Projects menu — keyboards, formatters, and DB queries."""

import html as _html
import datetime

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Project

# ── Constants ──────────────────────────────────────────────────────────────

ACTIVE_STAGES = [
    "עבודה אזרחית",
    "הרכבה חשמלית",
    "הרכבה חשמלית ובדיקות",
    "בדיקות",
]

DATE_OPTIONS = [
    ("late",   "🔴 באיחור"),
    ("q_cur",  "רבעון נוכחי"),
    ("q_next", "רבעון הבא"),
    ("2026",   "2026"),
    ("2027",   "2027"),
]

SHORTCUT_PRESETS: dict[str, dict] = {
    "late":    {"title": "🔴 פרוייקטים באיחור",  "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": "late"},
    "handle":  {"title": "📌 לטיפול",             "stages": None, "type_": None, "mgr": None, "th": "__any__", "date_filter": None},
    "quarter": {"title": "📅 פרוייקטי הרבעון",    "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": "q_cur"},
    "all":     {"title": "📋 כל הפרוייקטים",      "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": None},
    "active":  {"title": "🏗️ פרוייקטים בביצוע",  "stages": ACTIVE_STAGES, "type_": None, "mgr": None, "th": None, "date_filter": None},
}


def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 באיחור",  callback_data="pm:late:0"),
            InlineKeyboardButton("📌 לטיפול",  callback_data="pm:handle:0"),
            InlineKeyboardButton("📅 הרבעון",  callback_data="pm:quarter:0"),
        ],
        [
            InlineKeyboardButton("📋 הכל",     callback_data="pm:all:0"),
            InlineKeyboardButton("🏗️ בביצוע", callback_data="pm:active:0"),
            InlineKeyboardButton("🔍 סינון",   callback_data="pm_cf:open"),
        ],
    ])


def build_results_keyboard(shortcut: str, page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm:{shortcut}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm:{shortcut}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_custom_results_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm_cf:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm_cf:pg:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_detail_back_keyboard(shortcut: str, page: int) -> InlineKeyboardMarkup:
    if shortcut == "cf":
        back_cd = "pm_cf:open"
    else:
        back_cd = f"pm:{shortcut}:{page}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 חזרה לרשימה", callback_data=back_cd),
            InlineKeyboardButton("🏠 תפריט",        callback_data="pm:menu"),
        ]
    ])


def build_custom_filter_keyboard(state: dict, filter_options: dict) -> InlineKeyboardMarkup:
    def _btn(label: str, cd: str, active: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{label} ✓" if active else label, callback_data=cd)

    rows = []

    # Stage — wrap at 3 per row
    stage_btns = [_btn("הכל", "pm_cf:stage:all", state["stage"] is None)]
    for idx, val in enumerate(filter_options.get("stage", [])):
        stage_btns.append(_btn(val, f"pm_cf:stage:{idx}", state["stage"] == val))
    for chunk in _chunk(stage_btns, 3):
        rows.append(chunk)

    # Type — wrap at 4 per row
    type_btns = [_btn("הכל", "pm_cf:type:all", state["type"] is None)]
    for idx, val in enumerate(filter_options.get("type", [])):
        type_btns.append(_btn(val, f"pm_cf:type:{idx}", state["type"] == val))
    for chunk in _chunk(type_btns, 4):
        rows.append(chunk)

    # Manager — "הכל" alone, then 2 per row
    rows.append([_btn("הכל", "pm_cf:mgr:all", state["mgr"] is None)])
    mgr_btns = []
    for idx, val in enumerate(filter_options.get("mgr", [])):
        mgr_btns.append(_btn(val, f"pm_cf:mgr:{idx}", state["mgr"] == val))
    for chunk in _chunk(mgr_btns, 2):
        rows.append(chunk)

    # to_handle — strip "חסם לטיפול " prefix for display, wrap at 2 per row
    th_btns = [_btn("הכל", "pm_cf:th:all", state["th"] is None)]
    for idx, val in enumerate(filter_options.get("th", [])):
        label = val.replace("חסם לטיפול ", "")
        th_btns.append(_btn(label, f"pm_cf:th:{idx}", state["th"] == val))
    for chunk in _chunk(th_btns, 2):
        rows.append(chunk)

    # Date — wrap at 3
    date_btns = [_btn("הכל", "pm_cf:date:all", state["date"] is None)]
    for key, label in DATE_OPTIONS:
        date_btns.append(_btn(label, f"pm_cf:date:{key}", state["date"] == key))
    for chunk in _chunk(date_btns, 3):
        rows.append(chunk)

    rows.append([
        InlineKeyboardButton("🔍 הצג תוצאות", callback_data="pm_cf:show"),
        InlineKeyboardButton("🔙 תפריט",       callback_data="pm_cf:back"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Formatters ─────────────────────────────────────────────────────────────

def get_menu_text(total: int | None = None) -> str:
    header = "‏📁 <b>פרוייקטים</b>"
    if total is not None:
        header += f"\n<i>סה\"כ: {total} פרוייקטים פעילים</i>"
    return header + "\n\nבחר תצוגה מהירה:"


def format_project_line(p: Project) -> str:
    name = p.name or ""
    if len(name) > 35:
        name = name[:35] + "…"
    date_str = ""
    if p.estimated_finish_date:
        date_str = p.estimated_finish_date.strftime("%m/%y")
    stage_part = p.stage or ""
    tail = f"  |  {stage_part} · {date_str}" if date_str else f"  |  {stage_part}"
    return f"📁 <b>#{p.id}</b> · {_html.escape(name)}{tail}"


def format_results_message(title: str, projects: list, total: int, page: int) -> str:
    if not projects:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו פרוייקטים."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(projects)
    lines  = [
        f"‏<b>{_html.escape(title)}</b> ({total})",
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>",
        "──────────────────",
    ]
    lines.extend(format_project_line(p) for p in projects)
    return "\n".join(lines)


def build_project_card(p: Project) -> str:
    today = datetime.date.today()
    finish_str = ""
    overdue = False
    if p.estimated_finish_date:
        finish_str = p.estimated_finish_date.strftime("%m/%Y")
        overdue = p.estimated_finish_date < today

    dev_str = p.dev_plan_date.strftime("%m/%Y") if p.dev_plan_date else "—"
    to_handle = p.to_handle or "—"
    summary = p.weekly_report_brief or "אין"

    date_line = finish_str
    if overdue:
        date_line += " 🔴 באיחור"

    return (
        f"‏📁 <b>פרוייקט #{p.id}</b>\n"
        f"<b>{_html.escape(p.name or '')}</b>\n"
        "──────────────────\n"
        f"🆔 <b>מזהה:</b> {_html.escape(p.project_identifier or '')}\n"
        f"🏷️ <b>סוג:</b> {_html.escape(p.project_type or '—')}\n"
        f"🏗️ <b>שלב:</b> {_html.escape(p.stage or '—')}\n"
        f"🧑‍💼 <b>מנה\"פ:</b> {_html.escape(p.manager or '—')}\n"
        f"📅 <b>תאריך חישמול:</b> {date_line or '—'}\n"
        f"📅 <b>תאריך ת\"פ:</b> {dev_str}\n"
        "──────────────────\n"
        f"📌 <b>לטיפול:</b>\n"
        f"{_html.escape(to_handle)}\n"
        "──────────────────\n"
        f"📋 <b>סיכום שבועי:</b>\n"
        f"<i>{_html.escape(summary)}</i>"
    )


def build_custom_filter_message() -> str:
    return (
        "‏🔍 <b>סינון פרוייקטים</b>\n\n"
        "בחר פילטרים ולחץ הצג:\n"
        "──────────────────\n"
        "🏗️ <b>שלב</b> · 🏷️ <b>סוג</b> · 🧑‍💼 <b>מנהל</b> · 📌 <b>לטיפול</b> · 📅 <b>תאריך</b>"
    )
