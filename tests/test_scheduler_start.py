"""The scheduler must actually start. No database.

`day_of_week="sun-thu"` is an inverted range for APScheduler (mon=0..sun=6):
CronTrigger raised inside start_scheduler, sch.start() was never reached and
every job — morning digest, overdue alerts, weekly report, pending queue —
silently never ran while the site stayed green (found 2026-11-11).
"""
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services import eval_cron


def test_work_week_is_a_valid_cron_expression():
    CronTrigger(day_of_week=eval_cron.WORK_WEEK, hour=7, timezone="Asia/Jerusalem")
    assert "-" not in eval_cron.WORK_WEEK  # a sun-* range is exactly the bug
    with pytest.raises(ValueError):
        CronTrigger(day_of_week="sun-thu", hour=7)


def test_start_scheduler_registers_every_job_and_starts(monkeypatch):
    started = []
    monkeypatch.setattr(AsyncIOScheduler, "start", lambda self, *a, **k: started.append(self))
    monkeypatch.setattr(eval_cron, "_scheduler", None)
    eval_cron.start_scheduler()
    try:
        assert started, "sch.start() was never reached"
        ids = {j.id for j in started[0].get_jobs()}
        assert {"missions_daily_digest", "missions_overdue_check", "weekly_report",
                "pending_queue", "dossier_drip", "missions_daily_xlsx"} <= ids
        assert len(ids) == 12
        # The unattended gold-eval jobs stay off (they spent the whole Groq day budget).
        assert eval_cron.EVAL_JOBS_ENABLED is False
        assert not ids & {"eval_nightly", "batch_eval", "weekly_eval_summary"}
    finally:
        monkeypatch.setattr(eval_cron, "_scheduler", None)


def test_health_reports_a_scheduler_that_is_not_running(monkeypatch):
    monkeypatch.setattr(eval_cron, "_scheduler", None)
    assert eval_cron.scheduler_running() is False
