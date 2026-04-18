"""Admin logs router — query logs, user feedback, and RAG self-optimization."""

import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, desc
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


# ---------------------------------------------------------------------------
# Knowledge Inspector — GET + DELETE endpoints
# ---------------------------------------------------------------------------

@router.get("/api/knowledge/inspector")
async def get_knowledge_entries(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Fetch synonyms and individual instructions from __global_instructions__ for the inspector UI."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.models import QuerySynonym

    result = await session.execute(select(QuerySynonym))
    rows = result.scalars().all()

    # Fetch only synonym rows (source != 'instruction')
    synonyms_data = [
        {
            "id": row.id,
            "original": row.original,
            "synonyms": row.synonyms,
        }
        for row in rows
        if row.source != "instruction"
    ]

    # Fetch __global_instructions__ and flatten it into individual items
    instructions_data = []
    global_instr = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    if global_instr and global_instr.synonyms:
        instructions_data = [
            {
                "id": global_instr.id,
                "index": idx,
                "text": text,
            }
            for idx, text in enumerate(global_instr.synonyms)
        ]

    return JSONResponse({
        "synonyms": synonyms_data,
        "instructions": instructions_data,
    })


@router.post("/api/knowledge/reorganize")
async def reorganize_knowledge(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """LLM-powered full reorganization of synonyms + instructions."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.optimization_service import reorganize_knowledge as _reorg
    try:
        result = await _reorg(session)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Reorganize failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/knowledge/entry/{entry_id}")
async def delete_knowledge_entry(
    entry_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a synonym or instruction by ID."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.models import QuerySynonym
    from sqlalchemy import delete

    row = await session.get(QuerySynonym, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    await session.execute(delete(QuerySynonym).where(QuerySynonym.id == entry_id))
    await session.commit()

    return JSONResponse({"ok": True, "deleted_id": entry_id})


@router.delete("/api/knowledge/instruction/{index}")
async def delete_instruction_by_index(
    index: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a specific instruction by index from __global_instructions__."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.models import QuerySynonym

    row = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    if not row:
        raise HTTPException(status_code=404, detail="Global instructions not found")

    if index < 0 or index >= len(row.synonyms):
        raise HTTPException(status_code=400, detail="Index out of range")

    # Remove instruction at this index
    row.synonyms = [text for idx, text in enumerate(row.synonyms) if idx != index]
    await session.commit()
    logger.info(f"Deleted instruction at index {index}")

    return JSONResponse({"ok": True, "deleted_index": index})


class SynonymUpdateBody(BaseModel):
    original: str
    synonyms: list[str]


@router.put("/api/knowledge/entry/{entry_id}")
async def update_knowledge_entry(
    entry_id: int,
    body: SynonymUpdateBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    from app.models import QuerySynonym
    row = await session.get(QuerySynonym, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    row.original = body.original.strip()
    row.synonyms = [s.strip() for s in body.synonyms if s.strip()]
    await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/knowledge/entry")
async def add_knowledge_entry(
    body: SynonymUpdateBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    from app.models import QuerySynonym
    row = QuerySynonym(
        original=body.original.strip(),
        synonyms=[s.strip() for s in body.synonyms if s.strip()],
        source="manual",
    )
    session.add(row)
    await session.commit()
    return JSONResponse({"ok": True})


class InstructionUpdateBody(BaseModel):
    text: str


@router.put("/api/knowledge/instruction/{index}")
async def update_instruction(
    index: int,
    body: InstructionUpdateBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    from app.models import QuerySynonym
    row = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    if not row:
        raise HTTPException(status_code=404, detail="Global instructions not found")
    if index < 0 or index >= len(row.synonyms):
        raise HTTPException(status_code=400, detail="Index out of range")
    updated = list(row.synonyms)
    updated[index] = body.text.strip()
    row.synonyms = updated
    await session.commit()
    return JSONResponse({"ok": True})


@router.post("/api/knowledge/instruction")
async def add_instruction(
    body: InstructionUpdateBody,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    from app.models import QuerySynonym
    row = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    if row:
        row.synonyms = list(row.synonyms) + [body.text.strip()]
    else:
        row = QuerySynonym(
            original="__global_instructions__",
            synonyms=[body.text.strip()],
            source="instruction",
        )
        session.add(row)
    await session.commit()
    return JSONResponse({"ok": True})
