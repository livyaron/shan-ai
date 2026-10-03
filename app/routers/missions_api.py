"""Read-only missions list for a war-room operator.

GET /api/missions is JSON only. It does not write, and it uses the same
INVENTORY_API_KEY as GET /api/inventory so no second secret is required.
The route stays closed (503) until that key is set.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Mission, MissionStatusEnum
from app.routers.inventory import _iso, _require_inventory_key
from app.services.missions_menu_service import quadrant_key
from app.services.war_room_kpis import week_ago

router = APIRouter(prefix="/api", tags=["missions"])

# One line for an operator, not the update log. Cut in Python so a long note
# cannot blow up the payload.
_NOTE_MAX = 200

_OPEN = MissionStatusEnum.OPEN.value
_CLOSED = (MissionStatusEnum.DONE.value, MissionStatusEnum.CANCELLED.value)


def _loaded_attr(mission, name: str):
    """Attribute value, or None when an ORM relation was not eager-loaded.

    A SimpleNamespace (tests) has no inspection state and is read directly.
    An unloaded relationship is left alone: touching it would lazy-load, which
    raises under asyncio.
    """
    try:
        from sqlalchemy import inspect as sa_inspect
        state = sa_inspect(mission)
    except Exception:
        return getattr(mission, name, None)
    if name in state.unloaded:
        return None
    return getattr(mission, name, None)


def _owner_name(mission) -> str | None:
    """Username only. Never email, telegram id, or password."""
    owner = _loaded_attr(mission, "owner")
    if owner is None:
        return None
    name = getattr(owner, "username", None)
    if name is None:
        return None
    name = str(name).strip()
    return name or None


def _latest_note(mission) -> str | None:
    updates = _loaded_attr(mission, "updates") or []
    if not updates:
        return None

    def _key(update):
        created = getattr(update, "created_at", None) or datetime.min
        return (created, getattr(update, "id", 0) or 0)

    text = getattr(max(updates, key=_key), "text", None) or ""
    flat = " ".join(str(text).split())
    if not flat:
        return None
    if len(flat) <= _NOTE_MAX:
        return flat
    return flat[: _NOTE_MAX - 3].rstrip() + "..."


def _status(mission) -> str:
    raw = mission.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _due(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _mission_row(mission: Mission) -> dict:
    return {
        "id": mission.id,
        "title": mission.title,
        "description": mission.description,
        "status": _status(mission),
        "is_urgent": bool(mission.is_urgent),
        "is_important": bool(mission.is_important),
        "quadrant": quadrant_key(mission),
        "due_date": _due(mission.due_date),
        "owner": _owner_name(mission),
        "parent_id": mission.parent_id,
        "completed_at": _iso(mission.completed_at),
        "created_at": _iso(mission.created_at),
        "updated_at": _iso(mission.updated_at),
        "latest_note": _latest_note(mission),
    }


def _missions_query(now: datetime | None = None):
    """Open missions, plus done/cancelled closed inside the war-room week window.

    "Recent" is the same 7-day naive-UTC cutoff as the הושלמו השבוע KPI
    (war_room_kpis.week_ago). Cancelled rows use completed_at the same way
    done rows do. A closed row with no stamp still counts when updated_at is
    inside the window, so a legacy NULL does not hide it forever.
    """
    cutoff = week_ago(now)
    recent_closed = and_(
        Mission.status.in_(_CLOSED),
        or_(
            Mission.completed_at >= cutoff,
            and_(Mission.completed_at.is_(None), Mission.updated_at >= cutoff),
        ),
    )
    return (
        select(Mission)
        .options(
            selectinload(Mission.owner),
            selectinload(Mission.updates),
        )
        .where(or_(Mission.status == _OPEN, recent_closed))
        .order_by(
            case((Mission.status == _OPEN, 0), else_=1),
            Mission.due_date.asc().nulls_last(),
            Mission.title.asc(),
        )
    )


@router.get("/missions")
async def missions(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    """Active and recent missions. Read-only."""
    _require_inventory_key(
        authorization,
        unconfigured_detail="Missions API is not configured",
    )
    result = await session.execute(_missions_query())
    rows = result.scalars().all()
    return {"missions": [_mission_row(m) for m in rows]}
