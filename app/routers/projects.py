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

    from app.services import insight_access
    try:
        insights_allowed = (await insight_access.scope_for(session, current_user)).allowed
    except Exception:   # the button is a convenience; never let it break the page
        insights_allowed = bool(current_user.is_admin)

    return templates.TemplateResponse("projects.html", {
        "request": request,
        "current_user": current_user,
        "projects": projects,
        "master_synced_at": master_synced_at,
        "master_file_name": master_file_name,
        "insights_allowed": insights_allowed,
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


@router.post("/backfill")
async def backfill_history(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    """Admin: load historical weekly master files as history (PLAN.md P1).

    Files are replayed oldest-first and never touch live project rows, so the
    order they are picked in does not matter.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="טעינת היסטוריה זמינה למנהל מערכת בלבד")
    from app.services.project_sync import BACKFILL_STATUS, backfill_files
    if BACKFILL_STATUS["running"]:
        raise HTTPException(status_code=409, detail="טעינת היסטוריה כבר רצה — המתן לסיומה")
    bad = [f.filename for f in files if _ext(f.filename or "") != "xlsx"]
    if bad:
        raise HTTPException(status_code=400, detail=f"רק קבצי XLSX: {', '.join(bad)}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, str]] = []
    for f in files:
        path = UPLOAD_DIR / f"{uuid.uuid4().hex}_history_{f.filename}"
        path.write_bytes(await f.read())
        saved.append((str(path), f.filename or path.name))

    BACKFILL_STATUS.update(running=True, files=[])   # visible before the task starts
    background_tasks.add_task(backfill_files, saved)
    return JSONResponse({"status": "ok", "message": f"{len(saved)} קבצים נקלטו — הטעינה רצה ברקע."})


@router.get("/backfill/status")
async def backfill_status(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403)
    from app.services.project_sync import BACKFILL_STATUS
    return JSONResponse(BACKFILL_STATUS)


@router.get("/insights", response_class=HTMLResponse)
async def project_insights_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """The pattern engine drawn as a page, filtered to what this viewer may
    see (insight_access). The raw JSON (/patterns) stays admin-only."""
    from app.services import insight_access, stage_sectors
    from app.services.pattern_service import compute
    scope = await insight_access.scope_for(session, current_user)
    if not scope.allowed:
        raise HTTPException(status_code=403, detail="אין לך עדיין שיוך לתצוגת הדפוסים — פנה למנהל המערכת")
    p = await compute(session)
    preview, preview_label, options = None, None, None
    if scope.admin:
        # "View as": an admin sees exactly what any other viewer sees.
        qp = request.query_params
        preview = await insight_access.preview_scope(
            session, qp.get("as_user"), qp.get("as_sector"), qp.get("as_manager"))
        options = await insight_access.preview_options(session, p)
        if preview:
            scope, preview_label = preview
    return templates.TemplateResponse("project_insights.html", {
        "request": request,
        "current_user": current_user,
        "p": p,
        "view": insight_access.view_for(scope, p),
        "sector_labels": stage_sectors.SECTORS,
        "preview_label": preview_label,
        "preview_options": options,
    })


@router.get("/insights/access", response_class=HTMLResponse)
async def insights_access_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Admin: link file names (מנה"פ) to users and give users a sector."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403)
    from rapidfuzz import process as rf_process
    from sqlalchemy import func
    from app.models import ManagerAlias
    from app.services import stage_sectors
    names = (await session.execute(
        select(Project.manager, func.count()).where(Project.is_active, Project.manager.isnot(None))
        .group_by(Project.manager).order_by(func.count().desc()))).all()
    users = (await session.execute(select(User).order_by(User.username))).scalars().all()
    links = dict((await session.execute(select(ManagerAlias.alias, ManagerAlias.user_id))).all())
    usernames = {u.id: u.username or "" for u in users}
    rows = []
    for name, count in names:
        hint = rf_process.extractOne(name, usernames, score_cutoff=60) if usernames else None
        rows.append({"name": name, "count": count, "user_id": links.get(name),
                     "hint": usernames.get(hint[2]) if hint else None})
    return templates.TemplateResponse("project_insights_access.html", {
        "request": request, "current_user": current_user, "rows": rows, "users": users,
        "sectors": stage_sectors.ASSIGNABLE_SECTORS, "saved": request.query_params.get("saved"),
    })


@router.post("/insights/access")
async def insights_access_save(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Admin: save the name links (alias::<name> = user id) and sectors
    (sector::<user id> = key). An empty choice removes a link / sector."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403)
    from fastapi.responses import RedirectResponse
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import ManagerAlias
    from app.services import stage_sectors
    form = await request.form()
    user_ids = set((await session.execute(select(User.id))).scalars().all())
    for key, value in form.multi_items():
        if key.startswith("alias::"):
            alias = key[len("alias::"):].strip()
            uid = int(value) if str(value).isdigit() and int(value) in user_ids else None
            stmt = pg_insert(ManagerAlias).values(alias=alias, user_id=uid)
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["alias"], set_={"user_id": uid}))
        elif key.startswith("sector::"):
            uid_s = key[len("sector::"):]
            if uid_s.isdigit() and int(uid_s) in user_ids:
                user = await session.get(User, int(uid_s))
                user.sector = value if value in stage_sectors.ASSIGNABLE_SECTORS else None
    await session.commit()
    return RedirectResponse("/dashboard/projects/insights/access?saved=1", status_code=303)


@router.get("/patterns")
async def project_patterns(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Admin: the pattern & risk engine's full output as JSON (PLAN.md P2).

    Admin-only until the per-role views (P3) decide who sees which part — the
    named league table and the leading indicators are not for everyone yet.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403)
    from app.services.pattern_service import compute
    return JSONResponse(await compute(session))


@router.post("/regenerate-briefs")
async def regenerate_briefs(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Trigger AI brief regeneration for all projects missing or having malformed briefs."""
    from app.services.project_sync import generate_all_briefs
    background_tasks.add_task(generate_all_briefs)
    return JSONResponse({"status": "ok", "message": "יצירת סיכומים החלה ברקע."})
