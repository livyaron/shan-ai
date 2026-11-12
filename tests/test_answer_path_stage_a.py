"""Answer path, stage A (2026-11-12): fewer model calls per question. No database.

1. The Telegram routing call already extracts the project intent — it reaches
   project_tools, so the intent is not asked for a second time.
2. A Groq model that said "try again in 7m" is skipped until then instead of
   being asked again on every call.
3. A count and a single resolved project are written by code, not by a model.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from groq import RateLimitError

from app.services import groq_client as gc
from app.services import project_tools as pt


def _rle(text: str) -> RateLimitError:
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return RateLimitError(text, response=resp, body=None)


TPD = ("Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): "
       "Limit 200000, Used 199456, Requested 1619. Please try again in 7m44.4s.")


@pytest.fixture(autouse=True)
def _no_cooldowns():
    gc._cooling.clear()
    yield
    gc._cooling.clear()


@pytest.mark.parametrize("text,seconds", [
    (TPD, 464.4),
    ("Please try again in 22m43.824s. Need more tokens?", 1363.824),
    ("Please try again in 1h2m3s", 3723.0),
    ("Please try again in 3.2s", 3.2),
    ("Rate limit reached", 0.0),
])
def test_retry_after_is_read_from_groqs_own_message(text, seconds):
    assert gc.retry_after_seconds(Exception(text)) == pytest.approx(seconds)


def _stub(create):
    return type("S", (), {"chat": type("C", (), {"completions": type("K", (), {"create": staticmethod(create)})})})()


@pytest.mark.asyncio
async def test_an_exhausted_provider_is_not_asked_again():
    calls = []

    async def create(**kw):
        calls.append(kw["model"])
        raise _rle(TPD)

    with patch.object(gc, "get_client", return_value=_stub(create)), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()) as slept:
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
        assert calls == gc.MODELS, "each model asked exactly once — no second round"
        slept.assert_not_awaited()

        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "again"}])
        assert calls == gc.MODELS, "second call fails over without a single request"


@pytest.mark.asyncio
async def test_the_model_is_asked_again_once_the_wait_is_over(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(gc.time, "monotonic", lambda: now["t"])
    state = {"fail": True}

    async def create(**kw):
        if state["fail"]:
            raise _rle(TPD)
        r = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" ok "))])
        return r

    with patch.object(gc, "get_client", return_value=_stub(create)), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
        state["fail"] = False
        with pytest.raises(RateLimitError):          # still inside the 7m44s
            await gc.groq_chat([{"role": "user", "content": "hi"}])
        now["t"] += 465
        assert await gc.groq_chat([{"role": "user", "content": "hi"}]) == "ok"


@pytest.mark.asyncio
async def test_a_day_long_wait_is_capped_so_the_model_is_reprobed(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(gc.time, "monotonic", lambda: now["t"])

    async def create(**kw):
        raise _rle("Please try again in 5h0m0s")

    with patch.object(gc, "get_client", return_value=_stub(create)), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert all(until <= gc.MAX_COOLDOWN_SECONDS for until, _ in gc._cooling.values())


@pytest.mark.asyncio
async def test_a_per_minute_limit_still_gets_its_second_round():
    calls = {"n": 0}

    async def create(**kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _rle("Please try again in 1.5s")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    with patch.object(gc, "get_client", return_value=_stub(create)), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        assert await gc.groq_chat([{"role": "user", "content": "hi"}]) == "ok"
    assert not gc._cooling


# ------------------------------------------------------------------ the intent is asked for once

def test_the_routing_intent_reaches_project_tools():
    from app.services import ask_router, telegram_polling as tp
    sig = inspect.signature(ask_router.route)
    assert {"precomputed_intent", "precomputed_param"} <= set(sig.parameters)
    src = inspect.getsource(ask_router.route)
    assert "precomputed_intent=precomputed_intent" in src
    handler = inspect.getsource(type(tp.telegram_bot).handle_message)
    assert 'precomputed_intent=_pre.get("intent")' in handler
    assert '_pre = routing if ai_route == "project" else {}' in handler


@pytest.mark.asyncio
async def test_a_precomputed_intent_skips_the_detection_call(monkeypatch):
    detect = AsyncMock(return_value=("general", None))
    monkeypatch.setattr(pt, "_ai_detect_intent", detect)
    monkeypatch.setattr(pt, "count_by_type", AsyncMock(return_value={"type": "הקמה", "count": 33}))
    monkeypatch.setattr(pt, "_log_query", AsyncMock(return_value=7))
    llm = AsyncMock()
    monkeypatch.setattr(pt, "llm_chat", llm)

    answer, log_id = await pt.answer_project_query(
        "כמה פרויקטי הקמה יש?", None, {}, precomputed_intent="count_by_type", precomputed_param="הקמה")

    detect.assert_not_awaited()
    llm.assert_not_awaited()           # the number is written by code
    assert answer == "יש 33 פרויקטי הקמה פעילים." and log_id == 7


@pytest.mark.asyncio
async def test_a_manager_question_that_names_a_project_gets_the_card_without_a_model(monkeypatch):
    project = {"project_identifier": "WBE-123", "name": "קסם"}
    monkeypatch.setattr(pt, "find_projects_by_identifier", AsyncMock(return_value=[project]))
    monkeypatch.setattr(pt, "_format_project_card", lambda p, i, n: f"CARD {p['project_identifier']}")
    monkeypatch.setattr(pt, "_log_query", AsyncMock(return_value=9))
    llm = AsyncMock()
    monkeypatch.setattr(pt, "llm_chat", llm)
    user_data = {}

    answer, _ = await pt.answer_project_query(
        "מי מנהל את קסם?", None, user_data, precomputed_intent="by_manager", precomputed_param="קסם")

    llm.assert_not_awaited()
    assert answer == "CARD WBE-123" and user_data["last_project"] == "WBE-123"
