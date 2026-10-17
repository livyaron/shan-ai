"""מערכת לקחים — who belongs to which referent group (PLAN-lessons-module.md decision 4).

Each group can ALSO have a login account of its own (owner decision 2026-10-10,
temporary until the referents get personal Shan-AI users): a Shan-AI user the
admin created for the role ("רפרנט מגזר ביצוע", role viewer) is linked to the
group's profile, so signing in as that user IS signing in as the referent.

`lessons.referent_members` is closed to PostgREST on purpose (nobody adds
themselves to a group), so a Shan-AI admin manages it here, picking people
from the Shan-AI users list — never by typed names. Shan-AI users are only
read; nothing outside schema `lessons` is written.

Included in app.main BEFORE lessons_gateway, whose /lessons/api/* catch-all
would otherwise answer these paths with a 404.
"""
from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.routers.lessons_gateway import _shan_admin, login_redirect
from app.services import lessons_access as la

# Historical role accounts — history only, never a login.
from app.services.lessons_import import NO_LOGIN_NAMES

router = APIRouter(prefix="/lessons/api/_groups", tags=["lessons"])


def render_groups(groups: list[dict[str, Any]], members: dict[str, list[dict[str, Any]]],
                  users: list[dict[str, Any]], notice: str | None = None) -> str:
    """The admin page. Pure: every name goes through html.escape."""
    e = html.escape
    options = "".join(
        f'<option value="{u["id"]}">{e(str(u["username"] or "").strip())}'
        f'{" — " + e(str(u["job_title"])) if u.get("job_title") else ""}</option>'
        for u in users
    )
    blocks = []
    for i, g in enumerate(groups, 1):
        rows = "".join(
            f'<li>{e(str(m["username"] or "").strip())}'
            f'<form method="post" action="/lessons/api/_groups/remove" style="display:inline;margin-inline-start:8px">'
            f'<input type="hidden" name="profile_id" value="{e(g["id"])}">'
            f'<input type="hidden" name="shan_user_id" value="{m["id"]}">'
            f'<button type="submit" title="הסר">✕</button></form></li>'
            for m in members.get(g["id"], [])
        ) or '<li style="color:#888">אין חברים עדיין</li>'
        if g.get("login_user_id"):
            login = (
                f'<p style="margin:4px 0">🔑 חשבון כניסה: <b>{e(str(g.get("login_username") or "").strip())}</b>'
                f'<form method="post" action="/lessons/api/_groups/login" style="display:inline;margin-inline-start:8px">'
                f'<input type="hidden" name="profile_id" value="{e(g["id"])}">'
                f'<input type="hidden" name="action" value="unlink">'
                f'<button type="submit">נתק</button></form></p>'
            )
        else:
            login = (
                f'<form method="post" action="/lessons/api/_groups/login" style="margin:4px 0">'
                f'🔑 חשבון כניסה: <input type="hidden" name="profile_id" value="{e(g["id"])}">'
                f'<input type="hidden" name="action" value="link">'
                f'<select name="shan_user_id" required><option value="">— אין —</option>{options}</select> '
                f'<button type="submit">קשר</button></form>'
            )
        blocks.append(
            f'<section style="border:1px solid #ddd;border-radius:10px;padding:12px 16px;margin:12px 0">'
            f'<h2 style="margin:0 0 6px;font-size:1.05rem">{i}. {e(g["name"])}</h2>'
            f'{login}<div style="color:#555;margin-top:6px">חברים:</div>'
            f'<ul style="margin:6px 0">{rows}</ul>'
            f'<form method="post" action="/lessons/api/_groups/add">'
            f'<input type="hidden" name="profile_id" value="{e(g["id"])}">'
            f'<select name="shan_user_id" required><option value="">— בחר משתמש —</option>{options}</select> '
            f'<button type="submit">הוסף</button></form></section>'
        )
    banner = (f'<p style="background:#eef6ee;padding:8px 12px;border-radius:8px">{e(notice)}</p>'
              if notice else "")
    return (
        '<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>קבוצות רפרנטים</title>'
        '<body style="font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 16px">'
        '<h1>קבוצות רפרנטים — מערכת לקחים</h1>'
        '<p>חבר בקבוצה רואה את התור שלה ופועל בשמה ("פעל בשם" במערכת הלקחים). '
        'אדם יכול להיות בכמה קבוצות. ההיסטוריה לא משתנה.</p>'
        '<p>🔑 חשבון כניסה (זמני): משתמש Shan-AI בשם התפקיד, שנכנס ישירות כרפרנט. '
        'יוצרים אותו בדף המשתמשים של Shan-AI בתפקיד "צופה", ומקשרים כאן.</p>'
        f'{banner}{"".join(blocks)}'
        '<p><a href="/lessons/">← חזרה למערכת הלקחים</a></p></body></html>'
    )


async def _load(session: AsyncSession) -> tuple[list, dict, list]:
    groups = [dict(r) for r in (await session.execute(text(
        "SELECT p.id, p.name, p.shan_user_id AS login_user_id, u.username AS login_username "
        "FROM lessons.profiles p LEFT JOIN public.users u ON u.id = p.shan_user_id "
        "WHERE p.role = 'referent' ORDER BY p.name"
    ))).mappings().all()]
    members: dict[str, list[dict[str, Any]]] = {}
    for r in (await session.execute(text(
        "SELECT m.profile_id, u.id, u.username FROM lessons.referent_members m "
        "JOIN public.users u ON u.id = m.shan_user_id ORDER BY u.username"
    ))).mappings().all():
        members.setdefault(r["profile_id"], []).append({"id": r["id"], "username": r["username"]})
    users = [dict(r) for r in (await session.execute(text(
        "SELECT id, username, job_title FROM public.users ORDER BY username"
    ))).mappings().all()]
    return groups, members, users


@router.get("")
async def groups_page(request: Request, msg: str | None = None,
                      session: AsyncSession = Depends(get_db_session)) -> Response:
    if (redirect := login_redirect(request)) is not None:
        return redirect
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    groups, members, users = await _load(session)
    return HTMLResponse(render_groups(groups, members, users, msg), headers={"Cache-Control": "no-store"})


async def _change(request: Request, session: AsyncSession, profile_id: str, shan_user_id: int,
                  add: bool) -> Response:
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    is_group = (await session.execute(text(
        "SELECT 1 FROM lessons.profiles WHERE id = :p AND role = 'referent'"), {"p": profile_id})).first()
    user = (await session.execute(text(
        "SELECT username FROM public.users WHERE id = :u"), {"u": shan_user_id})).first()
    if not is_group or not user:
        return RedirectResponse("/lessons/api/_groups?msg=" + "קבוצה+או+משתמש+לא+קיימים", status_code=303)
    if add:
        await session.execute(text(
            "INSERT INTO lessons.referent_members (profile_id, shan_user_id) "
            "VALUES (:p, :u) ON CONFLICT DO NOTHING"), {"p": profile_id, "u": shan_user_id})
        msg = "נוסף"
    else:
        await session.execute(text(
            "DELETE FROM lessons.referent_members WHERE profile_id = :p AND shan_user_id = :u"),
            {"p": profile_id, "u": shan_user_id})
        msg = "הוסר"
    await session.commit()
    return RedirectResponse(f"/lessons/api/_groups?msg={msg}", status_code=303)


@router.post("/add")
async def add_member(request: Request, profile_id: str = Form(...), shan_user_id: int = Form(...),
                     session: AsyncSession = Depends(get_db_session)) -> Response:
    return await _change(request, session, profile_id, shan_user_id, add=True)


@router.post("/remove")
async def remove_member(request: Request, profile_id: str = Form(...), shan_user_id: int = Form(...),
                        session: AsyncSession = Depends(get_db_session)) -> Response:
    return await _change(request, session, profile_id, shan_user_id, add=False)


@router.post("/login")
async def group_login(request: Request, profile_id: str = Form(...), action: str = Form(...),
                      shan_user_id: int | None = Form(None),
                      session: AsyncSession = Depends(get_db_session)) -> Response:
    """Give a referent group its own login account, or take it away."""
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    is_group = (await session.execute(text(
        "SELECT 1 FROM lessons.profiles WHERE id = :p AND role = 'referent'"), {"p": profile_id})).first()
    if not is_group or action not in {"link", "unlink"}:
        return RedirectResponse("/lessons/api/_groups?msg=" + "בקשה+לא+תקינה", status_code=303)
    if action == "unlink":
        await session.execute(text(
            "UPDATE lessons.profiles SET shan_user_id = NULL, is_login = false WHERE id = :p"), {"p": profile_id})
        await session.commit()
        return RedirectResponse("/lessons/api/_groups?msg=" + "חשבון+הכניסה+נותק", status_code=303)
    user = (await session.execute(text(
        "SELECT username FROM public.users WHERE id = :u"), {"u": shan_user_id})).first() if shan_user_id else None
    if not user:
        return RedirectResponse("/lessons/api/_groups?msg=" + "משתמש+לא+קיים", status_code=303)
    # One login profile per person: a user already signing in as someone else
    # (a person or another group) would silently become this group instead.
    taken = (await session.execute(text(
        "SELECT name FROM lessons.profiles WHERE shan_user_id = :u AND is_login AND role <> 'viewer' AND id <> :p"
    ), {"u": shan_user_id, "p": profile_id})).first()
    if taken:
        return RedirectResponse("/lessons/api/_groups?msg=" + "המשתמש+כבר+מקושר+לפרופיל+אחר", status_code=303)
    await session.execute(text(
        "UPDATE lessons.profiles SET shan_user_id = :u, is_login = true WHERE id = :p"),
        {"u": shan_user_id, "p": profile_id})
    await session.commit()
    return RedirectResponse("/lessons/api/_groups?msg=" + "חשבון+הכניסה+קושר", status_code=303)


# ------------------------------------------------------------------ people links
# The admin screen inside the module (ניהול מערכת → קישור משתמשים): every
# existing module profile — a person or a role imported from Lovable — is
# linked to the Shan-AI user who now signs in as it. The new user inherits the
# profile's role, projects and history; nothing is copied or merged. Picked
# from the Shan-AI users list only — never by matching names (a wrong link
# shows someone else's lessons).

links_router = APIRouter(prefix="/lessons/api/_links", tags=["lessons"])

def is_linkable(profile: dict[str, Any]) -> bool:
    """An existing module entity a Shan-AI user can take over.

    Not a local `s<id>` row (that row IS a Shan-AI user — a lazy viewer, or one
    an admin set up in ניהול משתמשים) and not a historical name kept only for
    the record.
    """
    if la.is_local(profile):
        return False
    return str(profile.get("name") or "").strip() not in NO_LOGIN_NAMES


def link_problem(profile: dict[str, Any] | None, user_exists: bool, taken_by: str | None) -> str | None:
    """Why a link may not be made, or None. Pure, so the rules are tested without a DB."""
    if profile is None:
        return "הפרופיל לא קיים"
    if not is_linkable(profile):
        return "לפרופיל הזה אי אפשר לקשר משתמש"
    if not user_exists:
        return "משתמש Shan-AI לא קיים"
    if taken_by:
        # One login profile per person: linking a second one would silently
        # change who they are in the module.
        return f"המשתמש כבר מקושר ל'{taken_by.strip()}' — נתק אותו שם קודם"
    return None


async def _links_payload(session: AsyncSession) -> dict[str, Any]:
    profiles = [dict(r) for r in (await session.execute(text(
        "SELECT p.id, p.name, p.email, p.role, p.is_login, p.shan_user_id, u.username AS shan_username "
        "FROM lessons.profiles p LEFT JOIN public.users u ON u.id = p.shan_user_id ORDER BY p.name"
    ))).mappings().all()]
    linked: dict[int, dict[str, Any]] = {}
    visited: set[int] = set()
    for p in profiles:
        uid = p["shan_user_id"]
        if uid is None:
            continue
        if is_linkable(p) and p["is_login"]:
            linked[uid] = {"id": p["id"], "name": p["name"], "role": p["role"]}
        elif not is_linkable(p):
            visited.add(uid)
    users = [
        {**dict(r), "linked_profile": linked.get(r["id"]), "visited": r["id"] in visited}
        for r in (await session.execute(text(
            "SELECT id, username, job_title FROM public.users ORDER BY username"
        ))).mappings().all()
    ]
    return {
        "profiles": [
            {k: p[k] for k in ("id", "name", "email", "role", "shan_user_id", "shan_username")}
            for p in profiles if is_linkable(p)
        ],
        "users": users,
    }


@links_router.get("")
async def links_list(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    return JSONResponse(await _links_payload(session), headers={"Cache-Control": "no-store"})


@links_router.post("")
async def links_set(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    """Body: {"profile_id": "...", "shan_user_id": 12 | null}. null = unlink."""
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    try:
        body = await request.json()
        profile_id = str(body["profile_id"])
        raw = body.get("shan_user_id")
        shan_user_id = None if raw in (None, "") else int(raw)
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"message": "בקשה לא תקינה"}, status_code=400)
    row = (await session.execute(text(
        "SELECT id, name, role FROM lessons.profiles WHERE id = :p"), {"p": profile_id})).mappings().first()
    profile = dict(row) if row else None
    if shan_user_id is None:
        if profile is None or not is_linkable(profile):
            return JSONResponse({"message": link_problem(profile, True, None)}, status_code=409)
        # A referent group without a login account is not a login (lessons_import).
        await session.execute(text(
            "UPDATE lessons.profiles SET shan_user_id = NULL, is_login = (role <> 'referent') WHERE id = :p"),
            {"p": profile_id})
        await session.commit()
        return JSONResponse(await _links_payload(session))
    user_exists = (await session.execute(text(
        "SELECT 1 FROM public.users WHERE id = :u"), {"u": shan_user_id})).first() is not None
    taken = (await session.execute(text(
        "SELECT id, name, role FROM lessons.profiles "
        "WHERE shan_user_id = :u AND is_login AND id <> :p"), {"u": shan_user_id, "p": profile_id}
    )).mappings().all()
    taken_by = next((t["name"] for t in taken if is_linkable(dict(t))), None)
    if (problem := link_problem(profile, user_exists, taken_by)) is not None:
        return JSONResponse({"message": problem}, status_code=409)
    await session.execute(text(
        "UPDATE lessons.profiles SET shan_user_id = :u, is_login = true WHERE id = :p"),
        {"u": shan_user_id, "p": profile_id})
    await session.commit()
    return JSONResponse(await _links_payload(session))


def migration_left(profiles: list[dict[str, Any]]) -> int:
    """People from the old system nobody has linked yet. Referent groups are
    roles, not people (their login account is optional), so they never hold
    the migration open. 0 = the move from Lovable is done."""
    return sum(1 for p in profiles
               if is_linkable(p) and p.get("role") != "referent" and p.get("shan_user_id") is None)


@links_router.get("/status")
async def links_status(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    rows = [dict(r) for r in (await session.execute(text(
        "SELECT id, name, role, shan_user_id FROM lessons.profiles"))).mappings().all()]
    left = migration_left(rows)
    return JSONResponse({"left": left, "done": left == 0}, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ people (ניהול משתמשים)
# Every Shan-AI user, with what the lessons module knows about them — also
# people the old system never had (a new PM): an admin sets them up BEFORE
# their first visit, so nobody's first look at the module is an empty viewer.
# People are created in Shan-AI only; this page only sets module fields.

people_router = APIRouter(prefix="/lessons/api/_people", tags=["lessons"])

PERSON_ROLES: frozenset[str] = frozenset({"admin", "project_manager", "viewer"})


def person_problems(profile: dict[str, Any] | None, groups: list[str]) -> list[str]:
    """Why a person is not set up yet (owner decision 2026-10-17). Pure.

    `unset`: never set up — no module row, or still the automatic viewer
    (an admin's explicit "צפייה בלבד" is set up; so is a referent-group member,
    who acts through the group). `pm_no_projects`: a PM who sees nothing.
    """
    if profile is None:
        return [] if groups else ["unset"]
    role = profile.get("role")
    if (la.is_local(profile) and role == "viewer" and profile.get("defined_at") is None
            and not groups):
        return ["unset"]
    if role == "project_manager" and not (profile.get("assigned_projects") or []):
        return ["pm_no_projects"]
    return []


def group_problems(group: dict[str, Any], member_count: int) -> list[str]:
    """A referent group a lesson can reach but nobody can answer for. Pure."""
    out = []
    if member_count == 0 and group.get("shan_user_id") is None:
        out.append("no_members")
    if not (group.get("assigned_stage_indexes") or []):
        out.append("no_stages")
    return out


_PROFILE_COLS = ("id, name, email, role, assigned_projects, assigned_stage_indexes, "
                 "shan_user_id, is_login, defined_at")


async def _people_payload(session: AsyncSession) -> dict[str, Any]:
    profiles = [dict(r) for r in (await session.execute(text(
        f"SELECT {_PROFILE_COLS} FROM lessons.profiles ORDER BY name"))).mappings().all()]
    members: dict[str, list[int]] = {}
    for pid, uid in (await session.execute(text(
            "SELECT profile_id, shan_user_id FROM lessons.referent_members"))).all():
        members.setdefault(pid, []).append(uid)
    users = [dict(r) for r in (await session.execute(text(
        "SELECT id, username, job_title, email FROM public.users ORDER BY username"))).mappings().all()]
    names = {u["id"]: (u["username"] or "").strip() for u in users}
    by_user: dict[int, list[dict[str, Any]]] = {}
    for p in profiles:
        if p["shan_user_id"] is not None:
            by_user.setdefault(p["shan_user_id"], []).append(p)
    group_rows = [p for p in profiles if p["role"] == "referent"]
    people = []
    for u in users:
        profile = la.pick_profile(by_user.get(u["id"], []))
        groups = [g["id"] for g in group_rows if u["id"] in members.get(g["id"], [])]
        people.append({
            "id": u["id"], "username": (u["username"] or "").strip(), "job_title": u["job_title"],
            "email": u["email"] or "",
            "profile": None if profile is None else {
                "id": profile["id"], "name": profile["name"], "role": profile["role"],
                "assigned_projects": list(profile["assigned_projects"] or []),
                "source": "local" if la.is_local(profile) else "imported",
            },
            "groups": groups,
            "problems": person_problems(profile, groups),
        })
    return {
        "people": people,
        "groups": [{
            "id": g["id"], "name": g["name"],
            "assigned_stage_indexes": list(g["assigned_stage_indexes"] or []),
            "members": [{"id": uid, "username": names.get(uid, str(uid))} for uid in members.get(g["id"], [])],
            "login_username": names.get(g["shan_user_id"]) if g["shan_user_id"] else None,
            "problems": group_problems(g, len(members.get(g["id"], []))),
        } for g in group_rows],
        "migration_left": migration_left(profiles),
    }


@people_router.get("")
async def people_list(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    return JSONResponse(await _people_payload(session), headers={"Cache-Control": "no-store"})


@people_router.post("")
async def people_set(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    """Body: {"shan_user_id": 12, "role": "project_manager", "assigned_projects": [3, 7],
    "groups": ["u17…"]}. Creates the person's local `s<id>` row when they have
    none yet (first visit not needed); edits their effective profile otherwise."""
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    try:
        body = await request.json()
        uid = int(body["shan_user_id"])
        role = str(body["role"])
        projects = sorted({int(x) for x in body.get("assigned_projects") or []})
        group_ids = sorted({str(x) for x in body.get("groups") or []})
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"message": "בקשה לא תקינה"}, status_code=400)
    if role not in PERSON_ROLES:
        return JSONResponse({"message": "תפקיד לא תקין"}, status_code=400)
    user = (await session.execute(text(
        "SELECT id, username, email FROM public.users WHERE id = :u"), {"u": uid})).mappings().first()
    if user is None:
        return JSONResponse({"message": "משתמש Shan-AI לא קיים"}, status_code=404)
    rows = [dict(r) for r in (await session.execute(text(
        f"SELECT {_PROFILE_COLS} FROM lessons.profiles WHERE shan_user_id = :u"), {"u": uid})).mappings().all()]
    profile = la.pick_profile(rows)
    if profile is not None and profile["role"] == "referent":
        # A group's own login account: the group is set up, not the account.
        return JSONResponse({"message": "זה חשבון כניסה של קבוצת רפרנט — מגדירים את הקבוצה עצמה"}, status_code=409)
    all_projects = [r[0] for r in (await session.execute(text(
        "SELECT id FROM lessons.projects ORDER BY id"))).all()]
    if set(projects) - set(all_projects):
        return JSONResponse({"message": "פרויקט לא קיים"}, status_code=400)
    known_groups = {r[0] for r in (await session.execute(text(
        "SELECT id FROM lessons.profiles WHERE role = 'referent'"))).all()}
    if set(group_ids) - known_groups:
        return JSONResponse({"message": "קבוצת רפרנט לא קיימת"}, status_code=400)
    # The edit dialog's rule: an admin holds every project, a viewer none.
    projects = all_projects if role == "admin" else projects if role == "project_manager" else []
    if profile is None:
        row = la.viewer_profile_row(uid, user["username"], user["email"])
        await session.execute(text(
            "INSERT INTO lessons.profiles (id, name, email, role, shan_user_id) "
            "VALUES (:id, :name, :email, :role, :shan_user_id) ON CONFLICT (id) DO NOTHING"), row)
        # ON CONFLICT: an `s<id>` row unlinked earlier is this user's own row.
        await session.execute(text(
            "UPDATE lessons.profiles SET shan_user_id = :u, is_login = true WHERE id = :id"),
            {"u": uid, "id": row["id"]})
        profile_id = row["id"]
    else:
        profile_id = profile["id"]
    await session.execute(text(
        "UPDATE lessons.profiles SET role = :r, assigned_projects = :ap, defined_at = now() WHERE id = :id"),
        {"r": role, "ap": projects, "id": profile_id})
    before = set((profile["assigned_projects"] or []) if profile else [])
    added = sorted(set(projects) - before)
    if role == "project_manager" and added:
        # The edit dialog's rule (UserContext.updateUser): a project has ONE
        # manager — taking it moves manager_id and drops it from the others.
        await session.execute(text(
            "UPDATE lessons.projects SET manager_id = :id WHERE id = ANY(:a)"), {"id": profile_id, "a": added})
        await session.execute(text(
            "UPDATE lessons.profiles SET assigned_projects = "
            "ARRAY(SELECT x FROM unnest(assigned_projects) AS x WHERE NOT (x = ANY(:a))) "
            "WHERE role = 'project_manager' AND id <> :id AND assigned_projects && CAST(:a AS integer[])"),
            {"id": profile_id, "a": added})
    await session.execute(text(
        "DELETE FROM lessons.referent_members WHERE shan_user_id = :u AND NOT (profile_id = ANY(:g))"),
        {"u": uid, "g": group_ids})
    for g in group_ids:
        await session.execute(text(
            "INSERT INTO lessons.referent_members (profile_id, shan_user_id) VALUES (:p, :u) "
            "ON CONFLICT DO NOTHING"), {"p": g, "u": uid})
    await session.commit()
    return JSONResponse(await _people_payload(session))


# ------------------------------------------------------------------ projects (קישור פרויקטים)
# Each lessons project ↔ the master file's project (PLAN-lessons-projects.md
# L1). The admin links from rapidfuzz suggestions, merges duplicates, or keeps
# a project local; then creates the file's remaining projects. The rules live
# in app/services/lessons_project_link.py.

project_links_router = APIRouter(prefix="/lessons/api/_project_links", tags=["lessons"])


@project_links_router.get("")
async def project_links_list(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    from app.services import lessons_project_link as lpl
    if await _shan_admin(request, session) is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    return JSONResponse(await lpl.payload(session), headers={"Cache-Control": "no-store"})


@project_links_router.post("")
async def project_links_act(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    """Body: {"action": "link", "project_id": 3, "identifier": "123" | null}
    · {"action": "local", "project_id": 3, "local": true}
    · {"action": "merge", "source_id": 3, "target_id": 7}
    · {"action": "manager", "project_id": 3, "mode": "file" | "user" | "keep", "user_id": 12}
    · {"action": "create_missing"}. Answers with the fresh payload."""
    from app.services import lessons_project_link as lpl
    admin = await _shan_admin(request, session)
    if admin is None:
        return JSONResponse({"message": "Shan-AI admin only"}, status_code=403)
    try:
        body = await request.json()
        action = str(body["action"])
        if action == "link":
            raw = body.get("identifier")
            problem = await lpl.link(session, admin, int(body["project_id"]),
                                     None if raw in (None, "") else str(raw))
        elif action == "local":
            problem = await lpl.keep_local(session, admin, int(body["project_id"]), bool(body.get("local", True)))
        elif action == "merge":
            problem = await lpl.merge(session, admin, int(body["source_id"]), int(body["target_id"]))
        elif action == "manager":
            raw_uid = body.get("user_id")
            problem = await lpl.set_manager(session, admin, int(body["project_id"]), str(body["mode"]),
                                            None if raw_uid in (None, "") else int(raw_uid))
        elif action == "create_missing":
            _, problem = await lpl.create_missing(session, admin)
        else:
            return JSONResponse({"message": "פעולה לא מוכרת"}, status_code=400)
    except (ValueError, TypeError, KeyError):
        await session.rollback()
        return JSONResponse({"message": "בקשה לא תקינה"}, status_code=400)
    except lpl.MergeAbort as e:
        await session.rollback()
        return JSONResponse({"message": f"{e} — המיזוג בוטל"}, status_code=409)
    if problem is not None:
        await session.rollback()
        return JSONResponse({"message": problem}, status_code=409)
    await session.commit()
    return JSONResponse(await lpl.payload(session))
