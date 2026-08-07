"""Telegram webhook endpoint."""

import asyncio
import logging
from fastapi import APIRouter, Request, Response, HTTPException

from telegram import Update

from app.config import settings
from app.services.telegram_polling import telegram_bot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get("/_diag_timing")
async def diag_timing():
    """TEMPORARY read-only latency + decision-health probe (no PII).

    Returns anonymous per-question latency percentiles from route_traces and a
    small sample of recent decisions (id/type/status/has-vector only). Used to
    diagnose bot response time from outside Railway. Safe to remove afterwards.
    """
    from app.database import async_session_maker
    from app.models import RouteTrace, Decision
    from sqlalchemy import select, desc

    out: dict = {}
    async with async_session_maker() as s:
        rows = (await s.execute(
            select(RouteTrace).order_by(desc(RouteTrace.created_at)).limit(50)
        )).scalars().all()
        ms = sorted(r.ms_total for r in rows if r.ms_total is not None)
        def _pct(p: float):
            if not ms:
                return None
            return ms[min(len(ms) - 1, int(p * len(ms)))]
        out["route_traces"] = {
            "count": len(ms),
            "p50_ms": _pct(0.50),
            "p95_ms": _pct(0.95),
            "max_ms": ms[-1] if ms else None,
            "recent": [
                {"path": r.path, "ms_total": r.ms_total,
                 "at": r.created_at.isoformat() if r.created_at else None}
                for r in rows[:8]
            ],
        }
        decs = (await s.execute(
            select(Decision).order_by(desc(Decision.created_at)).limit(8)
        )).scalars().all()
        out["decisions_recent"] = [
            {"id": d.id, "type": d.type.value if d.type else None,
             "status": d.status.value if d.status else None,
             "has_vector": d.embedding is not None,
             "at": d.created_at.isoformat() if d.created_at else None}
            for d in decs
        ]
    return out


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
