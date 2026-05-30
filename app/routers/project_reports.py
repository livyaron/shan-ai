"""Project report endpoints."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import ProjectReport, User
from app.routers.login import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/project-reports", tags=["project-reports"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def project_reports_list(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    reports = (await session.execute(
        select(ProjectReport)
        .where(ProjectReport.user_id == current_user.id)
        .order_by(desc(ProjectReport.generated_at))
        .limit(20)
    )).scalars().all()

    return templates.TemplateResponse("project_reports.html", {
        "request": request,
        "current_user": current_user,
        "reports": [
            {
                "id":             r.id,
                "generated_at":   r.generated_at.strftime("%d/%m/%Y %H:%M"),
                "has_video":      bool(r.video_path),
                "notebooklm_url": r.notebooklm_url,
            }
            for r in reports
        ],
    })


@router.post("/generate", response_class=HTMLResponse)
async def project_reports_generate(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.project_report_service import gather_report_data, generate_report_html

    report_data = await gather_report_data(current_user, session)
    html = await generate_report_html(report_data)

    report = ProjectReport(
        user_id=current_user.id,
        report_data=report_data,
        html_content=html,
    )
    session.add(report)
    await session.flush()
    report_id = report.id
    await session.commit()

    asyncio.create_task(_generate_video_background(report_id, report_data))

    return RedirectResponse(f"/dashboard/project-reports/{report_id}", status_code=302)


async def _generate_video_background(report_id: int, report_data: dict) -> None:
    from app.database import async_session_maker
    from app.services.video_report_service import generate_report_video

    video_path = await generate_report_video(report_data, report_id)
    if video_path:
        async with async_session_maker() as s:
            report = await s.get(ProjectReport, report_id)
            if report:
                report.video_path = video_path
                await s.commit()
        logger.info(f"Video saved: {video_path}")


# ── Schedule management (admin only) — declared BEFORE /{report_id} ──────────

def _check_admin(user: User) -> None:
    from app.models import RoleEnum
    allowed = {RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER}
    if not user.is_admin and user.role not in allowed:
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/schedule", response_class=HTMLResponse)
async def report_schedule_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    _check_admin(current_user)
    from app.models import RoleEnum, ProjectReportSchedule

    users = (await session.execute(
        select(User)
        .where(User.role.isnot(None), User.role != RoleEnum.VIEWER)
        .order_by(User.role, User.username)
    )).scalars().all()

    schedules = (await session.execute(select(ProjectReportSchedule))).scalars().all()
    sched_map = {s.user_id: s for s in schedules}

    rows = []
    for u in users:
        s = sched_map.get(u.id)
        rows.append({
            "id":          u.id,
            "username":    u.username,
            "role":        u.role.value if u.role else "",
            "telegram":    bool(u.telegram_id),
            "enabled":     s.enabled if s else False,
            "day_of_week": s.day_of_week if s else None,
            "hour_il":     s.hour_il if s else 8,
            "minute_il":   s.minute_il if s else 0,
            "last_sent":   s.last_sent_at.strftime("%d/%m/%Y %H:%M") if (s and s.last_sent_at) else "—",
        })

    return templates.TemplateResponse("report_schedule.html", {
        "request": request,
        "current_user": current_user,
        "rows": rows,
    })


@router.post("/schedule/save", response_class=HTMLResponse)
async def report_schedule_save(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    _check_admin(current_user)
    from app.models import ProjectReportSchedule

    form = await request.form()
    enabled_ids = {int(k.split("_")[1]) for k, v in form.items()
                   if k.startswith("enabled_") and v == "on"}
    all_ids = {int(k.split("_", 1)[1]) for k in form.keys()
               if k.startswith(("enabled_", "dow_", "hour_", "minute_"))}

    existing = {s.user_id: s for s in (await session.execute(
        select(ProjectReportSchedule)
    )).scalars().all()}

    for uid in all_ids:
        try:
            dow_raw = form.get(f"dow_{uid}", "")
            dow    = int(dow_raw) if dow_raw != "" else None
            hour   = int(form.get(f"hour_{uid}", 8))
            minute = int(form.get(f"minute_{uid}", 0))
        except (ValueError, TypeError):
            continue

        if uid in existing:
            s = existing[uid]
            s.enabled     = (uid in enabled_ids)
            s.day_of_week = dow
            s.hour_il     = max(0, min(23, hour))
            s.minute_il   = max(0, min(59, minute))
        else:
            s = ProjectReportSchedule(
                user_id       = uid,
                enabled       = (uid in enabled_ids),
                day_of_week   = dow,
                hour_il       = max(0, min(23, hour)),
                minute_il     = max(0, min(59, minute)),
                created_by_id = current_user.id,
            )
            session.add(s)

    await session.commit()
    return RedirectResponse("/dashboard/project-reports/schedule", status_code=302)


@router.post("/schedule/send-now/{user_id}", response_class=HTMLResponse)
async def report_schedule_send_now(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    _check_admin(current_user)

    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    from app.services.project_report_service import auto_send_project_report
    from app.services.telegram_polling import telegram_bot
    bot = (telegram_bot.application.bot
           if telegram_bot.application and telegram_bot.application.bot else None)

    asyncio.create_task(auto_send_project_report(target, session, bot))
    return RedirectResponse("/dashboard/project-reports/schedule", status_code=302)


# ── Report detail + delete (parameterized — must be LAST) ────────────────────

@router.get("/{report_id}", response_class=HTMLResponse)
async def project_report_detail(
    report_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    report = await session.scalar(
        select(ProjectReport).where(
            ProjectReport.id == report_id,
            ProjectReport.user_id == current_user.id,
        )
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return templates.TemplateResponse("project_report_detail.html", {
        "request": request,
        "current_user": current_user,
        "report": {
            "id":              report.id,
            "generated_at":    report.generated_at.strftime("%d/%m/%Y %H:%M"),
            "html_content":    report.html_content or "",
            "video_path":      report.video_path,
            "notebooklm_url":  report.notebooklm_url,
        },
    })


@router.post("/{report_id}/delete", response_class=HTMLResponse)
async def project_report_delete(
    report_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    import os
    report = await session.scalar(
        select(ProjectReport).where(
            ProjectReport.id == report_id,
            ProjectReport.user_id == current_user.id,
        )
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.video_path:
        full = os.path.join("static", report.video_path)
        try:
            os.unlink(full)
        except FileNotFoundError:
            pass

    await session.delete(report)
    await session.commit()
    return RedirectResponse("/dashboard/project-reports", status_code=302)
