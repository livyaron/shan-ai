"""Single-run job guard (second brain phase 0).

Deployment is Railway-only, so cross-instance double-runs are rare — but brief
redeploy overlap or an accidentally started second process would double-send
digests and double-burn Groq tokens. Daily jobs claim a (job_name, run_key) row
before working; the loser of the race skips silently.
"""

import logging
from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobRun

logger = logging.getLogger(__name__)


async def claim(session: AsyncSession, job_name: str, run_key: str | None = None) -> bool:
    """Try to claim this job run. True = we own it; False = someone already ran it.

    On any DB error the claim is granted (fail-open): a duplicate run is less
    harmful than silently never running the job.
    """
    key = run_key or date.today().isoformat()
    try:
        result = await session.execute(
            pg_insert(JobRun)
            .values(job_name=job_name, run_key=key)
            .on_conflict_do_nothing(index_elements=["job_name", "run_key"])
        )
        await session.commit()
        claimed = bool(result.rowcount)
        if not claimed:
            logger.info(f"job_guard: {job_name}/{key} already claimed — skipping")
        return claimed
    except Exception as e:
        logger.warning(f"job_guard: claim failed for {job_name}/{key} — running anyway: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return True
