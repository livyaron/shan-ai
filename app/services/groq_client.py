"""Shared Groq client with automatic fallback across models on rate limit."""

import asyncio
import logging
import re
import time
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


# A 429 says when the bucket reopens ("Please try again in 7m44.4s"). Until then
# the model is skipped instead of being asked again: with the day budget spent,
# every call used to walk both models twice (plus the SDK's own retries) before
# llm_router could fail over — the routing call of one real question took 33s
# and intent detection up to 107s that morning (2026-11-12).
MIN_COOLDOWN_SECONDS = 5          # shorter waits are the per-minute bucket: round 2 handles them
MAX_COOLDOWN_SECONDS = 30 * 60    # re-probe at least every half hour
_RETRY_IN = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)
_cooling: dict[str, tuple[float, Exception]] = {}


def retry_after_seconds(error: Exception) -> float:
    """Seconds until the bucket reopens, read from Groq's own 429 text; 0 if it does not say."""
    m = _RETRY_IN.search(str(error))
    if not m or not any(m.groups()):
        return 0.0
    h, mnt, sec = m.groups()
    return int(h or 0) * 3600 + int(mnt or 0) * 60 + float(sec or 0)


def _cooling_error(model: str, now: float) -> Exception | None:
    entry = _cooling.get(model)
    if entry is None:
        return None
    if now >= entry[0]:
        _cooling.pop(model, None)
        return None
    return entry[1]


async def groq_chat(
    messages: list,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    client: AsyncGroq = None,
    models: list[str] | None = None,
    reasoning_effort: str | None = None,
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
    if reasoning_effort:
        # gpt-oss spends its reasoning out of max_tokens; "low" leaves the room
        # to the answer. extra_body works on every SDK version.
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

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
        tried = False
        for i, model in enumerate(model_list):
            if model in dead:
                continue
            cooled = _cooling_error(model, time.monotonic())
            if cooled is not None:
                last_error = cooled
                continue
            tried = True
            try:
                resp = await _client.chat.completions.create(model=model, **kwargs)
                if i > 0 or rnd > 0:
                    logger.warning(f"Used fallback model [round {rnd}, {i}] {model}")
                return resp.choices[0].message.content.strip()
            except RateLimitError as e:
                last_error = e
                wait = retry_after_seconds(e)
                if wait >= MIN_COOLDOWN_SECONDS:
                    cooldown = min(wait, MAX_COOLDOWN_SECONDS)
                    _cooling[model] = (time.monotonic() + cooldown, e)
                    logger.warning(f"Rate limit on {model}: skipping it for {int(cooldown)}s")
                else:
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
        _now = time.monotonic()
        if not tried or all(m in dead or _cooling_error(m, _now) is not None for m in model_list):
            break   # every model is dead or cooling — fail over now, nothing to wait for
        if rnd < MAX_ROUNDS - 1:
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
