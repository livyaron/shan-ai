"""Google AI Studio (Gemma 4) client — mirrors groq_chat signature."""

import asyncio
import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GEMMA_MODELS = [
    "gemma-4-31b-it",          # best quality
    "gemma-4-26b-a4b-it",      # MoE variant, separate quota
    "gemma-3-27b-it",          # fallback
]

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_google_format(messages: list) -> tuple[str | None, list]:
    """Convert OpenAI-style messages to Google AI contents + system_instruction."""
    system_parts: list[str] = []
    contents: list[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
        else:  # user
            contents.append({"role": "user", "parts": [{"text": content}]})

    system_text = "\n\n".join(system_parts) if system_parts else None
    return system_text, contents


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences so JSON can be parsed cleanly."""
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


async def gemma_chat(
    messages: list,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
    models: list[str] | None = None,
) -> str:
    """Call Google AI Studio (Gemma 4) with automatic model fallback on quota errors.

    Signature matches groq_chat so llm_router can call either transparently.
    """
    system_text, contents = _to_google_format(messages)

    if json_mode:
        json_instruction = (
            "Return ONLY valid JSON. No explanation, no markdown, no code fences."
        )
        system_text = f"{system_text}\n\n{json_instruction}" if system_text else json_instruction

    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    if system_text:
        payload["system_instruction"] = {"parts": [{"text": system_text}]}

    model_list = models or GEMMA_MODELS
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, model in enumerate(model_list):
            try:
                url = f"{_BASE}/{model}:generateContent?key={settings.GOOGLE_AI_API_KEY}"
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if json_mode:
                    text = _strip_json_fences(text)
                if not text:
                    raise ValueError(f"gemma_chat: empty response from {model} (json_mode={json_mode})")
                if i > 0:
                    logger.warning(f"gemma_chat: used fallback model [{i}] {model}")
                return text
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503, 500):
                    last_error = e
                    logger.warning(
                        f"gemma_chat: quota/error on {model}"
                        + (", trying next..." if i < len(model_list) - 1 else "")
                    )
                    if i < len(model_list) - 1:
                        await asyncio.sleep(1)
                else:
                    raise
            except (KeyError, IndexError) as e:
                last_error = e
                logger.warning(f"gemma_chat: unexpected response from {model}: {e}")
                if i < len(model_list) - 1:
                    await asyncio.sleep(1)

    raise last_error or RuntimeError("gemma_chat: all models exhausted")
