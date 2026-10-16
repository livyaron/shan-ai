"""The main dashboard's three other stations: missions, projects, lessons.

`/dashboard` shows four domains side by side and zooms into any one of them.
Decisions are computed in `routers/dashboard._dashboard_stats`; this module adds
the other three with the same shape of answer (KPIs, a 7-day series, a
breakdown, a short list), so one template draws all four.

Every definition is borrowed, never re-spelled: "active" and "new" from
`missions_menu_service`, the KPI windows from `war_room_kpis`, the stage → sector
map from `stage_sectors`, "late" from `pattern_service`. Each domain is
best-effort: a failure returns `{"ok": False}` and the station shows "לא זמין"
instead of taking the whole page down.

Only `.all()` / `.scalar()` are called on results, so the no-DB test session
answers every query.
"""

import datetime
import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Mission, MissionStatusEnum, Project, User
from app.services import missions_menu_service as oms
from app.services import stage_sectors
from app.services import war_room_kpis as kpis
from app.services.pattern_service import LATE_MONTHS, MONTH_DAYS

logger = logging.getLogger(__name__)

RECENT_ROWS = 10      # rows in each zoomed station's list
TOP_BREAKDOWN = 8     # bars in a long breakdown (stages, lesson categories)

# lessons_ui/src/lib/lessonStatus.ts is the module's own source of truth for
# these buckets; this mirrors it for a count, so keep the two in step.
LESSON_APPROVED_STATUSES = ("approved", "distributed", "closed")
LESSON_APPROVED_WORKFLOW = "אושר והופץ"
LESSON_DRAFT_WORKFLOW = "ממתין להשלמת יוצר"
LESSON_RISKS = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}


def _days(n: int, today: datetime.date) -> list[datetime.date]:
    first = today - datetime.timedelta(days=n - 1)
    return [first + datetime.timedelta(days=i) for i in range(n)]


def _series(rows, days: list[datetime.date]) -> list[int]:
    by_day = {}
    for day, cnt in rows:
        if day is None:
            continue
        by_day[day.date() if isinstance(day, datetime.datetime) else day] = cnt
    return [by_day.get(d, 0) for d in days]


def _fmt(d: datetime.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


async def missions_stats(session: AsyncSession, n_days: int) -> dict:
    today = oms.today_il()
    now = datetime.datetime.utcnow()
    active = Mission.status.in_(oms.ACTIVE_STATUSES)

    counts, overdue = await oms.get_board_counts(session)
    done_week = (await session.execute(
        select(func.count(Mission.id)).where(
            Mission.status == MissionStatusEnum.DONE.value,
            Mission.completed_at >= kpis.week_ago(now),
        )
    )).scalar() or 0
    new_cut = now - datetime.timedelta(hours=oms.NEW_MISSION_HOURS)
    new_24h = (await session.execute(
        select(func.count(Mission.id)).where(active, Mission.created_at > new_cut)
    )).scalar() or 0
    undated = (await session.execute(
        select(func.count(Mission.id)).where(active, Mission.due_date.is_(None))
    )).scalar() or 0

    days = _days(n_days, now.date())
    since = datetime.datetime.combine(days[0], datetime.time.min)
    day = func.date_trunc("day", Mission.created_at)
    opened = (await session.execute(
        select(day, func.count()).where(Mission.created_at >= since).group_by(day)
    )).all()
    cday = func.date_trunc("day", Mission.completed_at)
    closed = (await session.execute(
        select(cday, func.count()).where(
            Mission.status == MissionStatusEnum.DONE.value, Mission.completed_at >= since,
        ).group_by(cday)
    )).all()

    owners = (await session.execute(
        select(User.username, func.count(Mission.id))
        .join(User, Mission.owner_id == User.id)
        .where(active)
        .group_by(User.username)
        .order_by(func.count(Mission.id).desc())
        .limit(TOP_BREAKDOWN)
    )).all()

    recent = (await session.execute(
        select(Mission.id, Mission.title, Mission.is_urgent, Mission.is_important,
               Mission.due_date, Mission.created_at,
               User.username)
        .join(User, Mission.owner_id == User.id)
        .where(active)
        .order_by(Mission.due_date.asc().nulls_last(), Mission.id.desc())
        .limit(RECENT_ROWS)
    )).all()
    rows = []
    for mid, title, urg, imp, due, created, owner in recent:
        m = Mission(id=mid, status=oms.ACTIVE_STATUSES[0], is_urgent=urg,
                    is_important=imp, due_date=due, created_at=created)
        q = oms.quadrant_key(m)
        rows.append({
            "id": mid, "title": title, "owner": owner, "quadrant": q,
            "quadrant_label": oms.quadrant_label(q), "due": _fmt(due),
            "overdue": oms.is_overdue(m, today), "new": oms.is_new(m, now),
        })

    return {
        "ok": True,
        "open": sum(counts.values()),
        "do_now": counts.get("do", 0),
        "overdue": overdue,
        "done_week": done_week,
        "new_24h": new_24h,
        "undated": undated,
        "quadrants": [[k, f"{e} {v}", counts.get(k, 0)] for k, e, v, _ in oms.QUADRANTS],
        "daily_data": _series(opened, days),
        "daily_done": _series(closed, days),
        "owners": [[name, n] for name, n in owners],
        "recent": rows,
    }


async def projects_stats(session: AsyncSession) -> dict:
    today = oms.today_il()
    late_days = round(LATE_MONTHS * MONTH_DAYS)
    rows = (await session.execute(
        select(Project.project_identifier, Project.name, Project.stage, Project.manager,
               Project.dev_plan_date, Project.estimated_finish_date,
               Project.finish_date_text, Project.short_supervisors, Project.short_testers)
        .where(Project.is_active)
    )).all()

    sectors = {k: 0 for k in stage_sectors.SECTORS}
    stages: dict[str, int] = {}
    active = past_due = late = undated = short = 0
    watch = []
    for ident, name, stage, manager, plan, forecast, ftext, s_sup, s_test in rows:
        stage = (stage or "").strip()
        if stage in stage_sectors.CLOSED_STAGES:
            continue
        active += 1
        stages[stage or "ללא סטטוס"] = stages.get(stage or "ללא סטטוס", 0) + 1
        for sec in stage_sectors.STAGE_SECTORS.get(stage, (stage_sectors.UNKNOWN,)):
            sectors[sec] += 1
        is_past = bool(forecast and forecast < today)
        slip = (forecast - plan).days if forecast and plan else None
        is_late = slip is not None and slip > late_days
        past_due += is_past
        late += is_late
        undated += bool(ftext) and not forecast
        short += bool(s_sup or s_test)
        if is_past or is_late:
            watch.append({
                "id": ident, "name": name or ident, "stage": stage or "—",
                "manager": manager or "—", "forecast": _fmt(forecast), "plan": _fmt(plan),
                "slip_months": round(slip / MONTH_DAYS, 1) if slip is not None else None,
                "past_due": is_past, "_sort": slip if slip is not None else -1,
            })
    watch.sort(key=lambda r: (not r["past_due"], -r["_sort"]))
    for r in watch:
        r.pop("_sort")

    return {
        "ok": True,
        "active": active,
        "past_due": past_due,
        "late": late,
        "undated": undated,
        "short_staff": short,
        # per-sector, never summed: one stage belongs to two sectors
        "sectors": [[k, label, sectors[k]] for k, label in stage_sectors.SECTORS.items() if sectors[k]],
        "stages": sorted(stages.items(), key=lambda kv: -kv[1])[:TOP_BREAKDOWN],
        "watch": watch[:RECENT_ROWS],
        "watch_total": len(watch),
    }


async def lessons_stats(session: AsyncSession, n_days: int) -> dict:
    # The module's schema is created at startup and may be missing on a fresh
    # DB; asking first keeps a failed query from poisoning the transaction.
    if not (await session.execute(text("SELECT to_regclass('lessons.lessons') IS NOT NULL"))).scalar():
        return {"ok": False}
    p = {"appr": list(LESSON_APPROVED_STATUSES), "aw": LESSON_APPROVED_WORKFLOW, "dw": LESSON_DRAFT_WORKFLOW}
    bucket = """CASE WHEN status = ANY(:appr) OR workflow_status = :aw THEN 'approved'
                     WHEN workflow_status = :dw OR status = 'draft' THEN 'draft'
                     ELSE 'pending' END"""
    buckets = dict((await session.execute(
        text(f"SELECT {bucket} AS b, count(*) FROM lessons.lessons GROUP BY b"), p
    )).all())
    risks = dict((await session.execute(
        text("SELECT risk, count(*) FROM lessons.lessons GROUP BY risk")
    )).all())
    cats = (await session.execute(
        text("SELECT category, count(*) AS n FROM lessons.lessons GROUP BY category "
             "ORDER BY n DESC LIMIT :lim"), {"lim": TOP_BREAKDOWN}
    )).all()

    days = _days(n_days, datetime.datetime.utcnow().date())
    since = datetime.datetime.combine(days[0], datetime.time.min, tzinfo=datetime.timezone.utc)
    activity = (await session.execute(
        text("SELECT date_trunc('day', created_at AT TIME ZONE 'UTC') AS d, count(*) "
             "FROM lessons.lesson_workflow_events WHERE created_at >= :since GROUP BY d"),
        {"since": since},
    )).all()
    series = _series(activity, days)
    impl_open = (await session.execute(
        text("SELECT count(*) FROM lessons.lesson_implementations WHERE is_implemented IS NOT TRUE "
             "AND is_relevant IS NOT FALSE")
    )).scalar() or 0

    return {
        "ok": True,
        "total": sum(buckets.values()),
        "approved": buckets.get("approved", 0),
        "pending": buckets.get("pending", 0),
        "draft": buckets.get("draft", 0),
        "week_activity": sum(series),
        "impl_open": impl_open,
        "risks": [[k, LESSON_RISKS[k], risks.get(k, 0)] for k in LESSON_RISKS],
        "categories": [[c, n] for c, n in cats],
        "daily_data": series,
    }


async def all_domains(session: AsyncSession, n_days: int) -> dict:
    """The three stations, each isolated: one failing never hides the others."""
    out = {}
    for key, call in (
        ("missions", lambda: missions_stats(session, n_days)),
        ("projects", lambda: projects_stats(session)),
        ("lessons", lambda: lessons_stats(session, n_days)),
    ):
        try:
            # a savepoint, so a failed station never aborts the next one's queries
            nested = getattr(session, "begin_nested", None)
            if nested:
                async with nested():
                    out[key] = await call()
            else:
                out[key] = await call()
        except Exception:
            logger.exception("dashboard station %s failed", key)
            out[key] = {"ok": False}
    return out
