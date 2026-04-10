"""Dashboard router - HTML metrics and analytics for Shan-AI."""

import json
import random
import string
import secrets
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_, exists
from typing import Optional

from app.database import get_db_session
from app.models import Decision, User, DecisionTypeEnum, DecisionStatusEnum, RoleEnum, DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum, DecisionFeedback, DecisionRaciRole, RaciRoleEnum, LessonLearned
from app.routers.login import get_current_user
from app.config import settings

logger = logging.getLogger(__name__)


def _generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

def _generate_token() -> str:
    return secrets.token_hex(16)

_ROLE_HIERARCHY = {
    "division_manager": 1,
    "deputy_division_manager": 2,
    "department_manager": 3,
    "project_manager": 4,
}

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


def _can_edit(decision: Decision, user: User) -> bool:
    """Check if user can edit decision's content (summary, action, type)."""
    return user.is_admin or decision.submitter_id == user.id


def _can_change_status(decision: Decision, user: User, my_raci_roles: dict = None) -> bool:
    """Check if user can change decision's status."""
    if user.is_admin:
        return True
    # User is RACI A (Accountable) for this decision
    if my_raci_roles and my_raci_roles.get(decision.id) == "A":
        return True
    return False


def _can_delete(decision: Decision, user: User) -> bool:
    """Check if user can delete decision."""
    return user.is_admin or (decision.submitter_id == user.id and decision.status == DecisionStatusEnum.PENDING)


async def _pending_approvals_count(user_id: int, session: AsyncSession) -> int:
    """Count decisions awaiting approval by this user (where user is RACI A)."""
    from app.models import DecisionRaciRole, RaciRoleEnum

    # Subquery: decisions where user is RACI A
    subq = select(DecisionRaciRole.decision_id).where(
        DecisionRaciRole.user_id == user_id,
        DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE,
    )

    result = await session.execute(
        select(func.count()).select_from(Decision)
        .where(Decision.id.in_(subq))
        .where(Decision.status == DecisionStatusEnum.PENDING)
    )
    return result.scalar() or 0


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    uid = current_user.id

    # Subquery: decisions distributed to this user
    _dist_sent = exists(
        select(DecisionDistribution.id).where(
            DecisionDistribution.decision_id == Decision.id,
            DecisionDistribution.user_id == uid,
        )
    )
    # Subquery: decisions where user has RACI role
    _raci_assigned = exists(
        select(DecisionRaciRole.id).where(
            DecisionRaciRole.decision_id == Decision.id,
            DecisionRaciRole.user_id == uid,
        )
    )
    _involved = or_(Decision.submitter_id == uid, _dist_sent, _raci_assigned)

    # --- Counts by type ---
    type_counts_q = await session.execute(
        select(Decision.type, func.count().label("cnt"))
        .where(_involved)
        .group_by(Decision.type)
    )
    type_counts = {row.type.value: row.cnt for row in type_counts_q}

    # --- Counts by status ---
    status_counts_q = await session.execute(
        select(Decision.status, func.count().label("cnt"))
        .where(_involved)
        .group_by(Decision.status)
    )
    status_counts = {row.status.value: row.cnt for row in status_counts_q}

    # --- Average feedback score ---
    avg_feedback_q = await session.execute(
        select(func.avg(Decision.feedback_score))
        .where(_involved)
        .where(Decision.feedback_score.isnot(None))
    )
    avg_feedback = avg_feedback_q.scalar()
    avg_feedback = round(float(avg_feedback), 2) if avg_feedback else None

    # --- Total decisions & all users ---
    total_decisions = sum(type_counts.values())
    total_users_q = await session.execute(select(func.count()).select_from(User))
    total_users = total_users_q.scalar()

    # --- Last 7 days ---
    week_ago = datetime.utcnow() - timedelta(days=7)
    daily_q = await session.execute(
        select(
            func.date_trunc("day", Decision.created_at).label("day"),
            func.count().label("cnt")
        )
        .where(_involved)
        .where(Decision.created_at >= week_ago)
        .group_by("day")
        .order_by("day")
    )
    daily_rows = daily_q.all()
    daily_labels = [row.day.strftime("%d/%m") for row in daily_rows]
    daily_data = [row.cnt for row in daily_rows]

    # --- Recent decisions (last 10) ---
    recent_q = await session.execute(
        select(Decision, User.username)
        .join(User, Decision.submitter_id == User.id)
        .where(_involved)
        .order_by(Decision.created_at.desc())
        .limit(10)
    )
    recent_rows = recent_q.all()
    recent_decisions = [
        {
            "id": d.id,
            "type": d.type.value,
            "status": d.status.value,
            "summary": d.summary,
            "username": username,
            "created_at": d.created_at.strftime("%d/%m/%Y %H:%M") if d.created_at else "—",
            "feedback_score": d.feedback_score,
        }
        for d, username in recent_rows
    ]

    # --- Decisions per role ---
    role_q = await session.execute(
        select(User.role, func.count(Decision.id).label("cnt"))
        .join(Decision, Decision.submitter_id == User.id)
        .where(User.role.isnot(None))
        .where(_involved)
        .group_by(User.role)
    )
    role_counts = {row.role.value: row.cnt for row in role_q}

    # --- Pending approvals (decisions waiting for this user's approval) ---
    pending_approvals = await _pending_approvals_count(uid, session)

    # --- Decisions by RACI role (for current user) ---
    raci_counts_q = await session.execute(
        select(DecisionRaciRole.role, func.count().label("cnt"))
        .where(DecisionRaciRole.user_id == uid)
        .group_by(DecisionRaciRole.role)
    )
    raci_counts = {row.role.value: row.cnt for row in raci_counts_q}

    # --- Decisions written by current user ---
    written_count_q = await session.execute(
        select(func.count()).select_from(Decision)
        .where(Decision.submitter_id == uid)
    )
    written_count = written_count_q.scalar() or 0

    type_labels_he = {
        "info": "מידע",
        "normal": "רגיל",
        "critical": "קריטי",
        "uncertain": "לא ודאי",
    }
    status_labels_he = {
        "pending": "ממתין",
        "approved": "מאושר",
        "rejected": "נדחה",
        "executed": "בוצע",
    }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "current_user": current_user,
        "total_decisions": total_decisions,
        "total_users": total_users,
        "avg_feedback": avg_feedback,
        "type_counts": type_counts,
        "status_counts": status_counts,
        "daily_labels": json.dumps(daily_labels),
        "daily_data": json.dumps(daily_data),
        "recent_decisions": recent_decisions,
        "role_counts": role_counts,
        "type_labels_he": type_labels_he,
        "status_labels_he": status_labels_he,
        "pending_approvals": pending_approvals,
        "raci_counts": raci_counts,
        "written_count": written_count,
    })


# -----------------------------------------------------------------------
# User Management
# -----------------------------------------------------------------------

ROLE_LABELS = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
}


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    msg: str = None,
    error: str = None,
):
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    pending_approvals = await _pending_approvals_count(current_user.id, session)

    import json as _json
    users_json = _json.dumps([
        {
            "id": u.id,
            "username": u.username,
            "telegram_id": u.telegram_id or "",
            "role": u.role.value if u.role else "",
            "job_title": u.job_title or "",
            "manager_id": u.manager_id or "",
            "responsibilities": u.responsibilities or "",
        }
        for u in users
    ], ensure_ascii=False)

    return templates.TemplateResponse("users.html", {
        "request": request,
        "current_user": current_user,
        "users": users,
        "users_json": users_json,
        "roles": [r.value for r in RoleEnum],
        "role_labels": ROLE_LABELS,
        "msg": msg,
        "error": error,
        "pending_approvals": pending_approvals,
    })


@router.post("/users/create")
async def create_user(
    request: Request,
    username: str = Form(...),
    job_title: str = Form(""),
    role: str = Form(...),
    manager_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.utils.auth import get_default_password_hash

    # Check duplicate username
    existing = await session.scalar(select(User).where(User.username == username))
    if existing:
        return RedirectResponse(f"/dashboard/users?error=שם+משתמש+כבר+קיים", status_code=303)

    # Generate unique registration code
    for _ in range(10):
        code = _generate_code()
        clash = await session.scalar(select(User).where(User.registration_code == code))
        if not clash:
            break

    user = User(
        username=username,
        password_hash=get_default_password_hash(),
        job_title=job_title or None,
        role=RoleEnum(role),
        hierarchy_level=_ROLE_HIERARCHY.get(role),
        manager_id=int(manager_id) if manager_id.strip() else None,
        registration_code=code,
        profile_token=_generate_token(),
    )
    session.add(user)
    await session.commit()
    return RedirectResponse(f"/dashboard/users?msg=משתמש+נוצר+בהצלחה.+קוד+הרשמה:+{code}", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def toggle_admin(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = await session.get(User, user_id)
    if not user:
        return RedirectResponse("/dashboard/users?error=משתמש+לא+נמצא", status_code=303)

    # If removing admin, ensure at least one other admin remains
    if user.is_admin:
        admin_count_q = await session.execute(
            select(func.count()).select_from(User).where(User.is_admin == True)
        )
        admin_count = admin_count_q.scalar() or 0
        if admin_count <= 1:
            return RedirectResponse(
                "/dashboard/users?error=לא+ניתן+להסיר+את+המנהל+האחרון.+הוסף+מנהל+אחר+תחילה.",
                status_code=303,
            )

    user.is_admin = not user.is_admin
    await session.commit()
    status = "הוגדר כמנהל" if user.is_admin else "הוסר מניהול"
    return RedirectResponse(f"/dashboard/users?msg={user.username}+{status}", status_code=303)


@router.post("/users/{user_id}/set-role")
async def set_role(
    user_id: int,
    role: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = await session.get(User, user_id)
    if user:
        user.role = RoleEnum(role)
        await session.commit()
    return RedirectResponse("/dashboard/users?msg=תפקיד+עודכן", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = await session.get(User, user_id)
    if user:
        await session.delete(user)
        await session.commit()
    return RedirectResponse("/dashboard/users?msg=משתמש+נמחק", status_code=303)


@router.post("/users/{user_id}/regen-code")
async def regen_code(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    user = await session.get(User, user_id)
    if user:
        for _ in range(10):
            code = _generate_code()
            clash = await session.scalar(select(User).where(User.registration_code == code))
            if not clash:
                break
        user.registration_code = code
        await session.commit()
        return RedirectResponse(f"/dashboard/users?msg=קוד+חדש:+{code}", status_code=303)
    return RedirectResponse("/dashboard/users", status_code=303)


@router.post("/users/{user_id}/edit")
async def edit_user(
    user_id: int,
    username: str = Form(...),
    role: str = Form(""),
    job_title: str = Form(""),
    responsibilities: str = Form(""),
    manager_id: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.utils.auth import hash_password

    if not current_user.is_admin and current_user.id != user_id:
        return RedirectResponse("/dashboard/users?error=אין+הרשאה+לערוך+משתמש+זה", status_code=303)

    user = await session.get(User, user_id)
    if not user:
        return RedirectResponse("/dashboard/users?error=משתמש+לא+נמצא", status_code=303)

    user.username = username
    if role:
        user.role = RoleEnum(role)
        user.hierarchy_level = _ROLE_HIERARCHY.get(role)
    user.job_title = job_title or None
    user.responsibilities = responsibilities.strip() or None
    user.manager_id = int(manager_id) if manager_id.strip() else None
    if password.strip():
        user.password_hash = hash_password(password)
    await session.commit()
    return RedirectResponse("/dashboard/users?msg=פרטי+משתמש+עודכנו", status_code=303)


# -----------------------------------------------------------------------
# AI Analysis endpoints
# -----------------------------------------------------------------------

@router.get("/ai-analysis")
async def dashboard_ai_analysis(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Analyze the current user's full dashboard — decisions, trends, patterns, recommendations."""
    from fastapi.responses import JSONResponse
    import json as _json
    from datetime import datetime as _dt

    uid = current_user.id

    _dist_sent = exists(
        select(DecisionDistribution.id).where(
            DecisionDistribution.decision_id == Decision.id,
            DecisionDistribution.user_id == uid,
        )
    )
    _involved = or_(Decision.submitter_id == uid, _dist_sent)

    all_q = await session.execute(select(Decision).where(_involved).order_by(Decision.created_at))
    all_decisions = all_q.scalars().all()

    total = len(all_decisions)
    type_counts: dict = {}
    status_counts: dict = {}
    feedback_scores = []
    feedback_notes = []
    submitted = 0
    received_dist = 0
    pending_count = 0
    critical_count = 0

    for d in all_decisions:
        t = d.type.value if d.type else "unknown"
        s = d.status.value if d.status else "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        status_counts[s] = status_counts.get(s, 0) + 1
        if d.feedback_score:
            feedback_scores.append(d.feedback_score)
        if d.feedback_notes:
            feedback_notes.append(d.feedback_notes)
        if d.submitter_id == uid:
            submitted += 1
        else:
            received_dist += 1
        if s == "pending":
            pending_count += 1
        if t == "critical":
            critical_count += 1

    # 7-day trend
    week_ago = _dt.utcnow() - timedelta(days=7)
    recent_7 = [d for d in all_decisions if d.created_at >= week_ago]

    # Pending approvals
    pending_approvals = await _pending_approvals_count(uid, session)

    avg_fb = round(sum(feedback_scores) / len(feedback_scores), 1) if feedback_scores else None

    role_labels = {
        "project_manager": "מנהל פרויקט",
        "department_manager": "מנהל מחלקה",
        "deputy_division_manager": "סגן מנהל אגף",
        "division_manager": "מנהל אגף",
    }
    role_he = role_labels.get(current_user.role.value, current_user.role.value) if current_user.role else "לא מוגדר"

    recent_summaries = [
        f"[{d.type.value.upper()}][{d.status.value}] {d.summary or '—'}"
        for d in sorted(all_decisions, key=lambda x: x.created_at, reverse=True)[:6]
    ]

    data_block = f"""
משתמש: {current_user.username}
תפקיד: {role_he}
תואר: {current_user.job_title or '—'}

סטטיסטיקות כלליות:
- סה"כ החלטות (כולל שנשלחו אליו): {total}
- הגיש בעצמו: {submitted} | קיבל מאחרים: {received_dist}
- ממתינות לטיפול: {pending_count} | דורשות אישורו: {pending_approvals}
- קריטיות: {critical_count}
- ציון פידבק ממוצע: {avg_fb}/5 מתוך {len(feedback_scores)} ציונים

התפלגות לפי סוג: {_json.dumps(type_counts, ensure_ascii=False)}
התפלגות לפי סטטוס: {_json.dumps(status_counts, ensure_ascii=False)}

פעילות 7 ימים אחרונים: {len(recent_7)} החלטות

החלטות אחרונות:
{chr(10).join(recent_summaries) if recent_summaries else 'אין'}

משובים שהתקבלו:
{chr(10).join(f'• {n}' for n in feedback_notes[:4]) if feedback_notes else 'אין'}
"""

    system_prompt = """אתה יועץ ניהולי בכיר המנתח את ביצועי קבלת ההחלטות של מנהל בארגון תשתיות חשמל.
נתח את הדשבורד האישי שלו ותן ניתוח עמוק, ישיר ופרקטי.
השב בדיוק כ-JSON תקין עם המבנה:
{
  "sections": [
    {"icon": "📊", "title": "תמונת מצב כוללת", "content": "...", "color": "blue"},
    {"icon": "💪", "title": "מה עובד טוב", "content": "...", "color": "green"},
    {"icon": "⚠️", "title": "נקודות תשומת לב", "content": "...", "color": "yellow"},
    {"icon": "🎯", "title": "המלצות לשיפור מיידי", "content": "...", "color": "purple"}
  ]
}
כל content חייב להיות ספציפי לנתונים — עם מספרים, דפוסים ותובנות אמיתיות. אל תהיה גנרי."""

    try:
        from app.services.groq_client import groq_chat
        raw = await groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": data_block},
            ],
            max_tokens=1500,
            temperature=0.3,
            json_mode=True,
        )
        result = _json.loads(raw)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": f"שגיאה בניתוח AI: {str(e)[:100]}"}, status_code=500)


@router.get("/users/{user_id}/ai-analysis")
async def user_ai_analysis(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import JSONResponse
    import json as _json

    user = await session.get(User, user_id)
    if not user:
        return JSONResponse({"error": "משתמש לא נמצא"}, status_code=404)

    # Gather decision stats for this user
    decisions_q = await session.execute(
        select(Decision).where(Decision.submitter_id == user_id)
    )
    decisions = decisions_q.scalars().all()

    total = len(decisions)
    type_counts = {}
    status_counts = {}
    feedback_scores = []
    feedback_notes = []
    recent_summaries = []

    for d in sorted(decisions, key=lambda x: x.created_at, reverse=True):
        t = d.type.value if d.type else "unknown"
        s = d.status.value if d.status else "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        status_counts[s] = status_counts.get(s, 0) + 1
        if d.feedback_score:
            feedback_scores.append(d.feedback_score)
        if d.feedback_notes:
            feedback_notes.append(d.feedback_notes)
        if d.summary and len(recent_summaries) < 5:
            recent_summaries.append(f"[{t.upper()}] {d.summary}")

    # Distribution participation (decisions sent to this user)
    dist_q = await session.execute(
        select(DecisionDistribution).where(DecisionDistribution.user_id == user_id)
    )
    dists = dist_q.scalars().all()
    dist_received = len(dists)
    dist_responded = sum(1 for d in dists if d.status.value not in ("pending",))

    avg_feedback = round(sum(feedback_scores) / len(feedback_scores), 1) if feedback_scores else None

    role_labels = {
        "project_manager": "מנהל פרויקט",
        "department_manager": "מנהל מחלקה",
        "deputy_division_manager": "סגן מנהל אגף",
        "division_manager": "מנהל אגף",
    }
    role_he = role_labels.get(user.role.value, user.role.value) if user.role else "לא מוגדר"

    data_block = f"""
שם: {user.username}
תפקיד: {role_he}
תואר תפקיד: {user.job_title or '—'}

סטטיסטיקות החלטות:
- סה"כ החלטות שהוגשו: {total}
- לפי סוג: {_json.dumps(type_counts, ensure_ascii=False)}
- לפי סטטוס: {_json.dumps(status_counts, ensure_ascii=False)}
- ציון פידבק ממוצע: {avg_feedback}/5 (מתוך {len(feedback_scores)} ציונים)

השתתפות בהחלטות שנשלחו אליו: {dist_received} (מגיב ל-{dist_responded})

החלטות אחרונות:
{chr(10).join(recent_summaries) if recent_summaries else 'אין'}

משובים שהתקבלו:
{chr(10).join(f'• {n}' for n in feedback_notes[:5]) if feedback_notes else 'אין'}
"""

    system_prompt = """אתה מנתח ביצועים ארגוני בכיר. נתח את נתוני המשתמש ותן ניתוח מקיף, ישיר ופרקטי.
השב בדיוק כ-JSON תקין עם המבנה הבא (ללא markdown, ללא טקסט מחוץ ל-JSON):
{
  "sections": [
    {"icon": "📊", "title": "פרופיל קבלת החלטות", "content": "...", "color": "blue"},
    {"icon": "💪", "title": "נקודות חוזק", "content": "...", "color": "green"},
    {"icon": "⚠️", "title": "תחומים לשיפור", "content": "...", "color": "yellow"},
    {"icon": "🎯", "title": "המלצות פרקטיות", "content": "...", "color": "purple"}
  ]
}
כל section.content צריך להיות פסקה מלאה בעברית, עם תובנות ספציפיות לנתונים — לא גנריות. אם אין מספיק נתונים, ציין זאת בכנות."""

    try:
        from app.services.groq_client import groq_chat
        raw = await groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": data_block},
            ],
            max_tokens=1500,
            temperature=0.3,
            json_mode=True,
        )
        result = _json.loads(raw)
        return JSONResponse({"name": user.username, **result})
    except Exception as e:
        return JSONResponse({"error": f"שגיאה בניתוח AI: {str(e)[:100]}"}, status_code=500)


@router.get("/decisions/{decision_id}/ai-analysis")
async def decision_ai_analysis(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import JSONResponse
    import json as _json

    d = await session.get(Decision, decision_id)
    if not d:
        return JSONResponse({"error": "החלטה לא נמצאה"}, status_code=404)

    submitter = await session.get(User, d.submitter_id)

    # Distribution records
    dist_q = await session.execute(
        select(DecisionDistribution, User.username, User.job_title)
        .join(User, DecisionDistribution.user_id == User.id)
        .where(DecisionDistribution.decision_id == decision_id)
    )
    dist_rows = dist_q.all()
    dist_lines = [
        f"  • {uname} ({jtitle or '—'}) — {dist.distribution_type.value} → {dist.status.value}"
        for dist, uname, jtitle in dist_rows
    ]

    # Feedbacks
    feedbacks_q = await session.execute(
        select(DecisionFeedback, User.username)
        .join(User, DecisionFeedback.user_id == User.id)
        .where(DecisionFeedback.decision_id == decision_id)
        .order_by(DecisionFeedback.created_at.desc())
    )
    feedbacks_rows = feedbacks_q.all()
    feedbacks_lines = [
        f"  • {uname}: ⭐ {fb.score}/5 — {fb.notes or 'ללא הערות'}"
        for fb, uname in feedbacks_rows
    ]

    type_labels = {"info": "מידע", "normal": "רגיל", "critical": "קריטי", "uncertain": "לא ודאי"}
    status_labels = {"pending": "ממתין", "approved": "מאושר", "rejected": "נדחה", "executed": "בוצע"}
    meas_labels = {"MEASURABLE": "מדיד", "PARTIAL": "חלקי", "NOT_MEASURABLE": "לא מדיד"}

    elapsed_days = (
        (d.completed_at - d.created_at).days if d.completed_at else
        (__import__("datetime").datetime.utcnow() - d.created_at).days
    )

    data_block = f"""
מזהה החלטה: #{d.id}
סוג: {type_labels.get(d.type.value, d.type.value)}
סטטוס: {status_labels.get(d.status.value, d.status.value)}
דורש אישור: {'כן' if d.requires_approval else 'לא'}
מדידות: {meas_labels.get(d.measurability or '', d.measurability or '—')}
ימים מאז יצירה: {elapsed_days}

מגיש: {submitter.username if submitter else '—'} ({submitter.role.value if submitter and submitter.role else '—'})

תיאור הבעיה:
{d.problem_description or '—'}

סיכום AI:
{d.summary or '—'}

פעולה מומלצת:
{d.recommended_action or '—'}

הנחות בסיס: {d.assumptions or '[]'}
סיכונים שזוהו: {d.risks or '[]'}

הפצה ({len(dist_rows)} נמענים):
{chr(10).join(dist_lines) if dist_lines else 'לא הופצה'}

משובים מהמשתמשים ({len(feedbacks_rows)} משובים):
{chr(10).join(feedbacks_lines) if feedbacks_lines else 'אין משובים עדיין'}

סיכום פידבק AI: {d.feedback_score}/5 — {d.feedback_notes or 'אין הערות'}
"""

    system_prompt = """אתה מומחה לניהול החלטות ארגוניות. נתח את ההחלטה לעומק ותן הערכה מקצועית ישירה.
שים לב במיוחד למשובים שהתקבלו מהמשתמשים - הם מציינים כיצד ההחלטה עבדה בפועל בשטח.
השב בדיוק כ-JSON תקין עם המבנה הבא (ללא markdown, ללא טקסט מחוץ ל-JSON):
{
  "sections": [
    {"icon": "🔍", "title": "הערכת הסיווג", "content": "...", "color": "blue"},
    {"icon": "✅", "title": "מה בוצע טוב", "content": "...", "color": "green"},
    {"icon": "⚠️", "title": "חולשות וסיכונים שהוחמצו", "content": "...", "color": "yellow"},
    {"icon": "📚", "title": "לקחים והמלצות", "content": "...", "color": "purple"}
  ]
}
כל section.content צריך להיות ניתוח ספציפי לנתונים — לא גנרי. התחשב במשובים שניתנו מהמשתמשים בניתוחך. היה כן גם אם הניתוח שלילי."""

    try:
        from app.services.groq_client import groq_chat
        raw = await groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": data_block},
            ],
            max_tokens=1500,
            temperature=0.3,
            json_mode=True,
        )
        result = _json.loads(raw)
        return JSONResponse({"title": f"החלטה #{d.id}", **result})
    except Exception as e:
        return JSONResponse({"error": f"שגיאה בניתוח AI: {str(e)[:100]}"}, status_code=500)


# -----------------------------------------------------------------------
# Decisions Management
# -----------------------------------------------------------------------

TYPE_LABELS_HE = {
    "info": "מידע",
    "normal": "רגיל",
    "critical": "קריטי",
    "uncertain": "לא ודאי",
}
STATUS_LABELS_HE = {
    "pending": "ממתין",
    "approved": "מאושר",
    "rejected": "נדחה",
    "executed": "בוצע",
}
MEASURABILITY_LABELS = {
    "MEASURABLE": "מדיד",
    "PARTIAL": "חלקי",
    "NOT_MEASURABLE": "לא מדיד",
}


@router.get("/decisions", response_class=HTMLResponse)
async def decisions_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    msg: str = None,
    error: str = None,
    filter_status: str = None,
    filter_type: str = None,
):
    import json as _json

    uid = current_user.id
    _dist_sent = exists(
        select(DecisionDistribution.id).where(
            DecisionDistribution.decision_id == Decision.id,
            DecisionDistribution.user_id == uid,
        )
    )
    _raci_assigned = exists(
        select(DecisionRaciRole.id).where(
            DecisionRaciRole.decision_id == Decision.id,
            DecisionRaciRole.user_id == uid,
        )
    )
    q = (
        select(Decision, User.username.label("submitter_name"))
        .join(User, Decision.submitter_id == User.id)
        .where(or_(Decision.submitter_id == uid, _dist_sent, _raci_assigned))
        .order_by(Decision.created_at.desc())
    )
    if filter_status:
        q = q.where(Decision.status == DecisionStatusEnum(filter_status))
    if filter_type:
        q = q.where(Decision.type == DecisionTypeEnum(filter_type))

    rows = (await session.execute(q)).all()

    # Load RACI counts for all fetched decisions (bulk, single query)
    decision_ids = [d.id for d, _ in rows]
    from app.services.raci_service import get_raci_counts_for_decisions
    raci_counts = await get_raci_counts_for_decisions(decision_ids, session)

    # Load current user's own RACI role per decision (bulk)
    my_raci_q = await session.execute(
        select(DecisionRaciRole.decision_id, DecisionRaciRole.role)
        .where(DecisionRaciRole.decision_id.in_(decision_ids))
        .where(DecisionRaciRole.user_id == uid)
    )
    my_raci_roles = {row.decision_id: row.role.value for row in my_raci_q}

    # Load current user's own feedbacks for all decisions at once
    my_feedbacks_q = await session.execute(
        select(DecisionFeedback).where(DecisionFeedback.user_id == uid)
    )
    my_feedbacks = {fb.decision_id: fb for fb in my_feedbacks_q.scalars().all()}

    # Load RACI A (Accountable) user for each decision (bulk)
    accountable_q = await session.execute(
        select(DecisionRaciRole.decision_id, User.id.label("user_id"), User.username)
        .join(User, DecisionRaciRole.user_id == User.id)
        .where(DecisionRaciRole.decision_id.in_(decision_ids))
        .where(DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE)
    )
    accountable_map = {row.decision_id: {"id": row.user_id, "name": row.username} for row in accountable_q}

    decisions = []
    for d, submitter_name in rows:
        my_fb = my_feedbacks.get(d.id)
        can_feedback = d.status.value in ("approved", "executed") and d.submitter_id != uid
        can_edit = _can_edit(d, current_user)
        can_change_status = _can_change_status(d, current_user, my_raci_roles)
        can_delete = _can_delete(d, current_user)
        can_edit_raci = current_user.is_admin or d.submitter_id == current_user.id

        # Safely parse JSON fields
        try:
            assumptions = _json.loads(d.assumptions) if d.assumptions else []
        except (ValueError, TypeError):
            assumptions = []

        try:
            risks = _json.loads(d.risks) if d.risks else []
        except (ValueError, TypeError):
            risks = []

        decisions.append({
            "id": d.id,
            "type": d.type.value,
            "status": d.status.value,
            "summary": d.summary or "—",
            "problem_description": d.problem_description or "",
            "recommended_action": d.recommended_action or "",
            "requires_approval": d.requires_approval,
            "assumptions": assumptions,
            "risks": risks,
            "measurability": d.measurability or "",
            "feedback_score": d.feedback_score,
            "feedback_notes": d.feedback_notes or "",
            "submitter_id": d.submitter_id,
            "submitter_name": submitter_name,
            "created_at": d.created_at.strftime("%d/%m/%Y %H:%M") if d.created_at else "—",
            "completed_at": d.completed_at.strftime("%d/%m/%Y %H:%M") if d.completed_at else None,
            "can_feedback": can_feedback,
            "my_feedback_score": my_fb.score if my_fb else None,
            "my_feedback_notes": my_fb.notes or "" if my_fb else "",
            "raci_summary": (
                f"R:{raci_counts[d.id].get('R',0)} A:{raci_counts[d.id].get('A',0)} "
                f"C:{raci_counts[d.id].get('C',0)} I:{raci_counts[d.id].get('I',0)}"
                if d.id in raci_counts else "—"
            ),
            "raci_detail": raci_counts.get(d.id, {}),
            "my_raci_role": my_raci_roles.get(d.id, None),
            "accountable_name": accountable_map.get(d.id, {}).get("name"),
            "accountable_id": accountable_map.get(d.id, {}).get("id"),
            "can_approve": (
                my_raci_roles.get(d.id) == "A"
                and d.submitter_id != uid
                and d.status.value == "pending"
            ) or (
                current_user.is_admin
                and d.submitter_id != uid
                and d.status.value == "pending"
            ),
            "can_edit": can_edit,
            "can_change_status": can_change_status,
            "can_delete": can_delete,
            "can_edit_raci": can_edit_raci,
        })

    users_q = (await session.execute(select(User).order_by(User.username))).scalars().all()
    users = users_q
    users_json = [
        {"id": u.id, "username": u.username, "job_title": u.job_title or ""}
        for u in users_q
    ]

    pending_approvals = await _pending_approvals_count(current_user.id, session)
    return templates.TemplateResponse("decisions.html", {
        "request": request,
        "current_user": current_user,
        "decisions": decisions,
        "users": users,
        "users_json": users_json,
        "type_labels": TYPE_LABELS_HE,
        "status_labels": STATUS_LABELS_HE,
        "measurability_labels": MEASURABILITY_LABELS,
        "statuses": [s.value for s in DecisionStatusEnum],
        "types": [t.value for t in DecisionTypeEnum],
        "filter_status": filter_status or "",
        "filter_type": filter_type or "",
        "msg": msg,
        "error": error,
        "pending_approvals": pending_approvals,
    })


@router.post("/decisions/analyze", response_class=HTMLResponse)
async def analyze_decision(
    request: Request,
    problem_description: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Pre-classify, then run AI analysis on a problem description and show a review page."""
    from app.services.claude_service import ClaudeService
    from app.services import embedding_service

    role_str = current_user.role.value if current_user.role else "unknown"
    claude = ClaudeService()

    # --- Step 1: Pre-classify ---
    try:
        classify_result = await claude.classify(problem_description)
        verdict = classify_result.get("verdict", "DECISION")
    except Exception:
        verdict = "DECISION"
        classify_result = {}

    if verdict == "NOT_DECISION":
        return templates.TemplateResponse("decision_review.html", {
            "request": request,
            "current_user": current_user,
            "problem_description": problem_description,
            "mode": "not_decision",
            "ai_reply": classify_result.get("reply", "הטקסט שהוגש אינו נראה כהחלטה ארגונית."),
            "result": None,
        })

    if verdict == "UNCLEAR":
        return templates.TemplateResponse("decision_review.html", {
            "request": request,
            "current_user": current_user,
            "problem_description": problem_description,
            "mode": "unclear",
            "clarifying_question": classify_result.get("clarifying_question", "אנא פרט את ההחלטה."),
            "result": None,
        })

    # --- Step 2: Full analysis ---
    try:
        similar = await embedding_service.get_similar_decisions(session, problem_description)
        past_context = embedding_service.format_past_context(similar)
        result = await claude.analyze(problem_description, role_str, past_context)
    except Exception as e:
        from groq import RateLimitError
        if isinstance(e, RateLimitError) or "429" in str(e) or "rate limit" in str(e).lower():
            msg = "מגבלת קצב Groq הושגה. נסה שוב בעוד מספר שניות."
        else:
            msg = f"שגיאה בניתוח AI: {str(e)[:60]}"
        return RedirectResponse(f"/dashboard/decisions?error={msg}", status_code=303)

    # Get all users for RACI assignment selector
    all_users = (await session.execute(select(User).order_by(User.username))).scalars().all()

    return templates.TemplateResponse("decision_review.html", {
        "request": request,
        "current_user": current_user,
        "problem_description": problem_description,
        "mode": "decision",
        "result": result,
        "all_users": all_users,
    })


@router.post("/decisions/confirm")
async def confirm_decision(
    problem_description: str = Form(...),
    type: str = Form(...),
    summary: str = Form(...),
    recommended_action: str = Form(...),
    requires_approval: str = Form(...),
    assumptions: str = Form("[]"),
    risks: str = Form("[]"),
    measurability: str = Form(""),
    raci_r: str = Form(""),  # Responsible user IDs (comma-separated or empty)
    raci_a: str = Form(""),  # Accountable user IDs
    raci_c: str = Form(""),  # Consulted user IDs
    raci_i: str = Form(""),  # Informed user IDs
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Save a pre-analyzed decision after the user reviews and confirms it, including RACI assignments."""
    import json as _json
    from app.models import DecisionRaciRole, RaciRoleEnum

    decision = Decision(
        submitter_id=current_user.id,
        type=DecisionTypeEnum(type.lower()),
        status=DecisionStatusEnum.PENDING,
        summary=summary,
        problem_description=problem_description,
        recommended_action=recommended_action,
        requires_approval=requires_approval.lower() in ("true", "1", "yes"),
        assumptions=assumptions,
        risks=risks,
        measurability=measurability,
    )
    session.add(decision)
    await session.commit()

    # Parse RACI assignments
    # Each field is a comma-separated list of user IDs (from form or JavaScript)
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Decision #{decision.id} RACI data: R={raci_r}, A={raci_a}, C={raci_c}, I={raci_i}")

    raci_mapping = {
        "R": raci_r,
        "A": raci_a,
        "C": raci_c,
        "I": raci_i,
    }

    _raci_to_dist = {"R": "execution", "C": "info", "I": "info"}
    for role_letter, user_ids_str in raci_mapping.items():
        if user_ids_str and user_ids_str.strip():
            # Parse user IDs (could be "123,456,789" or just "123")
            user_ids = [int(uid.strip()) for uid in user_ids_str.split(",") if uid.strip().isdigit()]
            logger.info(f"Adding {len(user_ids)} users to role {role_letter}: {user_ids}")
            for user_id in user_ids:
                session.add(DecisionRaciRole(
                    decision_id=decision.id,
                    user_id=user_id,
                    role=RaciRoleEnum(role_letter),
                    assigned_by_ai=False,
                ))
                # Create distribution record from RACI assignment
                if role_letter == "A":
                    if user_id == current_user.id:
                        # Accountable = submitter → auto-approve
                        decision.status = DecisionStatusEnum.APPROVED
                        decision.completed_at = datetime.utcnow()
                        logger.info(f"confirm_decision: auto-approved decision #{decision.id} (accountable = submitter)")
                        continue
                    dist_type = DistributionTypeEnum.APPROVAL
                elif role_letter in _raci_to_dist:
                    dist_type = DistributionTypeEnum(_raci_to_dist[role_letter])
                else:
                    continue
                session.add(DecisionDistribution(
                    decision_id=decision.id,
                    user_id=user_id,
                    distribution_type=dist_type,
                    status=DistributionStatusEnum.PENDING,
                    sent_at=datetime.utcnow(),
                ))

    await session.commit()

    # Send Telegram notifications to all users assigned RACI roles
    new_assignments = {}
    for role_letter, user_ids_str in raci_mapping.items():
        if user_ids_str and user_ids_str.strip():
            user_ids = [int(uid.strip()) for uid in user_ids_str.split(",") if uid.strip().isdigit()]
            for user_id in user_ids:
                new_assignments[user_id] = role_letter

    if new_assignments:
        try:
            from app.services.raci_service import notify_changed_raci_users
            await notify_changed_raci_users(decision.id, {}, new_assignments, session)
            logger.info(f"confirm_decision: sent RACI notifications for decision #{decision.id}")
        except Exception as e:
            logger.warning(f"confirm_decision: failed to send notifications: {e}", exc_info=True)

    # Redirect to decisions page
    return RedirectResponse(f"/dashboard/decisions?msg=החלטה+#{decision.id}+נוצרה+בהצלחה", status_code=303)


@router.post("/decisions/{decision_id}/feedback")
async def submit_feedback(
    decision_id: int,
    score: int = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import JSONResponse

    d = await session.get(Decision, decision_id)
    if not d:
        return JSONResponse({"error": "החלטה לא נמצאה"}, status_code=404)
    if d.submitter_id == current_user.id:
        return JSONResponse({"error": "לא ניתן לתת משוב על החלטה שלך"}, status_code=403)
    if score < 1 or score > 5:
        return JSONResponse({"error": "ציון חייב להיות בין 1 ל-5"}, status_code=400)

    # Upsert: one feedback per user per decision
    existing = await session.scalar(
        select(DecisionFeedback)
        .where(DecisionFeedback.decision_id == decision_id)
        .where(DecisionFeedback.user_id == current_user.id)
    )
    if existing:
        existing.score = score
        existing.notes = notes.strip() or None
        existing.created_at = datetime.utcnow()
    else:
        session.add(DecisionFeedback(
            decision_id=decision_id,
            user_id=current_user.id,
            score=score,
            notes=notes.strip() or None,
        ))

    # Update Decision.feedback_score as average of all feedbacks
    await session.flush()
    avg_q = await session.execute(
        select(func.avg(DecisionFeedback.score))
        .where(DecisionFeedback.decision_id == decision_id)
    )
    avg = avg_q.scalar()
    if avg:
        avg_float = float(avg)
        d.feedback_score = round(avg_float)
        if notes.strip() and d.submitter_id == current_user.id:
            d.feedback_notes = notes.strip()
            # Re-embed with feedback context
            try:
                from app.services.embedding_service import embed
                combined = f"{d.problem_description or ''} {d.summary or ''} {d.recommended_action or ''} פידבק: {notes}"
                d.embedding = await embed(combined)
            except Exception:
                pass

    await session.commit()
    avg_float = float(avg) if avg else None
    return JSONResponse({"ok": True, "avg_score": round(avg_float, 1) if avg_float else score})


@router.post("/decisions/{decision_id}/update")
async def update_decision(
    decision_id: int,
    status: str = Form(""),
    summary: str = Form(""),
    recommended_action: str = Form(""),
    type: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    d = await session.get(Decision, decision_id)
    if not d:
        return RedirectResponse("/dashboard/decisions?error=החלטה+לא+נמצאה", status_code=303)

    # Check permissions: content fields vs status change
    has_content_changes = any([summary.strip(), recommended_action.strip(), type.strip()])

    if has_content_changes and not _can_edit(d, current_user):
        return RedirectResponse("/dashboard/decisions?error=אין+הרשאה+לעריכת+החלטה+זו", status_code=303)

    if status and not _can_change_status(d, current_user):
        return RedirectResponse("/dashboard/decisions?error=אין+הרשאה+לשינוי+סטטוס", status_code=303)

    if status:
        d.status = DecisionStatusEnum(status)
        if status in ("approved", "rejected", "executed") and not d.completed_at:
            d.completed_at = datetime.utcnow()
    if summary.strip():
        d.summary = summary
    if recommended_action.strip():
        d.recommended_action = recommended_action
    if type.strip():
        d.type = DecisionTypeEnum(type)

    await session.commit()
    return RedirectResponse("/dashboard/decisions?msg=החלטה+עודכנה", status_code=303)


@router.post("/decisions/{decision_id}/delete")
async def delete_decision(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    d = await session.get(Decision, decision_id)
    if not d:
        return RedirectResponse("/dashboard/decisions?error=החלטה+לא+נמצאה", status_code=303)

    if not _can_delete(d, current_user):
        return RedirectResponse("/dashboard/decisions?error=אין+הרשאה+למחיקת+החלטה+זו", status_code=303)

    await session.delete(d)
    await session.commit()
    return RedirectResponse("/dashboard/decisions?msg=החלטה+נמחקה", status_code=303)


@router.post("/decisions/{decision_id}/status")
async def quick_status_update(
    decision_id: int,
    new_status: str = Form(...),
    rejection_note: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Quick approve/reject for RACI A users directly from the decisions table."""
    d = await session.get(Decision, decision_id)
    if not d:
        return RedirectResponse("/dashboard/decisions?error=החלטה+לא+נמצאה", status_code=303)

    # Permission: must be RACI A (and not the submitter) or admin
    is_raci_a = await session.scalar(
        select(DecisionRaciRole.id)
        .where(DecisionRaciRole.decision_id == decision_id)
        .where(DecisionRaciRole.user_id == current_user.id)
        .where(DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE)
    )
    can_act = (is_raci_a and d.submitter_id != current_user.id) or current_user.is_admin
    if not can_act:
        return RedirectResponse("/dashboard/decisions?error=אין+הרשאה+לשינוי+סטטוס", status_code=303)

    if new_status not in ("approved", "rejected"):
        return RedirectResponse("/dashboard/decisions?error=סטטוס+לא+חוקי", status_code=303)

    d.status = DecisionStatusEnum(new_status)
    if not d.completed_at:
        d.completed_at = datetime.utcnow()
    if new_status == "rejected" and rejection_note.strip():
        d.feedback_notes = rejection_note.strip()

    await session.commit()
    status_he = "אושרה" if new_status == "approved" else "נדחתה"
    return RedirectResponse(f"/dashboard/decisions?msg=החלטה+%23{decision_id}+{status_he}", status_code=303)


@router.get("/decisions/{decision_id}/feedbacks")
async def get_feedbacks(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get all feedbacks for a decision."""
    from fastapi.responses import JSONResponse

    feedbacks_q = await session.execute(
        select(DecisionFeedback, User.username)
        .join(User, DecisionFeedback.user_id == User.id)
        .where(DecisionFeedback.decision_id == decision_id)
        .order_by(DecisionFeedback.created_at.desc())
    )
    feedbacks_rows = feedbacks_q.all()

    feedbacks = [
        {
            "username": username,
            "score": fb.score,
            "notes": fb.notes or "",
        }
        for fb, username in feedbacks_rows
    ]

    return JSONResponse({"feedbacks": feedbacks})


# ---------------------------------------------------------------------------
# RACI — get current + save edited assignments
# ---------------------------------------------------------------------------

@router.get("/decisions/{decision_id}/raci")
async def get_raci(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Return current RACI assignments + full user list for the edit modal."""
    from app.models import DecisionRaciRole
    from fastapi.responses import JSONResponse

    logger.info(f"get_raci: decision_id={decision_id}, user_id={current_user.id}")

    # Check decision exists
    d = await session.get(Decision, decision_id)
    if not d:
        logger.info(f"get_raci: Decision {decision_id} not found")
        return JSONResponse({"error": "החלטה לא נמצאה"}, status_code=404)

    # Permission check: only admin or submitter can view RACI for editing
    if not current_user.is_admin and d.submitter_id != current_user.id:
        logger.warning(f"get_raci: Permission denied for user {current_user.id} on decision {decision_id}")
        return JSONResponse({"error": "אין הרשאה לצפות בהקצאת RACI להחלטה זו"}, status_code=403)

    raci_rows = (await session.execute(
        select(DecisionRaciRole, User)
        .join(User, DecisionRaciRole.user_id == User.id)
        .where(DecisionRaciRole.decision_id == decision_id)
    )).all()

    assignments = {
        row.DecisionRaciRole.user_id: row.DecisionRaciRole.role.value
        for row in raci_rows
    }

    all_users = (await session.execute(
        select(User).where(User.role.isnot(None)).order_by(User.username)
    )).scalars().all()

    return JSONResponse({
        "assignments": assignments,
        "users": [
            {"id": u.id, "username": u.username, "job_title": u.job_title or ""}
            for u in all_users
        ],
    })


@router.get("/get-all-users")
async def get_all_users(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Return all active users for RACI table building."""
    from fastapi.responses import JSONResponse

    users = (await session.execute(
        select(User).where(User.role.isnot(None)).order_by(User.username)
    )).scalars().all()

    return JSONResponse({
        "users": [
            {"id": u.id, "username": u.username, "job_title": u.job_title or "", "role": u.role.value if u.role else ""}
            for u in users
        ]
    })


@router.post("/decisions/analyze/preview")
async def analyze_preview(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Combined endpoint: Analyze decision + get RACI suggestions in one JSON response.
    For use by Dashboard modal — returns analysis results + RACI suggestions + all users.
    """
    from fastapi.responses import JSONResponse
    from app.services.claude_service import ClaudeService
    from app.services import embedding_service
    from app.services.raci_service import get_ai_raci_suggestions_from_text

    body = await request.json()
    problem_text = body.get("problem_description", "").strip()

    if not problem_text:
        return JSONResponse({"error": "תיאור בעיה ריק"}, status_code=400)

    try:
        # 1. Pre-classify
        claude = ClaudeService()
        classify_result = await claude.classify(problem_text)
        verdict = classify_result.get("verdict", "DECISION")

        if verdict != "DECISION":
            return JSONResponse({
                "verdict": verdict,
                "message": classify_result.get("reply", "לא זוהתה החלטה"),
                "analysis": None,
                "raci_suggestions": {},
                "users": [],
            })

        # 2. Full AI analysis
        role_str = current_user.role.value if current_user.role else "unknown"
        similar = await embedding_service.get_similar_decisions(session, problem_text)
        past_context = embedding_service.format_past_context(similar)
        result = await claude.analyze(problem_text, role_str, past_context)

        # 3. Get RACI suggestions
        raci_suggestions = await get_ai_raci_suggestions_from_text(problem_text)
        raci_dict = {str(s["user_id"]): s["role"] for s in raci_suggestions}

        # 4. Get all users for dropdown
        all_users = (await session.execute(select(User).order_by(User.username))).scalars().all()
        users_list = [
            {"id": u.id, "username": u.username, "job_title": u.job_title or "", "role": u.role.value if u.role else ""}
            for u in all_users
        ]

        return JSONResponse({
            "verdict": "DECISION",
            "analysis": result,
            "raci_suggestions": raci_dict,
            "users": users_list,
        })

    except Exception as e:
        from groq import RateLimitError
        if isinstance(e, RateLimitError) or "429" in str(e) or "rate limit" in str(e).lower():
            msg = "מגבלת קצב Groq הושגה. נסה שוב בעוד מספר שניות."
        else:
            msg = f"שגיאה בניתוח AI: {str(e)[:60]}"
        return JSONResponse({"error": msg}, status_code=500)


@router.post("/decisions/analyze/raci-suggest")
async def suggest_raci_from_text(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get AI RACI suggestions from free-text problem description (before decision is created)."""
    from fastapi.responses import JSONResponse
    from app.services.raci_service import get_ai_raci_suggestions_from_text

    body = await request.json()
    problem_text = body.get("problem_description", "").strip()

    if not problem_text:
        return JSONResponse({"error": "תיאור בעיה ריק"}, status_code=400)

    logger.info(f"suggest_raci_from_text: generating suggestions for {len(problem_text)} chars")

    suggestions = await get_ai_raci_suggestions_from_text(problem_text)
    logger.info(f"suggest_raci_from_text: got {len(suggestions)} suggestions")

    result = {str(s["user_id"]): s["role"] for s in suggestions}
    logger.info(f"suggest_raci_from_text: returning {result}")
    # Return as {user_id: role} dict for easy frontend use
    return JSONResponse({"suggestions": result})


@router.post("/decisions/{decision_id}/raci")
async def save_raci(
    decision_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Replace all RACI assignments for a decision with the submitted ones."""
    from app.models import DecisionRaciRole, RaciRoleEnum
    from fastapi.responses import JSONResponse

    logger.info(f"save_raci: START for decision {decision_id}, user {current_user.id}")

    d = await session.get(Decision, decision_id)
    if not d:
        return JSONResponse({"ok": False, "error": "החלטה לא נמצאה"}, status_code=404)

    # Permission check: only admin or submitter can edit RACI
    if not current_user.is_admin and d.submitter_id != current_user.id:
        return JSONResponse({"ok": False, "error": "אין הרשאה לעדכן RACI להחלטה זו"}, status_code=403)

    body = await request.json()
    assignments = body.get("assignments", {})  # {user_id: "R"|"A"|"C"|"I"|""}
    logger.info(f"save_raci: assignments received: {assignments}")

    # Capture existing assignments BEFORE deleting (for diff-based notification)
    existing = (await session.execute(
        select(DecisionRaciRole).where(DecisionRaciRole.decision_id == decision_id)
    )).scalars().all()
    old_assignments: dict[int, str] = {row.user_id: row.role.value for row in existing}
    logger.info(f"save_raci: old_assignments: {old_assignments}")

    for row in existing:
        await session.delete(row)

    # Validate: only one Accountable allowed
    valid_roles = {e.value for e in RaciRoleEnum}
    accountable_users = [uid for uid, role in assignments.items() if role == "A"]
    if len(accountable_users) > 1:
        return JSONResponse({"ok": False, "error": "ניתן להגדיר רק אחראי סמכות אחד (A) להחלטה"}, status_code=400)

    # Insert new ones (skip empty/null roles)
    new_assignments: dict[int, str] = {}
    for user_id_str, role in assignments.items():
        if role not in valid_roles:
            continue
        uid_int = int(user_id_str)
        new_assignments[uid_int] = role
        session.add(DecisionRaciRole(
            decision_id=decision_id,
            user_id=uid_int,
            role=RaciRoleEnum(role),
            assigned_by_ai=False,
        ))

    await session.commit()
    logger.info(f"save_raci: new_assignments: {new_assignments}")

    # Auto-approve if accountable is the submitter
    try:
        from app.services.raci_service import check_and_auto_approve
        await check_and_auto_approve(decision_id, session)
    except Exception as e:
        logger.warning(f"save_raci: auto-approve failed: {e}")

    # Notify only users whose role changed (or is new)
    logger.info(f"save_raci: about to notify changed RACI users for decision {decision_id}")
    try:
        from app.services.raci_service import notify_changed_raci_users
        await notify_changed_raci_users(decision_id, old_assignments, new_assignments, session)
        logger.info(f"save_raci: notify_changed_raci_users completed for decision {decision_id}")
    except Exception as e:
        logger.warning(f"save_raci: failed to notify users for decision {decision_id}: {e}", exc_info=True)

    logger.info(f"save_raci: COMPLETE for decision {decision_id}")
    return JSONResponse({"ok": True})


@router.get("/decisions/{decision_id}/suggest-distribution")
async def suggest_distribution_api(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import JSONResponse
    from app.services.distribution_service import suggest_distribution
    d = await session.get(Decision, decision_id)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    submitter = await session.get(User, d.submitter_id)
    suggestions = await suggest_distribution(d, submitter, session)
    return JSONResponse({
        "suggestions": suggestions,
        "decision_type": d.type.value,
        "ai_powered": True,
    })


@router.post("/decisions/{decision_id}/distribute")
async def distribute_decision(
    decision_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.services.distribution_service import send_distribution
    from app.services.telegram_polling import telegram_bot

    d = await session.get(Decision, decision_id)
    if not d:
        return RedirectResponse("/dashboard/decisions?error=החלטה+לא+נמצאה", status_code=303)

    form = await request.form()
    override_type = form.get("override_type", "")

    # Collect recipients from form: user_{id}_type = info|execution|approval
    recipients = []
    for key, value in form.items():
        if key.startswith("user_") and key.endswith("_type") and value:
            try:
                uid = int(key.split("_")[1])
                recipients.append({"user_id": uid, "dist_type": value})
            except (ValueError, IndexError):
                pass

    if not recipients:
        return RedirectResponse(f"/dashboard/decisions?error=לא+נבחרו+נמענים", status_code=303)

    submitter = await session.get(User, d.submitter_id)
    bot = telegram_bot.application.bot if telegram_bot.application else None

    sent = await send_distribution(d, submitter, recipients, session, bot, override_type or None)
    return RedirectResponse(f"/dashboard/decisions?msg=ההפצה+נשלחה+ל-{sent}+משתמשים", status_code=303)


@router.get("/decisions/{decision_id}/distribution-status")
async def distribution_status(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import JSONResponse
    rows = await session.execute(
        select(DecisionDistribution, User.username, User.job_title)
        .join(User, DecisionDistribution.user_id == User.id)
        .where(DecisionDistribution.decision_id == decision_id)
    )
    result = []
    for dist, username, job_title in rows:
        result.append({
            "id": dist.id,
            "username": username,
            "job_title": job_title or "",
            "dist_type": dist.distribution_type.value,
            "status": dist.status.value,
            "notes": dist.notes or "",
            "sent_at": dist.sent_at.strftime("%d/%m %H:%M") if dist.sent_at else None,
            "responded_at": dist.responded_at.strftime("%d/%m %H:%M") if dist.responded_at else None,
        })
    return JSONResponse({"distributions": result})


# -----------------------------------------------------------------------
# User self-edit profile page (accessed via unique token)
# -----------------------------------------------------------------------

profile_router = APIRouter(prefix="/profile", tags=["profile"])
profile_templates = Jinja2Templates(directory="app/templates")


@profile_router.get("/{token}", response_class=HTMLResponse)
async def profile_page(token: str, request: Request, session: AsyncSession = Depends(get_db_session),
                       msg: str = None, error: str = None):
    user = await session.scalar(select(User).where(User.profile_token == token))
    if not user:
        return HTMLResponse("<h3>קישור לא תקין</h3>", status_code=404)

    all_users = (await session.execute(select(User).where(User.id != user.id))).scalars().all()
    return profile_templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "all_users": all_users,
        "token": token,
        "msg": msg,
        "error": error,
        "role_labels": ROLE_LABELS,
    })


# -----------------------------------------------------------------------
# Learning / Calibration Dashboard
# -----------------------------------------------------------------------

@router.get("/learning", response_class=HTMLResponse)
async def learning_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Calibration dashboard — how well is the AI performing per decision type?"""
    pending_approvals = await _pending_approvals_count(current_user.id, session)

    TYPE_LABELS = {
        "info": "מידע", "normal": "רגיל",
        "critical": "קריטי", "uncertain": "לא ודאי",
    }

    # Simplified: no calibration without confidence metric
    calibration = []
    worst_decisions = []
    best_decisions = []

    # Recent lessons
    lessons_rows = (await session.execute(
        select(LessonLearned, Decision)
        .join(Decision, LessonLearned.decision_id == Decision.id)
        .order_by(LessonLearned.created_at.desc())
        .limit(10)
    )).all()
    lessons = [
        {
            "id": ll.id,
            "decision_id": ll.decision_id,
            "lesson_text": ll.lesson_text,
            "decision_type": ll.decision_type,
            "type_he": TYPE_LABELS.get(ll.decision_type or "", ll.decision_type or "—"),
            "tags": json.loads(ll.tags or "[]"),
            "created_at": ll.created_at.strftime("%d/%m/%Y") if ll.created_at else "—",
            "feedback_score": d.feedback_score,
        }
        for ll, d in lessons_rows
    ]

    # 5. Monthly decision trend (last 6 months)
    six_months_ago = datetime.utcnow() - timedelta(days=180)
    trend_rows = (await session.execute(
        select(
            func.date_trunc("month", Decision.created_at).label("month"),
            func.count().label("cnt"),
            func.avg(Decision.feedback_score).label("avg_fb"),
        )
        .where(Decision.created_at >= six_months_ago)
        .group_by("month")
        .order_by("month")
    )).all()
    trend_labels = [row.month.strftime("%m/%Y") for row in trend_rows]
    trend_data = [row.cnt for row in trend_rows]
    trend_feedback = [round(float(row.avg_fb), 2) if row.avg_fb else None for row in trend_rows]

    # 6. Total lessons count
    total_lessons = await session.scalar(select(func.count()).select_from(LessonLearned))

    # 7. Overall stats
    overall = (await session.execute(
        select(
            func.count().label("total"),
            func.avg(Decision.feedback_score).label("avg_fb"),
        )
        .where(Decision.feedback_score.isnot(None))
    )).one_or_none()
    overall_stats = {
        "total_with_feedback": overall.total if overall else 0,
        "avg_fb": round(float(overall.avg_fb), 2) if overall and overall.avg_fb else None,
    }

    # 8. Phase 4 — pending extraction count + knowledge summaries
    from app.services.lessons_service import get_pending_extraction_count, get_knowledge_summaries
    from app.models import KnowledgeSummary
    pending_extraction = await get_pending_extraction_count(session)
    raw_summaries = await get_knowledge_summaries(session)
    TYPE_LABELS_FULL = {"info": "מידע", "normal": "רגיל", "critical": "קריטי", "uncertain": "לא ודאי"}
    knowledge_summaries = []
    for ks in raw_summaries:
        try:
            parsed = json.loads(ks.summary_text)
        except Exception:
            parsed = {}
        knowledge_summaries.append({
            "decision_type": ks.decision_type,
            "type_he": TYPE_LABELS_FULL.get(ks.decision_type, ks.decision_type),
            "lesson_count": ks.lesson_count,
            "updated_at": ks.updated_at.strftime("%d/%m/%Y %H:%M"),
            "principles": parsed.get("principles", []),
            "risks": parsed.get("risks", []),
            "success_factors": parsed.get("success_factors", []),
            "ai_guidance": parsed.get("ai_guidance", ""),
        })

    return templates.TemplateResponse("learning.html", {
        "request": request,
        "current_user": current_user,
        "pending_approvals": pending_approvals,
        "calibration": calibration,
        "worst_decisions": worst_decisions,
        "best_decisions": best_decisions,
        "lessons": lessons,
        "total_lessons": total_lessons or 0,
        "trend_labels": json.dumps(trend_labels),
        "trend_data": json.dumps(trend_data),
        "trend_feedback": json.dumps(trend_feedback),
        "overall_stats": overall_stats,
        "pending_extraction": pending_extraction,
        "knowledge_summaries": knowledge_summaries,
    })


@router.get("/learning/batch-status")
async def learning_batch_status(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Return how many decisions are pending lesson extraction."""
    from app.services.lessons_service import get_pending_extraction_count
    from app.models import LessonLearned
    pending = await get_pending_extraction_count(session)
    total_lessons = await session.scalar(select(func.count()).select_from(LessonLearned))
    return {"pending": pending, "total_lessons": total_lessons or 0}


@router.post("/learning/batch-extract")
async def learning_batch_extract(
    current_user: User = Depends(get_current_user),
):
    """Trigger batch lesson extraction in the background. Returns immediately."""
    import asyncio
    from app.services.lessons_service import run_batch_extraction
    asyncio.get_event_loop().create_task(run_batch_extraction())
    return {"status": "started"}


@router.post("/learning/summarize/{decision_type}")
async def learning_summarize_type(
    decision_type: str,
    current_user: User = Depends(get_current_user),
):
    """Regenerate knowledge summary for a specific decision type."""
    import asyncio
    from app.services.lessons_service import generate_knowledge_summary
    asyncio.get_event_loop().create_task(generate_knowledge_summary(decision_type))
    return {"status": "started", "type": decision_type}


@profile_router.post("/{token}")
async def profile_save(
    token: str,
    job_title: str = Form(""),
    responsibilities: str = Form(""),
    hierarchy_level: str = Form(""),
    manager_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
):
    user = await session.scalar(select(User).where(User.profile_token == token))
    if not user:
        return HTMLResponse("<h3>קישור לא תקין</h3>", status_code=404)

    user.job_title = job_title or None
    user.responsibilities = responsibilities.strip() or None
    user.hierarchy_level = int(hierarchy_level) if hierarchy_level.strip() else None
    user.manager_id = int(manager_id) if manager_id.strip() else None
    await session.commit()
    return RedirectResponse(f"/profile/{token}?msg=הפרופיל+עודכן+בהצלחה", status_code=303)
