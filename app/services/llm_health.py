"""Is each LLM provider actually reachable, and is the backup real?

llm_router falls back Groq → Gemma (and back) on any primary failure, but the
backup is only ever exercised *during* an outage — so a missing GOOGLE_AI_API_KEY
or a dead free-tier quota stays invisible until the moment it is needed. This
module makes that checkable on demand.
"""

import logging
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.gemma_client import redact

logger = logging.getLogger(__name__)

# Cheap, but not so cheap it lies. At 8 tokens the gemma models spent the whole
# budget on their chain-of-thought preamble and returned a candidate with no text
# at all — so the probe reported the provider dead when the only thing that had
# failed was the probe's own ceiling. Same trap as the wall's hourly line.
_PROBE_MESSAGES = [{"role": "user", "content": "Reply with the single word: OK"}]
_PROBE_MAX_TOKENS = 256


async def _probe_models(chat, models: list[str]) -> dict[str, str]:
    """One-word round-trip per model. Without this the report says "groq is down"
    when the truth is "the first model in the list was decommissioned"."""
    out: dict[str, str] = {}
    for model in models:
        try:
            await chat(_PROBE_MESSAGES, max_tokens=_PROBE_MAX_TOKENS,
                       temperature=0, models=[model])
            out[model] = "ok"
        except Exception as e:
            out[model] = redact(f"{type(e).__name__}: {str(e)}")[:120]
    return out


async def _probe_groq() -> dict:
    from app.services.groq_client import groq_chat, MODELS

    t0 = perf_counter()
    try:
        text = await groq_chat(_PROBE_MESSAGES, max_tokens=_PROBE_MAX_TOKENS, temperature=0)
        return {"status": "ok", "ms": int((perf_counter() - t0) * 1000),
                "reply": (text or "")[:40],
                "per_model": await _probe_models(groq_chat, MODELS)}
    except Exception as e:
        return {"status": "error", "ms": int((perf_counter() - t0) * 1000),
                # Redacted: the Google AI key rides in the request URL, so an
                # un-scrubbed error string published this endpoint's own
                # credentials to anyone who opened it.
                "error": redact(f"{type(e).__name__}: {str(e)}")[:200],
                "per_model": await _probe_models(groq_chat, MODELS)}


async def _probe_gemma() -> dict:
    from app.services.gemma_client import gemma_chat, GEMMA_MODELS

    t0 = perf_counter()
    try:
        text = await gemma_chat(_PROBE_MESSAGES, max_tokens=_PROBE_MAX_TOKENS, temperature=0)
        return {"status": "ok", "ms": int((perf_counter() - t0) * 1000),
                "reply": (text or "")[:40],
                "per_model": await _probe_models(gemma_chat, GEMMA_MODELS)}
    except Exception as e:
        return {"status": "error", "ms": int((perf_counter() - t0) * 1000),
                "error": redact(f"{type(e).__name__}: {str(e)}")[:200],
                "per_model": await _probe_models(gemma_chat, GEMMA_MODELS)}


async def check_providers(probe: bool = False) -> dict:
    """Per-provider health. `probe=False` only checks configuration (free).

    `probe=True` spends a handful of tokens per provider on a one-word round-trip
    — the only way to catch an exhausted quota or a revoked key before it matters.
    """
    from app.services.groq_client import MODELS as GROQ_MODELS
    from app.services.gemma_client import GEMMA_MODELS

    out = {
        "groq": {"configured": bool(settings.GROQ_API_KEY), "models": GROQ_MODELS},
        "gemma": {"configured": bool(settings.GOOGLE_AI_API_KEY), "models": GEMMA_MODELS},
    }

    for name, prober in (("groq", _probe_groq), ("gemma", _probe_gemma)):
        if not out[name]["configured"]:
            out[name]["status"] = "not_configured"
            continue
        if not probe:
            out[name]["status"] = "not_probed"
            continue
        out[name].update(await prober())

    return out


async def check_routing(session: AsyncSession) -> dict:
    """How the configured usages are routed, and where the backup is switched off."""
    from app.models import LLMConfig
    from app.services.llm_router import USAGE_LABELS

    rows = {c.usage_name: c for c in (await session.scalars(select(LLMConfig))).all()}

    by_provider: dict[str, int] = {}
    fallback_off: list[str] = []
    for usage in USAGE_LABELS:
        cfg = rows.get(usage)
        # Same defaults llm_router._get_config falls back to when a row is missing.
        provider = cfg.provider if cfg else "groq"
        fallback = cfg.fallback if cfg else True
        by_provider[provider] = by_provider.get(provider, 0) + 1
        if not fallback:
            fallback_off.append(usage)

    return {"by_provider": by_provider, "fallback_disabled_for": sorted(fallback_off)}


def summarize(providers: dict, routing: dict | None = None) -> dict:
    """One-line verdict: is there a working primary, and is there a real backup?"""
    def _usable(p: dict) -> bool:
        # "not_probed" counts as usable: configured, just not spot-checked.
        if not p.get("configured", False):
            return False
        if p.get("status") != "error":
            return True
        # A provider whose call failed but which still has ONE live model is not
        # down — that is exactly the state a dead model id at the head of the list
        # produces, and calling it "no provider available" sends people to check
        # the account instead of the model list.
        return any(v == "ok" for v in (p.get("per_model") or {}).values())

    usable = [name for name, p in providers.items() if _usable(p)]
    healthy = bool(usable)
    backup = len(usable) >= 2

    if not healthy:
        verdict = "‏❌ אין ספק LLM זמין — כל הקריאות ייכשלו."
    elif not backup:
        verdict = (f"‏⚠️ רק ספק אחד זמין ({usable[0]}) — אין גיבוי אם הוא ייפול.")
    else:
        verdict = "‏✅ שני הספקים זמינים — יש גיבוי."

    out = {"healthy": healthy, "backup_available": backup,
           "usable_providers": usable, "verdict": verdict}

    if routing and routing.get("fallback_disabled_for"):
        n = len(routing["fallback_disabled_for"])
        out["warning"] = (f"‏⚠️ ל-{n} שימושים כובה המעבר לגיבוי — הם ייכשלו "
                          f"אם הספק הראשי שלהם ייפול.")
    return out


# ── Hebrew report ──────────────────────────────────────────────────────────

_PROVIDER_LABELS = {"groq": "Groq", "gemma": "Gemma (Google)"}

# A provider error is a full API payload; on a phone screen only the head of it
# is readable, and the rest pushes the useful lines off the message.
_ERR_CHARS = 110


def _status_icon(status: str) -> str:
    return {"ok": "✅", "error": "❌", "not_configured": "🔑", "not_probed": "➖"}.get(status, "❔")


def format_report_he(providers: dict, routing: dict, probed: bool) -> str:
    """The health check as one Telegram message. Pure formatting — no I/O, no bot.

    Per-model lines are the point: "Groq is down" sent people looking at the
    account, when the truth was one decommissioned model id at the head of a list
    with a working model behind it.
    """
    import datetime
    import html as _html
    from app.services import missions_menu_service as oms

    stamp = datetime.datetime.now(oms._IL_TZ).strftime("%d/%m %H:%M")
    mode = "בדיקה חיה" if probed else "בדיקת הגדרות בלבד"
    lines = ["‏🩺 <b>תקינות ספקי ה-AI</b>", f"<i>{mode} · {stamp}</i>", ""]

    if probed:
        lines.append(summarize(providers, routing)["verdict"])
    else:
        # A configuration check proves keys exist, nothing more. Saying "both
        # providers are available" off the back of it is a promise this check
        # cannot make — and it was made while one provider's first model was
        # answering 404 on every call.
        configured = [k for k, v in providers.items() if v.get("configured")]
        if not configured:
            lines.append("‏❌ אין מפתח לאף ספק — כל הקריאות ייכשלו.")
        else:
            lines.append(f"‏🔑 {len(configured)} ספקים מוגדרים. "
                         "האם הם עונים בפועל — לא נבדק.")
    lines.append("")

    for key, data in providers.items():
        label = _PROVIDER_LABELS.get(key, key)
        status = data.get("status", "unknown")
        head = f"{_status_icon(status)} <b>{label}</b>"
        if not data.get("configured"):
            lines.append(f"{head} — אין מפתח מוגדר")
            lines.append("")
            continue
        if status == "not_probed":
            lines.append(f"{head} — מוגדר, לא נבדק בפועל")
            lines.append("")
            continue
        if status == "ok":
            head += f" — תקין ({data.get('ms', 0)} מ״ש)"
        else:
            head += " — נכשל"
        lines.append(head)

        per_model = data.get("per_model") or {}
        for model, result in per_model.items():
            if result == "ok":
                lines.append(f"  ✅ <code>{_html.escape(model)}</code>")
            else:
                lines.append(f"  ❌ <code>{_html.escape(model)}</code> — "
                             f"{_html.escape(redact(result)[:_ERR_CHARS])}")
        if not per_model and status == "error":
            lines.append(f"  {_html.escape(redact(str(data.get('error', '')))[:_ERR_CHARS])}")
        lines.append("")

    if routing:
        by = routing.get("by_provider", {})
        spread = " · ".join(f"{_PROVIDER_LABELS.get(k, k)}: {v}" for k, v in sorted(by.items()))
        lines.append(f"<b>ניתוב שימושים</b> — {spread}")
        off = routing.get("fallback_disabled_for") or []
        if off:
            lines.append(f"‏⚠️ גיבוי מכובה ב-{len(off)} שימושים: {', '.join(off[:5])}")

    if not probed:
        lines.append("")
        lines.append("<i>‏/llm בדיקה — מריץ קריאה אמיתית לכל מודל.</i>")

    return "\n".join(lines).strip()
