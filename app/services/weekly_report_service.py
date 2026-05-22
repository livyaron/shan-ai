"""Weekly intelligence report — role-scoped AI digest sent every Thursday at 17:00 Israel time."""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Decision, Project, RoleEnum, DecisionStatusEnum
import app.database as _app_database
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    RoleEnum.PROJECT_MANAGER:          "מנהל פרויקט",
    RoleEnum.DEPARTMENT_MANAGER:       "מנהל מחלקה",
    RoleEnum.DEPUTY_DIVISION_MANAGER:  "סגן מנהל אגף",
    RoleEnum.DIVISION_MANAGER:         "מנהל אגף",
}

_REPORT_PROMPT = """\
אתה עוזר BI לצוות תשתיות חשמל. צור סיכום שבועי תמציתי בעברית (עד 300 מילה).
תפקיד המקבל: {role}

נתונים:
- החלטות השבוע: {decisions_json}
- פרויקטים עם סיכונים: {risks_json}
- פרויקטים לטיפול: {handle_json}

כלול: ספירה לפי סוג, אחוז אישורים, 2–3 ממצאים בולטים, אנומליות אם קיימות.
סיים עם משפט עידוד קצר.
טקסט עברית בלבד — ללא JSON, ללא markdown."""


async def generate_report_for_user(user: User, session: AsyncSession) -> str:
    """Build a role-scoped weekly report for one user. Returns Hebrew HTML-safe string."""
    since = datetime.utcnow() - timedelta(days=7)
    role_label = _ROLE_LABELS.get(user.role, user.role.value if user.role else "משתמש")

    decisions_data = await _decisions_for_role(user, session, since)
    risks_data = await _risky_projects_for_role(user, session)
    handle_data = await _handle_projects_for_role(user, session)

    if not decisions_data and not risks_data and not handle_data:
        return f"‏📊 <b>דוח שבועי — {role_label}</b>\n\nלא נמצאו נתונים לסיכום השבוע."

    prompt = _REPORT_PROMPT.format(
        role=role_label,
        decisions_json=json.dumps(decisions_data, ensure_ascii=False),
        risks_json=json.dumps(risks_data[:5], ensure_ascii=False),
        handle_json=json.dumps(handle_data[:5], ensure_ascii=False),
    )
    try:
        body = await llm_chat(
            "weekly_report",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        return f"‏📊 <b>דוח שבועי — {role_label}</b>\n\n{body.strip()}"
    except Exception as exc:
        logger.error(f"Weekly report LLM call failed for user {user.id}: {exc}")
        return "‏⚠️ לא הצלחתי לייצר דוח שבועי. נסה שוב מאוחר יותר."


async def send_weekly_reports(bot) -> None:
    """Send weekly report to every active non-VIEWER user that has a telegram_id."""
    async with _app_database.async_session_maker() as session:
        stmt = select(User).where(
            User.telegram_id.isnot(None),
            User.role.isnot(None),
        )
        all_users = (await session.execute(stmt)).scalars().all()
        # Filter VIEWER in Python to avoid DB enum mismatch on legacy schemas
        users = [u for u in all_users if u.role != RoleEnum.VIEWER]

        for user in users:
            try:
                text = await generate_report_for_user(user, session)
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
                logger.info(f"Weekly report sent to user {user.id} ({user.username})")
            except Exception as exc:
                logger.error(f"Weekly report send failed for user {user.id}: {exc}")


async def _decisions_for_role(user: User, session: AsyncSession, since: datetime) -> list[dict]:
    stmt = select(Decision).where(Decision.created_at >= since)

    if user.role == RoleEnum.PROJECT_MANAGER:
        stmt = stmt.where(Decision.submitter_id == user.id)
    elif user.role == RoleEnum.DEPARTMENT_MANAGER:
        sub_ids = await _subordinate_ids(user, session)
        stmt = stmt.where(or_(
            Decision.submitter_id == user.id,
            Decision.submitter_id.in_(sub_ids) if sub_ids else Decision.submitter_id == user.id,
        ))
    # DEPUTY / DIVISION_MANAGER: no filter — see all

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return []

    type_counts: dict[str, int] = {}
    approved = 0
    for d in rows:
        t = d.type.value if d.type else "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        if d.status == DecisionStatusEnum.APPROVED:
            approved += 1

    return [{
        "total": len(rows),
        "by_type": type_counts,
        "approval_rate_pct": round(approved / len(rows) * 100),
        "sample": [
            {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
            for d in rows[:8]
        ],
    }]


async def _risky_projects_for_role(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.risks.isnot(None),
        Project.risks != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    rows = (await session.execute(stmt.limit(20))).scalars().all()
    return [{"identifier": p.project_identifier, "name": p.name or "", "risks": (p.risks or "")[:120]}
            for p in rows]


async def _handle_projects_for_role(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.to_handle.isnot(None),
        Project.to_handle != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    rows = (await session.execute(stmt.limit(20))).scalars().all()
    return [{"identifier": p.project_identifier, "name": p.name or "", "to_handle": (p.to_handle or "")[:120]}
            for p in rows]


async def _subordinate_ids(user: User, session: AsyncSession) -> list[int]:
    stmt = select(User.id).where(User.manager_id == user.id)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
