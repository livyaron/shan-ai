"""Deferred-decision queue.

When a user submits a decision but every LLM provider is rate-limited / quota-
exhausted, we persist the raw text here instead of losing it. A background worker
(registered in eval_cron) retries analysis on an interval; when a provider frees
up it analyzes the text and PUSHES the AI preview back to the user via Telegram,
so they approve it exactly as in the normal interactive flow.
"""

import logging
from datetime import datetime

from sqlalchemy import select

from app.models import PendingDecision, User

logger = logging.getLogger(__name__)

# 5-min worker interval × 288 = 24h of retries before giving up on an item.
MAX_ATTEMPTS = 288
# Process a few per cycle; if quota is still down the first one fails and we stop,
# so this only matters once a provider is back.
BATCH_PER_CYCLE = 3


async def enqueue(session, *, user: User, telegram_id: int, raw_text: str,
                  conv_ctx: list | None) -> PendingDecision:
    """Persist a decision that couldn't be analyzed now. Returns the queued row."""
    row = PendingDecision(
        user_id=user.id,
        telegram_id=telegram_id,
        raw_text=raw_text,
        conv_ctx=conv_ctx or None,
        status="queued",
        attempts=0,
    )
    session.add(row)
    await session.commit()
    logger.info(f"pending_queue: enqueued decision id={row.id} for user={user.id}")
    return row


async def queue_depth(session) -> int:
    """Count items still waiting — used for status/visibility."""
    rows = (await session.execute(
        select(PendingDecision.id).where(PendingDecision.status == "queued")
    )).all()
    return len(rows)


async def process_pending_queue() -> None:
    """Worker: retry queued decisions oldest-first. Stops the cycle on the first
    overload (all providers down → no point hammering the rest)."""
    from app.database import async_session_maker
    from app.services.llm_router import is_overload_error

    async with async_session_maker() as session:
        items = (await session.execute(
            select(PendingDecision)
            .where(PendingDecision.status == "queued")
            .order_by(PendingDecision.created_at.asc())
            .limit(BATCH_PER_CYCLE)
        )).scalars().all()

        if not items:
            return
        logger.info(f"pending_queue: processing {len(items)} queued decision(s)")

        for item in items:
            try:
                await _process_one(session, item)
            except Exception as e:
                item.attempts += 1
                item.last_attempt_at = datetime.utcnow()
                if is_overload_error(e):
                    # Providers still exhausted — give up this cycle, retry next tick.
                    if item.attempts >= MAX_ATTEMPTS:
                        await _fail(session, item, gave_up=True)
                    else:
                        await session.commit()
                        logger.info(
                            f"pending_queue: still overloaded, item={item.id} "
                            f"attempt {item.attempts}/{MAX_ATTEMPTS} — will retry"
                        )
                    return  # stop the whole cycle on overload
                # Non-quota error — log, count it, keep going to the next item.
                logger.error(f"pending_queue: item={item.id} failed: {e}", exc_info=True)
                if item.attempts >= MAX_ATTEMPTS:
                    await _fail(session, item, gave_up=False)
                else:
                    await session.commit()


async def _process_one(session, item: PendingDecision) -> None:
    """Analyze one queued decision and push the preview to the user.

    Raises on overload so the caller can stop the cycle and retry later.
    """
    from app.services.claude_service import ClaudeService
    from app.services.decision_service import DecisionService
    from app.services.telegram_polling import (
        telegram_bot, _build_preview_text, _decision_preview_keyboard,
        _user_has_manager,
    )
    from app.services.telegram_state import _awaiting_decision_preview

    bot = (telegram_bot.application.bot
           if telegram_bot.application and telegram_bot.application.bot else None)
    if bot is None:
        raise RuntimeError("pending_queue: telegram bot not available")

    user = await session.get(User, item.user_id)
    if not user:
        await _fail(session, item, gave_up=False)
        return

    text = item.raw_text
    conv_ctx = item.conv_ctx or []

    # 1) Classify (LLM — may overload, which propagates up).
    classify_result = await ClaudeService().classify(text)
    verdict = classify_result.get("verdict", "DECISION")

    if verdict in ("NOT_DECISION", "UNCLEAR"):
        # Queued under the assumption it was a decision; on replay it isn't clearly one.
        await bot.send_message(
            chat_id=item.telegram_id,
            text=("‏ℹ️ ההודעה שהמתינה בתור נותחה, אך לא זוהתה כהחלטה ברורה. "
                  "אם זו החלטה — שלח אותה שוב כעת (המערכת פנויה)."),
            parse_mode="HTML",
        )
        item.status = "done"
        item.processed_at = datetime.utcnow()
        item.attempts += 1
        await session.commit()
        logger.info(f"pending_queue: item={item.id} resolved as {verdict}")
        return

    # 2) DECISION — analyze (LLM — may overload) then push preview for approval.
    svc = DecisionService(session, telegram_bot.application)
    pre_result = await svc.analyze_only(user, text, conversation_context=conv_ctx)

    _awaiting_decision_preview[item.telegram_id] = {
        "text": text,
        "result": pre_result,
        "user_has_manager": _user_has_manager(user),
    }
    preview_text = (
        "‏✅ <b>ההחלטה שהמתינה בתור נותחה כעת:</b>\n\n" + _build_preview_text(pre_result)
    )
    await bot.send_message(
        chat_id=item.telegram_id,
        text=preview_text,
        parse_mode="HTML",
        reply_markup=_decision_preview_keyboard(),
    )
    item.status = "done"
    item.processed_at = datetime.utcnow()
    item.attempts += 1
    await session.commit()
    logger.info(f"pending_queue: item={item.id} analyzed + preview pushed")


async def _fail(session, item: PendingDecision, *, gave_up: bool) -> None:
    """Mark an item failed and tell the user to resubmit."""
    item.status = "failed"
    item.last_attempt_at = datetime.utcnow()
    await session.commit()
    try:
        from app.services.telegram_polling import telegram_bot
        bot = (telegram_bot.application.bot
               if telegram_bot.application and telegram_bot.application.bot else None)
        if bot:
            reason = ("‏⚠️ לא הצלחנו לנתח החלטה שהמתנת לה (המערכת עמוסה זמן רב). "
                      if gave_up else
                      "‏⚠️ אירעה שגיאה בעיבוד החלטה שהמתינה בתור. ")
            await bot.send_message(
                chat_id=item.telegram_id,
                text=reason + "אנא שלח אותה שוב.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning(f"pending_queue: failed to notify user on give-up: {e}")
    logger.warning(f"pending_queue: item={item.id} marked failed (gave_up={gave_up})")
