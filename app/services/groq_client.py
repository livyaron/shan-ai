"""Shared Groq client with automatic fallback across models on rate limit."""

import asyncio
import logging
from groq import AsyncGroq, RateLimitError
from app.config import settings

logger = logging.getLogger(__name__)

# Tried in order — each has a separate rate-limit bucket on Groq
MODELS = [
    "llama-3.3-70b-versatile",                    # best quality
    "meta-llama/llama-4-scout-17b-16e-instruct",  # separate quota
    "llama-3.1-8b-instant",                       # fast, separate quota
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
    last_error = None
    for i, model in enumerate(model_list):
        try:
            resp = await _client.chat.completions.create(model=model, **kwargs)
            if i > 0:
                logger.warning(f"Used fallback model [{i}] {model}")
            return resp.choices[0].message.content.strip()
        except RateLimitError as e:
            last_error = e
            logger.warning(f"Rate limit on {model}" + (", trying next..." if i < len(model_list) - 1 else ""))
            if i < len(model_list) - 1:
                await asyncio.sleep(1)
        except Exception:
            raise

    raise last_error
