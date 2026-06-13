"""Nightly cron for the eval loop. 03:00 UTC kicks off run_cycle with a system user."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def format_eval_summary(cur: dict, prev: Optional[dict], newly_failing: list) -> str:
    """Hebrew Telegram summary of a completed eval run vs the previous one."""
    rate = round(cur["n_pass"] / cur["n_probes"] * 100) if cur["n_probes"] else 0
    lines = [
        f"‏\U0001f9ea סיכום eval שבועי ({cur['started_at']})",
        f"‏הצלחה: {cur['n_pass']}/{cur['n_probes']} ({rate}%)",
    ]
    if prev and prev.get("n_probes"):
        prev_rate = round(prev["n_pass"] / prev["n_probes"] * 100)
        arrow = "\U0001f4c8" if rate >= prev_rate else "\U0001f4c9"
        lines.append(f"‏{arrow} ריצה קודמת: {prev_rate}%")
    if newly_failing:
        lines.append("‏❌ נכשלו הפעם:")
        lines.extend(f"‏• {q}" for q in newly_failing[:10])
    return "\n".join(lines)

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
        "interval", minutes=15,
        id="project_report_cron", replace_existing=True,
    )
    sch.add_job(
        _weekly_eval_summary,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone="Asia/Jerusalem"),
        id="weekly_eval_summary", replace_existing=True,
    )
    sch.add_job(_batch_eval_run, "interval", hours=3, id="batch_eval", replace_existing=True)
    sch.start()
    _scheduler = sch
    logger.info("eval_cron: scheduler started (03:00 UTC nightly)")
    logger.info("eval_cron: weekly_report job registered (Thu 17:00 Asia/Jerusalem)")
    logger.info("eval_cron: project_report_cron job registered (every 15 min)")
    logger.info("eval_cron: weekly_eval_summary job registered (Sun 07:00 Asia/Jerusalem)")
    logger.info("eval_cron: batch_eval job registered (every 3h)")


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


async def _batch_eval_run() -> None:
    """Judge-only batch of gold questions (spaced to avoid Groq rate-limit bursts)."""
    from app.database import async_session_maker
    from app.services.per_question_loop_service import run_cycle

    async with async_session_maker() as s:
        try:
            await run_cycle(s, user_id=None, repair=False, batch=8)
        except Exception as e:
            logger.exception(f"batch_eval run failed: {e}")


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


async def _weekly_eval_summary() -> None:
    """Sunday 07:00 IL: judge-only eval over the gold set + admin Telegram summary."""
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models import EvalRun, User, RoleEnum
    from app.services.per_question_loop_service import run_cycle
    from app.services.telegram_polling import telegram_bot

    async with async_session_maker() as s:
        try:
            cycle_res = await run_cycle(s, user_id=None, repair=False)
        except Exception as e:
            logger.exception(f"weekly_eval_summary run failed: {e}")
            return

        # Extract newly-failing questions from cycle results
        newly_failing = []
        try:
            if cycle_res and isinstance(cycle_res, dict) and "results" in cycle_res:
                newly_failing = [
                    r.get("question", "")
                    for r in cycle_res["results"]
                    if r.get("status") in ("unfixable", "error") and r.get("question")
                ]
        except Exception as extract_err:
            logger.warning(f"Failed to extract newly_failing from cycle_res: {extract_err}")
            newly_failing = []

        runs = (await s.execute(
            select(EvalRun)
            .where(EvalRun.status == "completed")
            .order_by(EvalRun.id.desc())
            .limit(2)
        )).scalars().all()
        if not runs:
            return

        cur = {
            "n_probes": runs[0].n_probes,
            "n_pass": runs[0].n_pass,
            "started_at": runs[0].started_at.strftime("%d/%m"),
        }
        prev = (
            {"n_probes": runs[1].n_probes, "n_pass": runs[1].n_pass}
            if len(runs) > 1 else None
        )

        msg = format_eval_summary(cur, prev, newly_failing=newly_failing)

        admins = (await s.execute(
            select(User).where(
                User.role == RoleEnum.DIVISION_MANAGER,
                User.telegram_id.isnot(None),
            )
        )).scalars().all()

        bot = (
            telegram_bot.application.bot
            if telegram_bot.application and telegram_bot.application.bot
            else None
        )
        if bot:
            for a in admins:
                try:
                    await bot.send_message(chat_id=a.telegram_id, text=msg)
                except Exception as e:
                    logger.warning(f"weekly_eval_summary: send to {a.id} failed: {e}")
