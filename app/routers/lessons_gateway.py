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
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
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


# --------------------------------------------------------------------- AI functions

AI_FUNCTIONS = frozenset({"summarize-lessons", "review-lesson", "analyze-lessons",
                          "classify-ai-feedback", "suggest-categories", "admin-insights"})
# Per Shan-AI user, per minute. The Groq quota is shared with the bot and the
# dashboard; one user hammering "ניתוח AI" must not starve them.
AI_PER_MINUTE = 12
_ai_hits: dict[int, deque] = defaultdict(deque)
AI_BUSY = "שירות ה-AI עמוס כרגע, נסה שוב בעוד דקה"


def _acting_id(body: dict, keys: tuple[str, ...], identity: la.Identity) -> str:
    """The module id a request may act as: the one it names if it is the
    caller's own (or a group of theirs; admin: anyone), else the caller."""
    for k in keys:
        v = body.get(k)
        if v and (identity.is_admin or str(v) in identity.actor_ids):
            return str(v)
    return identity.profile_id


async def _prefs_and_insights(session: AsyncSession, user_id: str) -> tuple[list[dict], list[dict]]:
    prefs = [dict(r) for r in (await session.execute(text(
        "SELECT preference_key, preference_value FROM lessons.ai_user_preferences WHERE user_id = :u"
    ), {"u": user_id})).mappings().all()]
    insights = [dict(r) for r in (await session.execute(text(
        "SELECT insight_text, context_type, context_value FROM lessons.ai_global_insights"
    ))).mappings().all()]
    return prefs, insights


@router.api_route("/functions/v1/{name:path}", methods=["GET", "POST"])
async def ai_functions(name: str, request: Request, identity: la.Identity = Depends(current_identity),
                       session: AsyncSession = Depends(get_db_session)):
    from app.services import lessons_ai as ai

    if name not in AI_FUNCTIONS:
        return _json({"error": "Not found"}, 404)
    if request.method != "POST":
        return _json({"error": "Use POST"}, 405)
    hits = _ai_hits[identity.shan_user_id]
    now = time.monotonic()
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= AI_PER_MINUTE:
        return _json({"error": "חריגה ממגבלת בקשות AI, נסה שוב בעוד דקה"}, 429)
    hits.append(now)
    body, err = la.parse_json_body(await request.body())
    if err or not isinstance(body, dict):
        return _json({"error": "Expected a JSON object"}, 400)

    try:
        if name == "summarize-lessons":
            prefs, insights = await _prefs_and_insights(session, _acting_id(body, ("userId",), identity))
            system, user = ai.build_summarize(body, prefs, insights)
            answer = await ai.ask(system, user, json_mode=False, max_tokens=1800)
            return Response(ai.sse_body(answer), media_type="text/event-stream")

        if name == "review-lesson":
            built = ai.build_review(body)
            if built is None:
                return _json({"error": "Invalid type. Use 'pre_submit', 'post_approve', or 're_analyze'"}, 400)
            reply = ai.parse_json(await ai.ask(*built, json_mode=True))
            return _json({"review": ai.normalize_review(body.get("type"), reply)})

        if name == "analyze-lessons":
            prefs, insights = await _prefs_and_insights(session, _acting_id(body, ("userId",), identity))
            lesson_ids = [int(lid) for lid in (l.get("id") for l in body.get("allLessons") or [] if isinstance(l, dict))
                          if isinstance(lid, int)]
            rows = [dict(r) for r in (await session.execute(text(
                "SELECT lesson_id, is_relevant, is_implemented, responded_at FROM lessons.lesson_implementations "
                "WHERE lesson_id = ANY(:ids)"), {"ids": lesson_ids})).mappings().all()] if lesson_ids else []
            system, user = ai.build_analyze(body, prefs, insights, ai.quality_scores(lesson_ids, rows))
            reply = ai.parse_json(await ai.ask(system, user, json_mode=True))
            referent_ids = {str(r.get("id")) for r in body.get("referents") or [] if isinstance(r, dict) and r.get("id")}
            return _json(ai.normalize_analyze(reply, set(lesson_ids), referent_ids))

        if name == "classify-ai-feedback":
            return await _classify_feedback(body, identity, session)

        if name == "suggest-categories":
            reply = ai.parse_json(await ai.ask(*ai.build_suggest_categories(body), json_mode=True, max_tokens=800))
            sugg = [{"name": str(s.get("name")), "description": str(s.get("description") or "")}
                    for s in reply.get("suggestions") or [] if isinstance(s, dict) and s.get("name")]
            return _json({"suggestions": sugg})

        # admin-insights
        if not body.get("patterns"):
            return _json({"phrasings": []})
        reply = ai.parse_json(await ai.ask(*ai.build_admin_insights(body), json_mode=True, max_tokens=1000))
        phr = [{"id": str(p.get("id")), "sentence": str(p.get("sentence") or "")}
               for p in reply.get("phrasings") or [] if isinstance(p, dict) and p.get("id") is not None]
        return _json({"phrasings": phr})
    except ai.AIUnavailable:
        return _json({"error": AI_BUSY}, 503)


async def _classify_feedback(body: dict, identity: la.Identity, session: AsyncSession) -> Response:
    """classify-ai-feedback: classify, store, apply personal prefs, queue public insights."""
    from app.services import lessons_ai as ai

    feedback_text, user_id, user_name = body.get("feedback_text"), body.get("user_id"), body.get("user_name")
    if not feedback_text or not user_id or not user_name:
        return _json({"error": "חסרים שדות חובה"}, 400)
    if not identity.is_admin and str(user_id) not in identity.actor_ids:
        return _json({"error": "user_id must be you or a group you belong to"}, 403)
    reply = ai.parse_json(await ai.ask(ai.CLASSIFY_SYSTEM, f'פידבק המשתמש: "{feedback_text}"', json_mode=True, max_tokens=800))
    public, personal = ai.normalize_classify(reply)
    classification = "public" if public else "personal"
    fid = (await session.execute(text(
        "INSERT INTO lessons.ai_feedback (user_id, user_name, context_type, context_id, feedback_text, classification, "
        "extracted_mistakes, extracted_preferences, processed) VALUES (:u, :n, :ct, :cid, :t, :c, "
        "CAST(:m AS jsonb), CAST(:p AS jsonb), true) RETURNING id"
    ), {"u": str(user_id), "n": str(user_name), "ct": body.get("context_type") or "dashboard",
        "cid": body.get("context_id"), "t": str(feedback_text), "c": classification,
        "m": json.dumps(public, ensure_ascii=False), "p": json.dumps(personal, ensure_ascii=False)})).scalar_one()
    for pref in personal:
        await session.execute(text(
            "INSERT INTO lessons.ai_user_preferences (user_id, preference_key, preference_value, source_feedback_id, updated_at) "
            "VALUES (:u, :k, :v, :f, now()) ON CONFLICT (user_id, preference_key) DO UPDATE SET "
            "preference_value = EXCLUDED.preference_value, source_feedback_id = EXCLUDED.source_feedback_id, updated_at = now()"
        ), {"u": str(user_id), "k": pref["key"], "v": pref["value"], "f": fid})
    for item in public:
        probe = item["text"][:50].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        existing = (await session.execute(text(
            "SELECT id, frequency FROM lessons.ai_candidate_insights WHERE context_type = :ct AND status = 'pending' "
            "AND insight_text ILIKE :q LIMIT 1"), {"ct": item["context_type"], "q": f"%{probe}%"})).first()
        if existing:
            await session.execute(text(
                "UPDATE lessons.ai_candidate_insights SET frequency = frequency + 1, "
                "source_feedback_ids = COALESCE(source_feedback_ids, CAST('[]' AS jsonb)) || to_jsonb(CAST(:f AS integer)), "
                "confidence = LEAST(100, 50 + (frequency + 1) * 10) WHERE id = :id"), {"f": fid, "id": existing[0]})
        else:
            await session.execute(text(
                "INSERT INTO lessons.ai_candidate_insights (insight_text, context_type, context_value, source_feedback_ids, "
                "frequency, confidence, status) VALUES (:t, :ct, :cv, CAST(:ids AS jsonb), 1, 50, 'pending')"
            ), {"t": item["text"], "ct": item["context_type"], "cv": item["context_value"], "ids": json.dumps([fid])})
    await session.commit()
    return _json({"id": fid, "classification": classification,
                  "extracted_public": public, "extracted_personal": personal})


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


# --------------------------------------------------------------------- import (P3)

_IMPORT_PAGE = """<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ייבוא מערכת לקחים</title>
<body style="font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 16px">
<h1>ייבוא מערכת לקחים מ-Lovable</h1>
<p>קורא בלבד מהאתר הישן. "בדיקה" לא כותבת כלום. "ייבוא" מחליף את כל נתוני המודול בעסקה אחת —
כשל באמצע מבטל הכל. קישורי משתמשים, חברי קבוצות ופרופילי צפייה נשמרים.</p>
<button id="dry">1. בדיקה (בלי לכתוב)</button>
<button id="run" disabled>2. ייבוא</button>
<pre id="out" style="background:#f4f4f5;padding:1rem;white-space:pre-wrap;direction:ltr;text-align:left"></pre>
<script>
const out = document.getElementById('out'), run = document.getElementById('run');
async function go(mode) {
  out.textContent = '...';
  const r = await fetch('/lessons/api/_import?mode=' + mode, {method: 'POST', credentials: 'same-origin'});
  const j = await r.json();
  out.textContent = JSON.stringify(j, null, 2);
  if (mode === 'dry' && j.ok) run.disabled = false;
}
document.getElementById('dry').onclick = () => go('dry');
run.onclick = () => { if (confirm('לייבא ולהחליף את נתוני המודול?')) go('run'); };
</script></body></html>"""


def login_redirect(request: Request) -> Response | None:
    """For the admin HTML pages: no valid session → the login page, then back here."""
    from urllib.parse import quote

    from app.utils.session import verify_token

    token = request.cookies.get("access_token")
    if token and verify_token(token):
        return None
    return RedirectResponse(f"/login?next={quote(request.url.path, safe='/')}", status_code=303)


async def _shan_admin(request: Request, session: AsyncSession) -> User | None:
    user = await _session_user(request, session)
    return user if user.is_admin else None


@router.get("/_import")
async def import_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    if (redirect := login_redirect(request)) is not None:
        return redirect
    if await _shan_admin(request, session) is None:
        return _json({"message": "Shan-AI admin only"}, 403)
    return Response(_IMPORT_PAGE, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.post("/_import")
async def import_run(request: Request, mode: str = "dry", session: AsyncSession = Depends(get_db_session)):
    from app.database import engine
    from app.services import lessons_import as li

    admin = await _shan_admin(request, session)
    if admin is None:
        return _json({"message": "Shan-AI admin only"}, 403)
    if mode not in {"dry", "run"}:
        return _json({"message": "mode must be dry or run"}, 400)
    try:
        report = await li.run_import(engine, settings.LESSONS_SRC_URL, settings.LESSONS_SRC_KEY,
                                     write=(mode == "run"))
    except li.ImportAbort as e:
        logger.warning("lessons import aborted (%s): %s", mode, e)
        return _json({"ok": False, "error": str(e)}, 409)
    except Exception as e:  # the transaction rolled back; say why
        logger.exception("lessons import failed (%s)", mode)
        return _json({"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}, 500)
    logger.info("lessons import %s by user %s", mode, admin.id)
    return _json(report)


@router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"])
async def not_found(path: str):
    # Keeps an unknown API path a JSON 404 — never the SPA's index.html (P2).
    return _json({"error": "Not found"}, 404)
