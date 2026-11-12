"""GET /lessons/api/_bootstrap — the app's startup data in one request.

No DB, no network: PostgREST is a fake client. What must hold: every read
passes the gateway's own access rule, goes out pinned to schema `lessons`, a
failing table never takes the others down, and the server's table list is the
one the app expects.
"""
import asyncio
import json
import re
from pathlib import Path

import httpx

from app.routers import lessons_gateway as gw
from app.services import lessons_access as la

ROOT = Path(__file__).resolve().parent.parent


def _ident(role: str = "project_manager") -> la.Identity:
    return la.Identity(shan_user_id=24, profile={"id": "u2", "role": role, "name": "x"})


class _Reply:
    def __init__(self, status: int, rows: list | None = None):
        self.status_code = status
        self._rows = rows or []

    def json(self):
        return self._rows


class _FakePostgrest:
    def __init__(self, broken: dict[str, object] | None = None):
        self.calls: list[tuple[str, list, dict]] = []
        self.broken = broken or {}

    async def get(self, url, params=None, headers=None):
        table = url.rsplit("/", 1)[1]
        self.calls.append((table, list(params or []), dict(headers or {})))
        fault = self.broken.get(table)
        if isinstance(fault, Exception):
            raise fault
        if fault:
            return _Reply(int(fault))
        return _Reply(200, [{"id": f"{table}-1"}])


def _run(monkeypatch, fake: _FakePostgrest, identity: la.Identity | None = None) -> dict:
    monkeypatch.setattr(gw, "_client", lambda: fake)
    monkeypatch.setattr(gw.settings, "LESSONS_POSTGREST_URL", "http://postgrest.internal:3000/")
    resp = asyncio.run(gw.bootstrap(identity or _ident()))
    assert resp.status_code == 200
    return json.loads(resp.body)


def test_one_request_returns_me_and_all_nine_tables(monkeypatch):
    fake = _FakePostgrest()
    body = _run(monkeypatch, fake)
    assert body["me"]["profile"]["id"] == "u2"
    assert body["errors"] == {}
    assert list(body["tables"]) == [t for t, _ in gw.BOOTSTRAP_READS]
    assert len(fake.calls) == 9
    assert body["tables"]["lessons"] == [{"id": "lessons-1"}]


def test_every_read_is_pinned_to_the_lessons_schema(monkeypatch):
    fake = _FakePostgrest()
    _run(monkeypatch, fake)
    for table, _params, headers in fake.calls:
        low = {k.lower(): v for k, v in headers.items()}
        assert low.get("accept-profile") == "lessons", table


def test_the_orders_the_app_relied_on_are_kept(monkeypatch):
    fake = _FakePostgrest()
    _run(monkeypatch, fake)
    sent = {t: dict(p) for t, p, _ in fake.calls}
    assert sent["lessons"]["order"] == "id.desc"
    assert sent["notifications"]["order"] == "id.desc"
    assert sent["lesson_categories"]["order"] == "id.asc"
    assert sent["feedback"]["order"] == "created_at.desc"


def test_one_failing_table_does_not_take_the_others_down(monkeypatch):
    fake = _FakePostgrest(broken={"feedback": 500, "equipment": httpx.ConnectError("down")})
    body = _run(monkeypatch, fake)
    assert set(body["errors"]) == {"feedback", "equipment"}
    assert "feedback" not in body["tables"] and "equipment" not in body["tables"]
    assert len(body["tables"]) == 7


def test_every_read_passes_the_gateway_rule(monkeypatch):
    seen: list[tuple[str, str]] = []
    real = la.check_request

    def spy(method, path, params, body, identity):
        seen.append((method, path))
        if path == "/profiles":
            return 403, "no"
        return real(method, path, params, body, identity)

    monkeypatch.setattr(la, "check_request", spy)
    fake = _FakePostgrest()
    body = _run(monkeypatch, fake)
    assert seen == [("GET", "/" + t) for t, _ in gw.BOOTSTRAP_READS]
    assert body["errors"] == {"profiles": "no"}
    assert "profiles" not in [c[0] for c in fake.calls]  # a refused read never reaches PostgREST


def test_only_exposed_tables_are_bundled():
    for table, params in gw.BOOTSTRAP_READS:
        assert table in la.EXPOSED_TABLES, table
        assert la.check_request("GET", "/" + table, params, None, _ident("viewer")) is None


def test_server_and_app_agree_on_the_table_list():
    src = (ROOT / "lessons_ui/src/lib/startupData.ts").read_text(encoding="utf-8")
    block = re.search(r"STARTUP_TABLES = \[(.*?)\]", src, re.DOTALL).group(1)
    assert re.findall(r'"(\w+)"', block) == [t for t, _ in gw.BOOTSTRAP_READS]


def test_bootstrap_requires_a_session():
    """Same dependency as /me — no identity, no data."""
    route = next(r for r in gw.router.routes if r.path.endswith("/_bootstrap"))
    assert gw.current_identity in [d.call for d in route.dependant.dependencies]
