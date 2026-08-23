"""War Room router — operations-room mission board (חדר מבצעים) web screen."""

import datetime
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from pydantic import BeforeValidator

from app.database import get_db_session
from app.models import Mission, MissionStatusEnum, MissionUpdate, User, RoleEnum
from app.routers.login import get_current_user
from app.services import missions_menu_service as oms
from app.services import war_room_styles as wrs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/war-room", tags=["war-room"])
templates = Jinja2Templates(directory="app/templates")


def _blank_to_none(value: object) -> object:
    """An empty <select>/<input> submits `?owner=`, not a missing param.

    Without this, "כל האחראים" (value="") reaches Pydantic as "" and the filter
    form answers with a 422 int_parsing blob instead of the board. Blank means
    "no filter", exactly like the param being absent.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


_BLANK_TO_NONE = BeforeValidator(_blank_to_none)

# Optional int params that tolerate the empty string an HTML form submits.
# The query variant MUST carry an explicit `Query()` inside the Annotated —
# without it FastAPI 0.104 builds the field straight from `int | None` and the
# validator never runs, which is exactly how the 422 got shipped.
BlankableIntQuery = Annotated[int | None, Query(), _BLANK_TO_NONE]
BlankableIntForm = Annotated[int | None, _BLANK_TO_NONE]


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


def _quadrant_of(m: Mission) -> str:
    """Quadrant label for one mission — the layouts that don't draw the 2×2 grid
    still need to say which quadrant a mission sits in."""
    return oms.quadrant_label(oms.quadrant_key(m))


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
    owner: BlankableIntQuery = None,
    status: str = "active",
    q: str = "",
    style: str | None = None,
):
    today = oms.today_il()
    active_style = wrs.resolve(style, current_user.war_room_style)

    base = select(Mission).options(
        selectinload(Mission.owner),
        selectinload(Mission.created_by),
        selectinload(Mission.updates).selectinload(MissionUpdate.author),
    )
    if status == "active":
        base = base.where(Mission.status.in_(oms.ACTIVE_STATUSES))
    elif status in (s.value for s in MissionStatusEnum):
        base = base.where(Mission.status == status)
    if owner:
        base = base.where(Mission.owner_id == owner)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(
            Mission.title.ilike(like),
            Mission.description.ilike(like),
            select(MissionUpdate.id)
            .where(MissionUpdate.mission_id == Mission.id, MissionUpdate.text.ilike(like))
            .exists(),
        ))

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

    ctx = {
        "request": request,
        "current_user": current_user,
        "missions": missions,
        "quadrants": quadrants,
        "quadrant_defs": oms.QUADRANTS,
        "status_labels": oms.STATUS_LABELS,
        # Single source of truth for "is this mission live?" — the template must
        # never re-spell the status list.
        "active_statuses": oms.ACTIVE_STATUSES,
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
        "fmt_stamp": oms.format_stamp_il,
        "quadrant_of": _quadrant_of,
        "msg": request.query_params.get("msg", ""),
        # The style switcher renders in every layout, so every layout gets these.
        "styles": wrs.STYLES,
        "current_style": active_style,
        "style_labels": wrs.STYLE_LABELS,
    }

    # Per-style extras. Each block only runs for the style that needs it, so the
    # default board costs exactly what it cost before.
    if active_style == "focus":
        ctx.update(await _focus_context(session, current_user, today))
    elif active_style == "wall":
        ctx.update(await _wall_context(session, today))

    return templates.TemplateResponse(wrs.template_for(active_style), ctx)


async def _focus_context(session: AsyncSession, user: User, today: datetime.date) -> dict:
    """"My missions", split by when they are due.

    Deliberately board-wide and ignores the filter bar: the point of this layout
    is "what does today ask of me", which a leftover owner filter would lie about.
    """
    mine = list((await session.scalars(
        select(Mission)
        .options(selectinload(Mission.owner), selectinload(Mission.updates))
        .where(Mission.owner_id == user.id, Mission.status.in_(oms.ACTIVE_STATUSES))
        .order_by(Mission.due_date.asc().nulls_last(), Mission.id.desc())
    )).all())

    due_now = [m for m in mine if m.due_date and m.due_date <= today]
    upcoming = [m for m in mine if m.due_date and m.due_date > today]
    undated = [m for m in mine if m.due_date is None]
    return {
        "my_due_now": due_now,
        "my_upcoming": upcoming[:8],
        "my_undated": undated[:8],
        "my_total": len(mine),
    }


async def _wall_context(session: AsyncSession, today: datetime.date) -> dict:
    """What a room needs to see from four metres: what burns, who carries it, what just closed."""
    active = list((await session.scalars(
        select(Mission)
        .options(selectinload(Mission.owner))
        .where(Mission.status.in_(oms.ACTIVE_STATUSES))
        .order_by(Mission.due_date.asc().nulls_last(), Mission.id.desc())
    )).all())

    load: dict[str, int] = {}
    for m in active:
        name = m.owner.username if m.owner else "—"
        load[name] = load.get(name, 0) + 1
    owner_load = sorted(load.items(), key=lambda kv: -kv[1])[:6]

    closed = list((await session.scalars(
        select(Mission)
        .options(selectinload(Mission.owner))
        .where(Mission.status == MissionStatusEnum.DONE.value)
        .order_by(Mission.completed_at.desc().nulls_last(), Mission.id.desc())
        .limit(4)
    )).all())

    return {
        # Only dated missions can burn; an undated one has nothing to be late for.
        "wall_urgent": [m for m in active if m.due_date is not None][:5],
        "owner_load": owner_load,
        "owner_load_max": max((c for _, c in owner_load), default=1),
        "wall_closed": closed,
    }


@router.post("/style")
async def set_style(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    style: str = Form(...),
):
    """Save the signed-in user's board layout.

    Not an editor action: this changes nothing on the board, only what THIS user
    sees, so viewers may switch too.
    """
    if not wrs.is_known(style):
        return RedirectResponse("/dashboard/war-room?msg=תצוגה+לא+מוכרת", status_code=303)
    current_user.war_room_style = style
    session.add(current_user)
    await session.commit()
    return RedirectResponse("/dashboard/war-room", status_code=303)


@router.get("/report.xlsx")
async def download_report(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    ai: int = 1,
    refresh: int = 0,
):
    """Board report as XLSX. Read-only, so viewers may download it too.

    Served from the day cache built at 04:10. `?refresh=1` rebuilds it against
    current board data while reusing the day's cached AI narrative, so a refresh
    costs no extra Groq tokens. `?ai=0` skips the narrative entirely.
    """
    from io import BytesIO
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import missions_report_service as mrs

    payload, filename, _generated_at = await mrs.build_report_bytes(
        session, with_ai=bool(ai), refresh_data=bool(refresh)
    )
    # A raw Hebrew filename in the header breaks latin-1 encoding — ASCII fallback
    # plus RFC 5987 for the real name.
    ascii_name = f"war-room-report-{datetime.date.today():%d-%m-%Y}.xlsx"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@router.get("/summary")
async def focus_summary(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    refresh: int = 0,
):
    """AI situation assessment of overdue / nearly-overdue missions, for the page modal.

    Served from the day cache; `?refresh=1` rebuilds it against the current board.
    """
    from app.services import missions_report_service as mrs

    text = await mrs.build_focus_summary(session, force=bool(refresh))
    return JSONResponse({"status": "ok", "text": text})


@router.get("/cache-status")
async def cache_status(current_user: User = Depends(get_current_user)):
    """Which of today's expensive artifacts are warm — the answer to "was it prebuilt?".

    Without this there is no way to tell a cold press from a slow Groq call in
    production.
    """
    from app.services import missions_report_service as mrs

    return JSONResponse({"status": "ok", **mrs.cache_status()})


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


@router.post("/bulk")
async def bulk_action(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    ids: str = Form(...),
    action: str = Form(...),
    owner_id: BlankableIntForm = Form(None),
    due_date: str = Form(""),
    quadrant: str = Form(""),
    note: str = Form(""),
):
    """Apply one change to several missions at once — the table layout's reason to exist.

    Every branch routes through the same service helpers the single-mission
    endpoints use, so a bulk edit can never take a path the board itself cannot.
    """
    _require_editor(current_user)

    mission_ids = [int(x) for x in ids.split(",") if x.strip().lstrip("-").isdigit()]
    if not mission_ids:
        return JSONResponse({"status": "error", "message": "לא נבחרו משימות"}, status_code=400)
    if action not in ("assign", "due", "move", "done", "cancel"):
        return JSONResponse({"status": "error", "message": "פעולה לא מוכרת"}, status_code=400)
    if action == "assign" and not owner_id:
        return JSONResponse({"status": "error", "message": "נדרש אחראי"}, status_code=400)
    if action == "move" and quadrant not in {key for key, *_ in oms.QUADRANTS}:
        return JSONResponse({"status": "error", "message": "רביע לא מוכר"}, status_code=400)

    missions = list((await session.scalars(
        select(Mission).where(Mission.id.in_(mission_ids))
    )).all())
    if not missions:
        return JSONResponse({"status": "error", "message": "לא נמצאו משימות"}, status_code=404)

    for m in missions:
        if action == "assign":
            await oms.update_mission(session, m, owner_id=owner_id)
            await _notify_owner_via_telegram(session, m, current_user)
        elif action == "due":
            await oms.update_mission(session, m, due_date=_parse_due(due_date))
        elif action == "move":
            await oms.update_mission(session, m, quadrant=quadrant)
        else:
            if note.strip():
                await oms.add_mission_update(session, m, note, current_user, kind="close")
            await oms.set_status(
                session, m,
                MissionStatusEnum.DONE.value if action == "done" else MissionStatusEnum.CANCELLED.value,
            )

    return JSONResponse({"status": "ok", "message": f"עודכנו {len(missions)} משימות"})


@router.post("/{mission_id}/status")
async def change_status(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    action: str = Form(...),
    note: str = Form(""),
):
    """Change a mission's status. `note` is the optional closing text (mirrors the bot)."""
    _require_editor(current_user)
    new_status = {
        "done": MissionStatusEnum.DONE.value,
        "reopen": MissionStatusEnum.OPEN.value,
        "cancel": MissionStatusEnum.CANCELLED.value,
    }.get(action)
    if new_status is None:
        return JSONResponse({"status": "error", "message": "פעולה לא מוכרת"}, status_code=400)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    if note.strip() and action in ("done", "cancel"):
        await oms.add_mission_update(session, m, note, current_user, kind="close")
    await oms.set_status(session, m, new_status)
    return JSONResponse({"status": "ok", "message": "הסטטוס עודכן"})


@router.post("/{mission_id}/note")
async def add_note(
    mission_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    note: str = Form(...),
):
    """Append a free-text status update — mirrors '➕ הוסף סטטוס' on the bot card."""
    _require_editor(current_user)
    if not note.strip():
        return JSONResponse({"status": "error", "message": "נדרש טקסט לעדכון"}, status_code=400)
    m = await session.get(Mission, mission_id)
    if not m:
        return JSONResponse({"status": "error", "message": "המשימה לא נמצאה"}, status_code=404)
    await oms.add_mission_update(session, m, note, current_user)
    return JSONResponse({"status": "ok", "message": "העדכון נוסף"})


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
