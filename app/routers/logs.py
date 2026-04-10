"""Admin logs router — query logs, user feedback, and RAG self-optimization."""

import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import User, QueryLog
from app.routers.login import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin logs page
# ---------------------------------------------------------------------------

@router.get("/dashboard/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    filter: str = "all",
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    stmt = select(QueryLog).order_by(desc(QueryLog.timestamp))

    if filter == "negative":
        stmt = stmt.where(QueryLog.user_feedback == -1)
    elif filter == "notes":
        stmt = stmt.where(QueryLog.admin_note.isnot(None))

    result = await session.execute(stmt.limit(200))
    logs = result.scalars().all()

    # Synonym count
    from app.models import QuerySynonym
    syn_count = (await session.execute(select(QuerySynonym))).scalars().all()

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "current_user": current_user,
        "logs": logs,
        "active_filter": filter,
        "synonym_count": len(syn_count),
    })


# ---------------------------------------------------------------------------
# User feedback endpoint (all logged-in users)
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    log_id: int
    feedback: int   # 1 or -1


@router.post("/api/logs/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if body.feedback not in (1, -1):
        raise HTTPException(status_code=400, detail="feedback must be 1 or -1")

    log = await session.get(QueryLog, body.log_id)
    if not log:
        raise HTTPException(status_code=404, detail="log not found")

    log.user_feedback = body.feedback
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin note + is_accurate update
# ---------------------------------------------------------------------------

class NoteRequest(BaseModel):
    admin_note: str
    is_accurate: bool | None = None


@router.post("/dashboard/logs/{log_id}/note")
async def save_note(
    log_id: int,
    body: NoteRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    log = await session.get(QueryLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="log not found")

    log.admin_note = body.admin_note.strip() or None
    if body.is_accurate is not None:
        log.is_accurate = body.is_accurate
    # Reset analyzed so changed notes are re-processed on next optimization run
    log.analyzed = False
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Self-optimization trigger
# ---------------------------------------------------------------------------

@router.post("/dashboard/logs/clear")
async def clear_all_logs(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    from sqlalchemy import delete
    result = await session.execute(delete(QueryLog))
    await session.commit()
    return {"ok": True, "deleted": result.rowcount}


@router.post("/dashboard/logs/{log_id}/reprocess")
async def reprocess_from_log(
    log_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Trigger smart re-parse of the file associated with a STRUCTURE failure log."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    log = await session.get(QueryLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="log not found")

    # Extract file name from sources_used
    sources = log.sources_used or []
    file_name = next((s.get("file") for s in sources if s.get("file")), None)
    if not file_name:
        raise HTTPException(status_code=400, detail="No file source found in this log")

    # Find the KnowledgeFile by original name
    from app.models import KnowledgeFile
    kf = await session.scalar(
        select(KnowledgeFile).where(KnowledgeFile.original_name == file_name)
    )
    if not kf:
        raise HTTPException(status_code=404, detail=f"File '{file_name}' not found in knowledge base")

    # Run smart re-parse in background
    from app.services.knowledge_service import reprocess_file_with_context
    import asyncio
    asyncio.create_task(reprocess_file_with_context(kf.id))

    return {"ok": True, "file": file_name, "file_id": kf.id, "message": "עיבוד חכם החל ברקע"}


@router.post("/dashboard/logs/optimize")
async def run_optimization(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.optimization_service import run_optimization as _run
    try:
        result = await _run(session)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Optimization failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
