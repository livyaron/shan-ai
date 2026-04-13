"""LLM Router — single entry point for all AI calls.

Routes each usage to Groq or Gemma based on admin config stored in DB.
Config is cached in-memory with a 30-second TTL.
"""

import logging
import random
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Per-task context vars: set after every llm_chat call so callers can read metadata
_ctx_provider: ContextVar[str] = ContextVar("llm_last_provider", default="")
_ctx_is_fallback: ContextVar[bool] = ContextVar("llm_last_is_fallback", default=False)


def get_last_llm_meta() -> tuple[str, bool]:
    """Return (provider_name, is_fallback) for the most recent llm_chat call in this task."""
    return _ctx_provider.get(), _ctx_is_fallback.get()

# In-memory cache: {usage_name: (provider, fallback, timestamp)}
_cache: dict[str, tuple[str, bool, float]] = {}
CACHE_TTL = 30  # seconds

# Hebrew labels shown in the admin UI
USAGE_LABELS: dict[str, str] = {
    "decision_analysis":     "ניתוח החלטה",
    "raci_suggestions":      "הצעות RACI (טקסט חופשי)",
    "raci_assignment":       "שיבוץ RACI להחלטה",
    "lesson_extraction":     "חילוץ לקחים",
    "knowledge_summary":     "סיכום ידע",
    "file_summary":          "סיכום קובץ",
    "rag_answer":            "מענה לשאלה (RAG)",
    "query_expansion":       "הרחבת שאילתה",
    "optimization":          "אופטימיזציה ולמידה",
    "distribution_suggestion": "הצעת חלוקת משימות",
    "project_brief":           "תקציר דו\"ח שבועי (פרויקטים)",
    "project_query":           "שאלה על פרויקטים",
    "message_summary":         "סיכום תשובת בוט ארוכה",
    "dashboard_analysis":      "ניתוח AI בדשבורד",
}


async def _get_config(usage: str) -> tuple[str, bool]:
    """Return (provider, fallback) for a usage, with 30-second in-memory cache."""
    now = time.monotonic()
    if usage in _cache:
        provider, fallback, ts = _cache[usage]
        if now - ts < CACHE_TTL:
            return provider, fallback

    try:
        from app.database import async_session_maker
        from app.models import LLMConfig
        from sqlalchemy import select

        async with async_session_maker() as session:
            row = await session.execute(
                select(LLMConfig).where(LLMConfig.usage_name == usage)
            )
            cfg = row.scalar_one_or_none()
            if cfg:
                _cache[usage] = (cfg.provider, cfg.fallback, now)
                return cfg.provider, cfg.fallback
    except Exception as e:
        logger.warning(f"llm_router: could not load config for '{usage}': {e}")

    # Default: groq with fallback enabled
    return "groq", True


def invalidate_cache() -> None:
    """Clear the config cache. Call this after admin changes LLM settings."""
    _cache.clear()
    logger.info("llm_router: cache invalidated")


async def llm_chat(usage: str, messages: list, **kwargs) -> str:
    """Route an AI call to the configured provider for this usage.

    Args:
        usage:    One of the keys in USAGE_LABELS (e.g. "decision_analysis")
        messages: OpenAI-style message list [{"role": ..., "content": ...}]
        **kwargs: Forwarded to the underlying client (max_tokens, temperature, json_mode)

    Returns:
        str: The model's response text (already stripped)
    """
    from app.services.groq_client import groq_chat
    from app.services.gemma_client import gemma_chat

    provider, fallback = await _get_config(usage)

    if provider == "groq":
        primary, secondary = groq_chat, gemma_chat
        primary_name, secondary_name = "Groq", "Gemma"
    elif provider == "gemma":
        primary, secondary = gemma_chat, groq_chat
        primary_name, secondary_name = "Gemma", "Groq"
    else:  # "auto" — random 50/50 for load distribution
        if random.random() < 0.5:
            primary, secondary = groq_chat, gemma_chat
            primary_name, secondary_name = "Groq (auto)", "Gemma"
        else:
            primary, secondary = gemma_chat, groq_chat
            primary_name, secondary_name = "Gemma (auto)", "Groq"

    try:
        result = await primary(messages, **kwargs)
        logger.debug(f"llm_router [{usage}]: served by {primary_name}")
        _ctx_provider.set(primary_name)
        _ctx_is_fallback.set(False)
        return result
    except Exception as e:
        if fallback:
            logger.warning(
                f"llm_router [{usage}]: {primary_name} failed ({type(e).__name__}: {e}), "
                f"falling back to {secondary_name}"
            )
            result = await secondary(messages, **kwargs)
            _ctx_provider.set(secondary_name)
            _ctx_is_fallback.set(True)
            return result
        raise
