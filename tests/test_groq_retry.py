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
