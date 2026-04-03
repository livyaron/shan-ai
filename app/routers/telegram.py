"""Telegram webhook endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.services.telegram_service import TelegramService

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    update: dict,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Receive updates from Telegram webhook.

    Telegram sends POST requests to this endpoint with message updates.
    """
    try:
        service = TelegramService(session)
        await service.handle_incoming_message(update)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook processing error: {str(e)}",
        )
