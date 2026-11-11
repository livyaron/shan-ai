"""Telegram webhook endpoint."""

import asyncio
import hmac
import logging
from fastapi import APIRouter, Request, Response, HTTPException

from telegram import Update

from app.config import settings
from app.services.gemma_client import redact
from app.services.telegram_polling import telegram_bot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/webhook_status")
async def webhook_status():
    """Why is the bot silent? — answered from outside Railway.

    The container can be entirely healthy while Telegram holds no webhook for
    this bot, and no other endpoint can tell the two apart. `healthy: false`
    here means Telegram has nowhere to deliver messages to; `last_error_message`
    is Telegram's own reason when it does have somewhere and delivery fails.
    """
    try:
        status = await telegram_bot.webhook_status()
    except Exception as e:
        return {"ok": False, "error": redact(str(e))}
    status["ok"] = True
    return status


@router.get("/webhook_repair")
async def webhook_repair():
    """Re-register the webhook now, without waiting for the watchdog.

    Safe to hit at any time: it only ever registers the URL the app computes
    for itself (`settings.effective_webhook_url`), never one from the request,
    and it does nothing when the registration is already correct.
    """
    try:
        return {"ok": True, **(await telegram_bot.ensure_webhook())}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))}


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receive updates from Telegram via webhook.

    Telegram sends POST requests to this endpoint with message updates.
    The secret token header is always validated (settings.webhook_secret).
    """
    expected = settings.webhook_secret
    if expected:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(incoming_token, expected):
            logger.warning("Webhook request rejected: invalid secret token")
            raise HTTPException(status_code=403, detail="Invalid secret token")

    try:
        data = await request.json()
        update = Update.de_json(data, telegram_bot.application.bot)
        # Process in background so Telegram gets 200 immediately.
        # Awaiting process_update delays the response; if LLM calls exceed
        # Telegram's ~30s timeout, Telegram retries the same update — causing
        # the bot to reply multiple times for a single message.
        asyncio.create_task(telegram_bot.application.process_update(update))
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)

    # Always return 200 immediately so Telegram never retries this update.
    return Response(status_code=200)
