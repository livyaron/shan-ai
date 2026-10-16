"""Login and authentication router."""

from urllib.parse import quote

from fastapi import APIRouter, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request

from app.database import get_db_session
from app.models import User
from pydantic import BaseModel

from app.utils.session import ACCESS_TOKEN_EXPIRE_DAYS, create_access_token

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

ROLE_LABELS = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
}

def safe_next(target: str | None) -> str | None:
    """A post-login destination inside this site only — never an open redirect."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    if any(c in target for c in "\r\n"):
        return None
    return target


def _next_qs(target: str | None) -> str:
    # A failed attempt keeps where the user was headed (e.g. /lessons/).
    good = safe_next(target)
    return f"&next={quote(good, safe='/')}" if good else ""


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db_session), error: str = None,
                     next: str | None = None):
    """Display login page with user list."""
    result = await session.execute(select(User).where(User.role.isnot(None)).order_by(User.username))
    users = result.scalars().all()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "users": users,
        "role_labels": ROLE_LABELS,
        "error": error,
        "next_url": safe_next(next),
    })

@router.post("/login")
async def login(
    user_id: int = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    """Authenticate user selected from list."""
    user, error = await _check_credentials(session, user_id, password)
    if error:
        return RedirectResponse("/login?error=" + error.replace(" ", "+") + _next_qs(next), status_code=303)

    response = RedirectResponse(url=safe_next(next) or "/dashboard", status_code=303)
    _set_session(response, user, ACCESS_TOKEN_EXPIRE_DAYS)
    return response


async def _check_credentials(session: AsyncSession, user_id: int, password: str) -> tuple[User | None, str | None]:
    """The one password check — the HTML form and the app login both use it."""
    from app.utils.auth import verify_password

    user = await session.get(User, user_id)
    if not user:
        return None, "משתמש לא נמצא"
    if not user.password_hash:
        return None, "סיסמה לא הוגדרה. פנה למנהל"
    if not verify_password(password, user.password_hash):
        return None, "סיסמה שגויה"
    return user, None


def _set_session(response, user: User, days: int) -> None:
    token = create_access_token(user.id, user.username, days=days)
    response.set_cookie("access_token", token, max_age=days * 24 * 60 * 60, httponly=True)


def remember_days() -> int:
    """Session length when "השאר אותי מחובר" is ticked (PLAN-lessons-mobile.md M0)."""
    from app.config import settings

    return max(ACCESS_TOKEN_EXPIRE_DAYS, min(90, settings.LESSONS_REMEMBER_DAYS))


# --- The lessons phone app's own login (PLAN-lessons-mobile.md M0) ---------------
# An installed PWA must not leave its scope (/lessons/) to sign in: on iOS the
# login page would open outside the app and its cookie may never reach it. The
# app draws its own form and talks to these two JSON routes. Same user list and
# the same password check as GET/POST /login — nothing more is exposed.

@router.get("/login/users")
async def login_users(session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    result = await session.execute(select(User).where(User.role.isnot(None)).order_by(User.username))
    users = [{"id": u.id, "name": u.username, "title": u.job_title or ""} for u in result.scalars().all()]
    return JSONResponse({"users": users, "remember_days": remember_days()},
                        headers={"Cache-Control": "no-store"})


class AppLogin(BaseModel):
    user_id: int
    password: str
    remember: bool = False


@router.post("/login/app")
async def login_app(body: AppLogin, session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    user, error = await _check_credentials(session, body.user_id, body.password)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=401)
    days = remember_days() if body.remember else ACCESS_TOKEN_EXPIRE_DAYS
    response = JSONResponse({"ok": True, "days": days}, headers={"Cache-Control": "no-store"})
    _set_session(response, user, days)
    return response

@router.get("/logout")
async def logout():
    """Logout user."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response

async def get_current_user(request: Request, session: AsyncSession = Depends(get_db_session)) -> User:
    """Dependency to get current authenticated user."""
    from app.utils.session import verify_token

    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Not authenticated",
            headers={"Location": "/login"},
        )

    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Invalid token",
            headers={"Location": "/login"},
        )

    user = await session.get(User, payload["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="User not found",
            headers={"Location": "/login"},
        )

    return user
