"""How the lessons PROCESS is doing — numbers only, no names, no lesson text.

Answers the questions a process owner asks and the module's screens do not:
how long a lesson waits at each step, how many distributed lessons ever get an
answer, how long that takes, and who writes lessons at all.

Read-only over schema `lessons`. `load_rows` is the only DB access; `compute`
is pure. The result carries counts, days and percentages only, so it is safe
to write to the application log (which is how a remote session reads it:
the line starts with LOG_PREFIX).
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

LOG_PREFIX = "LESSONS_PROCESS_METRICS "
DISTRIBUTED_STATUS = "אושר והופץ"
REJECTED_STATUS = "נדחה"
FINAL_STATUSES = (DISTRIBUTED_STATUS, REJECTED_STATUS)
STALE_ANSWER_DAYS = 30


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400


def _spread(values: list[float]) -> dict[str, Any]:
    """n / median / p90 / max, in days with one decimal. Empty → n only."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return {"n": len(s), "median": round(median(s), 1), "p90": round(p90, 1), "max": round(s[-1], 1)}


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def compute(lessons: list[dict[str, Any]], events: list[dict[str, Any]],
            implementations: list[dict[str, Any]], projects: list[dict[str, Any]],
            now: datetime) -> dict[str, Any]:
    by_lesson: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in sorted(events, key=lambda e: (e["created_at"], e.get("id") or 0)):
        by_lesson[e["lesson_id"]].append(e)
    current = {l["id"]: l.get("workflow_status") for l in lessons}

    waits: dict[str, list[float]] = defaultdict(list)       # finished stays at a step
    open_age: dict[str, list[float]] = defaultdict(list)    # lessons sitting at a step right now
    end_to_end: list[float] = []
    distributed_at: dict[int, datetime] = {}
    first_seen: dict[int, datetime] = {}
    returned: set[int] = set()

    for lid, evs in by_lesson.items():
        first_seen[lid] = evs[0]["created_at"]
        status: str | None = None
        since: datetime | None = None
        for e in evs:
            if e.get("event_type") == "returned_to_creator":
                returned.add(lid)
            after = e.get("status_after")
            if not after or after == status:
                continue
            if status is not None and since is not None and status not in FINAL_STATUSES:
                waits[status].append(_days(since, e["created_at"]))
            status, since = after, e["created_at"]
            if after == DISTRIBUTED_STATUS and lid not in distributed_at:
                distributed_at[lid] = e["created_at"]
                end_to_end.append(_days(evs[0]["created_at"], e["created_at"]))
        if status and since and status not in FINAL_STATUSES and current.get(lid) == status:
            open_age[status].append(_days(since, now))

    months = Counter(d.strftime("%Y-%m") for d in first_seen.values())
    last_90 = sum(1 for d in first_seen.values() if _days(d, now) <= 90)

    # ── after distribution: does anyone answer?
    answered = [i for i in implementations if i.get("is_relevant") is not None]
    days_to_answer = [
        d for i in answered
        if i.get("responded_at") and i["lesson_id"] in distributed_at
        and (d := _days(distributed_at[i["lesson_id"]], i["responded_at"])) >= 0
    ]
    unanswered = [i for i in implementations if i.get("is_relevant") is None]
    stale = sum(1 for i in unanswered
                if i["lesson_id"] in distributed_at and _days(distributed_at[i["lesson_id"]], now) > STALE_ANSWER_DAYS)
    targeted = {i["project_id"] for i in implementations}
    answering = {i["project_id"] for i in answered}
    lessons_sent = {i["lesson_id"] for i in implementations}
    lessons_answered = {i["lesson_id"] for i in answered}

    creators = Counter(l.get("created_by") for l in lessons if l.get("created_by"))
    top = creators.most_common(3)
    with_lessons = {l["project_id"] for l in lessons if l.get("project_id") is not None}

    return {
        "as_of": now.isoformat(timespec="minutes"),
        "lessons": {
            "total": len(lessons),
            "by_workflow_status": dict(Counter(l.get("workflow_status") or "—" for l in lessons).most_common()),
            "with_history": len(by_lesson),
            "returned_to_creator": len(returned),
        },
        "written_by_month": dict(sorted(months.items())[-12:]),
        "written_last_90_days": last_90,
        "wait_days_per_step": {k: _spread(v) for k, v in waits.items()},
        "open_now_days_at_step": {k: _spread(v) for k, v in open_age.items()},
        "created_to_distributed_days": _spread(end_to_end),
        "after_distribution": {
            "sent": len(implementations),
            "answered": len(answered),
            "answer_rate_pct": _pct(len(answered), len(implementations)),
            "relevant_implemented": sum(1 for i in answered if i.get("is_relevant") and i.get("is_implemented")),
            "relevant_not_implemented": sum(1 for i in answered if i.get("is_relevant") and not i.get("is_implemented")),
            "not_relevant": sum(1 for i in answered if i.get("is_relevant") is False),
            "days_to_answer": _spread(days_to_answer),
            f"unanswered_over_{STALE_ANSWER_DAYS}_days": stale,
            "projects_targeted": len(targeted),
            "projects_that_never_answered": len(targeted - answering),
            "lessons_sent": len(lessons_sent),
            "lessons_nobody_answered": len(lessons_sent - lessons_answered),
        },
        "who_writes": {
            "writers": len(creators),
            "top_3_writers_share_pct": _pct(sum(n for _, n in top), len(lessons)),
            "projects_in_module": len(projects),
            "projects_with_a_lesson": len(with_lessons),
        },
        "where": {
            "by_stage": dict(Counter(l.get("stage") or "—" for l in lessons).most_common(6)),
            "by_category": dict(Counter(l.get("category") or "—" for l in lessons).most_common(6)),
        },
    }


async def load_rows(session) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    async def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in (await session.execute(text(sql))).mappings().all()]

    return (
        await rows("SELECT id, workflow_status, status, stage, category, project_id, created_by "
                   "FROM lessons.lessons WHERE workflow_status <> 'טיוטה'"),
        await rows("SELECT id, lesson_id, event_type, status_after, created_at FROM lessons.lesson_workflow_events"),
        await rows("SELECT lesson_id, project_id, is_relevant, is_implemented, responded_at "
                   "FROM lessons.lesson_implementations"),
        await rows("SELECT id FROM lessons.projects"),
    )


async def snapshot(session, log: bool = True) -> dict[str, Any]:
    """Compute now; never raises (the caller is a page or a startup task)."""
    try:
        async with session.begin_nested():
            data = await load_rows(session)
        result = compute(*data, now=datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001
        logger.warning("lessons process metrics failed: %s", e)
        return {"ok": False}
    if log:
        print(LOG_PREFIX + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return {"ok": True, **result}
