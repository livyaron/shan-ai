"""Shared Groq client with automatic fallback across models on rate limit."""

import asyncio
import logging
from groq import AsyncGroq, RateLimitError
from app.config import settings

logger = logging.getLogger(__name__)

# Tried in order — each has a separate rate-limit bucket on Groq.
# scout FIRST: its 30k TPM fits a typical RAG/decision request (~8.5k tokens) in one
# shot. 70b's 12k TPM 429s on big context almost every time, then falls to scout
# anyway — so leading with 70b doubled token spend per call (a 429'd request still
# counts against the daily TPD budget). scout-first halves that waste. 70b kept as a
# quality backup for the rare call small enough to fit its bucket.
# 8b dropped: free-tier TPM only 6,000 — too small, 429s every time.
# Callers needing a tiny+fast model can still pass models=["llama-3.1-8b-instant"].
MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",  # 30k TPM — most headroom, fits big context
    "llama-3.3-70b-versatile",                    # higher quality, 12k TPM — backup
    # qwen/qwen3-32b removed — thinking mode leaks chain-of-thought into answers
]


def get_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


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
    MAX_ROUNDS = 3
    last_error = None
    for rnd in range(MAX_ROUNDS):
        for i, model in enumerate(model_list):
            try:
                resp = await _client.chat.completions.create(model=model, **kwargs)
                if i > 0 or rnd > 0:
                    logger.warning(f"Used fallback model [round {rnd}, {i}] {model}")
                return resp.choices[0].message.content.strip()
            except RateLimitError as e:
                last_error = e
                logger.warning(f"Rate limit on {model} (round {rnd})")
                if i < len(model_list) - 1:
                    await asyncio.sleep(1)
            except Exception:
                raise
        if rnd < MAX_ROUNDS - 1:
            await asyncio.sleep(2 ** (rnd + 1))   # 2s, 4s between full-pass rounds
    raise last_error
