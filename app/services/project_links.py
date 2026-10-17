"""Optional link from a mission (חדר מבצעים) or a decision to a master-file
project — the ONLY place it is suggested, set and read back.

- `missions.project_id` / `decisions.project_id` (SET NULL, ALTERed at startup).
- `suggest` is rules, not a model: an identifier written in the text, then the
  project names scored by `lessons_project_link.weighted_score` (rare words —
  the place name — weigh more), with a bonus for projects whose מנה"פ is
  confirmed (manager_aliases) as the mission owner / decision submitter. A
  suggestion is never applied by itself: a person picks it (web or Telegram).
- `for_project` is what the project page shows. It never widens access:
  missions are the board every user already sees; decisions follow the
  decisions page (admin: all; others: submitted / distributed / RACI).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import exists, or_, select

from app.models import Decision, DecisionDistribution, DecisionRaciRole, ManagerAlias, Mission, Project, User
from app.services import lessons_project_link as lpl

logger = logging.getLogger(__name__)

SUGGESTIONS = 3
MIN_SCORE = 55          # a weaker name match is noise, not a suggestion
MANAGER_BONUS = 15      # ranking only — the shown score stays the name score
SEARCH_LIMIT = 8
_ID_TOKEN = re.compile(r"[A-Za-z]{2,}-?\d{2,}", re.I)


def _norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def suggest(text: str, projects: list[dict[str, Any]], manager_names: set[str] | None = None,
            limit: int = SUGGESTIONS) -> list[dict[str, Any]]:
    """Best projects for a free text, best first. Pure. Each row: id,
    identifier, name, score (0–100), reason ('id' | 'name')."""
    text = (text or "").strip()
    if not text or not projects:
        return []
    ids_in_text = {_norm_id(t) for t in _ID_TOKEN.findall(text)}
    idf = lpl.idf_weights([p["name"] for p in projects])
    managers = manager_names or set()
    scored = []
    for p in projects:
        ident = _norm_id(str(p["identifier"]))
        if len(ident) >= lpl.ID_MATCH_MIN_LEN and ident in ids_in_text:
            scored.append((1000, 100, "id", p))
            continue
        score = lpl.weighted_score(p["name"], text, idf) if p.get("name") else 0.0
        if score < MIN_SCORE:
            continue
        bonus = MANAGER_BONUS if p.get("manager") in managers else 0
        scored.append((score + bonus, round(score), "name", p))
    scored.sort(key=lambda r: (-r[0], str(r[3]["identifier"])))
    return [{"id": p["id"], "identifier": p["identifier"], "name": p["name"], "score": sc, "reason": why}
            for _, sc, why, p in scored[:limit]]


def search(q: str, projects: list[dict[str, Any]], limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Typed search (Telegram): identifier or a word of the name. Pure."""
    q = (q or "").strip()
    if not q:
        return []
    nq = _norm_id(q)
    exact = [p for p in projects if nq and _norm_id(str(p["identifier"])) == nq]
    if exact:
        return exact[:limit]
    words = lpl.norm_name(q).split()
    hits = [p for p in projects
            if (nq and nq in _norm_id(str(p["identifier"])))
            or (words and all(w in lpl.norm_name(p.get("name")) for w in words))]
    if hits:
        return sorted(hits, key=lambda p: str(p["identifier"]))[:limit]
    return suggest(q, projects, limit=limit)


def label(p: dict[str, Any] | Project | None) -> str:
    if p is None:
        return ""
    ident = p["identifier"] if isinstance(p, dict) else p.project_identifier
    name = p["name"] if isinstance(p, dict) else p.name
    return f"{ident} — {name}" if name else str(ident)


async def options(session) -> list[dict[str, Any]]:
    """Every ACTIVE project (inactive rows are phantoms of the old sync)."""
    rows = (await session.execute(
        select(Project.id, Project.project_identifier, Project.name, Project.manager)
        .where(Project.is_active).order_by(Project.project_identifier))).all()
    return [{"id": r[0], "identifier": r[1], "name": r[2] or "", "manager": r[3]} for r in rows]


async def manager_names_for(session, user_id: int | None) -> set[str]:
    """File מנה"פ names an admin confirmed as this user."""
    if not user_id:
        return set()
    return set((await session.execute(
        select(ManagerAlias.alias).where(ManagerAlias.user_id == user_id))).scalars().all())


async def suggest_for(session, text: str, user_id: int | None = None) -> list[dict[str, Any]]:
    return suggest(text, await options(session), await manager_names_for(session, user_id))


async def resolve(session, project_id: int | None) -> Project | None:
    """The project to link, or None (blank / unknown id → no link, never an error)."""
    if not project_id:
        return None
    return await session.get(Project, project_id)


async def get_label(session, project_id: int | None) -> str:
    return label(await resolve(session, project_id))


def mission_text(m: Mission) -> str:
    return " ".join(x for x in (m.title, m.description) if x)


def decision_text(d: Decision) -> str:
    return " ".join(x for x in (d.summary, d.problem_description, d.recommended_action) if x)


async def labels_for(session, project_ids: set[int]) -> dict[int, dict[str, Any]]:
    """id → {identifier, name, label} for chips on cards."""
    ids = {i for i in project_ids if i}
    if not ids:
        return {}
    rows = (await session.execute(
        select(Project.id, Project.project_identifier, Project.name).where(Project.id.in_(ids)))).all()
    return {r[0]: {"identifier": r[1], "name": r[2] or "",
                   "label": label({"identifier": r[1], "name": r[2]})} for r in rows}


async def for_project(session, identifier: str, user: User) -> dict[str, Any]:
    """Missions + decisions linked to the project, for its page. Never raises."""
    from app.services import decisions_menu_service as dms
    from app.services import missions_menu_service as oms
    try:
        async with session.begin_nested():
            pid = await session.scalar(select(Project.id).where(Project.project_identifier == identifier))
            if pid is None:
                return {"ok": True, "missions": [], "decisions": []}
            missions = (await session.execute(
                select(Mission, User.username).join(User, Mission.owner_id == User.id, isouter=True)
                .where(Mission.project_id == pid).order_by(Mission.created_at.desc()))).all()
            dq = (select(Decision, User.username).join(User, Decision.submitter_id == User.id, isouter=True)
                  .where(Decision.project_id == pid).order_by(Decision.created_at.desc()))
            if not user.is_admin:
                dq = dq.where(or_(
                    Decision.submitter_id == user.id,
                    exists(select(DecisionDistribution.id).where(
                        DecisionDistribution.decision_id == Decision.id, DecisionDistribution.user_id == user.id)),
                    exists(select(DecisionRaciRole.id).where(
                        DecisionRaciRole.decision_id == Decision.id, DecisionRaciRole.user_id == user.id)),
                ))
            decisions = (await session.execute(dq)).all()
        return {
            "ok": True,
            "missions": [{
                "id": m.id, "title": m.title, "owner": owner or "",
                "active": m.status in oms.ACTIVE_STATUSES,
                "status": "פתוחה" if m.status in oms.ACTIVE_STATUSES else ("בוטלה" if m.status == "cancelled" else "הושלמה"),
                "due": m.due_date.isoformat() if m.due_date else "",
                "overdue": oms.is_overdue(m),
            } for m, owner in missions],
            "decisions": [{
                "id": d.id, "summary": d.summary or "", "submitter": who or "",
                "type": f"{dms.TYPE_EMOJI.get(d.type, '')}" if d.type else "",
                "status": f"{dms.STATUS_EMOJI.get(d.status, '')} {dms.STATUS_LABEL.get(d.status, '')}".strip(),
                "date": d.created_at.date().isoformat() if d.created_at else "",
                "relevant": d.is_relevant,
            } for d, who in decisions],
        }
    except Exception as e:  # noqa: BLE001 — the project page renders without this card
        logger.warning("project links for %s failed: %s", identifier, e)
        return {"ok": False}
