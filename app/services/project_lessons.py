"""The lessons of one master-file project, for its project page.

Read-only over schema `lessons`. The bridge is ONLY the link an admin made
(`lessons.projects.shan_project_identifier`, lessons_project_link) — never a
name match. Two lists: lessons written ON the project, and lessons the module
distributed TO it (`lesson_implementations`) with its answer. Another user's
draft is never shown (the module's own rule: a draft is its creator's alone).
Best-effort: a missing schema or a failed query returns `{"ok": False}` and
never breaks the page.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

DRAFT_STATUS = "draft"          # lessons_ui/src/lib/lessonAssignment.ts DRAFT_STATUS
RISK_LABELS = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}   # lessons_ui constants riskLabelsShort
PRIORITY_LABELS = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}
LESSON_URL = "/lessons/lessons/{id}"
PROJECT_URL = "/lessons/projects/{id}"


def _visible(row: dict[str, Any]) -> bool:
    return row.get("status") != DRAFT_STATUS


def answer_label(is_relevant: bool | None, is_implemented: bool | None) -> str:
    """The project's answer to a distributed lesson, in words. Pure."""
    if is_relevant is False:
        return "לא רלוונטי"
    if is_implemented:
        return "יושם"
    if is_relevant:
        return "רלוונטי — טרם יושם"
    return "ממתין לתשובה"


def shape(projects: list[dict[str, Any]], written: list[dict[str, Any]],
          applied: list[dict[str, Any]]) -> dict[str, Any]:
    """The page's payload from raw rows. Pure — tested without a database."""
    def lesson(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r["id"], "title": r.get("title") or "", "stage": r.get("stage") or "",
            "category": r.get("category") or "", "risk": r.get("risk") or "",
            "risk_label": RISK_LABELS.get(r.get("risk") or "", r.get("risk") or ""),
            "workflow": r.get("workflow_status") or "", "date": r.get("event_date") or r.get("date") or "",
            "url": LESSON_URL.format(id=r["id"]),
        }
    seen: set[int] = set()
    out_written = []
    for r in written:
        if _visible(r) and r["id"] not in seen:
            seen.add(r["id"])
            out_written.append(lesson(r))
    out_applied = []
    for r in applied:
        if not _visible(r):
            continue
        row = lesson(r)
        row["answer"] = answer_label(r.get("is_relevant"), r.get("is_implemented"))
        row["priority"] = PRIORITY_LABELS.get(r.get("priority") or "", r.get("priority") or "")
        row["reason"] = r.get("reason") or ""
        out_applied.append(row)
    return {
        "ok": True, "linked": bool(projects),
        "projects": [{"id": p["id"], "name": p["name"], "url": PROJECT_URL.format(id=p["id"])} for p in projects],
        "written": out_written, "applied": out_applied,
    }


_COLS = "l.id, l.title, l.stage, l.category, l.risk, l.status, l.workflow_status, l.date, l.event_date"


async def for_project(session, identifier: str) -> dict[str, Any]:
    """Lessons of the master-file project `identifier`. Never raises."""
    try:
        async with session.begin_nested():   # a failed query must not poison the page's transaction
            projects = [dict(r) for r in (await session.execute(text(
                "SELECT id, name FROM lessons.projects WHERE shan_project_identifier = :i ORDER BY id"),
                {"i": identifier})).mappings().all()]
            ids = [p["id"] for p in projects]
            if not ids:
                return shape([], [], [])
            written = [dict(r) for r in (await session.execute(text(
                f"SELECT {_COLS} FROM lessons.lessons l WHERE l.project_id = ANY(:ids) "
                "ORDER BY l.updated_at DESC, l.id DESC"), {"ids": ids})).mappings().all()]
            applied = [dict(r) for r in (await session.execute(text(
                f"SELECT {_COLS}, i.is_relevant, i.is_implemented, i.priority, i.reason "
                "FROM lessons.lesson_implementations i JOIN lessons.lessons l ON l.id = i.lesson_id "
                "WHERE i.project_id = ANY(:ids) ORDER BY i.is_implemented NULLS FIRST, l.id DESC"),
                {"ids": ids})).mappings().all()]
        return shape(projects, written, applied)
    except Exception as e:  # noqa: BLE001 — the project page must render without the lessons module
        logger.warning("project lessons for %s failed: %s", identifier, e)
        return {"ok": False}


async def station_type(session, identifier: str) -> str | None:
    """The station type a PM chose for this project in the lessons module
    (`lessons.projects.station_type`, only where `station_type_set`), through
    the admin's link only. None when unlinked, unchosen, ambiguous (two linked
    lessons projects disagree) or the module is unreachable. Never raises."""
    try:
        async with session.begin_nested():
            kinds = {r[0] for r in (await session.execute(text(
                "SELECT station_type FROM lessons.projects "
                "WHERE shan_project_identifier = :i AND station_type_set"), {"i": identifier})).all()}
        return kinds.pop() if len(kinds) == 1 else None
    except Exception as e:  # noqa: BLE001 — the visual report must render without the lessons module
        logger.warning("station type for %s failed: %s", identifier, e)
        return None
