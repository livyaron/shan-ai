"""Dashboard router - HTML metrics and analytics for Shan-AI."""

import json
import random
import string
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from typing import Optional

from app.database import get_db_session
from app.models import Decision, User, DecisionTypeEnum, DecisionStatusEnum, RoleEnum, DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum
from app.routers.login import get_current_user


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


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):

    # --- Counts by type ---
    type_counts_q = await session.execute(
        select(Decision.type, func.count().label("cnt")).group_by(Decision.type)
    )
    type_counts = {row.type.value: row.cnt for row in type_counts_q}

    # --- Counts by status ---
    status_counts_q = await session.execute(
        select(Decision.status, func.count().label("cnt")).group_by(Decision.status)
    )
    status_counts = {row.status.value: row.cnt for row in status_counts_q}

    # --- Average confidence by type ---
    avg_conf_q = await session.execute(
        select(Decision.type, func.avg(Decision.confidence).label("avg_conf"))
        .group_by(Decision.type)
    )
    avg_confidence = {row.type.value: round(float(row.avg_conf) * 100) for row in avg_conf_q}

    # --- Average feedback score ---
    avg_feedback_q = await session.execute(
        select(func.avg(Decision.feedback_score)).where(Decision.feedback_score.isnot(None))
    )
    avg_feedback = avg_feedback_q.scalar()
    avg_feedback = round(float(avg_feedback), 2) if avg_feedback else None

    # --- Total decisions & users ---
    total_decisions = sum(type_counts.values())
    total_users_q = await session.execute(select(func.count()).select_from(User))
    total_users = total_users_q.scalar()

    # --- Last 7 days decisions ---
    week_ago = datetime.utcnow() - timedelta(days=7)
    daily_q = await session.execute(
        select(
            func.date_trunc("day", Decision.created_at).label("day"),
            func.count().label("cnt")
        )
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
            "confidence": round(d.confidence * 100) if d.confidence else 0,
            "username": username,
            "created_at": d.created_at.strftime("%d/%m/%Y %H:%M"),
            "feedback_score": d.feedback_score,
        }
        for d, username in recent_rows
    ]

    # --- Decisions per role ---
    role_q = await session.execute(
        select(User.role, func.count(Decision.id).label("cnt"))
        .join(Decision, Decision.submitter_id == User.id)
        .where(User.role.isnot(None))
        .group_by(User.role)
    )
    role_counts = {row.role.value: row.cnt for row in role_q}

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
        "avg_confidence": avg_confidence,
        "daily_labels": json.dumps(daily_labels),
        "daily_data": json.dumps(daily_data),
        "recent_decisions": recent_decisions,
        "role_counts": role_counts,
        "type_labels_he": type_labels_he,
        "status_labels_he": status_labels_he,
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
    return templates.TemplateResponse("users.html", {
        "request": request,
        "current_user": current_user,
        "users": users,
        "roles": [r.value for r in RoleEnum],
        "role_labels": ROLE_LABELS,
        "msg": msg,
        "error": error,
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
    telegram_id: str = Form(""),
    role: str = Form(""),
    job_title: str = Form(""),
    manager_id: str = Form(""),
    password: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.utils.auth import hash_password

    user = await session.get(User, user_id)
    if not user:
        return RedirectResponse("/dashboard/users?error=משתמש+לא+נמצא", status_code=303)

    user.username = username
    user.telegram_id = int(telegram_id) if telegram_id.strip() else None
    if role:
        user.role = RoleEnum(role)
        user.hierarchy_level = _ROLE_HIERARCHY.get(role)
    user.job_title = job_title or None
    user.manager_id = int(manager_id) if manager_id.strip() else None
    if password.strip():
        user.password_hash = hash_password(password)
    await session.commit()
    return RedirectResponse("/dashboard/users?msg=פרטי+משתמש+עודכנו", status_code=303)


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

    q = (
        select(Decision, User.username.label("submitter_name"))
        .join(User, Decision.submitter_id == User.id)
        .order_by(Decision.created_at.desc())
    )
    if filter_status:
        q = q.where(Decision.status == DecisionStatusEnum(filter_status))
    if filter_type:
        q = q.where(Decision.type == DecisionTypeEnum(filter_type))

    rows = (await session.execute(q)).all()

    decisions = []
    for d, submitter_name in rows:
        approver_name = None
        if d.approver_id:
            approver = await session.get(User, d.approver_id)
            approver_name = approver.username if approver else None
        decisions.append({
            "id": d.id,
            "type": d.type.value,
            "status": d.status.value,
            "summary": d.summary or "—",
            "problem_description": d.problem_description or "",
            "recommended_action": d.recommended_action or "",
            "confidence": round(d.confidence * 100) if d.confidence else 0,
            "requires_approval": d.requires_approval,
            "assumptions": _json.loads(d.assumptions) if d.assumptions else [],
            "risks": _json.loads(d.risks) if d.risks else [],
            "measurability": d.measurability or "",
            "feedback_score": d.feedback_score,
            "feedback_notes": d.feedback_notes or "",
            "submitter_id": d.submitter_id,
            "submitter_name": submitter_name,
            "approver_id": d.approver_id,
            "approver_name": approver_name,
            "created_at": d.created_at.strftime("%d/%m/%Y %H:%M"),
            "completed_at": d.completed_at.strftime("%d/%m/%Y %H:%M") if d.completed_at else None,
        })

    users_q = (await session.execute(select(User).order_by(User.username))).scalars().all()
    users = users_q
    users_json = [
        {"id": u.id, "username": u.username, "job_title": u.job_title or ""}
        for u in users_q
    ]

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
    })


@router.post("/decisions/{decision_id}/update")
async def update_decision(
    decision_id: int,
    status: str = Form(""),
    approver_id: str = Form(""),
    summary: str = Form(""),
    recommended_action: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    d = await session.get(Decision, decision_id)
    if not d:
        return RedirectResponse("/dashboard/decisions?error=החלטה+לא+נמצאה", status_code=303)

    if status:
        d.status = DecisionStatusEnum(status)
        if status in ("approved", "rejected", "executed") and not d.completed_at:
            d.completed_at = datetime.utcnow()
    if approver_id.strip():
        d.approver_id = int(approver_id)
    if summary.strip():
        d.summary = summary
    if recommended_action.strip():
        d.recommended_action = recommended_action

    await session.commit()
    return RedirectResponse("/dashboard/decisions?msg=החלטה+עודכנה", status_code=303)


@router.post("/decisions/{decision_id}/delete")
async def delete_decision(
    decision_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    d = await session.get(Decision, decision_id)
    if d:
        await session.delete(d)
        await session.commit()
    return RedirectResponse("/dashboard/decisions?msg=החלטה+נמחקה", status_code=303)


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


@profile_router.post("/{token}")
async def profile_save(
    token: str,
    job_title: str = Form(""),
    hierarchy_level: str = Form(""),
    manager_id: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
):
    user = await session.scalar(select(User).where(User.profile_token == token))
    if not user:
        return HTMLResponse("<h3>קישור לא תקין</h3>", status_code=404)

    user.job_title = job_title or None
    user.hierarchy_level = int(hierarchy_level) if hierarchy_level.strip() else None
    user.manager_id = int(manager_id) if manager_id.strip() else None
    await session.commit()
    return RedirectResponse(f"/profile/{token}?msg=הפרופיל+עודכן+בהצלחה", status_code=303)
