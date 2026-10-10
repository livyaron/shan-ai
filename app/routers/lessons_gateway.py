"""מערכת לקחים — the Supabase-compatible API the React UI talks to.

    /lessons/api/me                         → who the module thinks you are
    /lessons/api/rest/v1/*                  → guards (lessons_access) → PostgREST → schema lessons
    /lessons/api/functions/v1/send-notification-email → Resend
    /lessons/api/functions/v1/*             → 503 (AI not connected yet)
    /lessons/api/storage/v1/object/...      → LESSONS_UPLOAD_DIR on the uploads volume
    /lessons/api/_status                    → admin: schema, isolation, PostgREST reachability

Every call needs a Shan-AI session. API paths answer 401 JSON, never the 303
the HTML pages use, so the SPA can send the browser to /login?next=/lessons.
Ported from the verified Bun gateway (lessons-handoff/api.ts).
"""
from __future__ import annotations

import html
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import User
from app.services import lessons_access as la

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lessons/api", tags=["lessons"])

BUCKET = "lesson-files"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
EMAIL_PER_MINUTE = 30

# Filled by app.main at startup from lessons_schema.ensure_schema.
SCHEMA_STATUS: dict[str, Any] = {"schema": None, "roles": None, "leaks": None, "error": "not run yet"}

_http: httpx.AsyncClient | None = None
_mail_hits: dict[int, deque] = defaultdict(deque)


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    return _http


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status)


# --------------------------------------------------------------------- identity

async def _session_user(request: Request, session: AsyncSession) -> User:
    from app.utils.session import verify_token

    token = request.cookies.get("access_token")
    payload = verify_token(token) if token else None
    user = await session.get(User, payload["user_id"]) if payload else None
    if not user:
        raise HTTPException(status_code=401, detail={"message": "Not authenticated", "login": "/login?next=/lessons"})
    return user


async def load_identity(session: AsyncSession, user: User) -> la.Identity:
    """The caller's module profile (lazily a viewer) and their referent groups."""
    cols = ("id, name, email, role, assigned_projects, email_preferences, "
            "assigned_equipment_ids, assigned_stage_indexes, shan_user_id, is_login")
    rows = (await session.execute(
        text(f"SELECT {cols} FROM lessons.profiles WHERE shan_user_id = :uid"), {"uid": user.id},
    )).mappings().all()
    profile = la.pick_profile([dict(r) for r in rows])
    if profile is None:
        # Module data only: a Shan-AI user with no profile reads as a viewer.
        row = la.viewer_profile_row(user.id, user.username, user.email)
        await session.execute(text(
            "INSERT INTO lessons.profiles (id, name, email, role, shan_user_id) "
            "VALUES (:id, :name, :email, :role, :shan_user_id) ON CONFLICT (id) DO NOTHING"
        ), row)
        await session.commit()
        got = (await session.execute(
            text(f"SELECT {cols} FROM lessons.profiles WHERE id = :id"), {"id": row["id"]},
        )).mappings().first()
        profile = dict(got) if got else {**row, "is_login": True}
    groups = (await session.execute(text(
        f"SELECT {', '.join('p.' + c.strip() for c in cols.split(','))} "
        "FROM lessons.referent_members m JOIN lessons.profiles p ON p.id = m.profile_id "
        "WHERE m.shan_user_id = :uid AND p.role = 'referent' ORDER BY p.name"
    ), {"uid": user.id})).mappings().all()
    return la.Identity(shan_user_id=user.id, profile=profile, groups=tuple(dict(g) for g in groups))


async def current_identity(request: Request, session: AsyncSession = Depends(get_db_session)) -> la.Identity:
    user = await _session_user(request, session)
    try:
        return await load_identity(session, user)
    except SQLAlchemyError as e:
        # Schema missing or DB hiccup: say so, rather than a bare 500.
        logger.error("lessons identity lookup failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail={"message": "מערכת הלקחים אינה זמינה כרגע"}) from e


# --------------------------------------------------------------------- routes

@router.get("/me")
async def me(identity: la.Identity = Depends(current_identity)):
    return _json(la.me_payload(identity))


@router.get("/_status")
async def status(identity: la.Identity = Depends(current_identity)):
    if not identity.is_admin:
        return _json({"message": "Admin only"}, 403)
    reachable: bool | str
    try:
        r = await _client().get(settings.LESSONS_POSTGREST_URL.rstrip("/") + "/", timeout=3.0)
        reachable = r.status_code < 500
    except httpx.HTTPError as e:
        reachable = f"unreachable: {type(e).__name__}"
    return _json({**SCHEMA_STATUS, "postgrest": reachable})


@router.api_route("/rest/v1/{path:path}", methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"])
async def rest(path: str, request: Request, identity: la.Identity = Depends(current_identity)):
    method = request.method.upper()
    params = list(request.query_params.multi_items())
    raw = b"" if method in {"GET", "HEAD"} else await request.body()
    body, err = la.parse_json_body(raw)
    if err:
        return _json({"message": err}, 400)

    verdict = la.check_request(method, "/" + path, params, body, identity)
    if verdict:
        code, message = verdict
        return _json({"message": message}, code)

    url = settings.LESSONS_POSTGREST_URL.rstrip("/") + "/" + path
    try:
        upstream = await _client().request(
            method, url, params=params, content=raw or None,
            headers=la.forward_headers({k.lower(): v for k, v in request.headers.items()}),
        )
    except httpx.HTTPError as e:
        logger.error("lessons PostgREST unreachable: %s", type(e).__name__)
        return _json({"message": "שירות הנתונים של מערכת הלקחים אינו זמין כרגע"}, 503)

    headers = {h: upstream.headers[h] for h in la.FORWARD_RESPONSE_HEADERS if h in upstream.headers}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


@router.post("/functions/v1/send-notification-email")
async def send_email(request: Request, identity: la.Identity = Depends(current_identity)):
    if not settings.RESEND_API_KEY:
        return _json({"success": False, "error": "RESEND_API_KEY is not configured"}, 503)
    hits = _mail_hits[identity.shan_user_id]
    now = time.monotonic()
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= EMAIL_PER_MINUTE:
        return _json({"error": "rate limited"}, 429)
    hits.append(now)

    data, err = la.parse_json_body(await request.body())
    if err or not isinstance(data, dict):
        return _json({"error": "Expected a JSON object"}, 400)
    to, subject, body = data.get("to"), data.get("subject"), data.get("body")
    if not to or not subject or not body:
        return _json({"error": "Missing required fields: to, subject, body"}, 400)
    if settings.LESSONS_EMAIL_SANDBOX:
        if not settings.LESSONS_EMAIL_SANDBOX_TO:
            return _json({"success": False, "error": "LESSONS_EMAIL_SANDBOX_TO is not configured"}, 503)
        recipient = settings.LESSONS_EMAIL_SANDBOX_TO
    else:
        recipient = to
    payload: dict[str, Any] = {
        "from": settings.LESSONS_EMAIL_FROM,
        "to": [recipient] if isinstance(recipient, str) else recipient,
        "subject": subject,
        "html": _email_html(str(subject), str(body)),
    }
    attachments = data.get("attachments")
    if isinstance(attachments, list) and attachments:
        payload["attachments"] = [
            {"filename": a.get("filename"), "content": a.get("content"),
             "content_type": a.get("content_type") or "application/pdf"}
            for a in attachments if isinstance(a, dict)
        ]
    try:
        r = await _client().post("https://api.resend.com/emails", json=payload,
                                 headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
    except httpx.HTTPError as e:
        return _json({"success": False, "error": f"Resend unreachable: {type(e).__name__}"}, 502)
    try:
        result = r.json()
    except ValueError:
        result = {}
    if r.status_code >= 400:
        return _json({"success": False, "error": f"Resend API error [{r.status_code}]"}, 502)
    return _json({"success": True, "data": result})


def _email_html(subject: str, body: str) -> str:
    # body is HTML composed by the SPA (as on Lovable); the subject is plain text.
    return f"""
        <div dir="rtl" style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
          <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: #e94560; margin: 0; font-size: 20px;">מערכת ניהול לקחים</h1>
          </div>
          <div style="background: #ffffff; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <h2 style="color: #1a1a2e; margin-top: 0;">{html.escape(subject)}</h2>
            <div style="color: #374151; line-height: 1.6;">{body}</div>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
            <p style="color: #9ca3af; font-size: 12px; margin: 0;">הודעה זו נשלחה אוטומטית ממערכת ניהול הלקחים</p>
          </div>
        </div>"""


@router.api_route("/functions/v1/{name:path}", methods=["GET", "POST"])
async def ai_functions(name: str, identity: la.Identity = Depends(current_identity)):
    return _json({"error": "שירות ה-AI אינו מחובר כרגע"}, 503)


def storage_path(key: str) -> Path | None:
    """The file for a storage key, or None when the key escapes the bucket dir."""
    base = Path(settings.LESSONS_UPLOAD_DIR).resolve()
    if not key or "\x00" in key or key.startswith("/") or "\\" in key:
        return None
    target = (base / key).resolve()
    if target == base or base not in target.parents:
        return None
    return target


@router.api_route("/storage/v1/object/public/{bucket}/{key:path}", methods=["GET", "HEAD"])
async def storage_read(bucket: str, key: str, identity: la.Identity = Depends(current_identity)):
    if bucket != BUCKET:
        return _json({"error": "Bucket not found"}, 404)
    target = storage_path(key)
    if target is None or not target.is_file():
        return _json({"error": "Object not found"}, 404)
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


@router.api_route("/storage/v1/object/{bucket}/{key:path}", methods=["POST", "PUT"])
async def storage_upload(bucket: str, key: str, request: Request,
                         identity: la.Identity = Depends(current_identity)):
    if bucket != BUCKET:
        return _json({"error": "Bucket not found"}, 404)
    if identity.is_viewer:
        return _json({"error": "משתמש צפייה בלבד אינו יכול להעלות קבצים"}, 403)
    target = storage_path(key)
    if target is None:
        return _json({"error": "Invalid object key"}, 400)
    # supabase-js upload() defaults to upsert=false: an existing object is a
    # 409, not a silent overwrite of somebody else's file.
    upsert = request.headers.get("x-upsert", "").lower() == "true"
    if target.exists() and not upsert:
        return _json({"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"}, 409)

    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = next((v for v in form.values() if hasattr(v, "read")), None)
        if upload is None:
            return _json({"error": "No file"}, 400)
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
    else:
        data = await request.body()
    if not data:
        return _json({"error": "No file"}, 400)
    if len(data) > MAX_UPLOAD_BYTES:
        return _json({"error": "File too large"}, 413)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return _json({"Key": f"{BUCKET}/{key}", "Id": key, "path": key})


@router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"])
async def not_found(path: str):
    # Keeps an unknown API path a JSON 404 — never the SPA's index.html (P2).
    return _json({"error": "Not found"}, 404)
