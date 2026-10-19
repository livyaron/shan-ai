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

import json
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

# File project types that are NOT lessons-module projects at all (user decision
# 2026-10-19: "את כל הניידות תוריד מהפקת לקחים"). Never created, never offered
# for linking; the ones already created are removed by `remove_excluded` when
# they hold no lessons — one that does is kept and reported, never deleted.
EXCLUDED_TYPES = frozenset({"mobility"})


def excluded(master: dict[str, Any]) -> bool:
    return project_type_for(master.get("project_type")) in EXCLUDED_TYPES

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


def missing_fields(master: dict[str, Any], override: dict[str, Any] | None = None) -> list[str]:
    """The required fields neither the file nor an admin's completion gives
    (user decision 2026-10-19: no unfounded defaults — the project waits). Pure."""
    o = override or {}
    out = []
    if stage_indexes_for(master.get("stage")) is None and not o.get("stage_indexes"):
        out.append("stage")
    if project_type_for(master.get("project_type")) is None and not o.get("project_type"):
        out.append("project_type")
    return out


def fields_for(master: dict[str, Any], level: str | None, manager_pid: str | None,
               override: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The file-owned fields of the lessons project for one master project, or
    None while a required field is missing (stage or type the file does not
    give and no admin completed — never a guess)."""
    if missing_fields(master, override):
        return None
    o = override or {}
    return {
        "name": (master.get("name") or master["identifier"]).strip(),
        "stage_indexes": stage_indexes_for(master.get("stage")) or list(o["stage_indexes"]),
        "project_type": project_type_for(master.get("project_type")) or o["project_type"],
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


# A file project that MAY be one of the lessons projects nobody decided on yet
# waits for that decision instead of being created next to it (a duplicate).
# Everything else is created at once (user, 2026-10-19: "still missing lots of
# projects" — one undecided lessons project used to hold back ALL creation).
POSSIBLE_DUP_SCORE = 60


def possible_duplicates(undecided_projects: list[dict[str, Any]], masters: list[dict[str, Any]]) -> dict[str, str]:
    """file identifier → name of the undecided lessons project it may be. Pure.
    The same signal the tab shows as a suggestion: a recommendation, the
    identifier in the name, or a name similar enough."""
    out: dict[str, str] = {}
    for p in undecided_projects:
        for sg in suggest(p["name"], masters, manager=p.get("manager_name")):
            if sg["recommended"] or sg["id_match"] or sg["score"] >= POSSIBLE_DUP_SCORE:
                out.setdefault(sg["identifier"], p["name"])
    return out


def undecided(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lessons projects an admin has not linked or kept local yet."""
    return [p for p in projects if not p.get("shan_project_identifier") and p.get("shan_link_decision") != LOCAL]


def creation_plan(masters: list[dict[str, Any]], linked_ids: set[str],
                  levels: dict[str, str], alias_users: dict[str, int | None],
                  profile_of_user: dict[int, dict[str, Any]],
                  overrides: dict[str, dict[str, Any]] | None = None,
                  maybe_dup: dict[str, str] | None = None) -> dict[str, Any]:
    """What "create the missing" would do — the preview and the action read it. Pure.

    `held`: file projects that wait for an admin's completion (stage or type
    the file does not give) — listed with exactly the fields missing.
    `waiting_duplicate`: file projects that may be an undecided lessons project
    (`possible_duplicates`) — created once that project is linked or kept local."""
    overrides = overrides or {}
    maybe_dup = maybe_dup or {}
    create, held, waiting, managers = [], [], [], {}
    for m in masters:
        if m["identifier"] in linked_ids or excluded(m):
            continue
        if m["identifier"] in maybe_dup:
            waiting.append({"identifier": m["identifier"], "name": m.get("name"),
                            "candidate": maybe_dup[m["identifier"]]})
            continue
        ov = overrides.get(m["identifier"])
        pid, problem = manager_profile_for(m.get("manager"), alias_users, profile_of_user)
        f = fields_for(m, levels.get(m["identifier"]), pid, ov)
        if f is None:
            held.append({"identifier": m["identifier"], "name": m.get("name"), "manager": m.get("manager"),
                         "stage": m.get("stage"), "project_type": m.get("project_type"),
                         "missing": missing_fields(m, ov)})
            continue
        create.append({"identifier": m["identifier"], **f})
        if problem and problem != "no_name":
            name = (m.get("manager") or "").strip()
            managers.setdefault(name, {"name": name, "problem": problem, "projects": 0})["projects"] += 1
    return {
        "create": create,
        "held": held,
        "waiting_duplicate": waiting,
        "managers_unlinked": sorted(managers.values(), key=lambda r: -r["projects"]),
        "no_manager": sum(1 for c in create if c["manager_id"] == PLACEHOLDER_PM_ID),
    }


# What still needs the admin on one lessons project (user decision 2026-10-19).
# A project with none of these is "handled" and leaves the work list; it comes
# back only when a new problem appears. Station type / site / equipment are NOT
# here — the PM completes them in "השלמת נתונים" (decision א, 2026-10-19).
ISSUES = ("undecided", "manager_differs", "manager_unresolved", "manager_placeholder", "sync_error")


def link_issues(row: dict[str, Any]) -> list[str]:
    """The open problems of one lessons project row (as `payload` builds it). Pure.
    The ONLY definition of "ממתין לטיפול" — the list, the counters, the
    all-clear message and the Excel export all read it."""
    out = []
    if not row.get("identifier") and row.get("decision") != LOCAL:
        out.append("undecided")
    mc = row.get("manager_check") or {}
    if mc.get("status") == "differs":
        out.append("manager_differs")
    elif mc.get("status") == "unresolved":
        out.append("manager_unresolved")
    elif row.get("identifier") and row.get("manager_id") == PLACEHOLDER_PM_ID:
        out.append("manager_placeholder")
    if row.get("sync_error"):
        out.append("sync_error")
    return out


# ----------------------------------------------------------------- DB reads

async def _masters(session, include_excluded: bool = False) -> list[dict[str, Any]]:
    """Active file projects that are lessons projects: without an excluded type
    (ניידות) and without the ones an admin took out (`dismiss`)."""
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT project_identifier AS identifier, name, manager, stage, project_type "
        "FROM public.projects WHERE is_active ORDER BY name"))).mappings().all()]
    if include_excluded:
        return rows
    return drop_excluded(rows, await _dismissed_ids(session))


def drop_excluded(rows: list[dict[str, Any]], dismissed: set[str]) -> list[dict[str, Any]]:
    """The file projects the lessons module takes. Pure."""
    return [m for m in rows if not excluded(m) and m["identifier"] not in dismissed]


async def _dismissed_ids(session) -> set[str]:
    return {r[0] for r in (await session.execute(text(
        "SELECT identifier FROM lessons.master_overrides WHERE excluded"))).all()}


def dismiss_problem(master: dict[str, Any] | None, linked_to: str | None) -> str | None:
    """Why a file project may not be taken out of the lessons module, or None. Pure.
    Only a file project with no lessons project behind it: a linked one holds
    lessons and is handled in its own row (unlink / merge), never dropped here."""
    if master is None:
        return "הפרויקט לא נמצא בקובץ המאסטר (רק פרויקטים פעילים)"
    if linked_to:
        return f"הפרויקט כבר קיים במערכת הלקחים ('{linked_to}') — אי אפשר להוציא אותו מכאן"
    return None


async def dismiss(session, admin, identifier: str, out: bool = True) -> str | None:
    """Take a file project out of the lessons module (it is never created,
    never suggested, never asked about) — or bring it back. Reversible; the
    master file is never touched."""
    master = next((m for m in await _masters(session, include_excluded=True) if m["identifier"] == identifier), None)
    linked = (await session.execute(text(
        "SELECT name FROM lessons.projects WHERE shan_project_identifier = :i"), {"i": identifier})).scalar()
    if out and (problem := dismiss_problem(master, linked)) is not None:
        return problem
    if master is None:
        return "הפרויקט לא נמצא בקובץ המאסטר"
    await session.execute(text(
        "INSERT INTO lessons.master_overrides (identifier, excluded, set_by, set_at) VALUES (:i, :x, :by, now()) "
        "ON CONFLICT (identifier) DO UPDATE SET excluded = EXCLUDED.excluded, set_by = EXCLUDED.set_by, set_at = now()"),
        {"i": identifier, "x": out, "by": (admin.username or "").strip() if admin else SYNC_ACTOR})
    name = (master.get("name") or identifier).strip()
    await _log(session, admin, "master_dismissed" if out else "master_restored", "", name,
               f"{'הוצא ממערכת הלקחים' if out else 'הוחזר למערכת הלקחים'} ({identifier}) — קובץ המאסטר לא שונה")
    if not out and await _levels(session) is not None:
        await create_missing(session, admin, {identifier})
    return None


async def _dismissed(session) -> list[dict[str, Any]]:
    return [{"identifier": r["identifier"], "name": r["name"], "set_by": r["set_by"],
             "set_at": r["set_at"].isoformat() if r["set_at"] else None}
            for r in (await session.execute(text(
                "SELECT o.identifier, m.name, o.set_by, o.set_at FROM lessons.master_overrides o "
                "LEFT JOIN public.projects m ON m.project_identifier = o.identifier "
                "WHERE o.excluded ORDER BY m.name"))).mappings().all()]


async def _lessons_projects(session) -> list[dict[str, Any]]:
    return [dict(r) for r in (await session.execute(text(
        "SELECT p.id, p.name, p.site, p.lessons_count, p.manager_id, m.name AS manager_name, "
        "m.shan_user_id AS manager_user_id, p.shan_project_identifier, p.shan_link_decision, p.shan_manager_kept, "
        "p.shan_sync_error, p.shan_sync_error_at "
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


async def _levels(session) -> dict[str, str] | None:
    """identifier → handling level, from the same engine output the dashboard uses.
    None when the engine failed: then no risk is written and nothing is created
    (2026-10-19 — an empty answer used to reset every linked project to "low")."""
    from app.services import insight_triage, pattern_service
    try:
        async with session.begin_nested():   # a failed engine query must not poison the page's transaction
            p = await pattern_service.compute_cached(session)
    except Exception:  # noqa: BLE001 — never a failed page; the caller holds risk and creation
        return None
    return {k: v["level"] for k, v in insight_triage.triage_all(p).items()}


async def _overrides(session) -> dict[str, dict[str, Any]]:
    """An admin's completions of what the file does not give (stage / type)."""
    return {r["identifier"]: {"stage_indexes": list(r["stage_indexes"]) if r["stage_indexes"] else None,
                              "project_type": r["project_type"]}
            for r in (await session.execute(text(
                "SELECT identifier, stage_indexes, project_type FROM lessons.master_overrides"))).mappings().all()}


async def _dup_holds(session, masters: list[dict[str, Any]]) -> dict[str, str]:
    linked = {r[0] for r in (await session.execute(text(
        "SELECT shan_project_identifier FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"))).all()}
    open_rows = undecided(await _lessons_projects(session))
    return possible_duplicates(open_rows, [m for m in masters if m["identifier"] not in linked])


async def _last_sync(session) -> dict[str, Any] | None:
    row = (await session.execute(text(
        "SELECT at, actor, report FROM lessons.sync_runs ORDER BY id DESC LIMIT 1"))).mappings().first()
    if row is None:
        return None
    return {"at": row["at"].isoformat() if row["at"] else None, "actor": row["actor"], **(row["report"] or {})}


def _manager_check_row(p: dict[str, Any], master: dict[str, Any] | None, alias_users: dict[str, int | None],
                       profile_of_user: dict[int, dict[str, Any]], profile_names: dict[str, str]) -> dict[str, Any] | None:
    if master is None:
        return None
    fm = master.get("manager")
    pid, problem = manager_profile_for(fm, alias_users, profile_of_user)
    return {
        "status": manager_check(fm, pid, problem, p["manager_id"], p["shan_manager_kept"]),
        "file_manager": (fm or "").strip() or None,
        "problem": problem,
        "file_profile": {"id": pid, "name": profile_names.get(pid)} if pid else None,
        "current": {"id": p["manager_id"], "name": p["manager_name"]},
        # The likeliest person behind the file's name: the alias if one is
        # confirmed, else the lessons manager's own user. A hint only.
        "suggest_user_id": alias_users.get((fm or "").strip()) or p["manager_user_id"],
    }


STAGE_OPTIONS = list(STAGE_MAP)                       # an admin completes a stage in the file's own words
# What an admin may complete a type to — an excluded type is not a lessons project.
TYPE_LABELS = {"new_build": "הקמה", "expansion": "הרחבה", "maintenance": "שו״ש"}


def all_clear(summary: dict[str, Any]) -> bool:
    """"כל הפרויקטים סונכרנו וקושרו בהצלחה" — only when nothing at all is open. Pure."""
    return (summary["pending"] == 0 and not summary["held"] and summary["managers_open"] == 0
            and summary["to_create"] == 0 and summary["engine_ok"])


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
    levels = await _levels(session)
    free = [m for m in masters if m["identifier"] not in linked]
    plan = creation_plan(masters, set(linked), levels or {}, alias_users, profile_of_user, await _overrides(session),
                         possible_duplicates(undecided(projects), free))
    profile_names = {r[0]: r[1] for r in (await session.execute(text(
        "SELECT id, name FROM lessons.profiles"))).all()}
    users = [{"id": r[0], "username": (r[1] or "").strip()} for r in (await session.execute(text(
        "SELECT id, username FROM public.users ORDER BY username"))).all()]
    rows = []
    for p in projects:
        ident = p["shan_project_identifier"]
        row = {
            "manager_check": _manager_check_row(p, by_ident.get(ident) if ident else None,
                                                alias_users, profile_of_user, profile_names),
            "id": p["id"], "name": p["name"], "site": p["site"], "lessons_count": p["lessons_count"],
            "manager_name": p["manager_name"], "manager_id": p["manager_id"], "identifier": ident,
            "linked": by_ident.get(ident) if ident else None,
            "linked_missing": bool(ident and ident not in by_ident),
            "decision": p["shan_link_decision"],
            "sync_error": p["shan_sync_error"],
            "sync_error_at": p["shan_sync_error_at"].isoformat() if p["shan_sync_error_at"] else None,
            "suggestions": [] if ident else suggest(p["name"], pool, manager=p["manager_name"]),
        }
        row["issues"] = link_issues(row)
        rows.append(row)
    open_rows = undecided(projects)
    counts = {}
    for m in masters:
        if (m.get("manager") or "").strip():
            counts[m["manager"]] = counts.get(m["manager"], 0) + 1
    managers_open = sum(1 for r in manager_rows(counts, alias_users, profile_of_user, users) if r["problem"])
    summary = {
        "lessons_total": len(projects),
        "linked": len(linked),
        "local": sum(1 for p in projects if p["shan_link_decision"] == LOCAL and not p["shan_project_identifier"]),
        "undecided": len(open_rows),
        "master_total": len(masters),
        "to_create": len(plan["create"]) if levels is not None else 0,
        "held": plan["held"],
        "waiting_duplicate": plan["waiting_duplicate"],
        "dismissed": await _dismissed(session),
        "managers_unlinked": plan["managers_unlinked"],
        "managers_open": managers_open,
        "no_manager": plan["no_manager"],
        "pending": sum(1 for r in rows if r["issues"]),
        "handled": sum(1 for r in rows if not r["issues"]),
        "engine_ok": levels is not None,
    }
    summary["all_clear"] = all_clear(summary)
    return {
        "projects": rows,
        "masters": pool,
        "summary": summary,
        "can_create": bool(plan["create"]) and levels is not None,
        "users": users,
        "stage_options": STAGE_OPTIONS,
        "type_options": [{"value": k, "label": v} for k, v in TYPE_LABELS.items()],
        "last_sync": await _last_sync(session),
    }


SEARCH_LIMIT = 30


def search_reason(row: dict[str, Any]) -> str:
    """Why a file project is or is not offered for linking. Pure."""
    if not row.get("is_active"):
        return "inactive"
    if excluded(row) or row.get("dismissed"):
        return "excluded"
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
        "SELECT m.project_identifier AS identifier, m.name, m.manager, m.stage, m.project_type, m.is_active, "
        "m.last_updated, p.id AS linked_to, p.name AS linked_name, COALESCE(o.excluded, false) AS dismissed "
        "FROM public.projects m LEFT JOIN lessons.projects p ON p.shan_project_identifier = m.project_identifier "
        "LEFT JOIN lessons.master_overrides o ON o.identifier = m.project_identifier"
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


async def create_missing(session, admin, identifiers: set[str] | None = None) -> tuple[int, str | None]:
    """Create a lessons project for every active file project nobody links to
    (or only `identifiers`). A file project that may be an undecided lessons
    project waits for that decision (`possible_duplicates`); refused while the
    engine is down (no risk to give)."""
    levels = await _levels(session)
    if levels is None:
        return 0, "מנוע הדפוסים לא זמין כרגע — אין רמת סיכון לתת. נסה שוב בעוד כמה דקות"
    alias_users, profile_of_user = await _manager_chain(session)
    masters = await _masters(session)
    if identifiers is not None:
        masters = [m for m in masters if m["identifier"] in identifiers]
    linked = {r[0] for r in (await session.execute(text(
        "SELECT shan_project_identifier FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"))).all()}
    plan = creation_plan(masters, linked, levels, alias_users, profile_of_user, await _overrides(session),
                         await _dup_holds(session, masters))
    if not plan["create"]:
        return 0, None if identifiers is not None else "אין פרויקטים חדשים ליצירה"
    created, failed = await _insert_created(session, plan, admin)
    if failed and not created:
        return 0, f"היצירה נכשלה: {failed[0]['error']}"
    return len(created), None


def _short_error(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:300]


async def _insert_created(session, plan: dict[str, Any], actor=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Insert creation_plan's projects (admin's "create" and the upload sync),
    each in its own savepoint: one bad row is reported, never the whole batch.
    Returns (created, failed)."""
    if plan["no_manager"]:
        await session.execute(text(
            "INSERT INTO lessons.profiles (id, name, email, role, is_login) "
            "VALUES (:id, :name, '', 'project_manager', false) ON CONFLICT (id) DO NOTHING"),
            {"id": PLACEHOLDER_PM_ID, "name": PLACEHOLDER_PM_NAME})
    created, failed = [], []
    for c in plan["create"]:
        try:
            async with session.begin_nested():
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
                await _log(session, actor, "project_created_from_master", new_id, c["name"],
                           f"נוצר מקובץ המאסטר ({c['identifier']})"
                           + (f" — מנה\"פ '{PLACEHOLDER_PM_NAME}'" if c["manager_id"] == PLACEHOLDER_PM_ID else ""))
            created.append({"identifier": c["identifier"], "name": c["name"]})
        except Exception as e:  # noqa: BLE001 — reported per project, the rest still go in
            failed.append({"identifier": c["identifier"], "name": c["name"], "error": _short_error(e)})
    return created, failed


async def complete(session, admin, identifier: str, stage: str | None = None,
                   project_type: str | None = None, create: bool = True) -> str | None:
    """An admin completes what the file does not give for one file project
    (stage in the file's own words, or a project type). Only a field that is
    actually missing may be completed — a value the file gives is never
    overridden. The project is created at once when nothing else blocks it."""
    master = next((m for m in await _masters(session) if m["identifier"] == identifier), None)
    if master is None:
        return "הפרויקט לא נמצא בקובץ המאסטר (רק פרויקטים פעילים)"
    if (await session.execute(text(
            "SELECT 1 FROM lessons.projects WHERE shan_project_identifier = :i"), {"i": identifier})).first():
        return "הפרויקט כבר קיים במערכת הלקחים"
    missing = missing_fields(master)
    stages = None
    if stage:
        if "stage" not in missing:
            return "השלב כבר קיים בקובץ — אין מה להשלים"
        stages = stage_indexes_for(stage)
        if stages is None:
            return "שלב לא מוכר"
    if project_type:
        if "project_type" not in missing:
            return "סוג הפרויקט כבר קיים בקובץ — אין מה להשלים"
        if project_type not in TYPE_LABELS:
            return "סוג פרויקט לא מוכר"
    if not stages and not project_type:
        return "לא נבחר ערך"
    await session.execute(text(
        "INSERT INTO lessons.master_overrides (identifier, stage_indexes, project_type, set_by, set_at) "
        "VALUES (:i, :s, :t, :by, now()) ON CONFLICT (identifier) DO UPDATE SET "
        "stage_indexes = COALESCE(EXCLUDED.stage_indexes, lessons.master_overrides.stage_indexes), "
        "project_type = COALESCE(EXCLUDED.project_type, lessons.master_overrides.project_type), "
        "set_by = EXCLUDED.set_by, set_at = now()"),
        {"i": identifier, "s": stages, "t": project_type, "by": (admin.username or "").strip() if admin else SYNC_ACTOR})
    await _log(session, admin, "master_completed", "", (master.get("name") or identifier).strip(),
               f"הושלם ({identifier}): " + ", ".join(
                   x for x in (f"שלב {stage}" if stages else "", f"סוג {TYPE_LABELS.get(project_type or '', '')}"
                                                                   if project_type else "") if x))
    # Creation may still wait (another missing field, an undecided lessons
    # project, the engine) — the completion itself is saved either way, and the
    # next sync creates it.
    if create and await _levels(session) is not None:
        _, problem = await create_missing(session, admin, {identifier})
        return problem
    return None


# ----------------------------------------------------------------- L3: every live upload

SYNC_ACTOR = "סנכרון קובץ המאסטר"
FILE_FIELDS = ("name", "stage_indexes", "project_type", "risk")


def sync_plan(linked: list[dict[str, Any]], masters: dict[str, dict[str, Any]], levels: dict[str, str] | None,
              alias_users: dict[str, int | None], profile_of_user: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """What a live upload changes on each LINKED lessons project. Pure.

    The file owns name / stage / type / risk (LP-D3/D5/D6). An unknown stage or
    type leaves the module's value; `levels=None` (the engine failed) leaves
    every risk. The manager is filled automatically ONLY on a project still
    held by the "טרם שויך" placeholder (nobody's assignment is changed); a
    project whose real manager differs from the file waits for the admin's
    approval in the work list (user decision 2026-10-19). A project gone from
    the active file is marked shan_active=false — never deleted, its lessons stay.
    """
    out = []
    for p in linked:
        m = masters.get(p["shan_project_identifier"])
        if m is None:
            if p.get("shan_active") is not False:
                out.append({"id": p["id"], "fields": {}, "manager": None, "active": False})
            continue
        f: dict[str, Any] = {"name": (m.get("name") or m["identifier"]).strip()}
        if (stages := stage_indexes_for(m.get("stage"))) is not None:
            f["stage_indexes"] = stages
        if (ptype := project_type_for(m.get("project_type"))) is not None:
            f["project_type"] = ptype
        if levels is not None:
            f["risk"] = risk_for(levels.get(m["identifier"]))
        changed = {k: f[k] for k in FILE_FIELDS if k in f and f[k] != p.get(k)}
        pid, _ = manager_profile_for(m.get("manager"), alias_users, profile_of_user)
        manager = pid if pid is not None and p.get("manager_id") == PLACEHOLDER_PM_ID else None
        if changed or manager or p.get("shan_active") is not True:
            out.append({"id": p["id"], "fields": changed, "manager": manager, "active": True})
    return out


FIELD_LABELS = {"name": "שם", "stage_indexes": "שלב", "project_type": "סוג", "risk": "סיכון"}


def change_note(fields: dict[str, Any], manager: str | None, active: bool) -> str:
    """One activity-log line for one project's sync changes. Pure."""
    parts = [f"{FIELD_LABELS[k]}: {v}" for k, v in fields.items()]
    if manager:
        parts.append(f"מנה\"פ: {manager} (במקום '{PLACEHOLDER_PM_NAME}')")
    if not active:
        parts.append("יצא מקובץ המאסטר (סומן לא פעיל, לא נמחק)")
    return "סנכרון מקובץ המאסטר — " + ("; ".join(parts) if parts else "חזר לקובץ")


def sync_report(created: list[dict[str, Any]], failed: list[dict[str, Any]], rows: list[dict[str, Any]],
                held: list[dict[str, Any]], counts: dict[str, int], waiting: list[dict[str, Any]], engine_ok: bool,
                excluded_result: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """The five categories the admin asked for (2026-10-19), from one sync. Pure.
    `rows` are the linked projects after the sync, each with its `issues`."""
    linked_active = [r for r in rows if r.get("identifier") and not r.get("linked_missing")]
    return {
        "created": created,
        "linked_ok": sum(1 for r in linked_active if not r["issues"]),
        "awaiting_data": [{"identifier": h["identifier"], "name": h.get("name"), "missing": h["missing"]} for h in held],
        "awaiting_manager": sum(1 for r in linked_active
                                if {"manager_differs", "manager_unresolved", "manager_placeholder"} & set(r["issues"])),
        "failed": failed,
        "updated": counts["updated"],
        "managers_filled": counts["managers"],
        "inactive": counts["inactive"],
        "waiting_duplicate": waiting,
        "excluded_removed": (excluded_result or {}).get("removed", []),
        "excluded_kept": (excluded_result or {}).get("kept", []),
        "engine_ok": engine_ok,
    }


async def remove_excluded(session, actor=None) -> dict[str, list[dict[str, Any]]]:
    """Take the excluded types (ניידות, user 2026-10-19) out of the lessons
    module: a project with no lessons and no distributed lessons is deleted
    (its own savepoint, one log line); one that holds lessons is KEPT and
    reported — lessons are never lost. Idempotent; runs at every sync."""
    idents = [m["identifier"] for m in await _masters(session, include_excluded=True) if excluded(m)]
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT p.id, p.name, p.shan_project_identifier AS identifier, "
        "(SELECT count(*) FROM lessons.lessons l WHERE l.project_id = p.id) AS n_lessons, "
        "(SELECT count(*) FROM lessons.lesson_implementations i WHERE i.project_id = p.id) AS n_impl "
        "FROM lessons.projects p WHERE p.project_type = ANY(:types) OR p.shan_project_identifier = ANY(:ids)"),
        {"types": sorted(EXCLUDED_TYPES), "ids": idents})).mappings().all()]
    removed, kept = [], []
    for r in rows:
        item = {"id": r["id"], "name": r["name"], "identifier": r["identifier"]}
        if r["n_lessons"] or r["n_impl"]:
            kept.append({**item, "lessons": r["n_lessons"], "implementations": r["n_impl"]})
            continue
        async with session.begin_nested():
            await session.execute(text(
                "UPDATE lessons.profiles SET assigned_projects = array_remove(assigned_projects, CAST(:p AS integer)) "
                "WHERE CAST(:p AS integer) = ANY(assigned_projects)"), {"p": r["id"]})
            await session.execute(text("DELETE FROM lessons.projects WHERE id = :p"), {"p": r["id"]})
            await _log(session, actor, "project_removed_excluded_type", r["id"], r["name"],
                       f"הוסר ממערכת הלקחים — פרויקט ניידות ({r['identifier'] or 'לא מקושר'}), ללא לקחים")
        removed.append(item)
    return {"removed": removed, "kept": kept}


async def sync_from_file(session, actor=None) -> dict[str, Any]:
    """Bring the lessons module up to date with the file just synced LIVE
    (never a history replay — the caller checks). Updates linked projects,
    marks the vanished inactive, removes the excluded types (ניידות) that hold
    no lessons, and creates the file's new projects — except one that may be an
    undecided lessons project (it waits for that decision) — only with the
    engine up (no risk to give otherwise).

    Every project is its own savepoint: a failure is written on that project
    (`shan_sync_error`) and in the report; the rest of the sync goes on. Every
    change is one activity-log line; the run's report is kept in `sync_runs`."""
    excluded_result = await remove_excluded(session, actor)
    masters = {m["identifier"]: m for m in await _masters(session)}
    linked = [dict(r) for r in (await session.execute(text(
        "SELECT id, name, stage_indexes, project_type, risk, manager_id, shan_project_identifier, "
        "shan_manager_kept, shan_active FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"
    ))).mappings().all()]
    for p in linked:
        p["stage_indexes"] = list(p["stage_indexes"] or [])
    names = {p["id"]: p["name"] for p in linked}
    alias_users, profile_of_user = await _manager_chain(session)
    levels = await _levels(session)
    counts = {"updated": 0, "managers": 0, "inactive": 0}
    failed: list[dict[str, Any]] = []
    failed_ids: list[int] = []
    for ch in sync_plan(linked, masters, levels, alias_users, profile_of_user):
        try:
            async with session.begin_nested():
                sets = ", ".join(f"{k} = :{k}" for k in ch["fields"])
                await session.execute(text(
                    f"UPDATE lessons.projects SET {sets + ', ' if sets else ''}shan_active = :active, "
                    "shan_synced_at = now(), shan_sync_error = NULL, shan_sync_error_at = NULL WHERE id = :id"),
                    {**ch["fields"], "active": ch["active"], "id": ch["id"]})
                if ch["manager"]:
                    await apply_manager(session, ch["id"], ch["manager"])
                await _log(session, actor, "project_synced_from_master", ch["id"], names.get(ch["id"], ""),
                           change_note(ch["fields"], ch["manager"], ch["active"]))
        except Exception as e:  # noqa: BLE001 — written on the project, the sync goes on
            failed_ids.append(ch["id"])
            failed.append({"id": ch["id"], "name": names.get(ch["id"]), "error": _short_error(e)})
            await session.execute(text(
                "UPDATE lessons.projects SET shan_sync_error = :e, shan_sync_error_at = now() WHERE id = :id"),
                {"e": failed[-1]["error"], "id": ch["id"]})
            continue
        if ch["manager"]:
            counts["managers"] += 1
        counts["updated" if ch["active"] else "inactive"] += 1
    # An error from an earlier run is cleared once the project went through this one.
    await session.execute(text(
        "UPDATE lessons.projects SET shan_sync_error = NULL, shan_sync_error_at = NULL "
        "WHERE shan_sync_error IS NOT NULL AND NOT (id = ANY(:f))"), {"f": failed_ids})
    created: list[dict[str, Any]] = []
    plan = creation_plan(list(masters.values()), {p["shan_project_identifier"] for p in linked},
                         levels or {}, alias_users, profile_of_user, await _overrides(session),
                         await _dup_holds(session, list(masters.values())))
    if levels is not None and plan["create"]:
        created, create_failed = await _insert_created(session, plan, actor)
        failed.extend(create_failed)
    rows = (await payload(session))["projects"]
    report = sync_report(created, failed, rows, plan["held"], counts, plan["waiting_duplicate"],
                         levels is not None, excluded_result)
    await session.execute(text(
        "INSERT INTO lessons.sync_runs (actor, report) VALUES (:a, CAST(:r AS jsonb))"),
        {"a": (actor.username or "").strip() if actor else SYNC_ACTOR, "r": json.dumps(report, ensure_ascii=False)})
    return report


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


# ----------------------------------------------------------------- the completion table (Excel, option ב)

def completion_items(data: dict[str, Any], manager_rows_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every item only a person can give, from `payload` + `managers_payload`. Pure.

    · a held file project: each missing field (stage / type), current = the file's raw value
    · a file מנה"פ name nobody confirmed: one row per NAME (all its projects at once)
    · a linked project held by "טרם שויך" whose file cell is EMPTY: one row per project
    A manager that DIFFERS is an approval, not a completion — it stays in the tab."""
    items = []
    for h in data["summary"]["held"]:
        for f in h["missing"]:
            items.append({"key": f"{f}|{h['identifier']}", "kind": f, "identifier": h["identifier"],
                          "name": h.get("name"), "manager": h.get("manager"),
                          "current": (h.get("stage") if f == "stage" else h.get("project_type")) or "",
                          "note": "השלב בקובץ לא מוכר" if f == "stage" else "סוג הפרויקט בקובץ לא מוכר"})
    for m in manager_rows_:
        if m["problem"]:
            items.append({"key": f"alias|{m['name'].strip()}", "kind": "alias", "identifier": "", "alias": m["name"],
                          "name": f"{m['projects']} פרויקטים", "manager": m["name"].strip(),
                          "current": (m.get("username") or "").strip(),
                          "note": {"alias_unlinked": "השם בקובץ לא קושר למשתמש",
                                   "no_profile": "למשתמש אין פרופיל במערכת הלקחים",
                                   "not_pm": "המשתמש לא מוגדר כמנהל פרויקט"}.get(m["problem"], m["problem"])})
    for r in data["projects"]:
        mc = r.get("manager_check") or {}
        if "manager_placeholder" in r["issues"] and mc.get("status") == "no_name":
            items.append({"key": f"pm|{r['id']}", "kind": "pm", "identifier": r.get("identifier") or "",
                          "name": r["name"], "manager": "", "current": "", "project_id": r["id"],
                          "note": "אין מנה״פ בקובץ — הפרויקט אצל \"טרם שויך\""})
    return items


async def completion_state(session) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(open items, users) — what the export writes and the import checks against, now."""
    data = await payload(session)
    managers = await managers_payload(session)
    return completion_items(data, managers["rows"]), data["users"]


async def apply_completion(session, admin, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply validated rows (lessons_link_sheet.validate → `apply`), each in its
    own savepoint, then ONE sync (which creates what is now complete and fills
    the placeholders). Returns what happened per row."""
    done, failed = [], []
    by_ident: dict[str, dict[str, Any]] = {}
    for c in changes:
        if c["kind"] in ("stage", "project_type"):
            by_ident.setdefault(c["identifier"], {})[c["kind"]] = c["value"]
    for ident, f in by_ident.items():
        try:
            async with session.begin_nested():
                problem = await complete(session, admin, ident, f.get("stage"), f.get("project_type"), create=False)
                if problem:
                    raise ValueError(problem)
            done.append(ident)
        except Exception as e:  # noqa: BLE001 — per item, the rest go on
            failed.append({"key": ident, "error": str(e)[:300]})
    for c in changes:
        if c["kind"] not in ("alias", "pm"):
            continue
        try:
            async with session.begin_nested():
                if c["kind"] == "alias":
                    problem = await confirm_alias(session, admin, c["alias"], int(c["value"]))
                else:
                    problem = await set_manager(session, admin, int(c["project_id"]), "user", int(c["value"]))
                if problem:
                    raise ValueError(problem)
            done.append(c["key"])
        except Exception as e:  # noqa: BLE001
            failed.append({"key": c["key"], "error": str(e)[:300]})
    report = await sync_from_file(session, admin) if done else None
    await _log(session, admin, "completion_imported", "", "טבלת השלמה",
               f"נקלטו {len(done)} השלמות מטבלת Excel, {len(failed)} נכשלו")
    return {"applied": len(done), "failed": failed, "sync": report}
