"""No credential may leave the app inside an error string.

The Google AI key is a query parameter on every request URL, so an httpx error
carries it verbatim — into the logs and out through /dashboard/llm-health, which
is how a live key ended up being read out of a diagnostics page.
"""
from app.services.gemma_client import redact


def test_a_key_in_a_url_is_redacted():
    text = ("HTTPStatusError: 429 for url 'https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-2.5-flash:generateContent?key=AIzaSyABCDEFG1234567890'")
    out = redact(text)
    assert "AIzaSy" not in out
    assert "<redacted>" in out
    assert "gemini-2.5-flash" in out       # the useful part survives


def test_bare_keys_are_redacted_by_shape():
    for key in ("AIzaSyABCDEFG1234567890", "gsk_ABCDEFGHIJ1234567890",
                "sk-ant-ABCDEFGHIJ1234567890"):
        assert key not in redact(f"failed with {key} at the end")


def test_ordinary_text_is_untouched():
    text = "NotFoundError: The model `llama-4-scout` does not exist"
    assert redact(text) == text
    assert redact("") == ""


def test_the_health_probe_redacts_what_it_returns():
    """The endpoint publishes provider errors to the browser — it must scrub them."""
    import inspect
    from app.services import llm_health
    src = inspect.getsource(llm_health)
    assert src.count("redact(") >= 3
    assert 'f"{type(e).__name__}: {str(e)[:200]}"' not in src   # the un-scrubbed shape


# --------------------------------------------------------------------------
# a probe that starves the model is not a health check
# --------------------------------------------------------------------------

def test_the_probe_budget_is_not_starvation_tight():
    """At 8 tokens the gemma models spent the whole budget on a reasoning preamble
    and returned no text — the probe called the provider dead over its own ceiling."""
    from app.services import llm_health
    assert llm_health._PROBE_MAX_TOKENS >= 128


def test_a_textless_candidate_reports_why_not_keyerror():
    """'KeyError: parts' told nobody anything. finishReason does."""
    import inspect
    from app.services import gemma_client
    src = inspect.getsource(gemma_client)
    assert 'data["candidates"][0]["content"]["parts"]' not in src
    assert "finishReason" in src
