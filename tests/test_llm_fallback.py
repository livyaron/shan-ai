"""The backup path: what actually happens when Groq goes down.

llm_router falls back Groq → Gemma, but that path is only ever exercised during
a real outage — so it is exactly the code most likely to be quietly broken.
"""

import pytest

from app.services import llm_health, llm_router


class _Boom(Exception):
    pass


class _RateLimitError(Exception):
    """Stands in for groq.RateLimitError — matched by name in is_overload_error."""


@pytest.fixture(autouse=True)
def _clear_router_cache():
    llm_router._cache.clear()
    yield
    llm_router._cache.clear()


def _patch_providers(monkeypatch, groq, gemma):
    """llm_chat imports both clients inside the call, so patch at the source module."""
    from app.services import groq_client, gemma_client
    monkeypatch.setattr(groq_client, "groq_chat", groq)
    monkeypatch.setattr(gemma_client, "gemma_chat", gemma)


def _config(monkeypatch, provider="groq", fallback=True):
    async def _cfg(_usage):
        return provider, fallback
    monkeypatch.setattr(llm_router, "_get_config", _cfg)


# ── Groq down → Gemma answers ──────────────────────────────────────────────

async def test_groq_failure_falls_back_to_gemma(monkeypatch):
    calls = []

    async def _groq(*a, **k):
        calls.append("groq")
        raise _Boom("groq exploded")

    async def _gemma(*a, **k):
        calls.append("gemma")
        return "תשובה מהגיבוי"

    _config(monkeypatch)
    _patch_providers(monkeypatch, _groq, _gemma)

    assert await llm_router.llm_chat("missions_summary", []) == "תשובה מהגיבוי"
    assert calls == ["groq", "gemma"]

    provider, is_fallback = llm_router.get_last_llm_meta()
    assert provider == "Gemma" and is_fallback is True


async def test_rate_limit_also_falls_back(monkeypatch):
    """The common case: Groq's free-tier TPD is spent, not down."""
    async def _groq(*a, **k):
        raise _RateLimitError("429 rate limit reached")

    async def _gemma(*a, **k):
        return "גיבוי"

    _config(monkeypatch)
    _patch_providers(monkeypatch, _groq, _gemma)
    assert await llm_router.llm_chat("missions_summary", []) == "גיבוי"


async def test_fallback_can_be_switched_off_per_usage(monkeypatch):
    """With fallback off there is no backup — the failure must surface, not hide."""
    async def _groq(*a, **k):
        raise _Boom("groq exploded")

    async def _gemma(*a, **k):
        raise AssertionError("must not be called when fallback is off")

    _config(monkeypatch, fallback=False)
    _patch_providers(monkeypatch, _groq, _gemma)

    with pytest.raises(_Boom):
        await llm_router.llm_chat("missions_summary", [])


async def test_both_providers_down_keeps_the_original_cause(monkeypatch):
    """A both-down failure must still read as a rate limit for is_overload_error."""
    async def _groq(*a, **k):
        raise _RateLimitError("groq 429")

    async def _gemma(*a, **k):
        raise _Boom("gemma also down")

    _config(monkeypatch)
    _patch_providers(monkeypatch, _groq, _gemma)

    with pytest.raises(_Boom) as excinfo:
        await llm_router.llm_chat("missions_summary", [])
    assert llm_router.is_overload_error(excinfo.value), \
        "the Groq rate limit must stay reachable through the exception chain"


async def test_gemma_primary_falls_back_to_groq(monkeypatch):
    """The fallback is symmetric — a usage routed to Gemma backs off to Groq."""
    async def _groq(*a, **k):
        return "מ-Groq"

    async def _gemma(*a, **k):
        raise _Boom("gemma down")

    _config(monkeypatch, provider="gemma")
    _patch_providers(monkeypatch, _groq, _gemma)
    assert await llm_router.llm_chat("missions_summary", []) == "מ-Groq"


# ── A missing key must say so, not fail opaquely ───────────────────────────

async def test_gemma_without_a_key_names_the_real_problem(monkeypatch):
    from app.services import gemma_client
    monkeypatch.setattr(gemma_client.settings, "GOOGLE_AI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GOOGLE_AI_API_KEY"):
        await gemma_client.gemma_chat([{"role": "user", "content": "x"}])


def test_groq_without_a_key_names_the_real_problem(monkeypatch):
    from app.services import groq_client
    monkeypatch.setattr(groq_client.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(groq_client, "_shared_client", None)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        groq_client.get_client()


def test_groq_client_is_shared_between_calls(monkeypatch):
    """A new TLS pool per call cost ~100-300ms and leaked the socket."""
    from app.services import groq_client
    monkeypatch.setattr(groq_client.settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(groq_client, "_shared_client", None)
    assert groq_client.get_client() is groq_client.get_client()


# ── The health check ───────────────────────────────────────────────────────

async def test_health_reports_a_missing_backup(monkeypatch):
    monkeypatch.setattr(llm_health.settings, "GROQ_API_KEY", "set")
    monkeypatch.setattr(llm_health.settings, "GOOGLE_AI_API_KEY", "")

    providers = await llm_health.check_providers(probe=False)
    assert providers["groq"]["status"] == "not_probed"
    assert providers["gemma"]["status"] == "not_configured"

    verdict = llm_health.summarize(providers)
    assert verdict["healthy"] is True
    assert verdict["backup_available"] is False
    assert "אין גיבוי" in verdict["verdict"]


async def test_health_reports_no_provider_at_all(monkeypatch):
    monkeypatch.setattr(llm_health.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(llm_health.settings, "GOOGLE_AI_API_KEY", "")
    verdict = llm_health.summarize(await llm_health.check_providers())
    assert verdict["healthy"] is False and verdict["backup_available"] is False


async def test_health_probe_marks_a_dead_provider(monkeypatch):
    monkeypatch.setattr(llm_health.settings, "GROQ_API_KEY", "set")
    monkeypatch.setattr(llm_health.settings, "GOOGLE_AI_API_KEY", "set")

    from app.services import groq_client, gemma_client

    async def _groq(*a, **k):
        raise _RateLimitError("429 daily quota exceeded")

    async def _gemma(*a, **k):
        return "OK"

    monkeypatch.setattr(groq_client, "groq_chat", _groq)
    monkeypatch.setattr(gemma_client, "gemma_chat", _gemma)

    providers = await llm_health.check_providers(probe=True)
    assert providers["groq"]["status"] == "error"
    assert "429" in providers["groq"]["error"]
    assert providers["gemma"]["status"] == "ok"

    verdict = llm_health.summarize(providers)
    assert verdict["usable_providers"] == ["gemma"]
    assert verdict["backup_available"] is False, "one dead provider means no backup left"


async def test_health_probe_is_skipped_when_not_asked(monkeypatch):
    """The default check must not spend tokens."""
    monkeypatch.setattr(llm_health.settings, "GROQ_API_KEY", "set")
    monkeypatch.setattr(llm_health.settings, "GOOGLE_AI_API_KEY", "set")

    from app.services import groq_client

    async def _never(*a, **k):
        raise AssertionError("probe=False must not call the provider")

    monkeypatch.setattr(groq_client, "groq_chat", _never)
    providers = await llm_health.check_providers(probe=False)
    assert providers["groq"]["status"] == "not_probed"


async def test_routing_flags_usages_with_the_backup_switched_off():
    from app.models import LLMConfig

    class _Session:
        async def scalars(self, _stmt):
            return _Rows([
                LLMConfig(usage_name="missions_summary", provider="groq", fallback=False),
                LLMConfig(usage_name="rag_answer", provider="gemma", fallback=True),
            ])

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    routing = await llm_health.check_routing(_Session())
    assert routing["fallback_disabled_for"] == ["missions_summary"]
    assert routing["by_provider"]["gemma"] == 1
    # Every usage without a row defaults to groq, exactly like llm_router does.
    assert routing["by_provider"]["groq"] == len(llm_router.USAGE_LABELS) - 1

    summary = llm_health.summarize(
        {"groq": {"configured": True, "status": "ok"},
         "gemma": {"configured": True, "status": "ok"}},
        routing,
    )
    assert "warning" in summary


# ── No hardcoded deployment domain in application code ─────────────────────

def test_no_hardcoded_railway_domain_in_app_code():
    """A pinned domain silently sends users to the previous deployment.

    settings.public_base_url reads Railway's auto-injected RAILWAY_PUBLIC_DOMAIN,
    so a domain change self-heals with no code edit.
    """
    import pathlib

    offenders = [
        f"{path}:{i}"
        for path in pathlib.Path("app").rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "up.railway.app" in line
    ]
    assert not offenders, f"hardcoded deployment domain in: {offenders}"


def test_public_base_url_prefers_the_railway_domain(monkeypatch):
    from app.config import Settings

    s = Settings(RAILWAY_PUBLIC_DOMAIN="example.up.railway.app",
                 BASE_URL="http://localhost:8000")
    assert s.public_base_url == "https://example.up.railway.app"

    s = Settings(RAILWAY_PUBLIC_DOMAIN="", BASE_URL="http://localhost:8000")
    assert s.public_base_url == "http://localhost:8000"
