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
import re
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.routers.lessons_gateway import _shan_admin, login_redirect

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

_LAZY_VIEWER = re.compile(r"^s\d+$")


def is_linkable(profile: dict[str, Any]) -> bool:
    """An existing module entity a Shan-AI user can take over.

    Not a lazy `s<id>` viewer row (that row IS a Shan-AI user, created on first
    visit) and not a historical name kept only for the record.
    """
    if _LAZY_VIEWER.match(str(profile.get("id") or "")) and profile.get("role") == "viewer":
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
