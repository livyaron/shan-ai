"""Distinct-question aggregation over query_logs.

Collapses duplicate-heavy traffic to one representative per normalized question
(latest row wins), so eval metrics reflect the spread of questions rather than
the volume of repeats. Pure reads — no judging, no writes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import question_hash


async def _representatives(session: AsyncSession) -> list[dict]:
    """One dict per distinct question_hash, from the LATEST row, with dup count."""
    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
    )).scalars().all()

    seen: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in rows:
        h = question_hash(r.question)
        counts[h] = counts.get(h, 0) + 1
        if h not in seen:                      # first encountered = latest (desc order)
            seen[h] = {
                "question": r.question,
                "question_hash": h,
                "verdict": r.judge_verdict,
                "failure_type": r.failure_type,
                "judged_against_gold": r.judged_against_gold,
                "_rep_id": r.id,
            }
    for h, d in seen.items():
        d["count"] = counts[h]
    return list(seen.values())


async def distinct_question_eval(session: AsyncSession) -> list[dict]:
    """Public: list of distinct-question entries (latest verdict, dup count)."""
    reps = await _representatives(session)
    for d in reps:
        d.pop("_rep_id", None)
    return reps


def summarize(reps: list[dict]) -> dict:
    """Pure aggregation over an already-computed reps list (no DB access)."""
    total = len(reps)
    passed = sum(1 for d in reps if d["verdict"] == "PASS")
    failed = sum(1 for d in reps if d["verdict"] == "FAIL")
    unjudged = sum(1 for d in reps if d["verdict"] is None)
    gold_backed = sum(1 for d in reps if d["judged_against_gold"] is True)
    judged = passed + failed
    pass_rate = round(passed / judged * 100) if judged else 0
    return {
        "distinct_total": total,
        "distinct_pass": passed,
        "distinct_fail": failed,
        "distinct_unjudged": unjudged,
        "gold_backed": gold_backed,
        "pass_rate": pass_rate,
    }


async def distinct_summary(session: AsyncSession) -> dict:
    """Async wrapper kept for backward compatibility (existing callers/tests)."""
    return summarize(await _representatives(session))
