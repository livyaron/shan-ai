"""Projects menu — keyboards, formatters, and DB queries."""

import html as _html
import datetime

from sqlalchemy import select, func, distinct, or_, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Project

# ── Constants ──────────────────────────────────────────────────────────────

DATE_OPTIONS = [
    ("late",   "🔴 באיחור"),
    ("q_cur",  "רבעון נוכחי"),
    ("q_next", "רבעון הבא"),
    ("2026",   "2026"),
    ("2027",   "2027"),
]

_DATE_KEY_TO_LABEL = dict(DATE_OPTIONS)

FILTER_FIELDS = [
    ("stage", "🏗️ שלב"),
    ("type",  "🏷️ סוג"),
    ("mgr",   "🧑‍💼 מנהל"),
    ("th",    "📌 לטיפול"),
    ("date",  "📅 תאריך"),
]

# Shortcuts still used by main menu buttons
TYPE_ORDER = ["הקמה", "הרחבה", "שוש", "ניידות"]

SHORTCUT_PRESETS: dict[str, dict] = {
    "late":    {"title": "🔴 פרוייקטים באיחור", "stages": None, "types": None, "mgrs": None, "ths": None, "dates": ["late"]},
    "quarter": {"title": "📅 פרוייקטי הרבעון",  "stages": None, "types": None, "mgrs": None, "ths": None, "dates": ["q_cur"]},
}


def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 באיחור",  callback_data="pm:late:0"),
            InlineKeyboardButton("📌 לטיפול",  callback_data="pm:th_menu"),
        ],
        [
            InlineKeyboardButton("📅 הרבעון",  callback_data="pm:quarter:0"),
            InlineKeyboardButton("🔍 סינון",   callback_data="pm_cf:open"),
        ],
    ])


def _type_filter_row(base_cd: str, page: int, type_key: int | None, all_cd: str) -> list:
    """One row of type filter buttons. base_cd is prefix for type-filtered callbacks."""
    btns = []
    for i, label in enumerate(TYPE_ORDER):
        txt = f"✓{label}" if type_key == i else label
        btns.append(InlineKeyboardButton(txt, callback_data=f"{base_cd}:{i}"))
    all_label = "✓הכל" if type_key is None else "הכל"
    btns.append(InlineKeyboardButton(all_label, callback_data=all_cd))
    return btns


def build_results_keyboard(shortcut: str, page: int, total: int, type_key: int | None = None) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total > 20:
        type_suffix = f":{type_key}" if type_key is not None else ""
        rows.append(_type_filter_row(
            base_cd=f"pm:{shortcut}:{page}",
            page=page,
            type_key=type_key,
            all_cd=f"pm:{shortcut}:{page}",
        ))
    if total_pages > 1:
        type_suffix = f":{type_key}" if type_key is not None else ""
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm:{shortcut}:{page - 1}{type_suffix}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm:{shortcut}:{page + 1}{type_suffix}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_th_sub_keyboard(th_options: list[str]) -> InlineKeyboardMarkup:
    """לטיפול chooser — one button per distinct to_handle value."""
    rows = []
    for idx, val in enumerate(th_options):
        label = val.replace("חסם לטיפול ", "")
        rows.append([InlineKeyboardButton(label, callback_data=f"pm:th:{idx}:0")])
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_th_results_keyboard(idx: int, page: int, total: int, type_key: int | None = None) -> InlineKeyboardMarkup:
    """Nav keyboard for לטיפול specific-value results."""
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total > 20:
        rows.append(_type_filter_row(
            base_cd=f"pm:th:{idx}:{page}",
            page=page,
            type_key=type_key,
            all_cd=f"pm:th:{idx}:{page}",
        ))
    if total_pages > 1:
        type_suffix = f":{type_key}" if type_key is not None else ""
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm:th:{idx}:{page - 1}{type_suffix}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm:th:{idx}:{page + 1}{type_suffix}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("🔙 לטיפול", callback_data="pm:th_menu"),
        InlineKeyboardButton("🏠 תפריט",  callback_data="pm:menu"),
    ])
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


def build_detail_back_keyboard(shortcut: str, page: int, type_key: int | None = None) -> InlineKeyboardMarkup:
    type_suffix = f":{type_key}" if type_key is not None else ""
    if shortcut == "cf":
        back_cd = f"pm_cf:pg:{page}"
    elif shortcut == "viewer":
        back_cd = "pm:menu"
    elif shortcut.startswith("th") and shortcut[2:].isdigit():
        back_cd = f"pm:th:{shortcut[2:]}:{page}{type_suffix}"
    else:
        back_cd = f"pm:{shortcut}:{page}{type_suffix}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 חזרה לרשימה", callback_data=back_cd),
            InlineKeyboardButton("🏠 תפריט",        callback_data="pm:menu"),
        ]
    ])


# ── Filter keyboards (two-level multi-choice) ──────────────────────────────

def build_filter_field_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Level 1 — choose which field to filter on."""
    rows = []
    for dim, label in FILTER_FIELDS:
        count = len(state.get(dim, []))
        suffix = f" ✓{count}" if count else ""
        rows.append([InlineKeyboardButton(f"{label}{suffix}", callback_data=f"pm_cf:f:{dim}")])
    has_any = any(state.get(d) for d in ("stage", "type", "mgr", "th", "date"))
    footer = []
    if has_any:
        footer.append(InlineKeyboardButton("🗑 נקה הכל", callback_data="pm_cf:clr"))
    footer.append(InlineKeyboardButton("🔍 הצג", callback_data="pm_cf:show"))
    footer.append(InlineKeyboardButton("🔙 תפריט", callback_data="pm_cf:back"))
    rows.append(footer)
    return InlineKeyboardMarkup(rows)


def build_filter_value_keyboard(dim: str, options: list[str], selected: list[str]) -> InlineKeyboardMarkup:
    """Level 2 — multi-select values for a DB-backed field (stage/type/mgr/th)."""
    rows = []
    for idx, val in enumerate(options):
        label = val.replace("חסם לטיפול ", "") if dim == "th" else val
        tick = "✓ " if val in selected else ""
        rows.append([InlineKeyboardButton(f"{tick}{label}", callback_data=f"pm_cf:t:{dim}:{idx}")])
    rows.append([InlineKeyboardButton("✅ אישור", callback_data="pm_cf:fd")])
    return InlineKeyboardMarkup(rows)


def build_filter_date_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Level 2 — multi-select date options (fixed values)."""
    rows = []
    for key, label in DATE_OPTIONS:
        tick = "✓ " if key in selected else ""
        rows.append([InlineKeyboardButton(f"{tick}{label}", callback_data=f"pm_cf:t:date:{key}")])
    rows.append([InlineKeyboardButton("✅ אישור", callback_data="pm_cf:fd")])
    return InlineKeyboardMarkup(rows)


# ── Formatters ─────────────────────────────────────────────────────────────

def get_menu_text(total: int | None = None) -> str:
    header = "‏📁 <b>פרוייקטים</b>"
    if total is not None:
        header += f"\n<i>סה\"כ: {total} פרוייקטים פעילים</i>"
    return header + "\n\nבחר תצוגה מהירה:"


def get_th_sub_text() -> str:
    return "‏📌 <b>לטיפול</b>\n\nבחר סוג חסם:"


def get_filter_field_text(state: dict) -> str:
    parts = []
    for dim, label in FILTER_FIELDS:
        vals = state.get(dim, [])
        if vals:
            if dim == "date":
                display = ", ".join(_DATE_KEY_TO_LABEL.get(v, v) for v in vals)
            elif dim == "th":
                display = ", ".join(v.replace("חסם לטיפול ", "") for v in vals)
            else:
                display = ", ".join(vals)
            parts.append(f"{label}: <i>{_html.escape(display)}</i>")
    active_line = "\n".join(parts) if parts else "<i>אין פילטרים פעילים</i>"
    return f"‏🔍 <b>סינון פרוייקטים</b>\n\n{active_line}\n\nבחר שדה לעריכה:"


def get_filter_value_text(dim: str) -> str:
    labels = dict(FILTER_FIELDS)
    return f"‏{labels.get(dim, dim)} — בחר ערכים (ניתן לבחור מספר):"


def format_project_line(p: Project) -> str:
    name = p.name or ""
    if len(name) > 35:
        name = name[:35] + "…"
    date_str = p.estimated_finish_date.strftime("%m/%y") if p.estimated_finish_date else ""
    stage_part = p.stage or ""
    tail = f"  |  {stage_part} · {date_str}" if date_str else f"  |  {stage_part}"
    return f"📁 {_html.escape(name)}{tail}"


def format_results_message(title: str, projects: list, total: int, page: int) -> str:
    if not projects:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו פרוייקטים."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(projects)
    return (
        f"‏<b>{_html.escape(title)}</b> ({total})\n"
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>"
    )


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
    date_line = (finish_str + " 🔴 באיחור") if overdue else finish_str

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


# ── DB Queries ─────────────────────────────────────────────────────────────

async def get_total_active(session: AsyncSession) -> int:
    result = await session.scalar(
        select(func.count(Project.id)).where(Project.is_active.is_(True))
    )
    return result or 0


async def get_filter_options(session: AsyncSession) -> dict:
    """Return distinct non-null values for each filter dimension."""
    async def _distinct(col):
        rows = await session.scalars(
            select(distinct(col)).where(col.isnot(None)).order_by(col)
        )
        return list(rows.all())

    return {
        "stage": await _distinct(Project.stage),
        "type":  await _distinct(Project.project_type),
        "mgr":   await _distinct(Project.manager),
        "th":    await _distinct(Project.to_handle),
    }


def _date_clause(key: str):
    """Return a SQLAlchemy WHERE clause for one date key."""
    today = datetime.date.today()
    if key == "late":
        return Project.estimated_finish_date < today
    if key == "q_cur":
        q_start = datetime.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        q_m_end = ((today.month - 1) // 3) * 3 + 3
        q_end = (datetime.date(today.year, q_m_end, 1) + datetime.timedelta(days=31)).replace(day=1) - datetime.timedelta(days=1)
        return and_(Project.estimated_finish_date >= q_start, Project.estimated_finish_date <= q_end)
    if key == "q_next":
        cur_q = (today.month - 1) // 3
        next_q = cur_q + 1
        year = today.year
        if next_q > 3:
            next_q, year = 0, year + 1
        nq_start = datetime.date(year, next_q * 3 + 1, 1)
        nq_m_end = next_q * 3 + 3
        nq_end = (datetime.date(year, nq_m_end, 1) + datetime.timedelta(days=31)).replace(day=1) - datetime.timedelta(days=1)
        return and_(Project.estimated_finish_date >= nq_start, Project.estimated_finish_date <= nq_end)
    if key in ("2026", "2027"):
        yr = int(key)
        return and_(Project.estimated_finish_date >= datetime.date(yr, 1, 1),
                    Project.estimated_finish_date <= datetime.date(yr, 12, 31))
    return None


async def query_projects(
    session: AsyncSession,
    stages: list[str] | None,
    types: list[str] | None,
    mgrs: list[str] | None,
    ths: list[str] | None,
    dates: list[str] | None,
    page: int,
    type_key: int | None = None,
) -> tuple[list[Project], int]:
    """Query active projects with optional multi-value filters. Returns (rows, total).

    Each list param: None or [] = no filter; non-empty = filter (AND across dims, OR within).
    ths special value: ["__any__"] = any project with a non-empty to_handle.
    """
    base = select(Project).where(Project.is_active.is_(True))

    if type_key is not None and 0 <= type_key < len(TYPE_ORDER):
        base = base.where(Project.project_type == TYPE_ORDER[type_key])
    elif types:
        base = base.where(Project.project_type.in_(types))
    if stages:
        base = base.where(Project.stage.in_(stages))
    if mgrs:
        base = base.where(Project.manager.in_(mgrs))
    if ths == ["__any__"]:
        base = base.where(Project.to_handle.isnot(None), Project.to_handle != "")
    elif ths:
        base = base.where(Project.to_handle.in_(ths))

    if dates:
        clauses = [c for d in dates if (c := _date_clause(d)) is not None]
        if clauses:
            base = base.where(or_(*clauses))

    count_q = select(func.count()).select_from(base.subquery())
    total: int = await session.scalar(count_q) or 0

    type_order = case(
        *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
        else_=len(TYPE_ORDER),
    )
    rows_q = base.order_by(type_order, Project.id.desc()).offset(page * 10).limit(10)
    projects = list((await session.scalars(rows_q)).all())

    return projects, total
