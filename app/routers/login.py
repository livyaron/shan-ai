"""Login and authentication router."""

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

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db_session), error: str = None):
    """Display login page with list of users."""
    result = await session.execute(select(User).order_by(User.username))
    users = result.scalars().all()

    return templates.TemplateResponse("login.html", {
        "request": request,
        "users": users,
        "error": error,
    })

@router.post("/login")
async def login(
    user_id: int = Form(...),
    session: AsyncSession = Depends(get_db_session),
):
    """Authenticate user from list selection."""
    user = await session.get(User, user_id)

    if not user:
        return RedirectResponse(
            "/login?error=משתמש+לא+נמצא",
            status_code=303
        )

    # Create JWT token
    token = create_access_token(user.id, user.username)

    # Redirect to dashboard with token in cookie
    response = RedirectResponse(url="/dashboard", status_code=303)
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
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

    user = await session.get(User, payload["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    return user
