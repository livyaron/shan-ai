"""Google AI Studio (Gemma 4) client — mirrors groq_chat signature."""

import asyncio
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# gemini-2.5-flash first: it honors responseMimeType (real enforced JSON mode) and
# does NOT leak chain-of-thought, unlike the gemma-4 models which emit a reasoning
# preamble that breaks JSON parsing. gemma-4 kept as text/backup.
# (gemini-2.0-flash omitted — free-tier quota is 0 on this key → 429.)
GEMMA_MODELS = [
    "gemini-2.5-flash",        # clean JSON, no thinking leak
    "gemma-4-31b-it",          # backup
    "gemma-4-26b-a4b-it",      # MoE variant, separate quota
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
    """Extract a clean JSON value from a model response.

    Handles markdown code fences AND a chain-of-thought preamble (the gemma-4
    models leak reasoning lines before the JSON). Slices from the first '{' or
    '[' to its matching closing bracket so leading/trailing prose is dropped.
    """
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Find the first JSON opener and slice to its matching close (ignoring
    # brackets inside strings) — strips any thinking preamble/trailing text.
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        return text
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text[start:]  # unbalanced — return best effort


def _strip_thinking(text: str) -> str:
    """Strip Gemma chain-of-thought preamble from non-JSON responses.

    Gemma sometimes emits internal reasoning lines (English meta-commentary,
    lines starting with '* ', 'Keep it', 'Since these', etc.) before the
    actual Hebrew answer.  We detect a Hebrew answer block and return only that.
    """
    # If the text is mostly Hebrew (>30% Hebrew chars) from the start — no stripping needed
    hebrew_chars = sum(1 for c in text[:200] if "\u05d0" <= c <= "\u05ea")
    if hebrew_chars / max(len(text[:200]), 1) > 0.3:
        return text

    # Find first line that starts with a Hebrew character or Hebrew bullet (•/-)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and ("\u05d0" <= stripped[0] <= "\u05ea" or
                         (len(stripped) > 1 and stripped[0] in ("•", "-", "*") and
                          "\u05d0" <= stripped[1:].lstrip()[0:1].ljust(1)[0] <= "\u05ea")):
            return "\n".join(lines[i:]).strip()

    return text  # nothing to strip — return as-is


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

    gen_config: dict = {
        "maxOutputTokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        # Enforced JSON mode (honored by gemini models; ignored harmlessly by gemma).
        gen_config["responseMimeType"] = "application/json"
    payload: dict = {
        "contents": contents,
        "generationConfig": gen_config,
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
                parts = data["candidates"][0]["content"]["parts"]
                text_parts = [p["text"] for p in parts if not p.get("thought") and "text" in p]
                text = " ".join(text_parts).strip()
                if json_mode:
                    text = _strip_json_fences(text)
                else:
                    text = _strip_thinking(text)
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
            except (KeyError, IndexError, ValueError) as e:
                # ValueError = empty response (model returned nothing usable).
                # Treat like a transient failure and try the next model instead of
                # aborting the whole chain — an empty gemma-26b shouldn't kill fallback.
                last_error = e
                logger.warning(f"gemma_chat: unexpected/empty response from {model}: {e}")
                if i < len(model_list) - 1:
                    await asyncio.sleep(1)

    raise last_error or RuntimeError("gemma_chat: all models exhausted")
