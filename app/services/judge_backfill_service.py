"""Offline LLM-judge backfill for query_logs.

Labels rows where judge_verdict IS NULL with PASS/PARTIAL/FAIL and,
for non-PASS rows, a failure_type from the fixed taxonomy.

Judging strategy per row:
1. propose_gold(question) -> grounded reference answer from current DB.
2. If both reference and answer are "no info" -> PASS.
3. Otherwise compare_to_gold(question, ai_response, reference) -> score
   -> verdict via score_to_verdict. NOTE: compare_to_gold currently
   returns binary 0.0/1.0, so PARTIAL is unreachable in practice;
   thresholds exist for when graded scoring lands (phase 3).
4. For non-PASS rows, one extra LLM call classifies failure_type.

Idempotent: only selects judge_verdict IS NULL. One bad row never aborts
the batch. Module-level _progress dict supports UI polling.
"""

import asyncio
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import propose_gold, compare_to_gold, get_gold
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

NO_INFO = "אין מידע"

FAILURE_TYPES = ("WRONG_PROJECT", "MISSING_DATA", "HALLUCINATION", "UNSTABLE", "STRUCTURE", "REFUSED")  # UNSTABLE: set by eval loop only, never returned by the backfill LLM prompt

_progress = {"running": False, "total": 0, "done": 0, "judged": 0, "errors": 0}


def get_progress() -> dict:
    return dict(_progress)


def score_to_verdict(score: float) -> str:
    if score >= 0.8:
        return "PASS"
    if score >= 0.5:
        return "PARTIAL"
    return "FAIL"


def parse_failure_type(raw: str | None) -> str | None:
    """Extract a taxonomy token from LLM output; garbage -> None."""
    if not raw:
        return None
    up = raw.upper()
    for ft in FAILURE_TYPES:
        if re.search(rf"\b{ft}\b", up):
            return ft
    return None


async def _classify_failure(question: str, answer: str, reference: str) -> str | None:
    sys_prompt = (
        "אתה מסווג כשלים של מערכת שאלות-תשובות. החזר אך ורק אחת מהמילים: "
        "WRONG_PROJECT (ענה על פרויקט/ישות לא נכונים), "
        "MISSING_DATA (המידע קיים בהפניה אך חסר בתשובה), "
        "HALLUCINATION (התשובה מכילה עובדות שאינן בהפניה), "
        "STRUCTURE (פלט שבור/לא קריא), "
        "REFUSED (סירוב או תשובה ריקה)."
    )
    user = f"שאלה: {question}\nתשובת המערכת: {answer}\nתשובת ההפניה (אמת): {reference}"
    try:
        raw = await llm_chat(
            "eval_judge",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}],
            max_tokens=20,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"judge_backfill: classify failed: {e}")
        return None
    return parse_failure_type(raw)


def _is_no_info(text: str) -> bool:
    return NO_INFO in (text or "")


async def judge_one(session: AsyncSession, log: QueryLog) -> tuple[str, str | None, bool]:
    """Judge a single QueryLog row.

    Returns (verdict, failure_type, gold_backed). gold_backed is True when the
    comparison used a real human-approved gold answer (trustworthy), False when
    it fell back to an LLM-guessed reference.
    """
    answer = (log.ai_response or "").strip()
    if not answer:
        return "FAIL", "REFUSED", False

    gold = await get_gold(session, log.question)
    if gold is not None:
        reference = gold.gold_answer
        gold_backed = True
    else:
        ref = await propose_gold(session, log.question)
        reference = ref["answer"]
        gold_backed = False

    if _is_no_info(reference) and _is_no_info(answer):
        return "PASS", None, gold_backed

    score = await compare_to_gold(log.question, answer, reference)
    verdict = score_to_verdict(score)
    if verdict == "PASS":
        return verdict, None, gold_backed

    failure = await _classify_failure(log.question, answer, reference)
    return verdict, failure, gold_backed


async def run_backfill(session: AsyncSession, limit: int = 200) -> dict:
    """Judge up to `limit` unjudged rows, newest first. Returns stats dict."""
    rows = (await session.execute(
        select(QueryLog)
        .where(QueryLog.judge_verdict.is_(None))
        .where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
        .limit(limit)
    )).scalars().all()

    _progress.update({"running": True, "total": len(rows), "done": 0, "judged": 0, "errors": 0})

    try:
        for log in rows:
            try:
                verdict, failure, gold_backed = await judge_one(session, log)
                log.judge_verdict = verdict
                log.failure_type = failure
                log.judged_against_gold = gold_backed
                await session.commit()
                _progress["judged"] += 1
            except Exception as e:
                await session.rollback()
                _progress["errors"] += 1
                logger.warning(f"judge_backfill: row {log.id} failed: {e}")
                await asyncio.sleep(2)
                if not session.is_active:
                    logger.error("judge_backfill: session no longer active, aborting batch")
                    break
            finally:
                _progress["done"] += 1
    finally:
        _progress["running"] = False

    stats = get_progress()
    logger.info(f"judge_backfill: finished {stats}")
    return stats
