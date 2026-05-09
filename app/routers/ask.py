"""Web Q&A screen — ask questions, get answers from knowledge base + decisions."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import User
from app.routers.login import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard/ask", response_class=HTMLResponse)
async def ask_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse("ask.html", {
        "request": request,
        "current_user": current_user,
    })


class AskRequest(BaseModel):
    question: str


@router.post("/dashboard/ask/query")
async def ask_query(
    body: AskRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.services.ask_router import route
    result = await route(body.question, session, current_user.id)
    return JSONResponse({
        "answer":        result.answer,
        "sources_text":  result.sources_text,
        "has_files":     result.has_files,
        "has_decisions": result.has_decisions,
        "file_names":    result.file_names,
        "log_id":        result.log_id,
    })
