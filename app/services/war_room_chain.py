"""חדר מבצעים — משימת המשך: the chain a mission grew out of.

A mission is closed and the work it produced becomes the next mission. The
closed one must NOT come back as a card on the open board (it is closed), but
the live mission has to be able to say where it came from. That history is this
module: `load_chains` walks `Mission.parent_id` upwards and returns, per live
mission, the whole line of missions that led to it, oldest first, with the live
one last and flagged `current`.

Pure shaping over the DB — the layouts only draw what comes back, and never
re-walk the links themselves.
"""

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Mission, MissionStatusEnum, MissionUpdate
from app.services.missions_menu_service import ACTIVE_STATUSES

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# A chain deeper than this is a loop or a data error, not a work history. The
# walk stops rather than hanging the board while it follows links forever.
MAX_DEPTH = 12

# What the live link at the end of a chain says instead of a closing date.
TODAY_LABEL = "היום"


def _il_date(dt: datetime.datetime | None) -> datetime.date | None:
    """Naive-UTC stamp (as stored) → the Israel-local calendar day it happened on."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(_IL_TZ).date()


def _closed_at(m: Mission) -> datetime.datetime | None:
    """When this mission left the board. Deferred import on purpose.

    The fallback rule (completed_at → the closing note → updated_at) has exactly
    one home, in missions_report_service; re-spelling it here is how the XLSX and
    the board would start disagreeing about when a mission closed.
    """
    from app.services import missions_report_service as mrs
    return mrs._closed_at(m)


def _range(m: Mission) -> str:
    """'20/08/2026–01/09/2026' for a closed link, '…–היום' for the live one."""
    start = _il_date(m.created_at)
    head = start.strftime("%d/%m/%Y") if start else "—"
    if m.status in ACTIVE_STATUSES:
        return f"{head}–{TODAY_LABEL}"
    end = _il_date(_closed_at(m))
    return f"{head}–{end.strftime('%d/%m/%Y')}" if end else head


def _step(m: Mission, current: bool) -> dict:
    return {
        "id": m.id,
        "title": m.title or "",
        "status": m.status,
        "current": current,
        "done": m.status == MissionStatusEnum.DONE.value,
        "cancelled": m.status == MissionStatusEnum.CANCELLED.value,
        "range": _range(m),
    }


async def load_chains(session: AsyncSession, missions: list[Mission]) -> dict[int, list[dict]]:
    """{mission id → its chain, oldest first, itself last} — only for missions that have one.

    A mission with no parent gets no entry at all, so a template can ask
    `chains.get(m.id)` and draw nothing for the ordinary case, which is most of
    the board.

    Loaded level by level (one query per generation, not one per mission) and
    cycle-guarded: a row whose parent chain loops back on itself stops the walk
    instead of looping.
    """
    roots = [m for m in missions if getattr(m, "parent_id", None)]
    if not roots:
        return {}

    known: dict[int, Mission] = {m.id: m for m in missions if m.id is not None}
    wanted = {m.parent_id for m in roots if m.parent_id not in known}
    depth = 0
    while wanted and depth < MAX_DEPTH:
        rows = list((await session.scalars(
            select(Mission)
            # The closing-date fallback reads the status-update log; without this
            # it is unloaded under asyncio and every closed link in the chain
            # would silently lose its end date.
            .options(selectinload(Mission.updates).selectinload(MissionUpdate.author))
            .where(Mission.id.in_(wanted))
        )).all())
        if not rows:
            break
        for m in rows:
            known[m.id] = m
        wanted = {m.parent_id for m in rows if m.parent_id and m.parent_id not in known}
        depth += 1

    chains: dict[int, list[dict]] = {}
    for m in roots:
        line: list[dict] = [_step(m, current=True)]
        seen = {m.id}
        parent_id = m.parent_id
        while parent_id and parent_id not in seen and len(line) <= MAX_DEPTH:
            parent = known.get(parent_id)
            if parent is None:
                break
            line.append(_step(parent, current=False))
            seen.add(parent.id)
            parent_id = parent.parent_id
        if len(line) > 1:            # a parent that no longer exists is not a chain
            chains[m.id] = list(reversed(line))
    return chains


async def load_child_counts(session: AsyncSession, missions: list[Mission]) -> dict[int, int]:
    """{mission id → how many follow-up missions were opened from it}.

    What the closed parent needs in order to say "this did not just vanish" when
    someone finds it through the status filter.
    """
    ids = [m.id for m in missions if m.id is not None]
    if not ids:
        return {}
    rows = (await session.execute(
        select(Mission.parent_id, func.count(Mission.id))
        .where(Mission.parent_id.in_(ids))
        .group_by(Mission.parent_id)
    )).all()
    return {pid: n for pid, n in rows if pid is not None}
