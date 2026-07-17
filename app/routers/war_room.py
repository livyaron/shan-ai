"""War Room router — operations-room mission board (חדר מבצעים) web screen."""

import datetime
import logging

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from app.database import get_db_session
from app.models import Mission, MissionStatusEnum, User, RoleEnum
from app.routers.login import get_current_user
from app.services import missions_menu_service as oms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/war-room", tags=["war-room"])
templates = Jinja2Templates(directory="app/templates")


def _require_editor(user: User) -> None:
    """Write actions are blocked for viewer-role users (mirrors the Telegram rule)."""
    if user.role == RoleEnum.VIEWER:
        raise HTTPException(status_code=403, detail="חדר המבצעים במצב צפייה בלבד עבורך")


async def _notify_owner_via_telegram(session: AsyncSession, mission: Mission, actor: User) -> None:
    """Ping the new owner on Telegram — same behavior as assignment from the bot."""
    if mission.owner_id == actor.id:
        return
    owner = await session.get(User, mission.owner_id)
    if not owner or not owner.telegram_id:
        return
    from app.services.telegram_polling import telegram_bot  # deferred: avoids circular import
    bot = (telegram_bot.application.bot
           if telegram_bot.application and telegram_bot.application.bot else None)
    if bot is None:
        return
    import html as _html
    try:
        await bot.send_message(
            chat_id=owner.telegram_id,
            text=(
                f"‏🎯 <b>משימה חדשה הוקצתה לך</b>\n"
                f"<b>{_html.escape(mission.title or '')}</b>\n"
                f"{oms.quadrant_label(oms.quadrant_key(mission), with_axis=True)}\n"
                f"📅 יעד: {oms.format_due(mission.due_date)}\n"
                f"<i>הוקצתה ע\"י {_html.escape(actor.username or '')}</i>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"war_room: owner notification failed: {e}")


def _parse_due(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


@router.get("", response_class=HTMLResponse)
async def war_room_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    owner: int | None = None,
    status: str = "active",
    q: str = "",
):
    today = oms.today_il()

    base = select(Mission).options(
        selectinload(Mission.owner), selectinload(Mission.created_by)
    )
    if status == "active":
        base = base.where(Mission.status.in_(oms.ACTIVE_STATUSES))
    elif status in (s.value for s in MissionStatusEnum):
        base = base.where(Mission.status == status)
    if owner:
        base = base.where(Mission.owner_id == owner)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(Mission.title.ilike(like), Mission.description.ilike(like)))

    missions = list((await session.scalars(
        base.order_by(Mission.due_date.asc().nulls_last(), Mission.id.desc())
    )).all())

    quadrants = {key: [] for key, *_ in oms.QUADRANTS}
    for m in missions:
        quadrants[oms.quadrant_key(m)].append(m)

    # Stat row (always board-wide, independent of filters)
    counts, overdue_count = await oms.get_board_counts(session)
    week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    done_week = await session.scalar(
        select(func.count(Mission.id)).where(
            Mission.status == MissionStatusEnum.DONE.value,
            Mission.completed_at >= week_ago,
        )
    ) or 0

    users = await oms.list_assignable_users(session)

    return templates.TemplateResponse("war_room.html", {
        "request": request,
        "current_user": current_user,
        "quadrants": quadrants,
        "quadrant_defs": oms.QUADRANTS,
        "status_labels": oms.STATUS_LABELS,
        "stats": {
            "open": sum(counts.values()),
            "do_now": counts.get("do", 0),
            "overdue": overdue_count,
            "done_week": done_week,
        },
        "users": users,
        "today": today,
        "filters": {"owner": owner, "status": status, "q": q},
        "is_viewer": current_user.role == RoleEnum.VIEWER,
        "msg": request.query_params.get("msg", ""),
    })


@router.post("/create")
async def create_mission_web(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    title: str = Form(...),
    description: str = Form(""),
    quadrant: str = Form("backlog"),
    owner_id: int = Form(...),
    due_date: str = Form(""),
):
    _require_editor(current_user)
    if not title.strip():
        return RedirectResponse("/dashboard/war-room?msg=נדרשת+כותרת", status_code=303)
    urg, imp = oms.quadrant_flags(quadrant)
    m = await oms.create_mission(
        session,
        title=title,
        description=description.strip() or None,
        is_urgent=urg,
        is_important=imp,
        owner_id=owner_id,
        created_by_id=current_user.id,
        due_date=_parse_due(due_date),
    )
    await _notify_owner_via_telegram(session, m, current_user)
    return RedirectResponse("/dashboard/war-room?msg=המשימה+נוצרה", status_code=303)


@router.post("/{mission_id}/status")
async def change_status(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    action: str = Form(...),
):
    _require_editor(current_user)
    new_status = {
        "start": MissionStatusEnum.IN_PROGRESS.value,
        "done": MissionStatusEnum.DONE.value,
        "reopen": MissionStatusEnum.OPEN.value,
        "cancel": MissionStatusEnum.CANCELLED.value,
    }.get(action)
    if new_status is None:
        return JSONResponse({"status": "error", "message": "פעולה לא מוכרת"}, status_code=400)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    await oms.set_status(session, m, new_status)
    return JSONResponse({"status": "ok", "message": "הסטטוס עודכן"})


@router.post("/{mission_id}/move")
async def move_quadrant(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    quadrant: str = Form(...),
):
    _require_editor(current_user)
    if quadrant not in {key for key, *_ in oms.QUADRANTS}:
        return JSONResponse({"status": "error", "message": "רביע לא מוכר"}, status_code=400)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    await oms.update_mission(session, m, quadrant=quadrant)
    return JSONResponse({"status": "ok", "message": "הרביע עודכן"})


@router.post("/{mission_id}/assign")
async def assign_owner(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    owner_id: int = Form(...),
):
    _require_editor(current_user)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    await oms.update_mission(session, m, owner_id=owner_id)
    await _notify_owner_via_telegram(session, m, current_user)
    return JSONResponse({"status": "ok", "message": "האחראי עודכן"})


@router.post("/{mission_id}/due")
async def change_due(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    due_date: str = Form(""),
):
    _require_editor(current_user)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    await oms.update_mission(session, m, due_date=_parse_due(due_date))
    return JSONResponse({"status": "ok", "message": "תאריך היעד עודכן"})
