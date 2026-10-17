"""מערכת לקחים ↔ קובץ המאסטר — project linking (PLAN-lessons-projects.md L1).

The ONLY place that decides how a master-file project becomes a lessons
project: which lessons stages a file stage is (`STAGE_MAP`), which project
type (`TYPE_MAP`), which risk (the pattern engine's handling level,
`RISK_BY_LEVEL`) and which manager (the confirmed `manager_aliases` chain —
never a name match). The pure functions are tested without a DB; the async
ones are the admin page's actions.

Owner decisions (2026-10-17): every ACTIVE project in the file, finished ones
included (inactive rows are phantoms of the old multi-sheet sync, never
projects); existing lessons projects are linked or merged by an admin from
rapidfuzz suggestions — a suggestion is never applied on its own; the file
owns name / manager / stage / type / risk, the module owns station type /
site / equipment. Nothing outside schema `lessons` is written; public tables
are only read.
"""
from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from sqlalchemy import text

from app.services import stage_sectors as ss

# Master-file stage → lessons stage indexes (lessons_ui PROJECT_STAGES:
# 0 הקפאת תכולה · 1 הקפאת תצורה · 2 תכנון · 3 קבלת היתר · 4 בחירת קבלן ·
# 5 עבודות אזרחיות · 6 הרכבות חשמליות · 7 בדיקות · 8 טופס 4 · 9 הסתיים ·
# 10 חישמול — the file never writes 10). User decisions LP-D5 / LP-D8.
STAGE_MAP: dict[str, tuple[int, ...]] = {
    "הקפאת תכולה": (0,),
    "הקפאת תצורה": (1,),
    "תכנון": (2,),
    "קבלת היתר": (3,),
    "בחירת קבלן": (4,),
    "לקראת ביצוע": (4,),
    "עבודה אזרחית": (5,),
    "הרכבה חשמלית": (6,),
    "הרכבה חשמלית ובדיקות": (6, 7),
    "בדיקות": (7,),
    "עבודה אזרחית והרכבות": (5, 6),
    "הסכם- אגירת אנרגיה": (5, 6),
    "טופס 4": (8,),
    "הסתיים": (9,),
}

# Master-file project type → lessons project_type (LP-D7). Compared without
# quotes/gershayim/spaces, so שוש / שו"ש / שו״ש are one type.
TYPE_MAP: dict[str, str] = {
    "הקמה": "new_build",
    "הרחבה": "expansion",
    "שוש": "maintenance",
    "ניידות": "mobility",
}
DEFAULT_TYPE = "new_build"

# insight_triage level → lessons risk (LP-D6). A project the engine does not
# rate (finished) is low.
RISK_BY_LEVEL: dict[str, str] = {"escalate": "high", "week": "high", "follow": "medium", "fyi": "low"}
DEFAULT_RISK = "low"

# The manager of a project whose file name is not linked to a lessons profile
# yet (LP-D9). Never a login; listed in lessons_import.NO_LOGIN_NAMES.
PLACEHOLDER_PM_ID = "pm_unassigned"
PLACEHOLDER_PM_NAME = "טרם שויך"

SUGGESTIONS = 3
LOCAL = "local"   # lessons.projects.shan_link_decision: an admin kept it module-only

_QUOTES = re.compile(r"[\"'״׳`\s]")


def stage_indexes_for(stage: object) -> list[int] | None:
    """Lessons stage indexes for a file stage; None for an empty or unseen one."""
    hit = STAGE_MAP.get(ss.normalize_stage(stage))
    return list(hit) if hit else None


def project_type_for(value: object) -> str | None:
    """Lessons project_type for the file's type; None when the file's value is unknown."""
    key = _QUOTES.sub("", value) if isinstance(value, str) else ""
    return TYPE_MAP.get(key)


def risk_for(level: str | None) -> str:
    return RISK_BY_LEVEL.get(level or "", DEFAULT_RISK)


# A suggestion is marked "recommended" (and pre-selected — still one click to
# link) only on a signal stronger than a similar name: the file identifier
# written in the lessons name, or the same manager (and the only suggestion
# with that manager) on a similar enough name, or a near-identical name well
# ahead of the next one. Thresholds set on the real screen (2026-10-17):
# 'אוניברסיטה' scored 40 against its own project.
MANAGER_BONUS = 20         # ranking only; the shown score stays the name score
REC_MANAGER_MIN_SCORE = 35
REC_NAME_MIN_SCORE = 85
REC_NAME_MARGIN = 15
REC_LEAD_MIN_SCORE = 60     # …or a fair score far ahead of the rest
REC_LEAD_MARGIN = 20
ID_MATCH_MIN_LEN = 5

_TOKEN = re.compile(r"[\w-]+")


def _words(s: str | None) -> frozenset[str]:
    return frozenset(w for w in re.split(r"[\s,]+", (s or "").strip()) if len(w) > 1)


def same_manager(a: str | None, b: str | None) -> bool:
    """'משה כהן' and 'כהן משה' are one person; one shared word is not enough
    for a two-word name (a common first name), it is for a one-word name."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) >= min(2, len(wa), len(wb))


_ABBREV = re.compile(r"[\"'״׳]")


def norm_name(s: str | None) -> str:
    """A name as the matcher compares it: gershayim dropped BEFORE punctuation
    becomes space (תחמ"ש → תחמש, not "תחמ ש"), and double yod/vav collapsed
    (קריית = קרית, 2026-10-17: the file and the module spell places both ways)."""
    t = _ABBREV.sub("", s or "")
    t = t.replace("יי", "י").replace("וו", "ו")
    return default_process(t)


# Hebrew one-letter prefixes (ה/ב/ל/ו/מ/כ/ש): "הגליל" is "גליל", "בתחמש" is "תחמש".
_PREFIXES = "הבלומכש"
TOKEN_FUZZ = 88            # one typo in a word of 8+ letters still counts as the word
IDF_SHARE = 0.75           # the rest of the score is plain token_set_ratio


def _variants(tok: str) -> set[str]:
    out = {tok}
    if len(tok) >= 4 and tok[0] in _PREFIXES:
        out.add(tok[1:])
    return out


def _tokens(name: str | None) -> list[str]:
    return [t for t in norm_name(name).split() if len(t) >= 2]


def idf_weights(names: list[str | None]) -> dict[str, float]:
    """How rare each word is across the file's project names. 'הוספת שנאי'
    is in dozens of names; '<the place>' in one — the place name is what tells
    projects apart (2026-10-17: equal weights let 'הוספת שנאי רביעי' in five
    other projects bury '<the place>')."""
    import math
    df: dict[str, int] = {}
    for n in names:
        for v in {v for t in _tokens(n) for v in _variants(t)}:
            df[v] = df.get(v, 0) + 1
    total = len(names)
    return {t: math.log((total + 1) / (c + 1)) + 1 for t, c in df.items()} | {"": math.log(total + 1) + 1}


def _token_hit(tok: str, cand: set[str]) -> bool:
    vs = _variants(tok)
    if vs & cand:
        return True
    return len(tok) >= 8 and any(fuzz.ratio(v, c) >= TOKEN_FUZZ for v in vs for c in cand if abs(len(c) - len(v)) <= 2)


def weighted_score(name: str | None, cand_name: str | None, idf: dict[str, float]) -> float:
    """Share of the lessons name's weight (rare words weigh more) found in the
    file name, blended with token_set_ratio. 0–100."""
    q = _tokens(name)
    if not q:
        return 0.0
    cand = {v for t in _tokens(cand_name) for v in _variants(t)}
    default = idf[""]

    def w(t: str) -> float:
        return max(idf.get(v, default) for v in _variants(t))

    total = sum(w(t) for t in q)
    hit = sum(w(t) for t in q if _token_hit(t, cand))
    tsr = fuzz.token_set_ratio(norm_name(name), norm_name(cand_name))
    return IDF_SHARE * (100 * hit / total) + (1 - IDF_SHARE) * tsr


def suggest(name: str, candidates: list[dict[str, Any]], limit: int = SUGGESTIONS,
            manager: str | None = None) -> list[dict[str, Any]]:
    """The file projects most likely to be this lessons project, best first.
    A hint for the admin only — nothing is linked from it. Each carries
    `score` (name similarity, rare words weighted up), `id_match`, `manager_match`; at most the first
    carries `recommended`."""
    q = norm_name(name)
    if not q:
        return []
    tokens = {t.lower() for t in _TOKEN.findall(name or "")}
    idf = idf_weights([c["name"] for c in candidates])
    scored = []
    for c in candidates:
        score = weighted_score(name, c["name"], idf)
        ident = str(c["identifier"]).lower()
        # Short identifiers would hit numbers in the name ("161" kV); only a real code counts.
        id_match = len(ident) >= ID_MATCH_MIN_LEN and ident in tokens
        mgr = same_manager(manager, c.get("manager"))
        if score <= 0 and not id_match:
            continue
        rank = (id_match, score + (MANAGER_BONUS if mgr else 0))
        scored.append((rank, round(score), id_match, mgr, c))
    scored.sort(key=lambda r: (not r[0][0], -r[0][1], r[4]["identifier"]))
    out = [{**c, "score": 100 if idm else sc, "id_match": idm, "manager_match": mgr, "recommended": False}
           for _, sc, idm, mgr, c in scored[:limit]]
    if out:
        top, second = out[0], (out[1]["score"] if len(out) > 1 else 0)
        # A file project already linked to ANOTHER lessons project is shown (so a
        # duplicate is visible — 2026-10-17) but is never the recommendation:
        # the answer there is a merge, not a link.
        top["recommended"] = not top.get("linked_to") and (top["id_match"]
                              or (top["manager_match"] and top["score"] >= REC_MANAGER_MIN_SCORE
                                  and not any(o["manager_match"] for o in out[1:]))
                              or (top["score"] >= REC_NAME_MIN_SCORE and top["score"] - second >= REC_NAME_MARGIN)
                              or (top["score"] >= REC_LEAD_MIN_SCORE and top["score"] - second >= REC_LEAD_MARGIN))
    return out


def manager_profile_for(manager: str | None, alias_users: dict[str, int | None],
                        profile_of_user: dict[int, dict[str, Any]]) -> tuple[str | None, str | None]:
    """(lessons profile id, problem) for the file's מנה"פ name.

    The chain is the confirmed one only: name → manager_aliases.user_id →
    the user's lessons profile. Problems: `no_name` (empty cell),
    `alias_unlinked` (an admin has not confirmed who the name is),
    `no_profile` (the user has no module profile), `not_pm` (the profile is a
    viewer/referent — it would own a project it cannot see).
    """
    name = (manager or "").strip()
    if not name:
        return None, "no_name"
    uid = alias_users.get(name)
    if uid is None:
        return None, "alias_unlinked"
    prof = profile_of_user.get(uid)
    if prof is None:
        return None, "no_profile"
    if prof.get("role") not in ("project_manager", "admin"):
        return None, "not_pm"
    return str(prof["id"]), None


MANAGER_PROBLEMS = ("alias_unlinked", "no_profile", "not_pm")


def manager_check(file_manager: str | None, resolved: str | None, problem: str | None,
                  current_id: str | None, kept: str | None) -> str:
    """Does a linked project's lessons manager agree with the file? Pure.

    `match` · `differs` (the file's name resolves to another profile) ·
    `unresolved` (the file's name is not a confirmed user with a PM profile) ·
    `kept` (an admin chose to keep the lessons manager against THIS file name —
    asked again once the file names someone else) · `no_name` (empty cell).
    """
    name = (file_manager or "").strip()
    if not name:
        return "no_name"
    if resolved is not None and resolved == current_id:
        return "match"
    if kept is not None and kept.strip() == name:
        return "kept"
    return "differs" if resolved is not None else "unresolved"


def fields_for(master: dict[str, Any], level: str | None, manager_pid: str | None) -> dict[str, Any] | None:
    """The file-owned fields of the lessons project for one master project, or
    None when the stage is unknown (a project is never created on a guessed stage)."""
    stages = stage_indexes_for(master.get("stage"))
    if stages is None:
        return None
    return {
        "name": (master.get("name") or master["identifier"]).strip(),
        "stage_indexes": stages,
        "project_type": project_type_for(master.get("project_type")) or DEFAULT_TYPE,
        "risk": risk_for(level),
        "manager_id": manager_pid or PLACEHOLDER_PM_ID,
        "shan_active": True,
    }


def link_problem(project: dict[str, Any] | None, identifier: str, master_ids: set[str],
                 taken_by: str | None) -> str | None:
    """Why a lessons project may not be linked to a file project, or None. Pure."""
    if project is None:
        return "פרויקט הלקחים לא קיים"
    if identifier not in master_ids:
        return "הפרויקט לא נמצא בקובץ המאסטר (רק פרויקטים פעילים)"
    if taken_by is not None:
        return f"הפרויקט בקובץ כבר מקושר ל'{taken_by}'"
    if project.get("shan_project_identifier") not in (None, identifier):
        return "פרויקט הלקחים כבר מקושר לפרויקט אחר — נתק אותו קודם"
    return None


def merge_problem(source: dict[str, Any] | None, target: dict[str, Any] | None) -> str | None:
    """Why `source` may not be merged into `target` (source is deleted), or None. Pure."""
    if source is None or target is None:
        return "פרויקט לא קיים"
    if source["id"] == target["id"]:
        return "אי אפשר למזג פרויקט לתוך עצמו"
    s, t = source.get("shan_project_identifier"), target.get("shan_project_identifier")
    if s and t and s != t:
        return "שני הפרויקטים מקושרים לפרויקטים שונים בקובץ — זה לא אותו פרויקט"
    return None


def undecided(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lessons projects an admin has not linked or kept local yet."""
    return [p for p in projects if not p.get("shan_project_identifier") and p.get("shan_link_decision") != LOCAL]


def creation_plan(masters: list[dict[str, Any]], linked_ids: set[str],
                  levels: dict[str, str], alias_users: dict[str, int | None],
                  profile_of_user: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """What "create the missing" would do — the preview and the action read it. Pure."""
    create, unknown_stage, managers = [], [], {}
    for m in masters:
        if m["identifier"] in linked_ids:
            continue
        pid, problem = manager_profile_for(m.get("manager"), alias_users, profile_of_user)
        f = fields_for(m, levels.get(m["identifier"]), pid)
        if f is None:
            unknown_stage.append({"identifier": m["identifier"], "name": m.get("name"), "stage": m.get("stage")})
            continue
        create.append({"identifier": m["identifier"], **f})
        if problem and problem != "no_name":
            name = (m.get("manager") or "").strip()
            managers.setdefault(name, {"name": name, "problem": problem, "projects": 0})["projects"] += 1
    return {
        "create": create,
        "unknown_stage": unknown_stage,
        "managers_unlinked": sorted(managers.values(), key=lambda r: -r["projects"]),
        "no_manager": sum(1 for c in create if c["manager_id"] == PLACEHOLDER_PM_ID),
        "type_unknown": sorted({str(m.get("project_type")) for m in masters
                                if m["identifier"] not in linked_ids and m.get("project_type")
                                and project_type_for(m.get("project_type")) is None}),
    }


# ----------------------------------------------------------------- DB reads

async def _masters(session) -> list[dict[str, Any]]:
    return [dict(r) for r in (await session.execute(text(
        "SELECT project_identifier AS identifier, name, manager, stage, project_type "
        "FROM public.projects WHERE is_active ORDER BY name"))).mappings().all()]


async def _lessons_projects(session) -> list[dict[str, Any]]:
    return [dict(r) for r in (await session.execute(text(
        "SELECT p.id, p.name, p.site, p.lessons_count, p.manager_id, m.name AS manager_name, "
        "m.shan_user_id AS manager_user_id, p.shan_project_identifier, p.shan_link_decision, p.shan_manager_kept "
        "FROM lessons.projects p LEFT JOIN lessons.profiles m ON m.id = p.manager_id ORDER BY p.name"
    ))).mappings().all()]


async def _manager_chain(session) -> tuple[dict[str, int | None], dict[int, dict[str, Any]]]:
    from app.services import lessons_access as la
    alias_users = {(a or "").strip(): u for a, u in (await session.execute(text(
        "SELECT alias, user_id FROM public.manager_aliases"))).all()}
    by_user: dict[int, list[dict[str, Any]]] = {}
    for r in (await session.execute(text(
            "SELECT id, role, shan_user_id, is_login FROM lessons.profiles WHERE shan_user_id IS NOT NULL"
    ))).mappings().all():
        by_user.setdefault(r["shan_user_id"], []).append(dict(r))
    profile_of_user = {}
    for uid, rows in by_user.items():
        prof = la.pick_profile(rows)
        if prof is not None:
            profile_of_user[uid] = prof
    return alias_users, profile_of_user


async def _levels(session) -> dict[str, str]:
    """identifier → handling level, from the same engine output the dashboard uses."""
    from app.services import insight_triage, pattern_service
    try:
        async with session.begin_nested():   # a failed engine query must not poison the page's transaction
            p = await pattern_service.compute_cached(session)
    except Exception:  # noqa: BLE001 — no engine output = every risk is low, never a failed page
        return {}
    return {k: v["level"] for k, v in insight_triage.triage_all(p).items()}


async def payload(session) -> dict[str, Any]:
    masters = await _masters(session)
    projects = await _lessons_projects(session)
    by_ident = {m["identifier"]: m for m in masters}
    linked = {p["shan_project_identifier"]: p for p in projects if p["shan_project_identifier"]}
    names_by_id = {p["id"]: p["name"] for p in projects}
    # Suggestions run over EVERY active file project; one linked elsewhere carries
    # who holds it, and the panel offers a merge instead of a link.
    pool = [{**m, "linked_to": linked[m["identifier"]]["id"] if m["identifier"] in linked else None,
             "linked_name": names_by_id.get(linked[m["identifier"]]["id"]) if m["identifier"] in linked else None}
            for m in masters]
    alias_users, profile_of_user = await _manager_chain(session)
    plan = creation_plan(masters, set(linked), await _levels(session), alias_users, profile_of_user)
    profile_names = {r[0]: r[1] for r in (await session.execute(text(
        "SELECT id, name FROM lessons.profiles"))).all()}
    users = [{"id": r[0], "username": (r[1] or "").strip()} for r in (await session.execute(text(
        "SELECT id, username FROM public.users ORDER BY username"))).all()]
    rows = []
    for p in projects:
        ident = p["shan_project_identifier"]
        mcheck = None
        if ident and ident in by_ident:
            fm = by_ident[ident].get("manager")
            pid, problem = manager_profile_for(fm, alias_users, profile_of_user)
            mcheck = {
                "status": manager_check(fm, pid, problem, p["manager_id"], p["shan_manager_kept"]),
                "file_manager": (fm or "").strip() or None,
                "problem": problem,
                "file_profile": {"id": pid, "name": profile_names.get(pid)} if pid else None,
                "current": {"id": p["manager_id"], "name": p["manager_name"]},
                # The likeliest person behind the file's name: the alias if one is
                # confirmed, else the lessons manager's own user. A hint only.
                "suggest_user_id": alias_users.get((fm or "").strip()) or p["manager_user_id"],
            }
        rows.append({
            "manager_check": mcheck,
            "id": p["id"], "name": p["name"], "site": p["site"], "lessons_count": p["lessons_count"],
            "manager_name": p["manager_name"], "identifier": ident,
            "linked": by_ident.get(ident) if ident else None,
            "linked_missing": bool(ident and ident not in by_ident),
            "decision": p["shan_link_decision"],
            "suggestions": [] if ident else suggest(p["name"], pool, manager=p["manager_name"]),
        })
    open_rows = undecided(projects)
    return {
        "projects": rows,
        "masters": pool,
        "summary": {
            "lessons_total": len(projects),
            "linked": len(linked),
            "local": sum(1 for p in projects if p["shan_link_decision"] == LOCAL and not p["shan_project_identifier"]),
            "undecided": len(open_rows),
            "master_total": len(masters),
            "to_create": len(plan["create"]),
            "unknown_stage": plan["unknown_stage"],
            "managers_unlinked": plan["managers_unlinked"],
            "manager_open": sum(1 for r in rows if r["manager_check"]
                                and r["manager_check"]["status"] in ("differs", "unresolved")),
            "no_manager": plan["no_manager"],
            "type_unknown": plan["type_unknown"],
        },
        "can_create": not open_rows and bool(plan["create"]),
        "users": users,
    }


SEARCH_LIMIT = 30


def search_reason(row: dict[str, Any]) -> str:
    """Why a file project is or is not offered for linking. Pure."""
    if not row.get("is_active"):
        return "inactive"
    if row.get("linked_to") is not None:
        return "linked"
    return "free"


async def search_file(session, q: str) -> list[dict[str, Any]]:
    """Every file project — active AND inactive — whose name, identifier or
    manager matches `q` (normalized like the suggestions), each with the reason
    it is or is not offered. The answer to "why isn't X in the list?"."""
    nq = norm_name(q)
    if len(nq) < 2:
        return []
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT m.project_identifier AS identifier, m.name, m.manager, m.stage, m.is_active, "
        "m.last_updated, p.id AS linked_to, p.name AS linked_name "
        "FROM public.projects m LEFT JOIN lessons.projects p ON p.shan_project_identifier = m.project_identifier"
    ))).mappings().all()]
    words = nq.split()
    out = []
    for r in rows:
        hay = " ".join([norm_name(r["name"]), norm_name(r["identifier"]), norm_name(r["manager"])])
        if all(w in hay for w in words):
            score = 100
        else:
            score = fuzz.token_set_ratio(nq, norm_name(r["name"]))
            if score < 70:
                continue
        out.append({**r, "score": round(score), "reason": search_reason(r),
                    "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None})
    out.sort(key=lambda r: (-r["score"], r["reason"] != "free", r["name"] or ""))
    return out[:SEARCH_LIMIT]


# ----------------------------------------------------------------- actions

async def _log(session, admin, action: str, entity_id: Any, label: str, description: str) -> None:
    await session.execute(text(
        "INSERT INTO lessons.activity_logs (user_id, user_name, user_email, user_role, action_type, "
        "entity_type, entity_id, object_label, description) "
        "VALUES (:uid, :name, :email, 'admin', :action, 'project', :eid, :label, :desc)"), {
        "uid": f"s{admin.id}" if admin else "system",
        "name": (admin.username or "").strip() if admin else SYNC_ACTOR,
        "email": (getattr(admin, "email", None) or "") if admin else "",
        "action": action, "eid": str(entity_id), "label": label or "", "desc": description})


async def _project(session, pid: int) -> dict[str, Any] | None:
    row = (await session.execute(text(
        "SELECT id, name, site, equipment_ids, shan_project_identifier, shan_link_decision "
        "FROM lessons.projects WHERE id = :p"), {"p": pid})).mappings().first()
    return dict(row) if row else None


async def link(session, admin, project_id: int, identifier: str | None) -> str | None:
    """Link (identifier) or unlink (None) one lessons project. Returns a problem or None."""
    project = await _project(session, project_id)
    if identifier is None:
        if project is None:
            return "פרויקט הלקחים לא קיים"
        await session.execute(text(
            "UPDATE lessons.projects SET shan_project_identifier = NULL, shan_active = NULL, "
            "shan_link_decision = NULL WHERE id = :p"), {"p": project_id})
        await _log(session, admin, "project_unlinked", project_id, project["name"],
                   f"נותק מקובץ המאסטר ({project['shan_project_identifier'] or '—'})")
        return None
    masters = {m["identifier"]: m for m in await _masters(session)}
    taken = (await session.execute(text(
        "SELECT name FROM lessons.projects WHERE shan_project_identifier = :i AND id <> :p"),
        {"i": identifier, "p": project_id})).scalar()
    if (problem := link_problem(project, identifier, set(masters), taken)) is not None:
        return problem
    await session.execute(text(
        "UPDATE lessons.projects SET shan_project_identifier = :i, shan_active = true, "
        "shan_link_decision = NULL WHERE id = :p"), {"i": identifier, "p": project_id})
    await _log(session, admin, "project_linked", project_id, project["name"],
               f"קושר לפרויקט {identifier} בקובץ המאסטר ({masters[identifier].get('name') or ''})")
    return None


async def keep_local(session, admin, project_id: int, local: bool) -> str | None:
    project = await _project(session, project_id)
    if project is None:
        return "פרויקט הלקחים לא קיים"
    if local and project["shan_project_identifier"]:
        return "הפרויקט מקושר — נתק אותו קודם"
    await session.execute(text(
        "UPDATE lessons.projects SET shan_link_decision = :d WHERE id = :p"),
        {"d": LOCAL if local else None, "p": project_id})
    await _log(session, admin, "project_kept_local" if local else "project_local_undone", project_id,
               project["name"], "נשאר פרויקט מקומי (לא בקובץ המאסטר)" if local else "בוטלה ההחלטה להשאיר מקומי")
    return None


async def merge(session, admin, source_id: int, target_id: int) -> str | None:
    """Move everything of `source` onto `target`, then delete `source`.

    Lessons and implementations move (an implementation of the same lesson on
    both keeps the target's row, taking the source's answer only when the
    target has none); profiles' assigned_projects follow; equipment is
    united; the target keeps its own fields. One transaction (the caller's).
    """
    source, target = await _project(session, source_id), await _project(session, target_id)
    if (problem := merge_problem(source, target)) is not None:
        return problem
    p = {"s": source_id, "t": target_id}
    before = (await session.execute(text(
        "SELECT (SELECT count(*) FROM lessons.lessons WHERE project_id IN (:s, :t)), "
        "(SELECT count(DISTINCT lesson_id) FROM lessons.lesson_implementations WHERE project_id IN (:s, :t))"), p)).one()
    await session.execute(text("UPDATE lessons.lessons SET project_id = :t WHERE project_id = :s"), p)
    await session.execute(text(
        "UPDATE lessons.lesson_implementations t SET is_relevant = s.is_relevant, is_implemented = s.is_implemented, "
        "notes = s.notes, responded_by = s.responded_by, responded_at = s.responded_at "
        "FROM lessons.lesson_implementations s "
        "WHERE t.project_id = :t AND s.project_id = :s AND s.lesson_id = t.lesson_id "
        "AND t.responded_at IS NULL AND s.responded_at IS NOT NULL"), p)
    await session.execute(text(
        "DELETE FROM lessons.lesson_implementations s USING lessons.lesson_implementations t "
        "WHERE s.project_id = :s AND t.project_id = :t AND t.lesson_id = s.lesson_id"), p)
    await session.execute(text(
        "UPDATE lessons.lesson_implementations SET project_id = :t WHERE project_id = :s"), p)
    await session.execute(text(
        "UPDATE lessons.profiles SET assigned_projects = ARRAY(SELECT DISTINCT x FROM unnest("
        "array_replace(assigned_projects, CAST(:s AS integer), CAST(:t AS integer))) AS x ORDER BY x) "
        "WHERE CAST(:s AS integer) = ANY(assigned_projects)"), p)
    # The source's link moves to the target; it is cleared on the source first,
    # or the unique identifier index sees it twice for a moment.
    await session.execute(text(
        "UPDATE lessons.projects SET shan_project_identifier = NULL WHERE id = :s"), p)
    await session.execute(text(
        "UPDATE lessons.projects t SET equipment_ids = ARRAY(SELECT DISTINCT x FROM unnest("
        "t.equipment_ids || s.equipment_ids) AS x ORDER BY x), site = COALESCE(t.site, s.site), "
        "shan_project_identifier = COALESCE(t.shan_project_identifier, CAST(:si AS varchar)), "
        "shan_active = COALESCE(t.shan_active, s.shan_active), shan_link_decision = NULL "
        "FROM lessons.projects s WHERE t.id = :t AND s.id = :s"),
        {**p, "si": source["shan_project_identifier"]})
    await session.execute(text("DELETE FROM lessons.projects WHERE id = :s"), p)
    after = (await session.execute(text(
        "SELECT (SELECT count(*) FROM lessons.lessons WHERE project_id = :t), "
        "(SELECT count(DISTINCT lesson_id) FROM lessons.lesson_implementations WHERE project_id = :t)"), p)).one()
    if tuple(after) != tuple(before):
        raise MergeAbort(f"אי-התאמה בספירות אחרי מיזוג (לפני {tuple(before)}, אחרי {tuple(after)})")
    await _log(session, admin, "project_merged", target_id, target["name"],
               f"מוזג לתוכו '{source['name']}' (#{source_id}): {before[0]} לקחים, {before[1]} המלצות יישום")
    return None


async def apply_manager(session, project_id: int, profile_id: str) -> None:
    """The lessons manager of one project, by the edit dialog's rule
    (UserContext.updateUser): ONE manager per project — the new one holds it
    in assigned_projects, every other PM (and the placeholder) loses it."""
    p = {"p": project_id, "m": profile_id}
    await session.execute(text(
        "UPDATE lessons.projects SET manager_id = :m, shan_manager_kept = NULL WHERE id = :p"), p)
    await session.execute(text(
        "UPDATE lessons.profiles SET assigned_projects = array_append(assigned_projects, CAST(:p AS integer)) "
        "WHERE id = :m AND NOT (CAST(:p AS integer) = ANY(assigned_projects))"), p)
    await session.execute(text(
        "UPDATE lessons.profiles SET assigned_projects = array_remove(assigned_projects, CAST(:p AS integer)) "
        "WHERE role = 'project_manager' AND id <> :m AND CAST(:p AS integer) = ANY(assigned_projects)"), p)


async def ensure_pm_profile(session, user_id: int) -> tuple[str | None, str | None]:
    """(lessons profile id, problem) for a Shan-AI user picked as a project's
    manager: their profile as is when PM/admin; a viewer (or someone never set
    up) becomes a PM — picking them as the manager says so; a referent group's
    login account is refused."""
    from app.services import lessons_access as la
    user = (await session.execute(text(
        "SELECT id, username, email FROM public.users WHERE id = :u"), {"u": user_id})).mappings().first()
    if user is None:
        return None, "משתמש Shan-AI לא קיים"
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT id, role, shan_user_id, is_login FROM lessons.profiles WHERE shan_user_id = :u"),
        {"u": user_id})).mappings().all()]
    prof = la.pick_profile(rows)
    if prof is not None and prof["role"] == "referent":
        return None, "זה חשבון כניסה של קבוצת רפרנט — לא מנהל פרויקט"
    if prof is None:
        row = la.viewer_profile_row(user_id, user["username"], user["email"])
        await session.execute(text(
            "INSERT INTO lessons.profiles (id, name, email, role, shan_user_id) "
            "VALUES (:id, :name, :email, :role, :shan_user_id) ON CONFLICT (id) DO NOTHING"), row)
        await session.execute(text(
            "UPDATE lessons.profiles SET shan_user_id = :u, is_login = true WHERE id = :id"),
            {"u": user_id, "id": row["id"]})
        prof = {"id": row["id"], "role": "viewer"}
    if prof["role"] not in ("project_manager", "admin"):
        await session.execute(text(
            "UPDATE lessons.profiles SET role = 'project_manager', defined_at = now() WHERE id = :id"),
            {"id": prof["id"]})
    return str(prof["id"]), None


async def set_manager(session, admin, project_id: int, mode: str, user_id: int | None = None) -> str | None:
    """The admin's answer to a linked project's manager check.

    `file`: apply the profile the file's name resolves to. `user`: the file's
    name IS this Shan-AI user — confirm that in manager_aliases (the same link
    the patterns page uses, keyed on the name exactly as the file spells it),
    make sure they are a PM in the module, apply. `keep`: keep the lessons
    manager against this file name (asked again if the file names someone else).
    """
    row = (await session.execute(text(
        "SELECT p.id, p.name, p.manager_id, p.shan_project_identifier, m.manager AS file_manager "
        "FROM lessons.projects p JOIN public.projects m ON m.project_identifier = p.shan_project_identifier "
        "WHERE p.id = :p AND m.is_active"), {"p": project_id})).mappings().first()
    if row is None:
        return "הפרויקט לא מקושר לפרויקט פעיל בקובץ"
    raw = row["file_manager"] or ""
    name = raw.strip()
    if mode == "keep":
        if not name:
            return "אין מנה״פ בקובץ"
        await session.execute(text(
            "UPDATE lessons.projects SET shan_manager_kept = :n WHERE id = :p"), {"n": name, "p": project_id})
        await _log(session, admin, "project_manager_kept", project_id, row["name"],
                   f"מנה״פ בלקחים נשאר, למרות שבקובץ: {name}")
        return None
    if mode == "file":
        alias_users, profile_of_user = await _manager_chain(session)
        pid, problem = manager_profile_for(raw, alias_users, profile_of_user)
        if pid is None:
            return "השם בקובץ עדיין לא מקושר למנהל פרויקט — בחר משתמש"
    elif mode == "user":
        if user_id is None:
            return "לא נבחר משתמש"
        pid, problem = await ensure_pm_profile(session, user_id)
        if pid is None:
            return problem
        if name:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from app.models import ManagerAlias
            await session.execute(pg_insert(ManagerAlias).values(alias=raw, user_id=user_id)
                                  .on_conflict_do_update(index_elements=["alias"], set_={"user_id": user_id}))
    else:
        return "פעולה לא מוכרת"
    await apply_manager(session, project_id, pid)
    await _log(session, admin, "project_manager_set", project_id, row["name"],
               f"מנה״פ הוגדר לפי הקובץ ({name or '—'}) → פרופיל {pid}")
    return None


class MergeAbort(Exception):
    """Counts did not add up — the caller rolls the whole merge back."""


async def create_missing(session, admin) -> tuple[int, str | None]:
    """Create a lessons project for every active file project nobody links to.
    Refused while any existing lessons project is undecided (it might BE one of them)."""
    data = await payload(session)
    if data["summary"]["undecided"]:
        return 0, f"{data['summary']['undecided']} פרויקטי לקחים עוד לא קושרו או סומנו כמקומיים"
    alias_users, profile_of_user = await _manager_chain(session)
    masters = await _masters(session)
    linked = {r[0] for r in (await session.execute(text(
        "SELECT shan_project_identifier FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"))).all()}
    plan = creation_plan(masters, linked, await _levels(session), alias_users, profile_of_user)
    if not plan["create"]:
        return 0, "אין פרויקטים חדשים ליצירה"
    await _insert_created(session, plan)
    await _log(session, admin, "projects_created_from_master", "", "קובץ המאסטר",
               f"נוצרו {len(plan['create'])} פרויקטים מקובץ המאסטר "
               f"({plan['no_manager']} עם מנה\"פ '{PLACEHOLDER_PM_NAME}')")
    return len(plan["create"]), None


async def _insert_created(session, plan: dict[str, Any]) -> None:
    """Insert creation_plan's projects (admin's "create" and the upload sync)."""
    if plan["no_manager"]:
        await session.execute(text(
            "INSERT INTO lessons.profiles (id, name, email, role, is_login) "
            "VALUES (:id, :name, '', 'project_manager', false) ON CONFLICT (id) DO NOTHING"),
            {"id": PLACEHOLDER_PM_ID, "name": PLACEHOLDER_PM_NAME})
    for c in plan["create"]:
        new_id = (await session.execute(text(
            "INSERT INTO lessons.projects (name, stage_indexes, project_type, risk, manager_id, station_type, "
            "station_type_set, shan_project_identifier, shan_active, shan_synced_at) "
            "VALUES (:name, :stage_indexes, :project_type, :risk, :manager_id, 'closed', false, "
            ":identifier, true, now()) RETURNING id"), c)).scalar_one()
        # One manager per project (UserContext.updateUser) + an admin holds every project.
        await session.execute(text(
            "UPDATE lessons.profiles SET assigned_projects = array_append(assigned_projects, CAST(:p AS integer)) "
            "WHERE (id = :m OR role = 'admin') AND NOT (CAST(:p AS integer) = ANY(assigned_projects))"),
            {"p": new_id, "m": c["manager_id"]})


# ----------------------------------------------------------------- L3: every live upload

SYNC_ACTOR = "סנכרון קובץ המאסטר"
FILE_FIELDS = ("name", "stage_indexes", "project_type", "risk")


def sync_plan(linked: list[dict[str, Any]], masters: dict[str, dict[str, Any]], levels: dict[str, str],
              alias_users: dict[str, int | None], profile_of_user: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """What a live upload changes on each LINKED lessons project. Pure.

    The file owns name / stage / type / risk (LP-D3/D5/D6). An unknown stage or
    type leaves the module's value. The manager moves only to a profile the
    confirmed chain resolves to, and never against a name an admin chose to
    keep (LP-D12); an unresolved name changes nothing (the manager check asks).
    A project gone from the active file is marked shan_active=false — never
    deleted, its lessons stay.
    """
    out = []
    for p in linked:
        m = masters.get(p["shan_project_identifier"])
        if m is None:
            if p.get("shan_active") is not False:
                out.append({"id": p["id"], "fields": {}, "manager": None, "active": False})
            continue
        pid, _ = manager_profile_for(m.get("manager"), alias_users, profile_of_user)
        f = fields_for(m, levels.get(m["identifier"]), pid) or {
            "name": (m.get("name") or m["identifier"]).strip(), "risk": risk_for(levels.get(m["identifier"]))}
        if project_type_for(m.get("project_type")) is None:
            f.pop("project_type", None)
        changed = {k: f[k] for k in FILE_FIELDS if k in f and f[k] != p.get(k)}
        status = manager_check(m.get("manager"), pid, None, p.get("manager_id"), p.get("shan_manager_kept"))
        manager = pid if status == "differs" else None
        if changed or manager or p.get("shan_active") is not True:
            out.append({"id": p["id"], "fields": changed, "manager": manager, "active": True})
    return out


async def sync_from_file(session) -> dict[str, int]:
    """Bring the lessons module up to date with the file just synced LIVE
    (never a history replay — the caller checks). Updates linked projects,
    marks the vanished inactive, and creates the file's new projects — the
    last only once the initial linking is done (no undecided lessons project),
    or a project not linked yet would be created twice."""
    masters = {m["identifier"]: m for m in await _masters(session)}
    linked = [dict(r) for r in (await session.execute(text(
        "SELECT id, name, stage_indexes, project_type, risk, manager_id, shan_project_identifier, "
        "shan_manager_kept, shan_active FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"
    ))).mappings().all()]
    for p in linked:
        p["stage_indexes"] = list(p["stage_indexes"] or [])
    alias_users, profile_of_user = await _manager_chain(session)
    levels = await _levels(session)
    counts = {"updated": 0, "managers": 0, "inactive": 0, "created": 0}
    for ch in sync_plan(linked, masters, levels, alias_users, profile_of_user):
        sets = ", ".join(f"{k} = :{k}" for k in ch["fields"])
        await session.execute(text(
            f"UPDATE lessons.projects SET {sets + ', ' if sets else ''}shan_active = :active, "
            "shan_synced_at = now() WHERE id = :id"), {**ch["fields"], "active": ch["active"], "id": ch["id"]})
        if ch["manager"]:
            await apply_manager(session, ch["id"], ch["manager"])
            counts["managers"] += 1
        counts["updated" if ch["active"] else "inactive"] += 1
    undecided_n = (await session.execute(text(
        "SELECT count(*) FROM lessons.projects WHERE shan_project_identifier IS NULL "
        "AND shan_link_decision IS DISTINCT FROM :l"), {"l": LOCAL})).scalar_one()
    if undecided_n == 0:
        plan = creation_plan(list(masters.values()), {p["shan_project_identifier"] for p in linked},
                             levels, alias_users, profile_of_user)
        if plan["create"]:
            await _insert_created(session, plan)
            counts["created"] = len(plan["create"])
    if any(counts.values()):
        await _log(session, None, "projects_synced_from_master", "", "קובץ המאסטר",
                   f"עודכנו {counts['updated']} פרויקטים (מנה\"פ: {counts['managers']}), "
                   f"{counts['inactive']} יצאו מהקובץ, נוצרו {counts['created']}"
                   + ("" if undecided_n == 0 else f" — יצירה מושהית: {undecided_n} פרויקטי לקחים עוד לא קושרו"))
    return {**counts, "undecided": undecided_n}


async def sync_after_upload() -> None:
    """The upload paths' hook: own session, own transaction, never raises —
    a lessons failure must not fail the master upload."""
    import logging

    from app.database import async_session_maker
    log = logging.getLogger(__name__)
    try:
        async with async_session_maker() as session:
            counts = await sync_from_file(session)
            await session.commit()
        log.info("lessons project sync: %s", counts)
    except Exception:
        log.exception("lessons project sync failed (the master upload itself is fine)")


# ----------------------------------------------------------------- L2: every file manager name at once

UNASSIGNED_NAMES = frozenset({"טרם הוקצאה"})   # PLAN.md §4: a reserved name, never a person
HINT_MIN = 60


def manager_hint(name: str, users: list[dict[str, Any]]) -> int | None:
    """The Shan-AI user a file name most likely is — a HINT, never applied
    without an admin's click. The same word set (the file writes 'כהן, משה')
    wins; else the closest username at ≥ HINT_MIN."""
    for u in users:
        if same_manager(name, u["username"]):
            return u["id"]
    best = max(((fuzz.token_sort_ratio(norm_name(name), norm_name(u["username"])), u["id"]) for u in users),
               default=(0, None))
    return best[1] if best[0] >= HINT_MIN else None


def manager_rows(counts: dict[str, int], alias_users: dict[str, int | None],
                 profile_of_user: dict[int, dict[str, Any]], users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per מנה"פ name in the active file: who it is (if confirmed), the
    problem if any, and a hint. Problems first, then by project count. Pure."""
    names = {u["id"]: u["username"] for u in users}
    rows = []
    for name, n in counts.items():
        if not name.strip() or name.strip() in UNASSIGNED_NAMES:
            continue
        pid, problem = manager_profile_for(name, alias_users, profile_of_user)
        uid = alias_users.get(name.strip())
        rows.append({
            "name": name, "projects": n, "user_id": uid, "username": names.get(uid) if uid else None,
            "profile_id": pid, "problem": problem,
            "hint_user_id": None if uid else manager_hint(name, users),
        })
    rows.sort(key=lambda r: (r["problem"] is None, -r["projects"], r["name"]))
    return rows


async def managers_payload(session) -> dict[str, Any]:
    counts = {r[0]: r[1] for r in (await session.execute(text(
        "SELECT manager, count(*) FROM public.projects WHERE is_active AND manager IS NOT NULL GROUP BY manager"
    ))).all()}
    alias_users, profile_of_user = await _manager_chain(session)
    users = [{"id": r[0], "username": (r[1] or "").strip()} for r in (await session.execute(text(
        "SELECT id, username FROM public.users ORDER BY username"))).all()]
    rows = manager_rows(counts, alias_users, profile_of_user, users)
    return {"rows": rows, "users": users, "open": sum(1 for r in rows if r["problem"])}


async def confirm_alias(session, admin, alias: str, user_id: int) -> str | None:
    """An admin confirms that the file's name IS this Shan-AI user: the alias
    (the same link the patterns page uses, keyed on the name as the file
    spells it) and a PM profile in the module. Projects follow at the next
    sync — or at once with sync_now."""
    exists = (await session.execute(text(
        "SELECT 1 FROM public.projects WHERE manager = :a AND is_active LIMIT 1"), {"a": alias})).first()
    if exists is None:
        return "השם לא נמצא בקובץ"
    pid, problem = await ensure_pm_profile(session, user_id)
    if pid is None:
        return problem
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import ManagerAlias
    await session.execute(pg_insert(ManagerAlias).values(alias=alias, user_id=user_id)
                          .on_conflict_do_update(index_elements=["alias"], set_={"user_id": user_id}))
    await _log(session, admin, "manager_alias_confirmed", "", alias.strip(),
               f"השם בקובץ '{alias.strip()}' = משתמש {user_id} (פרופיל {pid})")
    return None
