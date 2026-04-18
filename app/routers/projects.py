"""Projects router — project management dashboard."""

import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db_session
from app.models import Project, User, KnowledgeFile
from app.routers.login import get_current_user
from app.services.project_tools import _compute_delay

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads")
ALLOWED_EXTENSIONS = {"xlsx", "csv"}

router = APIRouter(prefix="/dashboard/projects", tags=["projects"])
templates = Jinja2Templates(directory="app/templates")


def _ext(filename: str) -> str:
    """Extract file extension from filename."""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.get("", response_class=HTMLResponse)
async def projects_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Render projects dashboard page with all active projects."""
    result = await session.execute(
        select(Project)
        .where(Project.is_active)
        .order_by(Project.name)
    )
    projects_orm = result.scalars().all()

    projects = [
        {
            "id":                    p.id,
            "project_identifier":    p.project_identifier,
            "name":                  p.name or "",
            "project_type":          p.project_type or "",
            "stage":                 p.stage or "",
            "manager":               p.manager or "",
            "weekly_report":         p.weekly_report or "",
            "weekly_report_brief":   p.weekly_report_brief or "",
            "risks":                 p.risks or "",
            "to_handle":             p.to_handle or "",
            "dev_plan_date":         p.dev_plan_date.strftime("%d/%m/%Y") if p.dev_plan_date else "",
            "estimated_finish_date": p.estimated_finish_date.strftime("%d/%m/%Y") if p.estimated_finish_date else "",
            "last_updated":          p.last_updated.strftime("%d/%m/%Y %H:%M") if p.last_updated else "",
            "delay_months":          _compute_delay(p.dev_plan_date, p.estimated_finish_date),
        }
        for p in projects_orm
    ]

    # Fetch the last master file upload time
    master_result = await session.execute(
        select(KnowledgeFile)
        .where(KnowledgeFile.is_master)
        .order_by(KnowledgeFile.created_at.desc())
        .limit(1)
    )
    master_file = master_result.scalars().first()
    master_synced_at = (
        master_file.created_at.strftime("%d/%m/%Y %H:%M")
        if master_file and master_file.created_at else None
    )
    master_file_name = master_file.original_name if master_file else None

    return templates.TemplateResponse("projects.html", {
        "request": request,
        "current_user": current_user,
        "projects": projects,
        "master_synced_at": master_synced_at,
        "master_file_name": master_file_name,
    })


@router.get("/data")
async def projects_data(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """JSON endpoint for fetching projects (for future AJAX use)."""
    result = await session.execute(
        select(Project).where(Project.is_active).order_by(Project.name)
    )
    projects_orm = result.scalars().all()

    return JSONResponse([
        {
            "id":                    p.id,
            "project_identifier":    p.project_identifier,
            "name":                  p.name or "",
            "project_type":          p.project_type or "",
            "stage":                 p.stage or "",
            "manager":               p.manager or "",
            "weekly_report":         p.weekly_report or "",
            "weekly_report_brief":   p.weekly_report_brief or "",
            "risks":                 p.risks or "",
            "to_handle":             p.to_handle or "",
        }
        for p in projects_orm
    ])


@router.post("/upload")
async def upload_project_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Upload and sync project master file (XLSX/CSV)."""
    ext = _ext(file.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="סוג קובץ לא נתמך. מותר: XLSX, CSV",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_projects_{file.filename}"
    file_path = UPLOAD_DIR / safe_name

    contents = await file.read()
    file_path.write_bytes(contents)

    # Dispatch to background task
    from app.services.project_sync import sync_projects_file
    background_tasks.add_task(sync_projects_file, str(file_path))

    return JSONResponse({
        "status": "ok",
        "message": "הקובץ הועלה ומעובד ברקע. רענן את הדף בעוד כמה שניות.",
        "filename": file.filename,
    })
