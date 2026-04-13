"""Telegram webhook endpoint."""

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
        await telegram_bot.application.process_update(update)
        return Response(status_code=200)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        # Return 200 anyway so Telegram does not retry the failed update
        return Response(status_code=200)
