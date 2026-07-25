"""Files router — knowledge base file management."""

import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db_session
from app.models import KnowledgeFile, KnowledgeChunk, User
from app.routers.login import get_current_user
from app.routers.dashboard import _pending_approvals_count

UPLOAD_DIR = Path("uploads")
ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx"}

router = APIRouter(prefix="/dashboard/files", tags=["files"])
templates = Jinja2Templates(directory="app/templates")


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.get("", response_class=HTMLResponse)
async def files_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    msg: str = None,
    error: str = None,
):
    result = await session.execute(
        select(KnowledgeFile, User.username.label("uploader_name"))
        .outerjoin(User, KnowledgeFile.uploader_id == User.id)
        .order_by(KnowledgeFile.created_at.desc())
    )
    rows = result.all()
    files = [
        {
            "id": kf.id,
            "original_name": kf.original_name,
            "file_type": kf.file_type,
            "file_size": kf.file_size,
            "uploader_name": uploader_name or "—",
            "summary": kf.summary or "",
            "chunk_count": kf.chunk_count,
            "status": kf.status,
            "is_master": kf.is_master,
            "created_at": kf.created_at.strftime("%d/%m/%Y %H:%M"),
        }
        for kf, uploader_name in rows
    ]
    pending_approvals = await _pending_approvals_count(current_user.id, session)
    return templates.TemplateResponse("files.html", {
        "request": request,
        "current_user": current_user,
        "files": files,
        "msg": msg,
        "error": error,
        "pending_approvals": pending_approvals,
    })


@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    is_master: str = Form("false"),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    ext = _ext(file.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        return RedirectResponse(
            "/dashboard/files?error=סוג+קובץ+לא+נתמך.+מותר:+PDF,+DOCX,+XLSX",
            status_code=303,
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name

    contents = await file.read()
    file_path.write_bytes(contents)

    make_master = is_master.lower() == "true" and ext == "xlsx"

    # Note: previous master versions are NOT deleted. They are archived
    # automatically once this new master finishes processing (see
    # process_master_file → archive_old_masters), so old versions stay
    # available for reports/depth while only the newest answers questions.

    kf = KnowledgeFile(
        original_name=file.filename,
        file_path=str(file_path),
        file_type=ext,
        file_size=len(contents),
        uploader_id=current_user.id,
        status="processing",
        is_master=make_master,
    )
    session.add(kf)
    await session.commit()
    await session.refresh(kf)

    if make_master:
        from app.services.knowledge_service import process_master_file
        background_tasks.add_task(process_master_file, kf.id)
        return RedirectResponse(
            "/dashboard/files?msg=קובץ+המאסטר+הועלה+ומעובד+בעיבוד+מיוחד.+יופיע+כ%22מוכן%22+בעוד+מספר+שניות.",
            status_code=303,
        )
    else:
        from app.services.knowledge_service import process_file
        background_tasks.add_task(process_file, kf.id)
        return RedirectResponse(
            "/dashboard/files?msg=הקובץ+הועלה+ומעובד.+יופיע+כ%22מוכן%22+בעוד+מספר+שניות.",
            status_code=303,
        )


@router.post("/{file_id}/set_master")
async def set_master_file(
    file_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Mark this file as the master and trigger master ETL.

    Previous master versions are kept and archived automatically once this one
    finishes processing (see process_master_file → archive_old_masters).
    """
    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")
    if kf.file_type != "xlsx":
        return RedirectResponse(
            "/dashboard/files?error=רק+קבצי+XLSX+יכולים+להיות+Master",
            status_code=303,
        )

    kf.is_master = True
    kf.status = "processing"
    await session.commit()

    from app.services.knowledge_service import process_master_file
    background_tasks.add_task(process_master_file, kf.id)

    return RedirectResponse(
        f"/dashboard/files?msg=הקובץ+{kf.original_name}+הוגדר+כ-Master+ומעובד.",
        status_code=303,
    )


@router.post("/{file_id}/unset_master")
async def unset_master_file(
    file_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Remove the master flag from this file."""
    kf = await session.get(KnowledgeFile, file_id)
    if kf:
        kf.is_master = False
        await session.commit()
    return RedirectResponse("/dashboard/files?msg=הקובץ+הוסר+מהגדרת+Master.", status_code=303)


@router.get("/sync-status")
async def sync_status(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Return JSON with current master-file processing progress."""
    from sqlalchemy import func
    from app.models import Project

    processing_file = (await session.execute(
        select(KnowledgeFile)
        .where(KnowledgeFile.status == "processing")
        .order_by(KnowledgeFile.created_at.desc())
        .limit(1)
    )).scalars().first()

    # Use the processing file if active, else fall back to the current master
    ref_file = processing_file
    if not ref_file:
        ref_file = (await session.execute(
            select(KnowledgeFile)
            .where(KnowledgeFile.is_master.is_(True))
            .order_by(KnowledgeFile.created_at.desc())
            .limit(1)
        )).scalars().first()

    is_processing = processing_file is not None

    projects_updated = 0
    projects_total = 0
    if ref_file:
        projects_updated = (await session.execute(
            select(func.count(Project.id)).where(
                Project.last_updated >= ref_file.created_at
            )
        )).scalar() or 0
        projects_total = (await session.execute(
            select(func.count(Project.id))
        )).scalar() or 0

    return {
        "is_processing": is_processing,
        "file_name": ref_file.original_name if ref_file else None,
        "file_status": ref_file.status if ref_file else None,
        "chunk_count": ref_file.chunk_count if ref_file else 0,
        "projects_updated": projects_updated,
        "projects_total": projects_total,
    }


_MIME = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

@router.get("/{file_id}/view")
async def view_file(
    file_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    kf = await session.get(KnowledgeFile, file_id)
    if not kf or not Path(kf.file_path).exists():
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")

    mime = _MIME.get(kf.file_type, "application/octet-stream")
    disposition = "inline" if kf.file_type == "pdf" else "attachment"
    return FileResponse(
        path=kf.file_path,
        media_type=mime,
        filename=kf.original_name,
        content_disposition_type=disposition,
    )


@router.get("/{file_id}/details", response_class=HTMLResponse)
async def file_details(
    file_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    msg: str = None,
    error: str = None,
):
    """Detail page: metadata, extracted content chunks, edit/replace/delete controls."""
    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")

    uploader_name = None
    if kf.uploader_id:
        uploader = await session.get(User, kf.uploader_id)
        uploader_name = uploader.username if uploader else None

    chunk_rows = (await session.execute(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.file_id == file_id)
        .order_by(KnowledgeChunk.chunk_idx.asc())
    )).scalars().all()
    chunks = [{"idx": c.chunk_idx, "content": c.content or ""} for c in chunk_rows]

    file_exists = Path(kf.file_path).exists() if kf.file_path else False

    file_info = {
        "id": kf.id,
        "original_name": kf.original_name,
        "file_type": kf.file_type,
        "file_size": kf.file_size,
        "uploader_name": uploader_name or "—",
        "summary": kf.summary or "",
        "chunk_count": kf.chunk_count,
        "status": kf.status,
        "is_master": kf.is_master,
        "created_at": kf.created_at.strftime("%d/%m/%Y %H:%M") if kf.created_at else "—",
        "file_exists": file_exists,
    }
    pending_approvals = await _pending_approvals_count(current_user.id, session)
    return templates.TemplateResponse("file_detail.html", {
        "request": request,
        "current_user": current_user,
        "file": file_info,
        "chunks": chunks,
        "msg": msg,
        "error": error,
        "pending_approvals": pending_approvals,
    })


@router.post("/{file_id}/edit")
async def edit_file(
    file_id: int,
    original_name: str = Form(...),
    summary: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Update editable metadata: display name and summary."""
    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")

    new_name = (original_name or "").strip()
    if new_name:
        kf.original_name = new_name
    kf.summary = (summary or "").strip() or None
    await session.commit()

    return RedirectResponse(
        f"/dashboard/files/{file_id}/details?msg=פרטי+הקובץ+עודכנו+בהצלחה",
        status_code=303,
    )


@router.post("/{file_id}/replace")
async def replace_file(
    file_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Replace the stored file with a new upload and re-run extraction/embedding."""
    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")

    ext = _ext(file.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        return RedirectResponse(
            f"/dashboard/files/{file_id}/details?error=סוג+קובץ+לא+נתמך.+מותר:+PDF,+DOCX,+XLSX",
            status_code=303,
        )

    # Remove old stored file from disk
    if kf.file_path:
        try:
            Path(kf.file_path).unlink(missing_ok=True)
        except Exception:
            pass

    # Write the new file to disk
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    new_path = UPLOAD_DIR / safe_name
    contents = await file.read()
    new_path.write_bytes(contents)

    # Clear old chunks — process_file/process_master_file append, they don't replace
    from sqlalchemy import delete as _delete
    await session.execute(_delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))

    # Update the record to point at the new file and reset processing state
    kf.original_name = file.filename
    kf.file_path = str(new_path)
    kf.file_type = ext
    kf.file_size = len(contents)
    kf.chunk_count = 0
    kf.summary = None
    kf.status = "processing"
    # A non-xlsx replacement can no longer be a master file
    if kf.is_master and ext != "xlsx":
        kf.is_master = False
    await session.commit()

    if kf.is_master:
        from app.services.knowledge_service import process_master_file
        background_tasks.add_task(process_master_file, kf.id)
    else:
        from app.services.knowledge_service import process_file
        background_tasks.add_task(process_file, kf.id)

    return RedirectResponse(
        f"/dashboard/files/{file_id}/details?msg=הקובץ+הוחלף+ומעובד+מחדש.+יופיע+כ%22מוכן%22+בעוד+מספר+שניות.",
        status_code=303,
    )


@router.post("/{file_id}/reprocess")
async def reprocess_file(
    file_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Re-run extraction/embedding on the existing stored file (no new upload)."""
    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")
    if not kf.file_path or not Path(kf.file_path).exists():
        return RedirectResponse(
            f"/dashboard/files/{file_id}/details?error=הקובץ+המקורי+לא+נמצא+בדיסק.+יש+להחליף+אותו.",
            status_code=303,
        )

    kf.status = "processing"
    await session.commit()

    if kf.is_master:
        from app.services.knowledge_service import process_master_file
        background_tasks.add_task(process_master_file, kf.id)
    else:
        from app.services.knowledge_service import reprocess_file_with_context
        background_tasks.add_task(reprocess_file_with_context, kf.id)

    return RedirectResponse(
        f"/dashboard/files/{file_id}/details?msg=הקובץ+נשלח+לעיבוד+מחדש.",
        status_code=303,
    )


@router.post("/{file_id}/delete")
async def delete_file(
    file_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    kf = await session.get(KnowledgeFile, file_id)
    if kf:
        # Remove file from disk
        try:
            Path(kf.file_path).unlink(missing_ok=True)
        except Exception:
            pass
        await session.delete(kf)
        await session.commit()
    return RedirectResponse("/dashboard/files?msg=הקובץ+נמחק+בהצלחה", status_code=303)
