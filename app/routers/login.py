"""Login and authentication router."""

from urllib.parse import quote

from fastapi import APIRouter, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.requests import Request

from app.database import get_db_session
from app.models import User
from app.utils.session import create_access_token

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
    from app.utils.auth import verify_password

    user = await session.get(User, user_id)

    if not user:
        return RedirectResponse("/login?error=משתמש+לא+נמצא" + _next_qs(next), status_code=303)

    if not user.password_hash:
        return RedirectResponse("/login?error=סיסמה+לא+הוגדרה.+פנה+למנהל" + _next_qs(next), status_code=303)

    if not verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=סיסמה+שגויה" + _next_qs(next), status_code=303)

    token = create_access_token(user.id, user.username)
    response = RedirectResponse(url=safe_next(next) or "/dashboard", status_code=303)
    response.set_cookie("access_token", token, max_age=7*24*60*60, httponly=True)
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
