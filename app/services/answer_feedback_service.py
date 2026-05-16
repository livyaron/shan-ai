"""Per-click answer feedback orchestration.

record_thumbs_up: writes AnswerFeedback row; auto-converts question into an
EvalGoldAnswer (source='auto_user_confirmed') only when no existing gold row
points to the same question_hash AND the user is not currently rate-limited
(>5 feedback clicks in the last 60s).

record_thumbs_down: writes AnswerFeedback row with correction_text; calls
save_gold() with the correction as the new gold (source='user_correction').
The save_gold helper updates the existing record in place when the
question_hash already exists, so user_correction overrides prior
auto_user_confirmed and manual rows for the same question — which is the
intended behavior (the human explicitly says "this is the right answer").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog
from app.services.gold_truth_service import question_hash, save_gold

logger = logging.getLogger(__name__)

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_THRESHOLD = 5


async def _is_rate_limited(session: AsyncSession, user_id: int | None) -> bool:
    """True when this user has > _RATE_LIMIT_THRESHOLD feedback rows in
    the last _RATE_LIMIT_WINDOW_SECONDS seconds."""
    if user_id is None:
        return False
    cutoff = datetime.utcnow() - timedelta(seconds=_RATE_LIMIT_WINDOW_SECONDS)
    count = await session.scalar(
        select(func.count(AnswerFeedback.id))
        .where(AnswerFeedback.user_id == user_id)
        .where(AnswerFeedback.created_at >= cutoff)
    )
    return (count or 0) > _RATE_LIMIT_THRESHOLD


async def record_thumbs_up(
    session: AsyncSession,
    log_id: int,
    user_id: int | None,
) -> AnswerFeedback:
    """Insert AnswerFeedback row. Attempt auto-gold conversion when
    eligible (no existing gold for this question + not rate-limited)."""
    fb = AnswerFeedback(query_log_id=log_id, user_id=user_id, vote="up")
    session.add(fb)
    await session.flush()

    log = await session.get(QueryLog, log_id)
    if log is None:
        await session.commit()
        return fb

    h = question_hash(log.question)
    existing = await session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    if existing is not None:
        # Don't insert a new gold — but link feedback to the existing one
        fb.gold_id = existing.id
        await session.commit()
        return fb

    if await _is_rate_limited(session, user_id):
        logger.info(f"auto-gold skipped (rate-limited) user_id={user_id} log_id={log_id}")
        await session.commit()
        return fb

    gold = await save_gold(
        session,
        question=log.question,
        gold_answer=log.ai_response or "",
        user_id=user_id,
        source="auto_user_confirmed",
    )
    fb.gold_id = gold.id
    await session.commit()
    return fb


async def record_thumbs_down(
    session: AsyncSession,
    log_id: int,
    user_id: int | None,
    correction_text: str,
) -> tuple[AnswerFeedback, EvalGoldAnswer]:
    """Insert AnswerFeedback row with correction, save correction as
    user_correction gold, link them."""
    if not correction_text or not correction_text.strip():
        raise ValueError("correction_text required for thumbs-down")

    log = await session.get(QueryLog, log_id)
    if log is None:
        raise LookupError(f"query_log {log_id} not found")

    fb = AnswerFeedback(
        query_log_id=log_id, user_id=user_id, vote="down",
        correction_text=correction_text.strip(),
    )
    session.add(fb)
    await session.flush()

    gold = await save_gold(
        session,
        question=log.question,
        gold_answer=correction_text.strip(),
        user_id=user_id,
        source="user_correction",
    )
    fb.gold_id = gold.id
    await session.commit()
    return fb, gold
