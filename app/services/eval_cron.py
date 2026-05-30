"""Nightly cron for the eval loop. 03:00 UTC kicks off run_cycle with a system user."""

import logging

logger = logging.getLogger(__name__)

_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except Exception as e:
        logger.warning(f"eval_cron: APScheduler not available ({e}); cron disabled")
        return

    sch = AsyncIOScheduler(timezone="UTC")
    sch.add_job(_nightly_run, CronTrigger(hour=3, minute=0), id="eval_nightly", replace_existing=True)
    sch.add_job(
        _weekly_report_run,
        CronTrigger(day_of_week="thu", hour=17, minute=0, timezone="Asia/Jerusalem"),
        id="weekly_report",
        replace_existing=True,
    )
    sch.add_job(
        _project_report_cron,
        CronTrigger(minute="*/15"),
        id="project_report_cron",
        replace_existing=True,
    )
    sch.start()
    _scheduler = sch
    logger.info("eval_cron: scheduler started (03:00 UTC nightly)")
    logger.info("eval_cron: weekly_report job registered (Thu 17:00 Asia/Jerusalem)")
    logger.info("eval_cron: project_report_cron registered (every 15 min)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"eval_cron: shutdown error: {e}")
    _scheduler = None


async def _nightly_run() -> None:
    """Run a per-question cycle owned by user_id=None (system)."""
    from app.database import async_session_maker
    from app.services.per_question_loop_service import run_cycle

    async with async_session_maker() as s:
        try:
            await run_cycle(s, user_id=None)
        except Exception as e:
            logger.exception(f"eval_cron nightly run failed: {e}")


async def _weekly_report_run() -> None:
    """Send weekly reports to all active users (Thursday 17:00 Israel time)."""
    from app.services.weekly_report_service import send_weekly_reports_cron
    from app.services.telegram_polling import telegram_bot
    if telegram_bot.application and telegram_bot.application.bot:
        await send_weekly_reports_cron(telegram_bot.application.bot)
    else:
        logger.warning("weekly_report_run: bot not available, skipping")


async def _project_report_cron() -> None:
    """Check project report schedules and send due reports (runs every 15 min)."""
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta
    from app.database import async_session_maker
    from app.models import ProjectReportSchedule, User
    from app.services.project_report_service import auto_send_project_report
    from app.services.telegram_polling import telegram_bot
    from sqlalchemy import select

    tz_il = ZoneInfo("Asia/Jerusalem")
    now_il = datetime.now(tz=tz_il)
    # Python weekday(): 0=Mon…6=Sun → remap to 0=Sun…6=Sat
    current_dow_sun = (now_il.weekday() + 1) % 7
    current_hour    = now_il.hour
    current_minute  = now_il.minute

    async with async_session_maker() as session:
        schedules = (await session.execute(
            select(ProjectReportSchedule).where(ProjectReportSchedule.enabled == True)
        )).scalars().all()

        bot = (telegram_bot.application.bot
               if telegram_bot.application and telegram_bot.application.bot else None)

        for sched in schedules:
            if sched.day_of_week is not None and sched.day_of_week != current_dow_sun:
                continue
            if sched.hour_il != current_hour:
                continue
            if not (sched.minute_il <= current_minute < sched.minute_il + 15):
                continue
            if sched.last_sent_at:
                if (datetime.utcnow() - sched.last_sent_at) < timedelta(minutes=30):
                    continue

            user = await session.get(User, sched.user_id)
            if not user:
                continue

            logger.info(f"project_report_cron: sending report for user {user.id} ({user.username})")
            ok = await auto_send_project_report(user, session, bot)
            if ok:
                sched.last_sent_at = datetime.utcnow()
                await session.commit()

        # ── User report schedules ─────────────────────────────────────
        for sched in schedules:
            if not sched.ur_enabled:
                continue
            if sched.ur_dow is not None and sched.ur_dow != current_dow_sun:
                continue
            if sched.ur_hour_il != current_hour:
                continue
            if not (sched.ur_minute_il <= current_minute < sched.ur_minute_il + 15):
                continue
            if sched.ur_last_sent_at:
                if (datetime.utcnow() - sched.ur_last_sent_at) < timedelta(minutes=30):
                    continue

            user = await session.get(User, sched.user_id)
            if not user or not user.telegram_id:
                continue

            from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
            logger.info(f"project_report_cron: sending user report for user {user.id} ({user.username})")
            try:
                sections = await generate_report_for_user(user, session, sent_via="cron")
                if bot:
                    await send_report_to_user(bot, user.telegram_id, sections)
                sched.ur_last_sent_at = datetime.utcnow()
                await session.commit()
            except Exception as exc:
                logger.error(f"user_report_cron failed for user {user.id}: {exc}")
