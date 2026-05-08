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
    sch.start()
    _scheduler = sch
    logger.info("eval_cron: scheduler started (03:00 UTC nightly)")


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
