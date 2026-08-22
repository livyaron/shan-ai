"""Operations Room (חדר מבצעים) — keyboards, formatters, and DB queries.

Shared layer used by the Telegram handlers, the web war-room router, and the
cron jobs. Pure functions only — never imports the bot (callers do the sends).
"""

import html as _html
import datetime
import re
from zoneinfo import ZoneInfo

from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Mission, MissionStatusEnum, MissionUpdate, User, RoleEnum

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# ── Constants ──────────────────────────────────────────────────────────────

# key → (emoji, verb, axis legend)
QUADRANTS: list[tuple[str, str, str, str]] = [
    ("do",       "🔥", "בצע עכשיו",   "דחוף · חשוב"),
    ("plan",     "🗓", "תכנן",         "חשוב · לא דחוף"),
    ("delegate", "🤝", "האצל",         "דחוף · לא חשוב"),
    ("backlog",  "🧺", "מאגר משימות", "לא דחוף · לא חשוב"),
]

_QUADRANT_FLAGS = {
    "do":       (True,  True),
    "plan":     (False, True),
    "delegate": (True,  False),
    "backlog":  (False, False),
}

STATUS_LABELS = {
    "open":      "⬜ פתוחה",
    "done":      "✅ הושלמה",
    "cancelled": "🚫 בוטלה",
}

# The board is open/closed only — see MissionStatusEnum. Every "is this mission
# live?" question in the app funnels through this list; never inline the values.
ACTIVE_STATUSES = [MissionStatusEnum.OPEN.value]

DUE_QUICK_PICKS = [
    ("today", "היום"),
    ("tomorrow", "מחר"),
    ("week", "עוד שבוע"),
    ("custom", "📅 תאריך אחר"),
    ("none", "ללא תאריך"),
]

PAGE_SIZE = 10
HISTORY_LIMIT = 15


# ── Helpers ────────────────────────────────────────────────────────────────

def today_il() -> datetime.date:
    return datetime.datetime.now(_IL_TZ).date()


def format_stamp_il(dt: datetime.datetime | None) -> str:
    """Naive-UTC timestamp (as stored) → Israel-local 'DD/MM HH:MM'."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(_IL_TZ).strftime("%d/%m %H:%M")


def quadrant_key(m: Mission) -> str:
    for key, (urg, imp) in _QUADRANT_FLAGS.items():
        if bool(m.is_urgent) == urg and bool(m.is_important) == imp:
            return key
    return "backlog"


def quadrant_flags(key: str) -> tuple[bool, bool]:
    """Return (is_urgent, is_important) for a quadrant key."""
    return _QUADRANT_FLAGS.get(key, (False, False))


def quadrant_label(key: str, with_axis: bool = False) -> str:
    for k, emoji, verb, axis in QUADRANTS:
        if k == key:
            return f"{emoji} {verb} ({axis})" if with_axis else f"{emoji} {verb}"
    return key


def is_overdue(m: Mission, today: datetime.date | None = None) -> bool:
    """The single overdue rule: active + due_date strictly before today (IL)."""
    if m.status not in ACTIVE_STATUSES:
        return False
    if m.due_date is None:
        return False
    return m.due_date < (today or today_il())


def resolve_due_quick_pick(key: str, today: datetime.date | None = None):
    """Map a quick-pick key to a date. Returns (handled, date|None); 'custom' is not handled."""
    today = today or today_il()
    if key == "today":
        return True, today
    if key == "tomorrow":
        return True, today + datetime.timedelta(days=1)
    if key == "week":
        return True, today + datetime.timedelta(days=7)
    if key == "none":
        return True, None
    return False, None


def parse_due_date_text(text: str, today: datetime.date | None = None) -> datetime.date | None:
    """Parse DD/MM or DD/MM/YYYY (also with '.' or '-'). Returns None on failure."""
    today = today or today_il()
    cleaned = text.strip().replace(".", "/").replace("-", "/")
    parts = cleaned.split("/")
    try:
        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            year = today.year
            candidate = datetime.date(year, month, day)
            if candidate < today:
                candidate = datetime.date(year + 1, month, day)
            return candidate
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return datetime.date(year, month, day)
    except (ValueError, TypeError):
        return None
    return None


def format_due(d: datetime.date | None) -> str:
    # DD/MM/YYYY only — digits are direction-neutral so this stays readable in RTL.
    return d.strftime("%d/%m/%Y") if d else "—"


def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard(counts: dict[str, int]) -> InlineKeyboardMarkup:
    # Origin tokens are colon-free ("qdo", "my", "late", "hist") so list/detail
    # callbacks can be split(':') safely.
    rows = []
    for pair in _chunk(QUADRANTS, 2):
        rows.append([
            InlineKeyboardButton(
                f"{emoji} {verb} ({counts.get(key, 0)})",
                callback_data=f"om:q{key}:0",
            )
            for key, emoji, verb, _axis in pair
        ])
    rows.append([
        InlineKeyboardButton("👤 המשימות שלי", callback_data="om:my:0"),
        InlineKeyboardButton("⚠️ באיחור",      callback_data="om:late:0"),
    ])
    rows.append([
        InlineKeyboardButton("✅ הושלמו",       callback_data="om:hist:0"),
        InlineKeyboardButton("➕ משימה חדשה",  callback_data="om:new"),
    ])
    rows.append([
        InlineKeyboardButton("📊 דוח Excel", callback_data="om:xls"),
        InlineKeyboardButton("🔄 רענן",      callback_data="om:xlsr"),
        InlineKeyboardButton("🧠 סיכום AI",  callback_data="om:sum"),
    ])
    return InlineKeyboardMarkup(rows)


def button_title(m: Mission, index: int) -> str:
    """Label for a mission's button in a list — the title itself, not "פרטים".

    Titles are stored raw but rendered through HTML elsewhere; button labels are
    plain text, so strip tags and unescape (same treatment as the projects menu).
    """
    title = _html.unescape(re.sub(r"<[^>]+>", "", m.title or "")).strip()
    if len(title) > 38:
        title = title[:38] + "…"
    emoji = next((e for k, e, _v, _a in QUADRANTS if k == quadrant_key(m)), "•")
    late = " ⚠️" if is_overdue(m) else ""
    return f"{index}. {emoji} {title}{late}"


def build_results_keyboard(
    origin: str,
    page: int,
    total: int,
    missions: list[Mission],
    with_done_shortcut: bool = False,
) -> InlineKeyboardMarkup:
    """List view: one button per mission, labelled with its title (+ optional ✅), nav, back."""
    rows = []
    for i, m in enumerate(missions, start=page * PAGE_SIZE + 1):
        btns = [InlineKeyboardButton(button_title(m, i), callback_data=f"om:d:{m.id}:{origin}:{page}")]
        if with_done_shortcut and m.status in ACTIVE_STATUSES:
            btns.append(InlineKeyboardButton("✅", callback_data=f"om:ld:{m.id}:{origin}:{page}"))
        rows.append(btns)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"om:{origin}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="om:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"om:{origin}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 חדר מבצעים", callback_data="om:menu")])
    return InlineKeyboardMarkup(rows)


def build_mission_card_keyboard(
    m: Mission, origin: str, page: int, show_all_updates: bool = False,
) -> InlineKeyboardMarkup:
    tail = f"{m.id}:{origin}:{page}"
    rows = []
    if m.status in ACTIVE_STATUSES:
        rows.append([
            InlineKeyboardButton("➕ הוסף סטטוס", callback_data=f"om:a:note:{tail}"),
            InlineKeyboardButton("✅ בוצע",        callback_data=f"om:a:done:{tail}"),
        ])
        rows.append([
            InlineKeyboardButton("🔀 שנה רביע",   callback_data=f"om:a:quad:{tail}"),
            InlineKeyboardButton("👤 שנה אחראי",  callback_data=f"om:a:own:{tail}"),
        ])
        rows.append([
            InlineKeyboardButton("📅 שנה תאריך",  callback_data=f"om:a:due:{tail}"),
            InlineKeyboardButton("🚫 בטל משימה",  callback_data=f"om:a:cancel:{tail}"),
        ])
    else:
        rows.append([InlineKeyboardButton("↩️ פתח מחדש", callback_data=f"om:a:reopen:{tail}")])
    # The card shows only the latest update by default — this toggles the full log.
    n_updates = len(get_mission_updates(m))
    if n_updates > 1:
        rows.append([
            InlineKeyboardButton("🔼 עדכון אחרון בלבד", callback_data=f"om:d:{tail}")
            if show_all_updates else
            InlineKeyboardButton(f"🗒 כל העדכונים ({n_updates})", callback_data=f"om:u:{tail}")
        ])
    rows.append([
        InlineKeyboardButton("🔙 חזרה לרשימה", callback_data=f"om:{origin}:{page}"),
        InlineKeyboardButton("🏠 חדר מבצעים",  callback_data="om:menu"),
    ])
    return InlineKeyboardMarkup(rows)


# Short status tokens keep om:cl callbacks well inside Telegram's 64-byte limit.
CLOSE_TOKENS = {
    "d": MissionStatusEnum.DONE.value,
    "x": MissionStatusEnum.CANCELLED.value,
}
CLOSE_TOKEN_BY_STATUS = {v: k for k, v in CLOSE_TOKENS.items()}


def build_close_prompt_keyboard(
    status: str, mission_id: int, origin: str, page: int, back_cd: str,
) -> InlineKeyboardMarkup:
    """Closing-note prompt: write a note, or close without one. Never blocking."""
    token = CLOSE_TOKEN_BY_STATUS.get(status, "d")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "⏭ סגור בלי הערה", callback_data=f"om:cl:{token}:{mission_id}:{origin}:{page}"
        )],
        [InlineKeyboardButton("❌ ביטול", callback_data=back_cd)],
    ])


def close_prompt_text(status: str) -> str:
    """Hebrew prompt shown before a mission is closed."""
    if status == MissionStatusEnum.CANCELLED.value:
        return ("\u200f🚫 <b>ביטול משימה</b>\n"
                "שלח את <b>סיבת הביטול</b> כטקסט חופשי — היא תישמר כעדכון סטטוס "
                "עם השם שלך והשעה, או סגור בלי הערה.")
    return ("\u200f✅ <b>סגירת משימה</b>\n"
            "שלח תיאור קצר של <b>מה בוצע</b> — הוא יישמר כעדכון סטטוס עם השם שלך "
            "והשעה, או סגור בלי הערה.")


def build_quadrant_pick_keyboard(prefix: str, abort_cd: str = "om:c:abort") -> InlineKeyboardMarkup:
    """4-button quadrant picker. prefix e.g. 'om:c:qd' (wizard) or 'om:e:qd:{id}:{origin}:{page}' (edit)."""
    rows = [
        [InlineKeyboardButton(f"{emoji} {verb} — {axis}", callback_data=f"{prefix}:{key}")]
        for key, emoji, verb, axis in QUADRANTS
    ]
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data=abort_cd)])
    return InlineKeyboardMarkup(rows)


def build_owner_pick_keyboard(
    users: list[User], prefix: str, include_me: bool = True, abort_cd: str = "om:c:abort",
) -> InlineKeyboardMarkup:
    """Owner picker — ids live in state, callback carries only the index."""
    rows = []
    if include_me:
        rows.append([InlineKeyboardButton("👤 אני", callback_data=f"{prefix}:me")])
    for idx, u in enumerate(users):
        rows.append([InlineKeyboardButton(u.username or f"#{u.id}", callback_data=f"{prefix}:{idx}")])
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data=abort_cd)])
    return InlineKeyboardMarkup(rows)


def build_due_pick_keyboard(prefix: str, abort_cd: str = "om:c:abort") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in DUE_QUICK_PICKS
    ]
    rows.append([InlineKeyboardButton("❌ ביטול", callback_data=abort_cd)])
    return InlineKeyboardMarkup(rows)


def build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ שמור משימה", callback_data="om:c:save"),
        InlineKeyboardButton("❌ ביטול",       callback_data="om:c:abort"),
    ]])


def build_skip_desc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭ דלג", callback_data="om:c:skipdesc"),
        InlineKeyboardButton("❌ ביטול", callback_data="om:c:abort"),
    ]])


def build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ ביטול", callback_data="om:c:abort")]])


def build_digest_keyboard(missions: list[Mission]) -> InlineKeyboardMarkup | None:
    """One-tap ✔️ done button per mission in the daily digest."""
    rows = []
    for m in missions:
        title = (m.title or "")[:28]
        rows.append([InlineKeyboardButton(f"✔️ {title}", callback_data=f"om:dg:done:{m.id}")])
    return InlineKeyboardMarkup(rows) if rows else None


# ── Formatters ─────────────────────────────────────────────────────────────

def get_menu_text(counts: dict[str, int], overdue: int) -> str:
    total = sum(counts.values())
    lines = [
        "‏🎯 <b>חדר מבצעים</b>",
        f"<i>{total} משימות פעילות" + (f" · ⚠️ {overdue} באיחור" if overdue else "") + "</i>",
        "",
    ]
    for key, emoji, verb, axis in QUADRANTS:
        lines.append(f"{emoji} <b>{verb}</b> ({counts.get(key, 0)}) — <i>{axis}</i>")
    lines.append("")
    lines.append("בחר תצוגה:")
    return "\n".join(lines)


def format_mission_line(m: Mission, today: datetime.date | None = None) -> str:
    title = m.title or ""
    if len(title) > 40:
        title = title[:40] + "…"
    key = quadrant_key(m)
    emoji = next(e for k, e, _v, _a in QUADRANTS if k == key)
    parts = [f"{emoji} {_html.escape(title)}"]
    owner_name = m.owner.username if m.owner else None
    if owner_name:
        parts.append(f"👤 {_html.escape(owner_name)}")
    if m.due_date:
        due = format_due(m.due_date)
        if is_overdue(m, today):
            due += " ⚠️"
        parts.append(f"📅 {due}")
    return "  |  ".join(parts)


def format_results_message(title: str, missions: list[Mission], total: int, page: int) -> str:
    if not missions:
        return f"‏<b>{_html.escape(title)}</b>\n\nאין משימות בתצוגה זו."
    today = today_il()
    from_n = page * PAGE_SIZE + 1
    lines = [f"‏<b>{_html.escape(title)}</b> ({total})"]
    for i, m in enumerate(missions, start=from_n):
        lines.append(f"{i}. {format_mission_line(m, today)}")
    return "\n".join(lines)


def get_mission_updates(m: Mission) -> list[MissionUpdate]:
    """Loaded status updates, oldest first. [] when the log was not eager-loaded.

    Never triggers a lazy load: under asyncio that raises MissingGreenlet.
    Callers that want the log eager-load Mission.updates (see get_mission).
    """
    from sqlalchemy import inspect as _inspect
    if "updates" in _inspect(m).unloaded:
        return []
    return list(m.updates or [])


def _update_line(u: MissionUpdate, esc) -> str:
    who = u.author_name or (u.author.username if u.author else "—")
    lock = "🔒 " if u.kind == "close" else ""
    return f"• {lock}{format_stamp_il(u.created_at)} · {esc(who)} — {esc(u.text or '')}"


def format_updates_block(
    m: Mission,
    html: bool = True,
    latest_only: bool = False,
    limit: int | None = None,
) -> str:
    """Status-update log, rendered as part of the mission description block.

    Returns "" when the mission has no updates. `html=False` yields plain text
    for the XLSX report / AI prompt payload. `latest_only` shows just the newest
    entry — what the mission cards show by default, so a long log never buries
    the card. `limit` keeps the newest N when the whole log is shown.
    """
    updates = get_mission_updates(m)
    if not updates:
        return ""
    esc = _html.escape if html else (lambda t: t)
    total = len(updates)

    if latest_only:
        head = "🗒 <b>עדכון אחרון:</b>" if html else "עדכון אחרון:"
        if total > 1:
            more = f"(מתוך {total} עדכונים)"
            head += f" <i>{more}</i>" if html else f" {more}"
        return f"{head}\n{_update_line(updates[-1], esc)}"

    shown = updates
    footer = ""
    if limit and total > limit:
        shown = updates[-limit:]
        note = f"מוצגים {limit} העדכונים האחרונים מתוך {total}"
        footer = f"<i>…{note}</i>" if html else f"…{note}"

    head = "🗒 <b>עדכוני סטטוס:</b>" if html else "עדכוני סטטוס:"
    lines = [head] + [_update_line(u, esc) for u in shown]
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def build_mission_card(m: Mission, show_all_updates: bool = False) -> str:
    """Mission detail card. Shows the latest status update; the full log on demand."""
    key = quadrant_key(m)
    today = today_il()
    due_line = format_due(m.due_date)
    if is_overdue(m, today):
        due_line += " ⚠️ באיחור"
    owner_name = m.owner.username if m.owner else "—"
    creator = m.created_by.username if m.created_by else "—"
    created_str = m.created_at.strftime("%d/%m/%Y") if m.created_at else "—"
    desc = f"\n📝 {_html.escape(m.description)}\n" if m.description else ""
    updates = format_updates_block(
        m,
        latest_only=not show_all_updates,
        limit=HISTORY_LIMIT if show_all_updates else None,
    )
    if updates:
        desc += f"\n{updates}\n"
    return (
        f"‏🎯 <b>משימה #{m.id}</b>\n"
        f"<b>{_html.escape(m.title or '')}</b>\n"
        "──────────────────\n"
        f"{quadrant_label(key, with_axis=True)}\n"
        f"📊 <b>סטטוס:</b> {STATUS_LABELS.get(m.status, m.status)}\n"
        f"👤 <b>אחראי:</b> {_html.escape(owner_name)}\n"
        f"📅 <b>יעד:</b> {due_line}\n"
        f"{desc}"
        "──────────────────\n"
        f"<i>נפתחה ע\"י {_html.escape(creator)} · {created_str}</i>"
    )


def format_create_progress(state: dict) -> str:
    """Wizard progress summary shown at each step."""
    lines = ["‏➕ <b>משימה חדשה</b>"]
    if state.get("title"):
        lines.append(f"📌 {_html.escape(state['title'])}")
    if state.get("description"):
        lines.append(f"📝 {_html.escape(state['description'][:60])}")
    if state.get("quadrant"):
        lines.append(quadrant_label(state["quadrant"], with_axis=True))
    if state.get("owner_name"):
        lines.append(f"👤 {_html.escape(state['owner_name'])}")
    if "due_date" in state:
        lines.append(f"📅 {format_due(state['due_date'])}")
    return "\n".join(lines)


def format_digest(
    missions: list[Mission],
    board_totals: tuple[int, int] | None = None,
) -> str:
    """Morning digest for one owner. board_totals=(open, overdue) adds a manager line."""
    today = today_il()
    overdue = [m for m in missions if is_overdue(m, today)]
    rest = [m for m in missions if not is_overdue(m, today)]
    lines = ["‏🎯 <b>חדר מבצעים — תמונת בוקר</b>"]
    if overdue:
        lines.append("")
        lines.append(f"⚠️ <b>באיחור ({len(overdue)}):</b>")
        for m in overdue:
            lines.append(f"• {format_mission_line(m, today)}")
    for key, emoji, verb, _axis in QUADRANTS:
        group = [m for m in rest if quadrant_key(m) == key]
        if not group:
            continue
        lines.append("")
        lines.append(f"{emoji} <b>{verb}:</b>")
        for m in group:
            lines.append(f"• {format_mission_line(m, today)}")
    if board_totals:
        b_open, b_late = board_totals
        lines.append("")
        lines.append(f"📊 <i>סה\"כ בחדר המבצעים: {b_open} פתוחות, {b_late} באיחור</i>")
    return "\n".join(lines)


# ── DB Queries ─────────────────────────────────────────────────────────────

def _priority_order():
    """Urgent+important first, then by quadrant weight."""
    return case(
        (Mission.is_urgent & Mission.is_important, 0),
        (Mission.is_important, 1),
        (Mission.is_urgent, 2),
        else_=3,
    )


async def get_board_counts(session: AsyncSession) -> tuple[dict[str, int], int]:
    """Active-mission count per quadrant + overdue count."""
    rows = (await session.execute(
        select(Mission.is_urgent, Mission.is_important, func.count(Mission.id))
        .where(Mission.status.in_(ACTIVE_STATUSES))
        .group_by(Mission.is_urgent, Mission.is_important)
    )).all()
    counts = {k: 0 for k, *_ in QUADRANTS}
    for urg, imp, n in rows:
        for key, (q_urg, q_imp) in _QUADRANT_FLAGS.items():
            if bool(urg) == q_urg and bool(imp) == q_imp:
                counts[key] += n
    overdue = await session.scalar(
        select(func.count(Mission.id)).where(
            Mission.status.in_(ACTIVE_STATUSES),
            Mission.due_date.isnot(None),
            Mission.due_date < today_il(),
        )
    ) or 0
    return counts, overdue


async def query_missions(
    session: AsyncSession,
    quadrant: str | None = None,
    owner_id: int | None = None,
    only_overdue: bool = False,
    statuses: list[str] | None = None,
    page: int = 0,
) -> tuple[list[Mission], int]:
    from sqlalchemy.orm import selectinload
    base = select(Mission).where(
        Mission.status.in_(statuses if statuses is not None else ACTIVE_STATUSES)
    )
    if quadrant:
        urg, imp = quadrant_flags(quadrant)
        base = base.where(Mission.is_urgent.is_(urg), Mission.is_important.is_(imp))
    if owner_id is not None:
        base = base.where(Mission.owner_id == owner_id)
    if only_overdue:
        base = base.where(Mission.due_date.isnot(None), Mission.due_date < today_il())

    total: int = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows_q = (
        base.options(selectinload(Mission.owner), selectinload(Mission.created_by))
        .order_by(_priority_order(), Mission.due_date.asc().nulls_last(), Mission.id.desc())
        .offset(page * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    missions = list((await session.scalars(rows_q)).all())
    return missions, total


async def get_done_history(session: AsyncSession, page: int = 0) -> tuple[list[Mission], int]:
    from sqlalchemy.orm import selectinload
    base = select(Mission).where(Mission.status == MissionStatusEnum.DONE.value)
    total: int = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows_q = (
        base.options(selectinload(Mission.owner), selectinload(Mission.created_by))
        .order_by(Mission.completed_at.desc().nulls_last())
        .offset(page * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return list((await session.scalars(rows_q)).all()), total


async def get_mission(session: AsyncSession, mission_id: int) -> Mission | None:
    from sqlalchemy.orm import selectinload
    return await session.scalar(
        select(Mission)
        .options(
            selectinload(Mission.owner),
            selectinload(Mission.created_by),
            selectinload(Mission.updates).selectinload(MissionUpdate.author),
        )
        .where(Mission.id == mission_id)
    )


async def create_mission(
    session: AsyncSession,
    title: str,
    description: str | None,
    is_urgent: bool,
    is_important: bool,
    owner_id: int,
    created_by_id: int | None,
    due_date: datetime.date | None,
) -> Mission:
    m = Mission(
        title=title.strip()[:255],
        description=(description or None),
        is_urgent=is_urgent,
        is_important=is_important,
        owner_id=owner_id,
        created_by_id=created_by_id,
        due_date=due_date,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


async def set_status(session: AsyncSession, m: Mission, new_status: str) -> Mission:
    """Idempotent status change; manages completed_at."""
    if m.status == new_status:
        return m
    m.status = new_status
    if new_status == MissionStatusEnum.DONE.value:
        m.completed_at = datetime.datetime.utcnow()
    elif new_status in ACTIVE_STATUSES:
        m.completed_at = None
    await session.commit()
    return m


async def add_mission_update(
    session: AsyncSession,
    m: Mission,
    text: str,
    author: User | None,
    kind: str | None = None,
) -> MissionUpdate:
    """Append a status update to a mission. author_name is snapshotted on purpose.

    kind=None is an ordinary update; kind="close" is the note written while
    closing the mission.
    """
    upd = MissionUpdate(
        mission_id=m.id,
        text=text.strip()[:2000],
        kind=kind,
        author_id=author.id if author else None,
        author_name=(author.username if author else None),
    )
    session.add(upd)
    # Reporting treats updated_at as "last touched" (the STALE_DAYS figure), so a
    # mission being actively reported on must not read as abandoned.
    m.updated_at = datetime.datetime.utcnow()
    await session.commit()
    await session.refresh(upd)
    return upd


async def update_mission(
    session: AsyncSession,
    m: Mission,
    *,
    owner_id: int | None = None,
    due_date: object = "__unset__",
    quadrant: str | None = None,
) -> Mission:
    if owner_id is not None:
        m.owner_id = owner_id
    if due_date != "__unset__":
        m.due_date = due_date  # type: ignore[assignment]
        m.overdue_notified_at = None  # re-arm the overdue alert
    if quadrant is not None:
        m.is_urgent, m.is_important = quadrant_flags(quadrant)
    await session.commit()
    return m


async def list_assignable_users(session: AsyncSession, exclude_id: int | None = None) -> list[User]:
    q = (
        select(User)
        .where(User.role.isnot(None), User.role != RoleEnum.VIEWER)
        .order_by(User.username)
    )
    users = list((await session.scalars(q)).all())
    if exclude_id is not None:
        users = [u for u in users if u.id != exclude_id]
    return users


async def get_users_with_active_missions(session: AsyncSession) -> list[int]:
    rows = await session.scalars(
        select(Mission.owner_id).where(Mission.status.in_(ACTIVE_STATUSES)).distinct()
    )
    return list(rows.all())


async def query_overdue_unnotified(session: AsyncSession) -> list[Mission]:
    from sqlalchemy.orm import selectinload
    q = (
        select(Mission)
        .options(selectinload(Mission.owner))
        .where(
            Mission.status.in_(ACTIVE_STATUSES),
            Mission.due_date.isnot(None),
            Mission.due_date < today_il(),
            Mission.overdue_notified_at.is_(None),
        )
    )
    return list((await session.scalars(q)).all())
