"""Web Q&A screen — ask questions, get answers from knowledge base + decisions."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
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


class CorrectionRequest(BaseModel):
    log_id: int
    correction_text: str


async def _schedule_repair_for_gold(gold_id: int, user_id: int | None) -> None:
    """Background task: run a single-question repair cycle for the given gold row.
    Opens its own DB session so it survives after the request completes."""
    import logging as _logging
    log = _logging.getLogger(__name__)
    try:
        from app.database import async_session_maker
        from app.models import EvalGoldAnswer
        from sqlalchemy import select as _select
        from app.services.per_question_loop_service import run_one_question
        async with async_session_maker() as s:
            gold = await s.get(EvalGoldAnswer, gold_id)
            if gold is None:
                log.warning(f"_schedule_repair_for_gold: gold {gold_id} not found")
                return
            all_gold = (await s.execute(
                _select(EvalGoldAnswer))).scalars().all()
            await run_one_question(
                s, gold, user_id=user_id,
                all_gold=list(all_gold),
                eval_run_id=None,
                max_repairs=3, threshold=0.8,
            )
    except Exception as e:
        log.warning(f"_schedule_repair_for_gold failed: {e}", exc_info=True)


@router.post("/dashboard/ask/correct")
async def ask_correct(
    body: CorrectionRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not body.correction_text or not body.correction_text.strip():
        raise HTTPException(status_code=400, detail="correction_text required")

    from app.services.answer_feedback_service import record_thumbs_down
    try:
        fb, gold = await record_thumbs_down(
            session, body.log_id, current_user.id, body.correction_text,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Schedule the single-question repair in the background.
    background.add_task(_schedule_repair_for_gold, gold.id, current_user.id)

    return {"status": "learning", "gold_id": gold.id, "feedback_id": fb.id}
