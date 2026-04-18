"""Telegram webhook endpoint."""

import asyncio
import logging
from fastapi import APIRouter, Request, Response, HTTPException

from telegram import Update

from app.config import settings
from app.services.telegram_polling import telegram_bot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receive updates from Telegram via webhook.

    Telegram sends POST requests to this endpoint with message updates.
    An optional secret token header is validated when WEBHOOK_SECRET_TOKEN is set.
    """
    # Validate secret token if configured
    if settings.WEBHOOK_SECRET_TOKEN:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming_token != settings.WEBHOOK_SECRET_TOKEN:
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
