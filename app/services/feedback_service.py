"""Feedback service - 48-hour feedback loop for completed decisions."""

import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from telegram import Bot

from app.database import async_session_maker
from app.models import Decision, User, DecisionStatusEnum

logger = logging.getLogger(__name__)

# In-memory: users awaiting feedback text after giving a score
# { telegram_id: decision_id }
_awaiting_feedback_text: dict[int, int] = {}


def get_awaiting_feedback() -> dict[int, int]:
    return _awaiting_feedback_text


async def send_feedback_requests(bot: Bot):
    """Find decisions completed 48h+ ago with no feedback and send rating requests."""
    cutoff = datetime.utcnow() - timedelta(hours=48)

    async with async_session_maker() as session:
        stmt = (
            select(Decision)
            .where(Decision.status.in_([DecisionStatusEnum.EXECUTED, DecisionStatusEnum.APPROVED]))
            .where(Decision.feedback_score.is_(None))
            .where(Decision.feedback_requested_at.is_(None))
            .where(Decision.completed_at <= cutoff)
        )
        result = await session.execute(stmt)
        decisions = result.scalars().all()

        for decision in decisions:
            submitter = await session.get(User, decision.submitter_id)
            if not submitter or not submitter.telegram_id:
                continue
            try:
                await bot.send_message(
                    chat_id=submitter.telegram_id,
                    text=(
                        f"\u200F📊 *משוב על החלטה #{decision.id} — Shan-AI*\n\n"
                        f"📋 *סיכום:* {decision.summary}\n"
                        f"🎯 *פעולה שבוצעה:* {decision.recommended_action}\n\n"
                        f"כיצד הסתיים הביצוע? שלח מספר בין 1 ל-5:\n\n"
                        f"1️⃣ — כישלון מוחלט\n"
                        f"2️⃣ — לא טוב\n"
                        f"3️⃣ — בסדר\n"
                        f"4️⃣ — טוב\n"
                        f"5️⃣ — מצוין"
                    ),
                    parse_mode="Markdown",
                )
                decision.feedback_requested_at = datetime.utcnow()
                await session.commit()
                logger.info(f"שלחתי בקשת פידבק להחלטה #{decision.id}")
            except Exception as e:
                logger.error(f"שגיאה בשליחת פידבק להחלטה #{decision.id}: {e}")


async def save_feedback_score(session, decision_id: int, score: int, submitter_telegram_id: int):
    """Save the numeric rating and mark user as awaiting text feedback."""
    decision = await session.get(Decision, decision_id)
    if not decision:
        return False
    decision.feedback_score = score
    await session.commit()
    _awaiting_feedback_text[submitter_telegram_id] = decision_id
    return True


async def save_feedback_text(session, decision_id: int, notes: str):
    """Save the text post-mortem and re-embed the decision with feedback context."""
    from app.services.embedding_service import embed

    decision = await session.get(Decision, decision_id)
    if not decision:
        return False

    decision.feedback_notes = notes

    # Re-embed with feedback context for better future similarity
    combined = (
        f"{decision.problem_description or ''} "
        f"{decision.summary or ''} "
        f"{decision.recommended_action or ''} "
        f"פידבק: {notes}"
    )
    try:
        decision.embedding = await embed(combined)
    except Exception as e:
        logger.warning(f"Re-embedding failed for decision #{decision_id}: {e}")

    await session.commit()
    logger.info(f"פידבק נשמר להחלטה #{decision_id}: ציון={decision.feedback_score}, הערות={notes[:50]}")
    return True


async def run_feedback_scheduler(bot: Bot):
    """Background task — checks for pending feedback every hour."""
    logger.info("מתחיל scheduler של פידבק 48 שעות")
    while True:
        try:
            await send_feedback_requests(bot)
        except Exception as e:
            logger.error(f"שגיאה ב-feedback scheduler: {e}")
        await asyncio.sleep(3600)
