"""Login and authentication router."""

import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
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
logger = logging.getLogger(__name__)

# --- Brute-force guard -------------------------------------------------------
# The user list is public by design (the login page is a pick-list), so the
# password is the only secret. Five wrong passwords lock that user's login for
# a quarter of an hour. In memory: one replica, and a restart only forgives.
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
LOCKED_ERROR = "יותר מדי ניסיונות כניסה. נסה שוב בעוד רבע שעה"
_failures: dict[int, deque] = defaultdict(deque)


def _is_locked(user_id: int) -> bool:
    hits = _failures.get(user_id)
    if not hits:
        return False
    now = time.monotonic()
    while hits and now - hits[0] > LOCK_SECONDS:
        hits.popleft()
    return len(hits) >= MAX_FAILURES


# --- The initial password is not a way in -------------------------------------
# Every account is created with auth.DEFAULT_PASSWORD, and anyone can read the
# user list — so typing it proves nothing. It only starts this: a 6-digit code
# goes to the user's own Telegram, and the code plus a new password of their own
# is what opens the account. No Telegram link → an admin resets the password.
CODE_TTL_SECONDS = 10 * 60
CODE_MAX_TRIES = 5
CODE_RESEND_SECONDS = 60
MIN_PASSWORD_LENGTH = 8
_codes: dict[int, dict] = {}


async def _send_telegram_code(telegram_id: int, code: str) -> None:
    from app.services.telegram_polling import telegram_bot

    bot = telegram_bot.application.bot if telegram_bot and telegram_bot.application else None
    if bot is None:
        raise RuntimeError("telegram bot is not running")
    await bot.send_message(
        chat_id=telegram_id,
        text=(f"\u200F🔐 קוד אימות לקביעת סיסמה ב-Shan-AI: {code}\n"
              "\u200Fהקוד תקף ל-10 דקות. אם לא ניסית להיכנס כעת — אל תמסור אותו לאיש ועדכן את מנהל המערכת."),
    )


async def start_password_setup(user: User) -> str | None:
    """Send the code. Returns the error to show, or None when it is on its way."""
    if not getattr(user, "telegram_id", None):
        return "הסיסמה הראשונית אינה מאפשרת כניסה. פנה למנהל המערכת לאיפוס סיסמה"
    now = time.monotonic()
    pending = _codes.get(user.id)
    if pending and now - pending["sent"] < CODE_RESEND_SECONDS:
        return None  # the code from a moment ago is still good — no second message
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        await _send_telegram_code(user.telegram_id, code)
    except Exception as e:
        logger.warning("password setup: could not send the code to user %s: %s", user.id, type(e).__name__)
        return "לא ניתן לשלוח קוד אימות בטלגרם כרגע. נסה שוב בעוד מספר דקות"
    _codes[user.id] = {"code": code, "sent": now, "tries": 0}
    return None


def _pending_code(user_id: int) -> dict | None:
    pending = _codes.get(user_id)
    if pending and time.monotonic() - pending["sent"] > CODE_TTL_SECONDS:
        _codes.pop(user_id, None)
        return None
    return pending


def _setup_url(user_id: int, target: str | None) -> str:
    return f"/login/new-password?user_id={user_id}" + _next_qs(target)

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
    if not error and _is_initial_password(password):
        error = await start_password_setup(user)
        if not error:
            return RedirectResponse(_setup_url(user.id, next), status_code=303)
    if error:
        return RedirectResponse("/login?error=" + error.replace(" ", "+") + _next_qs(next), status_code=303)

    response = RedirectResponse(url=safe_next(next) or "/dashboard/", status_code=303)
    _set_session(response, user, ACCESS_TOKEN_EXPIRE_DAYS)
    return response


async def _check_credentials(session: AsyncSession, user_id: int, password: str) -> tuple[User | None, str | None]:
    """The one password check — the HTML form and the app login both use it."""
    from app.utils.auth import verify_password

    if _is_locked(user_id):
        return None, LOCKED_ERROR
    user = await session.get(User, user_id)
    if not user:
        return None, "משתמש לא נמצא"
    if not user.password_hash:
        return None, "סיסמה לא הוגדרה. פנה למנהל"
    if not verify_password(password, user.password_hash):
        _failures[user_id].append(time.monotonic())
        return None, "סיסמה שגויה"
    _failures.pop(user_id, None)
    return user, None


def _is_initial_password(password: str) -> bool:
    from app.utils.auth import DEFAULT_PASSWORD

    return password == DEFAULT_PASSWORD


def _set_session(response, user: User, days: int) -> None:
    from app.config import settings

    token = create_access_token(user.id, user.username, days=days)
    # Secure wherever the site is served over https (Railway); plain http only locally.
    response.set_cookie("access_token", token, max_age=days * 24 * 60 * 60, httponly=True,
                        samesite="lax", secure=settings.public_base_url.startswith("https://"))


@router.get("/login/new-password", response_class=HTMLResponse)
async def new_password_page(request: Request, user_id: int, error: str | None = None, next: str | None = None):
    if _pending_code(user_id) is None:
        return RedirectResponse("/login?error=" + "קוד האימות פג. היכנס שוב לקבלת קוד חדש".replace(" ", "+")
                                + _next_qs(next), status_code=303)
    return templates.TemplateResponse("set_password.html", {
        "request": request, "user_id": user_id, "error": error,
        "next_url": safe_next(next), "min_length": MIN_PASSWORD_LENGTH,
    })


@router.post("/login/new-password")
async def new_password(
    user_id: int = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
    next: str | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
):
    from app.utils.auth import hash_password

    def back(message: str) -> RedirectResponse:
        return RedirectResponse(_setup_url(user_id, next) + "&error=" + message.replace(" ", "+"), status_code=303)

    pending = _pending_code(user_id)
    if pending is None:
        return RedirectResponse("/login?error=" + "קוד האימות פג. היכנס שוב לקבלת קוד חדש".replace(" ", "+")
                                + _next_qs(next), status_code=303)
    if not hmac.compare_digest(code.strip().encode(), pending["code"].encode()):
        pending["tries"] += 1
        if pending["tries"] >= CODE_MAX_TRIES:
            _codes.pop(user_id, None)
            return RedirectResponse("/login?error=" + "יותר מדי קודים שגויים. היכנס שוב לקבלת קוד חדש".replace(" ", "+")
                                    + _next_qs(next), status_code=303)
        return back("קוד האימות שגוי")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return back(f"הסיסמה חייבת להכיל לפחות {MIN_PASSWORD_LENGTH} תווים")
    if _is_initial_password(new_password):
        return back("לא ניתן לבחור שוב את הסיסמה הראשונית")
    if new_password != confirm:
        return back("שתי הסיסמאות אינן זהות")

    user = await session.get(User, user_id)
    if not user:
        return RedirectResponse("/login?error=" + "משתמש לא נמצא".replace(" ", "+"), status_code=303)
    user.password_hash = hash_password(new_password)
    user.password_is_default = False
    await session.commit()
    _codes.pop(user_id, None)
    logger.info("password setup: user %s set a personal password", user.id)

    response = RedirectResponse(url=safe_next(next) or "/dashboard/", status_code=303)
    _set_session(response, user, ACCESS_TOKEN_EXPIRE_DAYS)
    return response


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
        return JSONResponse({"ok": False, "error": error}, status_code=429 if error == LOCKED_ERROR else 401)
    if _is_initial_password(body.password):
        error = await start_password_setup(user)
        if error:
            return JSONResponse({"ok": False, "error": error}, status_code=403)
        return JSONResponse({"ok": False, "error": "נשלח אליך קוד אימות בטלגרם. יש לקבוע סיסמה אישית לפני הכניסה",
                             "change_password": _setup_url(user.id, "/lessons/")}, status_code=403)
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
