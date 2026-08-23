"""חדר מבצעים — the filter bar must survive "כל האחראים" (an empty <select>).

DB-free: the endpoint's own parameter annotations are mounted on a throwaway
FastAPI app, so this pins the exact thing that broke — `?owner=` reaching an
`int | None` param — without needing Postgres or a logged-in user.

The regression it guards: a plain `int | None` annotation makes FastAPI answer
the filter form with a 422 `int_parsing` body instead of the board.
"""
import pytest
from fastapi import FastAPI, Form
from httpx import ASGITransport, AsyncClient

from app.routers import war_room as wr


def _probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/q")
    async def q(owner: wr.BlankableIntQuery = None, status: str = "active"):
        return {"owner": owner, "status": status}

    @app.post("/f")
    async def f(owner_id: wr.BlankableIntForm = Form(None)):
        return {"owner_id": owner_id}

    return app


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=_probe_app()),
                           base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["/q?owner=&status=active&q=", "/q?owner=&status=done"])
async def test_empty_owner_is_no_filter_not_a_422(query):
    """Submitting the filter form with "כל האחראים" selected shows the board."""
    r = await _get(query)
    assert r.status_code == 200, r.text
    assert r.json()["owner"] is None


@pytest.mark.asyncio
async def test_owner_filter_still_parses_a_real_id():
    r = await _get("/q?owner=7")
    assert r.status_code == 200
    assert r.json()["owner"] == 7


@pytest.mark.asyncio
async def test_absent_owner_param_is_still_no_filter():
    r = await _get("/q")
    assert r.status_code == 200
    assert r.json()["owner"] is None


@pytest.mark.asyncio
async def test_garbage_owner_is_still_rejected():
    """Blank means "no filter" — it must not turn every bad value into one."""
    r = await _get("/q?owner=abc")
    assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [("", None), ("7", 7)])
async def test_bulk_owner_id_tolerates_the_empty_select(value, expected):
    """The table layout's bulk bar ships an empty "שינוי אחראי…" option too;
    an empty pick must reach the handler as None so it can answer "נדרש אחראי"."""
    async with AsyncClient(transport=ASGITransport(app=_probe_app()),
                           base_url="http://test") as client:
        r = await client.post("/f", data={"owner_id": value})
    assert r.status_code == 200, r.text
    assert r.json()["owner_id"] == expected


def test_board_endpoint_uses_the_blank_tolerant_annotation():
    """Guards the real signature — a probe app only proves the type works."""
    import inspect
    sig = inspect.signature(wr.war_room_page)
    assert sig.parameters["owner"].annotation is wr.BlankableIntQuery
    assert inspect.signature(wr.bulk_action).parameters["owner_id"].annotation \
        is wr.BlankableIntForm
