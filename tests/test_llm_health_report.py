"""/llm — the provider health check as a Telegram message.

DB-free and bot-free: the formatter is a pure function over the same dicts the
web endpoint returns, so the wording is testable without Postgres or Telegram.
"""
from app.services import llm_health as health

PRODUCTION_SHAPE = {
    "groq": {
        "configured": True, "status": "error", "ms": 54,
        "error": "NotFoundError: 404 - The model `llama-4-scout` does not exist",
        "per_model": {
            "meta-llama/llama-4-scout-17b-16e-instruct": "NotFoundError: 404 - does not exist",
            "llama-3.3-70b-versatile": "ok",
        },
    },
    "gemma": {
        "configured": True, "status": "error", "ms": 18350,
        "error": "HTTPStatusError: 429 Too Many Requests",
        "per_model": {"gemma-4-31b-it": "HTTPStatusError: 429 Too Many Requests"},
    },
}
ROUTING = {"by_provider": {"groq": 14, "gemma": 9}, "fallback_disabled_for": []}


def test_the_report_names_the_dead_model_not_just_the_provider():
    """'Groq is down' sent people to check the account. One model id was dead."""
    out = health.format_report_he(PRODUCTION_SHAPE, ROUTING, probed=True)
    assert "llama-4-scout" in out
    assert "llama-3.3-70b-versatile" in out
    assert "✅" in out and "❌" in out


def test_the_report_carries_the_verdict_and_the_routing():
    out = health.format_report_he(PRODUCTION_SHAPE, ROUTING, probed=True)
    assert "תקינות ספקי ה-AI" in out
    assert "ניתוב שימושים" in out
    assert "14" in out and "9" in out


def test_no_key_survives_into_the_message():
    providers = {
        "gemma": {
            "configured": True, "status": "error", "ms": 10,
            "per_model": {"gemma-4-31b-it": "429 for url 'https://x?key=AIzaSyABCDEFG1234567890'"},
        }
    }
    out = health.format_report_he(providers, {}, probed=True)
    assert "AIzaSy" not in out
    assert "&lt;redacted&gt;" in out or "<redacted>" in out


def test_a_missing_key_is_said_plainly():
    providers = {"gemma": {"configured": False, "status": "not_configured"}}
    out = health.format_report_he(providers, {}, probed=True)
    assert "אין מפתח מוגדר" in out


def test_the_free_mode_says_it_did_not_actually_call():
    providers = {"groq": {"configured": True, "status": "not_probed"}}
    out = health.format_report_he(providers, {}, probed=False)
    assert "לא נבדק בפועל" in out
    assert "/llm בדיקה" in out


def test_the_free_mode_never_claims_the_providers_are_available():
    """A key on disk is not a provider that answers. This report said "both
    providers are available, there is a backup" while one provider's first model
    was answering 404 on every call in production."""
    providers = {"groq": {"configured": True, "status": "not_probed"},
                 "gemma": {"configured": True, "status": "not_probed"}}
    out = health.format_report_he(providers, {}, probed=False)
    assert "זמינים" not in out
    assert "יש גיבוי" not in out
    assert "לא נבדק" in out


def test_the_free_mode_still_shouts_when_no_key_is_set_at_all():
    providers = {"groq": {"configured": False, "status": "not_configured"},
                 "gemma": {"configured": False, "status": "not_configured"}}
    out = health.format_report_he(providers, {}, probed=False)
    assert "❌" in out


def test_fallback_disabled_is_flagged():
    routing = {"by_provider": {"groq": 23}, "fallback_disabled_for": ["rag_answer", "eval_judge"]}
    out = health.format_report_he(PRODUCTION_SHAPE, routing, probed=True)
    assert "גיבוי מכובה" in out
    assert "rag_answer" in out


def test_the_message_fits_telegram():
    from app.services.telegram_routing import _TG_MAX
    providers = {
        name: {"configured": True, "status": "error", "ms": 99,
               "per_model": {f"model-{i}": "HTTPStatusError: 429 " + "x" * 400 for i in range(6)}}
        for name in ("groq", "gemma")
    }
    assert len(health.format_report_he(providers, ROUTING, probed=True)) < _TG_MAX


def test_the_command_is_registered_and_admin_only():
    import inspect
    from app.services import telegram_polling as tp
    src = inspect.getsource(tp)
    assert 'CommandHandler("llm", self.handle_llm)' in src
    handler_src = inspect.getsource(type(tp.telegram_bot).handle_llm)
    assert "is_admin" in handler_src
    assert "format_report_he" in handler_src


def test_a_provider_with_one_live_model_is_not_called_dead():
    """One dead model id must not read as 'no provider available' — that verdict
    sent people to check the Groq account when the model list was the problem."""
    verdict = health.summarize(PRODUCTION_SHAPE, ROUTING)
    assert verdict["healthy"] is True
    assert "groq" in verdict["usable_providers"]
    assert "gemma" not in verdict["usable_providers"]      # no live model there


def test_a_provider_with_no_live_model_is_still_called_dead():
    dead = {"groq": {"configured": True, "status": "error",
                     "per_model": {"a": "404", "b": "404"}}}
    assert health.summarize(dead, ROUTING)["healthy"] is False


# --------------------------------------------------------------------------
# /llm מודלים — what the account may actually call
# --------------------------------------------------------------------------

def test_the_model_report_flags_what_the_code_asks_for_but_cannot_call():
    """Both ids in MODELS 404'd on every request for weeks with nothing naming
    the replacement. This is the line that would have said it in one look."""
    available = {"groq": ["llama-3.1-8b-instant", "openai/gpt-oss-120b"]}
    out = health.format_models_he(available, {"groq": ["llama-3.3-70b-versatile"]})
    assert "בקוד אבל לא בחשבון" in out
    assert "llama-3.3-70b-versatile" in out
    assert "llama-3.1-8b-instant" in out


def test_a_model_in_use_is_marked():
    available = {"groq": ["a-model", "b-model"]}
    out = health.format_models_he(available, {"groq": ["a-model"]})
    assert "🔵" in out and "▫️" in out


def test_a_provider_error_is_shown_not_swallowed():
    out = health.format_models_he({"groq": "AuthenticationError: 401"}, {"groq": []})
    assert "401" in out
    assert "❌" in out


def test_the_models_subcommand_is_wired():
    import inspect
    from app.services import telegram_polling as tp
    src = inspect.getsource(type(tp.telegram_bot).handle_llm)
    assert "מודלים" in src
    assert "available_models" in src
    assert "format_models_he" in src
