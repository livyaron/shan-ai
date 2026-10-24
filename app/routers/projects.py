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
from app.services.il_format import register as _register_il_format  # noqa: E402
_register_il_format(templates.env)   # dates on the patterns pages read DD/MM/YYYY


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


async def _sync_and_summarize(file_path: str, original_name: str | None) -> None:
    """Sync the record sheet, then (live reports only) its "תקציר הנהלה" and
    contracts/tenders sheets."""
    import datetime as _dt
    from app.services import contracts_sheet, exec_summary
    from app.database import async_session_maker
    from app.services.project_sync import deactivate_missing, sync_projects_file
    result = await sync_projects_file(file_path)
    if result.get("processed") and not result.get("historical"):
        async with async_session_maker() as session:
            await deactivate_missing(session, result.get("identifiers"))
        from app.services.lessons_project_link import sync_after_upload
        await sync_after_upload()
        await exec_summary.save_from_file(file_path, original_name)
        rd = result.get("report_date")
        await contracts_sheet.save_from_file(file_path, original_name,
                                             _dt.date.fromisoformat(rd) if rd else None)


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
    background_tasks.add_task(_sync_and_summarize, str(file_path), file.filename)

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
    view = insight_access.view_for(scope, p)
    qp = request.query_params
    drill = None
    if qp.get("drill"):
        drill = insight_access.drill_for(view, p, qp.get("drill", ""), qp.get("dv", ""), qp.get("dm", "all"),
                                         qp.get("ds", ""))
    # Drill links keep the "view as" params, so a preview can be drilled too.
    from urllib.parse import urlencode
    keep = urlencode({k: qp[k] for k in ("as_user", "as_sector", "as_manager") if qp.get(k)})
    # Deep AI reading (PLAN.md P5): only the levels this viewer's scope allows.
    # A missing analysis for this report date is generated in the background
    # (single-flight, skips what exists) — the page never waits for a model.
    from app.services import insight_ai, insight_triage
    ai_rows = await insight_ai.load(session, view["ai_kinds"], p.get("as_of"))
    master = (await session.execute(select(KnowledgeFile).where(KnowledgeFile.is_master)
                                    .order_by(KnowledgeFile.created_at.desc()).limit(1))).scalars().first()
    ai_started = bool(set(view["ai_kinds"]) - set(ai_rows)) and insight_ai.claim_autostart(p.get("as_of"))
    if ai_started:
        import asyncio
        asyncio.create_task(insight_ai.generate_areas())
    # The file's "תקציר הנהלה" cards, counted over exactly the rows this page
    # shows — the dashboard counts the same cards the same way (project_kpis).
    from app.services import exec_summary, project_kpis
    kpi_board = project_kpis.board(view["projects"], p.get("as_of"), await exec_summary.latest(session))
    return templates.TemplateResponse("project_insights.html", {
        "request": request,
        "current_user": current_user,
        "p": p,
        "view": view,
        "kpi_board": kpi_board,
        "kpi_scope": "" if insight_access.sees_all(scope) else "?scope=mine",
        "drill": drill,
        "drill_requested": bool(qp.get("drill")),
        "drill_keep": keep,
        "sector_labels": stage_sectors.SECTORS,
        "preview_label": preview_label,
        "preview_options": options,
        "ai": {k: insight_ai.for_display(v) for k, v in ai_rows.items()},
        "ai_status": insight_ai.STATUS,
        "ai_started": ai_started,
        "master_loaded": {"at": master.created_at, "name": master.original_name} if master else None,
        "ai_sector_labels": {insight_ai.sector_kind(k): stage_sectors.SECTORS[k] for k in insight_ai.AREA_SECTORS},
        "levels": insight_triage.LEVELS,
        "level_order": insight_triage.ORDER,
    })


@router.post("/insights/ai/refresh")
async def insights_ai_refresh(current_user: User = Depends(get_current_user)):
    """Admin: rebuild the division + sector analyses for the newest report."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403)
    import asyncio
    from fastapi.responses import RedirectResponse
    from app.services import insight_ai
    asyncio.create_task(insight_ai.generate_areas(force=True))
    return RedirectResponse("/dashboard/projects/insights#ai", status_code=303)


@router.get("/insights/ai/status")
async def insights_ai_status(current_user: User = Depends(get_current_user)):
    from app.services import insight_ai
    return JSONResponse({"running": insight_ai.STATUS["running"], "done": insight_ai.STATUS["done"]})


async def _visible_project(session: AsyncSession, current_user: User, identifier: str):
    """(frames, p) when this viewer may open the project page, else HTTP error."""
    from app.services import insight_access, pattern_service, stage_sectors
    scope = await insight_access.scope_for(session, current_user)
    if not scope.allowed:
        raise HTTPException(status_code=403, detail="אין לך עדיין שיוך לתצוגת הדפוסים — פנה למנהל המערכת")
    frames = await pattern_service.load_frames(session)
    p = pattern_service._plain(pattern_service.compute_patterns(frames))
    view = insight_access.view_for(scope, p)
    full = scope.admin or scope.sector == stage_sectors.PM_DEPT
    if not full and identifier not in {x["identifier"] for x in view["projects"]}:
        raise HTTPException(status_code=404, detail="הפרויקט לא נמצא בתצוגה שלך")
    return frames, p


@router.post("/p/{identifier:path}/ai")
async def project_ai(
    identifier: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Level 3: a deep AI reading of one project, stored for this report date."""
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse
    from app.services import insight_ai
    frames, p = await _visible_project(session, current_user, identifier)
    done = await insight_ai.generate_project(session, frames, p, identifier)
    suffix = "" if done else "?ai_error=1"
    return RedirectResponse(f"/dashboard/projects/p/{quote(identifier)}{suffix}#ai", status_code=303)


@router.get("/p/{identifier:path}/visual", response_class=HTMLResponse)
async def project_visual_report(
    identifier: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """The project's risks as one animated visual report — the same data as
    the project page (project_visual.build), behind the same access gate.
    Declared before the page route: `{identifier:path}` would swallow `/visual`."""
    from app.services import insight_triage, pattern_service, project_chart, project_visual
    frames, p = await _visible_project(session, current_user, identifier)
    h = pattern_service.project_history(frames, p, identifier)
    if h is None:
        raise HTTPException(status_code=404, detail="פרויקט לא ידוע")
    h = pattern_service._plain(h)
    data = project_visual.build(h, project_chart.delay_story(h), project_chart.risk_matrix(h),
                                insight_triage.triage_all(p).get(identifier), insight_triage.LEVELS)
    return templates.TemplateResponse("project_visual.html", {
        "request": request, "current_user": current_user, "h": h, "data": data})


@router.get("/p/{identifier:path}", response_class=HTMLResponse)
async def project_page(
    identifier: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """One project: status, target history, every weekly report, its slip
    events and a computed risk read-out. Visible to whoever sees the project
    on the patterns page (insight_access) — admins and the PM department see
    every project, including ones that have left the file."""
    from app.services import (insight_ai, insight_triage, pattern_service, project_chart, project_lessons,
                              project_links, stage_sectors)
    frames, p = await _visible_project(session, current_user, identifier)
    h = pattern_service.project_history(frames, p, identifier)
    if h is None:
        raise HTTPException(status_code=404, detail="פרויקט לא ידוע")
    project = (await session.execute(
        select(Project).where(Project.project_identifier == identifier))).scalars().first()
    return templates.TemplateResponse("project_page.html", {
        "request": request, "current_user": current_user, "h": pattern_service._plain(h),
        "project": project,
        "delay": project_chart.delay_story(pattern_service._plain(h)),
        "matrix": project_chart.risk_matrix(pattern_service._plain(h)),
        "risk_changes": project_chart.risk_column_changes(pattern_service._plain(h)),
        "sector_labels": stage_sectors.SECTORS,
        "triage": insight_triage.triage_all(p).get(identifier),
        "levels": insight_triage.LEVELS,
        "ai": insight_ai.for_display((await insight_ai.load(
            session, [insight_ai.project_kind(identifier)], p.get("as_of"))).get(insight_ai.project_kind(identifier))),
        "ai_error": request.query_params.get("ai_error") == "1",
        "lessons": await project_lessons.for_project(session, identifier),
        "links": await project_links.for_project(session, identifier, current_user),
    })


@router.get("/link/options")
async def project_link_options(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Active projects for the optional mission/decision project picker."""
    from app.services import project_links
    return {"projects": [{k: p[k] for k in ("id", "identifier", "name")}
                         for p in await project_links.options(session)]}


@router.get("/link/suggest")
async def project_link_suggest(
    text: str = "",
    user_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Suggested projects for a mission/decision text — a hint a person picks
    from, never applied by itself. `user_id` = the mission owner / decision
    submitter (their confirmed מנה"פ name ranks their projects higher)."""
    from app.services import project_links
    return {"suggestions": await project_links.suggest_for(session, text[:4000], user_id or current_user.id)}


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


async def _kpi_rows(session: AsyncSession, current_user: User, scope_name: str):
    """(p, rows, exec payload) — the rows a projects card counts for this viewer:
    "mine" = the patterns-page view of this user, else every live project
    (the same rows the dashboard's "כל האגף" counts)."""
    from app.services import exec_summary, insight_access, pattern_service
    p = await pattern_service.compute_cached(session)
    if scope_name == "mine":
        scope = await insight_access.scope_for(session, current_user)
        rows = insight_access.view_for(scope, p)["projects"]
    else:
        rows = p.get("projects", [])
    return p, rows, await exec_summary.latest(session)


@router.get("/kpi/{key}", response_class=HTMLResponse)
async def project_kpi_list(
    key: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """The projects behind one card — exactly as many rows as the card says
    (project_kpis.rows_for is what both count)."""
    from app.services import project_kpis
    scope_name = "mine" if request.query_params.get("scope") == "mine" else "all"
    p, rows, ex = await _kpi_rows(session, current_user, scope_name)
    found = project_kpis.rows_for(key, rows, p.get("as_of"), ex)
    if found is None:
        raise HTTPException(status_code=404, detail="אין רשימה מאחורי הכרטיס הזה")
    label, help_, items = found
    return templates.TemplateResponse("project_kpi_list.html", {
        "request": request, "current_user": current_user, "label": label, "help": help_,
        "rows": items, "scope": scope_name, "as_of": p.get("as_of"), "table": None,
    })


@router.get("/contracts", response_class=HTMLResponse)
async def project_contracts_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """The master file's "סטטוס התקשרויות קבלנים ותיחורים" sheet — the whole
    table plus what the rules read out of it (contracts_sheet.analyse)."""
    import datetime as _dt
    from app.services import contracts_sheet, il_format
    payload = await contracts_sheet.latest(session)
    analysis = contracts_sheet.analyse(payload) if payload else None
    if payload:
        # a row links to its project page when one of its codes is a project in the record sheet
        codes = {c for r in payload["rows"] for c in r["codes"]}
        found = {}
        if codes:
            res = await session.execute(
                select(Project.project_identifier, Project.estimated_finish_date)
                .where(Project.project_identifier.in_(codes)))
            found = {pid: fc for pid, fc in res.all()}
        for r in payload["rows"]:
            pid = next((c for c in r["codes"] if c in found), None)
            r["link"] = pid
            rec = found.get(pid) if pid else None
            r["record_fc"] = rec.isoformat() if rec else None
            sheet_fc = r["forecast"]["date"]
            r["record_gap_days"] = ((rec - _dt.date.fromisoformat(sheet_fc)).days
                                    if rec and sheet_fc else None)
    return templates.TemplateResponse("project_contracts.html", {
        "request": request, "current_user": current_user,
        "p": payload, "a": analysis,
        "groups": contracts_sheet.GROUPS, "stages": contracts_sheet.LADDER,
        "topics": contracts_sheet.TOPICS, "soon_days": contracts_sheet.RUNWAY_SOON_DAYS,
        "phase_of": contracts_sheet.PHASE_OF, "group_labels": contracts_sheet.GROUP_LABELS,
        "stage_labels": contracts_sheet.STAGE_LABELS,
        "loaded_at_il": il_format.il_datetime(_dt.datetime.fromisoformat(payload["loaded_at"]))
        if payload and payload.get("loaded_at") else None,
    })


@router.get("/summary/{index}", response_class=HTMLResponse)
async def project_summary_table(
    index: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """One table of the master file's "תקציר הנהלה" sheet, as written."""
    from app.services import exec_summary
    ex = await exec_summary.latest(session)
    sections = (ex or {}).get("sections") or []
    if not 0 <= index < len(sections):
        raise HTTPException(status_code=404, detail="הטבלה לא נמצאה בתקציר האחרון")
    sec = sections[index]
    return templates.TemplateResponse("project_kpi_list.html", {
        "request": request, "current_user": current_user,
        "label": sec.get("title") or ex.get("title") or "טבלה",
        "help": f"כפי שנכתב בגיליון ״{ex.get('sheet')}״", "rows": None, "scope": "all",
        "as_of": ex.get("as_of"), "table": sec,
    })
