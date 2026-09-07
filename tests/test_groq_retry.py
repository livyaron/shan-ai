"""groq_chat retries the whole model list with backoff when all are rate-limited."""
import pytest
from unittest.mock import AsyncMock, patch
from groq import RateLimitError

from app.services import groq_client as gc


def _rle():
    import httpx
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return RateLimitError("rate", response=resp, body=None)


@pytest.mark.asyncio
async def test_retries_then_succeeds():
    calls = {"n": 0}

    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    calls["n"] += 1
                    if calls["n"] <= 3:           # whole first pass fails
                        raise _rle()
                    r = type("R", (), {})()
                    r.choices = [type("C", (), {"message": type("M", (), {"content": " ok "})()})()]
                    return r

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        out = await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert calls["n"] >= 4


@pytest.mark.asyncio
async def test_raises_after_max_rounds():
    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    raise _rle()

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])


# --------------------------------------------------------------------------
# a dead model must not take the provider down with it
# --------------------------------------------------------------------------

def _not_found(model: str):
    """What Groq returns for a decommissioned model id."""
    import httpx
    from groq import NotFoundError
    resp = httpx.Response(404, request=httpx.Request("POST", "http://x"))
    return NotFoundError(f"The model `{model}` does not exist", response=resp, body=None)


@pytest.mark.asyncio
async def test_a_decommissioned_first_model_falls_through_to_the_next():
    """The production outage: a 404 on the FIRST model aborted the whole call, so
    every AI feature failed while the healthy second model was never tried."""
    seen = []

    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(model=None, **kw):
                    seen.append(model)
                    if model == gc.MODELS[0]:
                        raise _not_found(model)
                    r = type("R", (), {})()
                    r.choices = [type("C", (), {"message": type("M", (), {"content": " ok "})()})()]
                    return r

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        out = await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert seen == gc.MODELS[:2]          # the dead one, then the working one


@pytest.mark.asyncio
async def test_a_dead_model_is_not_retried_on_the_second_round():
    """A 404 is permanent for this call — asking again only costs latency."""
    seen = []

    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(model=None, **kw):
                    seen.append(model)
                    raise _not_found(model)

    from groq import NotFoundError
    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(NotFoundError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert seen == gc.MODELS               # each model tried exactly once


@pytest.mark.asyncio
async def test_a_rate_limited_model_is_still_retried_next_round():
    """Only permanent failures are struck off — a 429 is transient."""
    seen = []

    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(model=None, **kw):
                    seen.append(model)
                    raise _rle()

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert len(seen) == len(gc.MODELS) * 2
