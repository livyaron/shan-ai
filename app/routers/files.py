"""Files router — knowledge base file management."""

import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db_session
from app.models import KnowledgeFile, User
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

    if make_master:
        # Unset any existing master before setting the new one
        from sqlalchemy import update as _update
        await session.execute(_update(KnowledgeFile).values(is_master=False))

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
    """Unset any existing master, mark this file as master, trigger master ETL."""
    from sqlalchemy import update as _update

    kf = await session.get(KnowledgeFile, file_id)
    if not kf:
        raise HTTPException(status_code=404, detail="קובץ לא נמצא")
    if kf.file_type != "xlsx":
        return RedirectResponse(
            "/dashboard/files?error=רק+קבצי+XLSX+יכולים+להיות+Master",
            status_code=303,
        )

    # Unset all existing masters atomically
    await session.execute(_update(KnowledgeFile).values(is_master=False))

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
