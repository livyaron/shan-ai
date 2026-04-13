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
    from app.services.telegram_polling import _is_project_query
    from app.services.knowledge_service import answer_with_full_context

    # Route project-related questions to project_tools, same as the Telegram bot does
    if _is_project_query(body.question):
        try:
            from app.services.project_tools import answer_project_query
            answer = await answer_project_query(body.question, session, {}, user_id=current_user.id)
            return JSONResponse({
                "answer": answer,
                "sources_text": "📂 מסד הפרויקטים",
                "has_files": True,
                "has_decisions": False,
                "file_names": [],
                "log_id": None,
            })
        except Exception:
            pass  # fall through to knowledge_service on error

    result = await answer_with_full_context(body.question, session, current_user.id)
    return JSONResponse(result)
