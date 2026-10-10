"""מערכת לקחים — who belongs to which referent group (PLAN-lessons-module.md decision 4).

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
from app.routers.lessons_gateway import _shan_admin

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
        blocks.append(
            f'<section style="border:1px solid #ddd;border-radius:10px;padding:12px 16px;margin:12px 0">'
            f'<h2 style="margin:0 0 6px;font-size:1.05rem">{i}. {e(g["name"])}</h2>'
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
        f'{banner}{"".join(blocks)}'
        '<p><a href="/lessons/">← חזרה למערכת הלקחים</a></p></body></html>'
    )


async def _load(session: AsyncSession) -> tuple[list, dict, list]:
    groups = [dict(r) for r in (await session.execute(text(
        "SELECT id, name FROM lessons.profiles WHERE role = 'referent' ORDER BY name"
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
