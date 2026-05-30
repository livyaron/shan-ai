"""Weekly intelligence report v2.

generate_report_for_user(user, session, triggered_by_id, sent_via) -> dict
    Role-scoped data gather → single LLM call → JSON sections → persist ReportHistory row.

send_report_to_user(bot, chat_id, sections, recipient_label) -> None
    Sends each non-null section as a separate Telegram message (avoids 4096-char limit).

send_weekly_reports_cron(bot) -> None
    Cron entry point — sends self-report to every active non-VIEWER user.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, desc, or_, func, and_, case as sa_case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Decision, Project, RoleEnum, DecisionStatusEnum, DecisionTypeEnum,
    DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum,
    ReportHistory,
)
import app.database as _app_database
from app.services.llm_router import llm_chat
from app.services.projects_menu_service import TYPE_ORDER

logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    RoleEnum.PROJECT_MANAGER:          "מנהל פרויקט",
    RoleEnum.DEPARTMENT_MANAGER:       "מנהל מחלקה",
    RoleEnum.DEPUTY_DIVISION_MANAGER:  "סגן מנהל אגף",
    RoleEnum.DIVISION_MANAGER:         "מנהל אגף",
}

_SECTION_HEADERS = [
    ("prologue",  "📊 פתיח"),
    ("decisions", "📋 החלטות השבוע"),
    ("projects",  "🏗️ מצב פרויקטים"),
    ("summary",   "✅ סיכום ומסקנות"),
    ("delta",     "📈 שינויים מהדוח הקודם"),
]

_REPORT_PROMPT = """\
אתה מנהל PMO בכיר לתשתיות חשמל. צור דוח שבועי בעברית עבור {username} (תפקיד: {role_label}).
תאריך: שבוע {date_range}

--- נתוני קלט ---
החלטות (7 ימים אחרונים): {decisions_json}
אישורים ממתינים שלך: {pending_json}
פרויקטים באיחור: {behind_json}
פרויקטים בסיכון: {risks_json}
פרויקטים לטיפול (to_handle): {handle_json}
{delta_section}
--- הנחיות לפלט ---

prologue (50-70 מילה):
שלום {username}, 1-2 פריטים קריטיים לטיפול היום, ספירות (החלטות/פרויקטים/אישורים).

decisions (80-100 מילה):
ספירה לפי סוג, אחוז אישורים, רשימה ממוספרת של אישורים ממתינים (#ID + תיאור קצר).
דגל ⚠️ אם נפח חריג.

projects (120-160 מילה) — ניתוח PMO מלא:
לכל פרויקט באיחור: השתמש בשם הפרויקט (לא ב-identifier), ציין 🔴/🟡 + כמה ימים + שלב נוכחי + סיבת שורש קצרה מה-brief.
לסיכונים: דרג לפי חומרה, ציין בעלים אם חסר.
"חייב לפעול השבוע" — 3 פריטים ממוספרים ספציפיים (מי/מה/מתי).
תחזית: אם המגמה הנוכחית תמשך, מה יקרה ב-30 ימים הבאים?

summary (60-80 מילה):
הישג בולט, סיכון מרכזי, המלצה אחת לשבוע הבא. משפט עידוד. אופטימי.

delta: {has_delta} — אם "true": שינויים מדודים (↑↓%), שינויי שלב, סיכונים חדשים/נסגרו, מגמה. אם "false": null.

--- פורמט תשובה (JSON בלבד, ללא טקסט לפני ואחרי) ---
{{"prologue":"...","decisions":"...","projects":"...","summary":"...","delta":"..."}}"""

_FALLBACK_SECTIONS = {
    "prologue":  "‏⚠️ שגיאה בייצור הדוח. נסה שוב מאוחר יותר.",
    "decisions": None,
    "projects":  None,
    "summary":   None,
    "delta":     None,
}


def _sanitize_json_string(s: str) -> str:
    """Escape literal control characters inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and in_string:
            result.append(c)
            i += 1
            if i < len(s):
                result.append(s[i])
            i += 1
            continue
        if c == '"':
            in_string = not in_string
        elif in_string:
            if c == '\n':
                result.append('\\n')
                i += 1
                continue
            elif c == '\r':
                result.append('\\r')
                i += 1
                continue
            elif c == '\t':
                result.append('\\t')
                i += 1
                continue
            elif ord(c) < 0x20:
                i += 1
                continue
        result.append(c)
        i += 1
    return ''.join(result)


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_report_for_user(
    user: User,
    session: AsyncSession,
    triggered_by_id: int | None = None,
    sent_via: str = "telegram",
) -> dict:
    """Generate, persist, and return sections dict for one user."""
    role_label = _ROLE_LABELS.get(user.role, user.role.value if user.role else "משתמש")
    today_str  = datetime.utcnow().strftime("%d/%m/%Y")
    since_str  = (datetime.utcnow() - timedelta(days=7)).strftime("%d/%m/%Y")

    raw = await _gather_raw_data(user, session)

    # Fetch previous report for delta
    prev_row = await session.scalar(
        select(ReportHistory)
        .where(ReportHistory.user_id == user.id)
        .order_by(desc(ReportHistory.generated_at))
        .limit(1)
    )

    delta_section_text    = ""
    has_delta             = "false"
    overdue_entered_json  = "[]"
    overdue_resolved_json = "[]"

    if prev_row and prev_row.raw_data:
        delta_input = _compute_delta(raw, prev_row.raw_data)
        prev_date   = prev_row.generated_at.strftime("%d/%m/%Y")
        delta_section_text = (
            f"שינויים מהדוח הקודם ({prev_date}):\n"
            f"{json.dumps(delta_input, ensure_ascii=False)}\n"
        )
        has_delta             = "true"
        overdue_entered_json  = json.dumps(delta_input.get("overdue_entered",  []), ensure_ascii=False)
        overdue_resolved_json = json.dumps(delta_input.get("overdue_resolved", []), ensure_ascii=False)

    prompt = _REPORT_PROMPT.format(
        role_label=role_label,
        username=user.username or role_label,
        date_range=f"{since_str}–{today_str}",
        decisions_json=json.dumps(raw["decisions"], ensure_ascii=False),
        critical_urgent_json=json.dumps(
            raw["decisions"].get("critical_urgent", []), ensure_ascii=False
        ),
        pending_json=json.dumps(raw["pending_approvals"][:5], ensure_ascii=False),
        behind_json=json.dumps(raw["projects_behind"][:8], ensure_ascii=False),
        risks_json=json.dumps(raw["projects_at_risk"][:8], ensure_ascii=False),
        handle_json=json.dumps(raw["handle_items"][:3], ensure_ascii=False),
        type_summary_json=json.dumps(raw.get("project_type_summary", {}), ensure_ascii=False),
        delta_section=delta_section_text,
        has_delta=has_delta,
        overdue_entered_json=overdue_entered_json,
        overdue_resolved_json=overdue_resolved_json,
    )

    sections = dict(_FALLBACK_SECTIONS)
    try:
        raw_response = await llm_chat(
            "weekly_report",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
            temperature=0.3,
        )
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = _sanitize_json_string(cleaned)
        parsed = json.loads(cleaned)
        sections = {
            "prologue":  str(parsed.get("prologue") or ""),
            "decisions": parsed.get("decisions") or None,
            "projects":  parsed.get("projects") or None,
            "summary":   parsed.get("summary") or None,
            "delta":     parsed.get("delta") or None,
        }
        if not sections["prologue"]:
            sections["prologue"] = _FALLBACK_SECTIONS["prologue"]
    except Exception as exc:
        logger.error(f"Weekly report LLM/parse failed for user {user.id}: {exc}")

    row = ReportHistory(
        user_id=user.id,
        sections=sections,
        raw_data=raw,
        triggered_by=triggered_by_id,
        sent_via=sent_via,
    )
    session.add(row)
    await session.commit()

    return sections


async def send_report_to_user(
    bot,
    chat_id: int,
    sections: dict,
    recipient_label: str = "",
) -> None:
    """Send sections as sequential Telegram messages. Skips null sections."""
    for i, (key, header) in enumerate(_SECTION_HEADERS):
        body = sections.get(key)
        if not body:
            continue
        prefix = f"‏👤 דוח עבור: <b>{recipient_label}</b>\n\n" if (i == 0 and recipient_label) else ""
        text = f"‏<b>{header}</b>\n\n{prefix}{body}"[:4000]
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def send_weekly_reports_cron(bot) -> None:
    """Cron entry point — send self-report to every active non-VIEWER user."""
    # Fetch user list with a short-lived session
    async with _app_database.async_session_maker() as list_session:
        stmt = select(User).where(
            User.telegram_id.isnot(None),
            User.role.isnot(None),
        )
        all_users = (await list_session.execute(stmt)).scalars().all()
        users = [u for u in all_users if u.role != RoleEnum.VIEWER]

    # Each user gets its own session so one failure doesn't break others
    for user in users:
        try:
            async with _app_database.async_session_maker() as user_session:
                sections = await generate_report_for_user(user, user_session, sent_via="cron")
            await send_report_to_user(bot, user.telegram_id, sections)
            logger.info(f"Weekly cron report sent to user {user.id} ({user.username})")
        except Exception as exc:
            logger.error(f"Weekly cron report failed for user {user.id}: {exc}")


# ── Data gathering ────────────────────────────────────────────────────────────

async def _gather_raw_data(user: User, session: AsyncSession) -> dict:
    since = datetime.utcnow() - timedelta(days=7)
    today = datetime.utcnow().date()

    decisions    = await _decisions_summary(user, session, since)
    pending      = await _pending_approvals(user, session)
    behind       = await _projects_behind_schedule(user, session, today)
    at_risk      = await _risky_projects(user, session)
    handle       = await _handle_projects(user, session)
    stage_map, name_map = await _project_stage_map(user, session)
    type_summary = await _project_type_summary(user, session)

    return {
        "decisions":            decisions,
        "pending_approvals":    pending,
        "projects_behind":      behind,
        "projects_at_risk":     at_risk,
        "handle_items":         handle,
        "stage_map":            stage_map,
        "name_map":             name_map,
        "project_type_summary": type_summary,
    }


async def _decisions_summary(user: User, session: AsyncSession, since: datetime) -> dict:
    stmt = select(Decision).where(Decision.created_at >= since, Decision.is_relevant == True)
    if user.role == RoleEnum.PROJECT_MANAGER:
        stmt = stmt.where(Decision.submitter_id == user.id)
    elif user.role == RoleEnum.DEPARTMENT_MANAGER:
        sub_ids = await _subordinate_ids(user, session)
        if sub_ids:
            stmt = stmt.where(or_(
                Decision.submitter_id == user.id,
                Decision.submitter_id.in_(sub_ids),
            ))
        else:
            stmt = stmt.where(Decision.submitter_id == user.id)

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return {}

    type_counts: dict[str, int] = {}
    approved = 0
    for d in rows:
        t = d.type.value.upper() if d.type else "UNKNOWN"
        type_counts[t] = type_counts.get(t, 0) + 1
        if d.status == DecisionStatusEnum.APPROVED:
            approved += 1

    _critical_types = {DecisionTypeEnum.CRITICAL, DecisionTypeEnum.UNCERTAIN}
    critical_urgent = sorted(
        [d for d in rows if d.type in _critical_types],
        key=lambda d: d.created_at or datetime.min,
        reverse=True,
    )[:8]
    sample = [d for d in rows if d.type not in _critical_types][:5]

    return {
        "total":             len(rows),
        "by_type":           type_counts,
        "approval_rate_pct": round(approved / len(rows) * 100),
        "critical_urgent": [
            {
                "id":                 d.id,
                "type":               d.type.value if d.type else "",
                "summary":            (d.summary or "")[:80],
                "recommended_action": (d.recommended_action or "")[:120],
            }
            for d in critical_urgent
        ],
        "sample": [
            {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
            for d in sample
        ],
    }


async def _pending_approvals(user: User, session: AsyncSession) -> list[dict]:
    stmt = (
        select(Decision)
        .join(DecisionDistribution, DecisionDistribution.decision_id == Decision.id)
        .where(
            DecisionDistribution.user_id == user.id,
            DecisionDistribution.distribution_type == DistributionTypeEnum.APPROVAL,
            DecisionDistribution.status == DistributionStatusEnum.PENDING,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
        for d in rows
    ]


async def _projects_behind_schedule(user: User, session: AsyncSession, today) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.estimated_finish_date.isnot(None),
        Project.estimated_finish_date <= today,
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    type_order_expr = sa_case(
        *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
        else_=len(TYPE_ORDER),
    )
    stmt = stmt.order_by(type_order_expr, Project.estimated_finish_date.asc())
    rows = (await session.execute(stmt.limit(15))).scalars().all()

    result = []
    for p in rows:
        days_behind = (today - p.estimated_finish_date).days
        health = "🔴 קריטי" if days_behind > 30 else "🟡 באיחור"
        result.append({
            "project":     f"{p.name or p.project_identifier} ({p.project_identifier})",
            "stage":       p.stage or "",
            "finish_date": str(p.estimated_finish_date),
            "days_behind": days_behind,
            "health":      health,
            "brief":       (p.weekly_report_brief or "")[:200],
            "manager":     p.manager or "",
        })
    # Python-side sort ensures correct order even when DB ordering is mocked
    result.sort(key=lambda x: (
        TYPE_ORDER.index(next((t for t in TYPE_ORDER if t in x["project"]), ""))
        if any(t in x["project"] for t in TYPE_ORDER) else len(TYPE_ORDER),
        -x["days_behind"]
    ))
    return result


async def _risky_projects(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.risks.isnot(None),
        Project.risks != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    type_order_expr = sa_case(
        *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
        else_=len(TYPE_ORDER),
    )
    stmt = stmt.order_by(type_order_expr)
    rows = (await session.execute(stmt.limit(12))).scalars().all()

    result = [
        {
            "project": f"{p.name or p.project_identifier} ({p.project_identifier})",
            "stage":   p.stage or "",
            "risks":   (p.risks or "")[:100],
            "brief":   (p.weekly_report_brief or "")[:150],
        }
        for p in rows
    ]
    result.sort(key=lambda x: (
        TYPE_ORDER.index(next((t for t in TYPE_ORDER if t in x["project"]), ""))
        if any(t in x["project"] for t in TYPE_ORDER) else len(TYPE_ORDER)
    ))
    return result


async def _handle_projects(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.to_handle.isnot(None),
        Project.to_handle != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(20))).scalars().all()
    return [
        {"project": f"{p.name or p.project_identifier} ({p.project_identifier})", "to_handle": (p.to_handle or "")[:120]}
        for p in rows
    ]


async def _project_type_summary(user: User, session: AsyncSession) -> dict:
    """Count active/delayed/at_risk projects per TYPE_ORDER type. Role-scoped."""
    today = datetime.utcnow().date()

    base_filters = [Project.is_active.is_(True)]
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        base_filters.append(Project.manager.ilike(f"%{user.username}%"))

    stmt = select(
        Project.project_type,
        func.count().label("active"),
        func.sum(
            sa_case(
                (
                    and_(
                        Project.estimated_finish_date.isnot(None),
                        Project.estimated_finish_date <= today,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("delayed"),
        func.sum(
            sa_case(
                (
                    and_(
                        Project.risks.isnot(None),
                        Project.risks != "",
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("at_risk"),
    ).where(*base_filters).group_by(Project.project_type)

    rows = (await session.execute(stmt)).all()
    counts = {
        row[0]: {"active": row[1], "delayed": int(row[2] or 0), "at_risk": int(row[3] or 0)}
        for row in rows
        if row[0]
    }
    return {t: counts.get(t, {"active": 0, "delayed": 0, "at_risk": 0}) for t in TYPE_ORDER}


async def _project_stage_map(user: User, session: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    stmt = select(Project.project_identifier, Project.stage, Project.name).where(Project.is_active == True)
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(200))).all()
    stage_map = {row[0]: (row[1] or "") for row in rows if row[0]}
    name_map  = {row[0]: (row[2] or row[0]) for row in rows if row[0]}
    return stage_map, name_map


async def _subordinate_ids(user: User, session: AsyncSession) -> list[int]:
    rows = (await session.execute(
        select(User.id).where(User.manager_id == user.id)
    )).scalars().all()
    return list(rows)


def _compute_delta(current: dict, prev: dict) -> dict:
    """Compute structured diff between current and previous raw_data snapshots."""
    c_dec = current.get("decisions") or {}
    p_dec = prev.get("decisions") or {}

    curr_total = c_dec.get("total", 0)
    prev_total = p_dec.get("total", 0)

    curr_stages = current.get("stage_map", {})
    prev_stages = prev.get("stage_map", {})
    curr_names  = current.get("name_map", {})
    prev_names  = prev.get("name_map", {})

    stage_changes = [
        {
            "id":   k,
            "name": curr_names.get(k, k),
            "from": prev_stages[k],
            "to":   curr_stages[k],
        }
        for k in curr_stages
        if k in prev_stages and curr_stages[k] != prev_stages[k]
    ]

    curr_risk_ids = {p.get("project") or p.get("identifier", "") for p in current.get("projects_at_risk", [])}
    prev_risk_ids = {p.get("project") or p.get("identifier", "") for p in prev.get("projects_at_risk", [])}

    curr_behind = current.get("projects_behind", [])
    prev_behind = prev.get("projects_behind", [])
    curr_behind_names = {p["project"] for p in curr_behind}
    prev_behind_names = {p["project"] for p in prev_behind}

    overdue_entered = [
        {"name": p["project"], "days_behind": p["days_behind"]}
        for p in curr_behind
        if p["project"] not in prev_behind_names
    ]
    overdue_resolved = list(prev_behind_names - curr_behind_names)

    return {
        "decisions_change":         curr_total - prev_total,
        "prev_decisions_total":     prev_total,
        "curr_decisions_total":     curr_total,
        "prev_approval_rate_pct":   p_dec.get("approval_rate_pct", 0),
        "curr_approval_rate_pct":   c_dec.get("approval_rate_pct", 0),
        "pending_approvals_change": (
            len(current.get("pending_approvals", [])) -
            len(prev.get("pending_approvals", []))
        ),
        "stage_changes":            stage_changes,
        "new_risks":                list(curr_risk_ids - prev_risk_ids),
        "resolved_risks":           list(prev_risk_ids - curr_risk_ids),
        "behind_schedule_change":   (
            len(current.get("projects_behind", [])) -
            len(prev.get("projects_behind", []))
        ),
        "overdue_entered":          overdue_entered,
        "overdue_resolved":         overdue_resolved,
    }
