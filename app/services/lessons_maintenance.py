"""מערכת לקחים — nightly housekeeping (PLAN-lessons-module.md §2.5).

Notifications older than NOTIFICATION_RETENTION_DAYS are deleted, as the
Lovable system intended. Its pg_cron job (monthly, `time::timestamptz < ...`)
failed EVERY month since June: one row holds the text "לפני שעה" instead of a
date, and the cast aborted the whole DELETE. Here only rows whose `time` looks
like a date are compared, so one bad row can never block the rest again.
Owner decision 2026-10-10: delete. Touches lessons.notifications only.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

NOTIFICATION_RETENTION_DAYS = 30
RUN_AT_UTC = dt.time(0, 30)

CLEANUP_SQL = (
    "DELETE FROM lessons.notifications "
    "WHERE \"time\" ~ '^\\d{4}-\\d{2}-\\d{2}' "
    f"AND \"time\"::timestamptz < now() - interval '{NOTIFICATION_RETENTION_DAYS} days'"
)


def seconds_until(now: dt.datetime, at: dt.time = RUN_AT_UTC) -> float:
    """Seconds from `now` (naive UTC) to the next `at` — today if still ahead, else tomorrow."""
    target = dt.datetime.combine(now.date(), at)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


async def cleanup_notifications(engine) -> int | None:
    """Delete old notifications. Returns the count, or None on failure. Never raises."""
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(CLEANUP_SQL))
        deleted = result.rowcount or 0
        logger.info("lessons notifications cleanup: deleted %d older than %d days",
                    deleted, NOTIFICATION_RETENTION_DAYS)
        return deleted
    except Exception as e:  # noqa: BLE001 — a housekeeping failure must never touch the app
        logger.error("lessons notifications cleanup failed: %s", e)
        return None


async def run_nightly(engine) -> None:
    """Forever: sleep until RUN_AT_UTC, clean up, repeat."""
    while True:
        await asyncio.sleep(seconds_until(dt.datetime.now(dt.UTC).replace(tzinfo=None)))
        await cleanup_notifications(engine)
