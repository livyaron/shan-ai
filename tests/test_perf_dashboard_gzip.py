"""Performance pass, 2026-11-12. No database.

1. The pattern engine's cache expired every ten minutes although nothing had
   changed, so the next visitor paid ~2s for the dashboard; and the pandas work
   ran on the event loop, freezing every other request meanwhile.
2. Nothing was compressed — pages, JSON and a 3.2MB script went out raw.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.services import pattern_service as ps


class _Session:
    """Answers the fingerprint query with a fixed key."""
    def __init__(self, key=(1, 1, 1, 1, None, 1)):
        self.key = key

    async def execute(self, *a, **k):
        return SimpleNamespace(one=lambda: self.key)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(ps, "_CACHE", {"key": None, "at": 0.0, "p": None})
    monkeypatch.setattr(ps, "_BUILD_LOCK", asyncio.Lock())
    calls = {"n": 0, "threads": set()}

    async def frames(session):
        return "FRAMES"

    def compute(f):
        calls["n"] += 1
        calls["threads"].add(threading.get_ident())
        time.sleep(0.05)                      # stands in for the pandas work
        return {"projects": [], "n": calls["n"]}

    monkeypatch.setattr(ps, "load_frames", frames)
    monkeypatch.setattr(ps, "compute_patterns", compute)
    return calls


@pytest.mark.asyncio
async def test_unchanged_data_is_served_from_the_cache_well_past_ten_minutes(engine, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    first = await ps.compute_cached(_Session())
    now["t"] += 601                           # the old bound
    assert await ps.compute_cached(_Session()) is first and engine["n"] == 1
    now["t"] += ps.CACHE_SECONDS              # the safety net still exists
    await ps.compute_cached(_Session())
    assert engine["n"] == 2
    assert ps.CACHE_SECONDS >= 3600


@pytest.mark.asyncio
async def test_a_change_in_the_data_recomputes_at_once(engine):
    await ps.compute_cached(_Session())
    await ps.compute_cached(_Session(key=(2, 2, 1, 1, None, 1)))
    assert engine["n"] == 2


@pytest.mark.asyncio
async def test_cold_visitors_arriving_together_build_it_once(engine):
    results = await asyncio.gather(*[ps.compute_cached(_Session()) for _ in range(6)])
    assert engine["n"] == 1 and all(r is results[0] for r in results)


@pytest.mark.asyncio
async def test_the_computation_leaves_the_event_loop_free(engine):
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await ps.compute_cached(_Session())
    beat.cancel()
    assert threading.get_ident() not in engine["threads"]      # ran in a worker thread
    assert ticks >= 3, "the loop was blocked while the engine computed"


# ------------------------------------------------------------------ compression

def test_what_gets_compressed():
    from app.main import _TextGZip
    for path in ("/dashboard/", "/dashboard/live", "/lessons/assets/index-abc.js",
                 "/lessons/assets/index-abc.css", "/lessons/api/rest/v1/lessons", "/static/js/substation_bg.js"):
        assert _TextGZip.compresses(path), path
    for path in ("/static/logo.png", "/lessons/fonts/karantina-700-hebrew.woff2", "/lessons/intro/ink.webp",
                 "/telegram/webhook", "/lessons/api/storage/v1/object/public/lesson-files/a.pdf",
                 "/dashboard/files/3/open", "/eval/stream", "/lessons/api/functions/v1/summarize-lessons",
                 "/dashboard/war-room/export.XLSX"):
        assert not _TextGZip.compresses(path), path


@pytest.mark.asyncio
async def test_a_page_is_gzipped_for_a_browser_that_accepts_it():
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, Response
    from starlette.routing import Route
    from app.main import _TextGZip

    page = "<html>" + "שלום עולם " * 400 + "</html>"
    inner = Starlette(routes=[
        Route("/dashboard/", lambda r: HTMLResponse(page)),
        Route("/tiny", lambda r: HTMLResponse("ok")),
        Route("/static/logo.png", lambda r: Response(b"x" * 5000, media_type="image/png")),
    ])
    wrapped = _TextGZip(inner, minimum_size=1024, compresslevel=5)
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://test") as c:
        r = await c.get("/dashboard/", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip" and r.text == page   # decodes to the same page
        assert int(r.headers["content-length"]) < len(page.encode()) // 5
        assert "content-encoding" not in (await c.get("/dashboard/", headers={"Accept-Encoding": "identity"})).headers
        assert "content-encoding" not in (await c.get("/tiny", headers={"Accept-Encoding": "gzip"})).headers
        assert "content-encoding" not in (await c.get("/static/logo.png", headers={"Accept-Encoding": "gzip"})).headers


def test_the_app_installs_the_compression_middleware():
    from app.main import _TextGZip, app
    assert any(m.cls is _TextGZip for m in app.user_middleware)
