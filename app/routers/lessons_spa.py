"""מערכת לקחים — the React app at /lessons/ (PLAN-lessons-module.md §2.4).

Built files live in static/lessons (Docker stage `ui`). A real file is served
as is: JS/CSS/icons are public code (the repo is public) and the browser fetches
the PWA manifest without cookies, so gating them would only break install.
Every other path is an app route: it needs a Shan-AI session and gets
index.html (SPA fallback); without one the browser goes to /login and comes
back here after.

Registered AFTER lessons_gateway, whose /lessons/api/* catch-all keeps an
unknown API path a JSON 404 rather than this page.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

router = APIRouter(tags=["lessons"])

SPA_DIR = Path("static/lessons")
# Must be re-checked on every load, or a deploy keeps serving the old app.
NO_CACHE_FILES = frozenset({"index.html", "sw.js", "registerSW.js", "manifest.webmanifest"})
_NO_CACHE = {"Cache-Control": "no-cache"}
# Hashed by Vite: a new build is a new name.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def spa_file(path: str, base: Path | None = None) -> Path | None:
    """The built file for a URL path, or None (not a file / outside the build)."""
    root = (base or SPA_DIR).resolve()
    if not path or "\x00" in path or "\\" in path:
        return None
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def _has_session(request: Request) -> bool:
    from app.utils.session import verify_token

    token = request.cookies.get("access_token")
    return bool(token and verify_token(token))


@router.get("/lessons", include_in_schema=False)
async def lessons_root() -> Response:
    return RedirectResponse("/lessons/", status_code=307)


@router.get("/lessons/{path:path}", include_in_schema=False)
async def lessons_app(path: str, request: Request) -> Response:
    if path == "api" or path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    target = spa_file(path)
    if target is not None and target.name != "index.html":
        if target.name in NO_CACHE_FILES:
            headers = _NO_CACHE
        elif path.startswith("assets/"):
            headers = _IMMUTABLE
        else:
            headers = {"Cache-Control": "public, max-age=3600"}
        return FileResponse(target, headers=headers)
    # A missing hashed asset (an old tab after a deploy) is a 404, not HTML
    # served as JavaScript.
    if path.startswith("assets/"):
        return Response(status_code=404)

    if not _has_session(request):
        return RedirectResponse(f"/login?next={quote('/lessons/' + path, safe='/')}", status_code=303)

    index = spa_file("index.html")
    if index is None:
        return HTMLResponse(
            "<!doctype html><html lang='he' dir='rtl'><meta charset='utf-8'>"
            "<title>מערכת לקחים</title><body style='font-family:sans-serif;padding:2rem'>"
            "ממשק מערכת הלקחים עדיין לא נבנה בשרת הזה.</body></html>",
            status_code=503,
        )
    return FileResponse(index, headers=_NO_CACHE)
