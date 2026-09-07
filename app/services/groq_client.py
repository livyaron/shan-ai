"""Shared Groq client with automatic fallback across models on rate limit."""

import asyncio
import logging
from groq import AsyncGroq, RateLimitError
from app.config import settings

logger = logging.getLogger(__name__)

# Tried in order — each has a separate rate-limit bucket on Groq.
#
# 2026-09-04: both previous entries (llama-4-scout, llama-3.3-70b-versatile) were
# retired by Groq and answered 404 model_not_found on every request for weeks.
# `/llm מודלים` asks the account what it can actually call; of the 14 ids it
# returned, only these two are general chat models worth routing to:
#   - openai/gpt-oss-120b — the large one, for the reasoning-heavy usages
#   - openai/gpt-oss-20b  — smaller and faster, the in-provider backup
# Deliberately NOT used:
#   - qwen/qwen3.6-27b, qwen/qwen3.8-27b — same family as the qwen3-32b that was
#     dropped before: thinking mode leaks chain-of-thought into the answer, which
#     is the exact failure that put a two-letter stub on the wall display.
#   - groq/compound, groq/compound-mini — agentic systems with built-in tools,
#     not plain chat completions.
#   - whisper-* (speech), orpheus-* (TTS), llama-prompt-guard-* (classifiers),
#     allam-2-7b (Arabic-focused, 7b) — wrong tool for this app's calls.
MODELS = [
    "openai/gpt-oss-120b",   # quality first — summaries, decisions, RAG answers
    "openai/gpt-oss-20b",    # faster fallback inside the same provider
]


# One client for the whole process. Building an AsyncGroq per call meant a fresh
# httpx pool and TLS handshake on every LLM request (~100-300ms of pure overhead)
# and leaked the socket, since nothing ever closed it.
_shared_client: AsyncGroq | None = None


def get_client() -> AsyncGroq:
    global _shared_client
    if not settings.GROQ_API_KEY:
        raise RuntimeError("groq_chat: GROQ_API_KEY is not set — the primary provider "
                           "is unavailable.")
    if _shared_client is None:
        _shared_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    return _shared_client


async def groq_chat(
    messages: list,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    client: AsyncGroq = None,
    models: list[str] | None = None,
) -> str:
    """Call Groq with automatic fallback across models on 429 rate limit.

    Returns the response text content (already stripped).
    Raises the last RateLimitError if all models are exhausted.

    models: override the default MODELS list (e.g. to start with a fast model).
    """
    _client = client or get_client()
    kwargs = dict(messages=messages, max_tokens=max_tokens, temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    model_list = models or MODELS
    # 2 rounds, not 3: each model has a separate rate-limit bucket, so we try them
    # back-to-back with no inter-model sleep. When the whole pass is rate-limited we
    # want to hand control back to llm_router quickly so it can fail over to Gemma —
    # a long internal backoff here just adds seconds of latency the user feels before
    # the other provider is even tried.
    MAX_ROUNDS = 2
    last_error = None
    # A model that answered 404/400 is not coming back inside this call — asking
    # it again on round 2 only burns latency.
    dead: set[str] = set()
    for rnd in range(MAX_ROUNDS):
        for i, model in enumerate(model_list):
            if model in dead:
                continue
            try:
                resp = await _client.chat.completions.create(model=model, **kwargs)
                if i > 0 or rnd > 0:
                    logger.warning(f"Used fallback model [round {rnd}, {i}] {model}")
                return resp.choices[0].message.content.strip()
            except RateLimitError as e:
                last_error = e
                logger.warning(f"Rate limit on {model} (round {rnd})")
                # No sleep between models — different buckets, retrying the next
                # one immediately is free.
            except Exception as e:
                # Was: `raise`. One decommissioned model at the head of the list
                # therefore took the WHOLE provider down without the healthy model
                # behind it ever being tried — which is exactly how a 404 on
                # llama-4-scout made every AI feature in the app fail while
                # llama-3.3-70b sat there working. A per-model failure is now a
                # per-model failure; only an empty list of survivors is an outage.
                last_error = e
                dead.add(model)
                logger.warning(f"Model {model} failed ({type(e).__name__}: {str(e)[:120]}) — trying the next")
        if rnd < MAX_ROUNDS - 1 and len(dead) < len(model_list):
            await asyncio.sleep(1)   # brief pause before one more full pass
    raise last_error


async def list_models() -> list[str]:
    """Model ids this API key can actually use, straight from Groq.

    Hardcoding a model list means a provider retirement shows up as a 404 on every
    request with no way to see what replaced it — which is exactly what happened
    to both entries in MODELS. Asking the account is a two-second answer.
    """
    resp = await get_client().models.list()
    return sorted(m.id for m in resp.data)
