"""LLM Config router — admin page to switch AI providers per usage."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import LLMConfig, User
from app.routers.login import get_current_user
from app.services.llm_router import USAGE_LABELS, invalidate_cache

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/dashboard", tags=["llm-config"])


@router.get("/llm-config", response_class=HTMLResponse)
async def llm_config_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    rows = await session.execute(select(LLMConfig).order_by(LLMConfig.usage_name))
    configs = {cfg.usage_name: cfg for cfg in rows.scalars().all()}

    # Build display list in a stable order
    items = []
    for usage_name, label_he in USAGE_LABELS.items():
        cfg = configs.get(usage_name)
        items.append(
            {
                "usage_name": usage_name,
                "label_he": label_he,
                "provider": cfg.provider if cfg else "groq",
                "fallback": cfg.fallback if cfg else True,
                "updated_at": cfg.updated_at if cfg else None,
                "updated_by": cfg.updated_by if cfg else None,
            }
        )

    return templates.TemplateResponse(
        "llm_config.html",
        {"request": request, "current_user": current_user, "items": items},
    )


@router.post("/llm-config")
async def llm_config_save(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Save provider choices submitted from the admin page."""
    form = await request.form()

    for usage_name in USAGE_LABELS:
        provider = form.get(f"provider_{usage_name}", "groq")
        fallback = form.get(f"fallback_{usage_name}") == "on"

        row = await session.execute(
            select(LLMConfig).where(LLMConfig.usage_name == usage_name)
        )
        cfg = row.scalar_one_or_none()

        if cfg:
            cfg.provider = provider
            cfg.fallback = fallback
            cfg.updated_at = datetime.utcnow()
            cfg.updated_by = current_user.username
        else:
            session.add(
                LLMConfig(
                    usage_name=usage_name,
                    provider=provider,
                    fallback=fallback,
                    label_he=USAGE_LABELS[usage_name],
                    updated_at=datetime.utcnow(),
                    updated_by=current_user.username,
                )
            )

    await session.commit()
    invalidate_cache()
    logger.info(f"LLM config saved by {current_user.username}")

    return RedirectResponse(url="/dashboard/llm-config?saved=1", status_code=303)
