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

    _DECISION_KEYWORDS = ("החלטה", "החלטות", "ההחלטה", "ההחלטות")

    # Route decision history queries directly to decisions DB
    if any(kw in body.question for kw in _DECISION_KEYWORDS):
        from app.services.knowledge_service import get_decisions_context, answer_decisions_question
        from app.models import QueryLog
        from app.services.llm_router import get_last_llm_meta
        decisions_ctx = await get_decisions_context(session, current_user.id)
        if decisions_ctx:
            answer = await answer_decisions_question(body.question, decisions_ctx)
        else:
            answer = "לא נמצאו החלטות עבורך במסד הנתונים."
        _provider, _is_fb = get_last_llm_meta()
        log = QueryLog(question=body.question, ai_response=answer,
                       sources_used=[{"source": "decisions_db"}], user_id=current_user.id,
                       llm_provider=_provider or None, is_fallback=_is_fb or None)
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return JSONResponse({
            "answer": answer,
            "sources_text": "📋 מסד ההחלטות",
            "has_files": False,
            "has_decisions": True,
            "file_names": [],
            "log_id": log.id,
        })

    # Route project-related questions to project_tools, same as the Telegram bot does
    if _is_project_query(body.question):
        try:
            from app.services.project_tools import answer_project_query
            answer, proj_log_id = await answer_project_query(body.question, session, {}, user_id=current_user.id)
            return JSONResponse({
                "answer": answer,
                "sources_text": "📂 מסד הפרויקטים",
                "has_files": True,
                "has_decisions": False,
                "file_names": [],
                "log_id": proj_log_id,
            })
        except Exception:
            pass  # fall through to knowledge_service on error

    result = await answer_with_full_context(body.question, session, current_user.id)
    return JSONResponse(result)
