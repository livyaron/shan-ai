"""Decisions menu — keyboards, formatters, and DB query."""

import html as _html
from datetime import datetime, timedelta

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Decision, DecisionDistribution, DecisionTypeEnum, DecisionStatusEnum, DecisionRaciRole, DecisionFeedback

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

RACI_BADGE = {"R": "R", "A": "A", "C": "C", "I": "I"}

# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard(feedback_count: int = 0) -> InlineKeyboardMarkup:
    feedback_label = f"⭐ ממתין למשוב ({feedback_count})" if feedback_count else "⭐ ממתין למשוב"
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
        [
            InlineKeyboardButton(feedback_label, callback_data="dm:feedback:0"),
        ],
    ])


def get_menu_shortcut_keyboard() -> InlineKeyboardMarkup:
    """Two-button keyboard appended to process() confirmation messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 ההחלטות שלי", callback_data="dm:my:0"),
        InlineKeyboardButton("📁 פרוייקטים",   callback_data="pm:menu"),
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

    raci = state.get("raci")
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
            _btn("RACI: הכל", "dm_cf:r:all", raci is None),
            _btn("R",          "dm_cf:r:R",   raci == "R"),
            _btn("A",          "dm_cf:r:A",   raci == "A"),
            _btn("C",          "dm_cf:r:C",   raci == "C"),
            _btn("I",          "dm_cf:r:I",   raci == "I"),
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

def format_result_line(d: Decision, raci_badge: str = "") -> str:
    t_emoji  = TYPE_EMOJI.get(d.type, "❓")
    s_emoji  = STATUS_EMOJI.get(d.status, "")
    s_label  = STATUS_LABEL.get(d.status, "")
    summary  = d.summary or ""
    if len(summary) > 40:
        summary = summary[:40] + "…"
    date_str = d.created_at.strftime("%d/%m") if d.created_at else ""
    date_part = f" · {date_str}" if date_str else ""
    raci_part = f"  <b>[{raci_badge}]</b>" if raci_badge else ""
    return f"{t_emoji} <b>#{d.id}</b> — {_html.escape(summary)}  {s_emoji} {s_label}{date_part}{raci_part}"


def format_results_message(title: str, decisions: list, total: int, page: int, raci_map: dict | None = None) -> str:
    if not decisions:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו החלטות."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(decisions)
    lines  = [
        f"‏<b>{_html.escape(title)}</b> ({total})",
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>",
        "──────────────────",
    ]
    lines.extend(format_result_line(d, (raci_map or {}).get(d.id, "")) for d in decisions)
    return "\n".join(lines)


def build_custom_filter_message() -> str:
    return (
        "‏🔍 <b>סינון מותאם אישית</b>\n\n"
        "בחר פילטרים ולחץ הצג:\n"
        "──────────────────\n"
        "👤 <b>מקור</b> · 🏷️ <b>סוג</b> · 📌 <b>סטטוס</b> · 📅 <b>תקופה</b>"
    )


def get_menu_text(counts: dict | None = None) -> str:
    if counts:
        stats = f"הגשתי: {counts['my']} · קיבלתי: {counts['recv']} · ממתינות: {counts['pending']}"
        return f"‏📋 <b>ההחלטות שלי</b>\n<i>{stats}</i>\n\nבחר תצוגה מהירה או סינון מותאם:"
    return _MENU_TEXT


async def get_menu_counts(session: AsyncSession, user_id: int) -> dict:
    recv_subq = (
        select(DecisionDistribution.decision_id)
        .where(DecisionDistribution.user_id == user_id)
        .scalar_subquery()
    )
    raci_subq = (
        select(DecisionRaciRole.decision_id)
        .where(DecisionRaciRole.user_id == user_id)
        .scalar_subquery()
    )
    my_count = await session.scalar(
        select(func.count(Decision.id)).where(Decision.submitter_id == user_id)
    ) or 0
    recv_count = await session.scalar(
        select(func.count(Decision.id)).where(Decision.id.in_(recv_subq))
    ) or 0
    pending_count = await session.scalar(
        select(func.count(Decision.id)).where(
            or_(Decision.submitter_id == user_id, Decision.id.in_(recv_subq)),
            Decision.status == DecisionStatusEnum.PENDING,
        )
    ) or 0
    rated_subq = (
        select(DecisionFeedback.decision_id)
        .where(DecisionFeedback.user_id == user_id)
        .scalar_subquery()
    )
    feedback_count = await session.scalar(
        select(func.count(func.distinct(Decision.id))).where(
            Decision.status.in_([DecisionStatusEnum.EXECUTED, DecisionStatusEnum.APPROVED]),
            Decision.submitter_id != user_id,
            Decision.id.notin_(rated_subq),
            or_(
                Decision.id.in_(recv_subq),
                Decision.id.in_(raci_subq),
            ),
        )
    ) or 0
    return {"my": my_count, "recv": recv_count, "pending": pending_count, "feedback": feedback_count}


# ── DB Query ───────────────────────────────────────────────────────────────

async def get_user_raci_roles(
    session: AsyncSession, decision_ids: list[int], user_id: int
) -> dict[int, str]:
    """Return {decision_id: role_letter} for decisions where user has a RACI role."""
    if not decision_ids:
        return {}
    rows = await session.execute(
        select(DecisionRaciRole.decision_id, DecisionRaciRole.role)
        .where(DecisionRaciRole.user_id == user_id)
        .where(DecisionRaciRole.decision_id.in_(decision_ids))
    )
    return {row.decision_id: row.role.value for row in rows}


async def query_pending_feedback(
    session: AsyncSession, user_id: int, page: int
) -> tuple[list[Decision], int]:
    """Completed decisions the user is involved in but has not yet rated."""
    recv_subq = (
        select(DecisionDistribution.decision_id)
        .where(DecisionDistribution.user_id == user_id)
        .scalar_subquery()
    )
    raci_subq = (
        select(DecisionRaciRole.decision_id)
        .where(DecisionRaciRole.user_id == user_id)
        .scalar_subquery()
    )
    rated_subq = (
        select(DecisionFeedback.decision_id)
        .where(DecisionFeedback.user_id == user_id)
        .scalar_subquery()
    )
    base_filter = [
        Decision.status.in_([DecisionStatusEnum.EXECUTED, DecisionStatusEnum.APPROVED]),
        Decision.submitter_id != user_id,
        Decision.id.notin_(rated_subq),
        or_(
            Decision.id.in_(recv_subq),
            Decision.id.in_(raci_subq),
        ),
    ]
    total = await session.scalar(
        select(func.count(func.distinct(Decision.id))).where(*base_filter)
    ) or 0
    rows = await session.execute(
        select(Decision).where(*base_filter)
        .order_by(Decision.created_at.desc())
        .limit(10).offset(page * 10)
    )
    return list(rows.scalars().all()), total


def build_feedback_results_keyboard(
    decisions: list, page: int, total: int
) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    for d in decisions:
        t_emoji = TYPE_EMOJI.get(d.type, "❓")
        summary = (d.summary or "")[:35]
        rows.append([InlineKeyboardButton(
            f"{t_emoji} #{d.id} — {summary}",
            callback_data=f"dm:fbsel:{d.id}:{page}",
        )])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"dm:feedback:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="dm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"dm:feedback:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu")])
    return InlineKeyboardMarkup(rows)


async def query_decisions(
    session: AsyncSession,
    user_id: int,
    owner: str,         # "my" | "recv" | "all"
    type_: str | None,  # "critical" | "normal" | "info" | "uncertain" | None
    status: str | None, # "pending" | "approved" | "rejected" | "executed" | None
    date_days: int,     # 0 = all time
    page: int,
    raci: str | None = None,  # "R" | "A" | "C" | "I" | None
) -> tuple[list[Decision], int]:
    recv_subq = (
        select(DecisionDistribution.decision_id)
        .where(DecisionDistribution.user_id == user_id)
        .scalar_subquery()
    )

    if owner == "my":
        base = select(Decision).where(Decision.submitter_id == user_id)
    elif owner == "recv":
        base = select(Decision).where(Decision.id.in_(recv_subq))
    else:  # "all" — no duplicates via OR
        base = select(Decision).where(
            or_(Decision.submitter_id == user_id, Decision.id.in_(recv_subq))
        )

    if type_:
        base = base.where(Decision.type == DecisionTypeEnum(type_))
    if status:
        base = base.where(Decision.status == DecisionStatusEnum(status))
    if date_days:
        cutoff = datetime.utcnow() - timedelta(days=date_days)
        base = base.where(Decision.created_at >= cutoff)
    if raci:
        base = (
            base
            .join(DecisionRaciRole, DecisionRaciRole.decision_id == Decision.id)
            .where(DecisionRaciRole.user_id == user_id)
            .where(DecisionRaciRole.role == raci)
        )

    count_q = select(func.count()).select_from(base.subquery())
    total: int = await session.scalar(count_q) or 0

    rows_q = base.order_by(Decision.created_at.desc()).offset(page * 10).limit(10)
    decisions = list((await session.scalars(rows_q)).all())

    return decisions, total
