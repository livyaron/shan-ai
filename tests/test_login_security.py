"""Login hardening (2026-11-11). No database.

The user list is public (the login page is a pick-list) and every account is
created with the same initial password, so: wrong passwords lock the login, and
the initial password never opens a session — it only sends a code to the user's
own Telegram, and the code plus a personal password is what gets in.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db_session
from app.main import app
from app.routers import login as lg
from app.utils.auth import DEFAULT_PASSWORD, hash_password, verify_password


class _User:
    def __init__(self, id_, telegram_id=None, password=DEFAULT_PASSWORD):
        self.id, self.username, self.job_title = id_, f"user{id_}", None
        self.telegram_id, self.role = telegram_id, "project_manager"
        self.password_hash = hash_password(password)
        self.password_is_default = password == DEFAULT_PASSWORD


class _Session:
    def __init__(self, users):
        self.users, self.commits = {u.id: u for u in users}, 0

    async def get(self, model, key):
        return self.users.get(key)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def world(monkeypatch):
    users = [_User(1, telegram_id=555), _User(2), _User(3, telegram_id=777, password="a-real-password")]
    session, sent = _Session(users), []

    async def fake_db():
        yield session

    async def fake_send(telegram_id, code):
        sent.append((telegram_id, code))

    monkeypatch.setattr(lg, "_send_telegram_code", fake_send)
    lg._failures.clear()
    lg._codes.clear()
    app.dependency_overrides[get_db_session] = fake_db
    yield session, sent
    app.dependency_overrides.clear()
    lg._failures.clear()
    lg._codes.clear()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_five_wrong_passwords_lock_even_the_right_one(world):
    async with _client() as c:
        for _ in range(lg.MAX_FAILURES):
            r = await c.post("/login/app", json={"user_id": 3, "password": "nope"})
            assert r.status_code == 401
        r = await c.post("/login/app", json={"user_id": 3, "password": "a-real-password"})
        assert r.status_code == 429 and r.json()["error"] == lg.LOCKED_ERROR
        assert "set-cookie" not in r.headers
        # another user is not affected
        r = await c.post("/login/app", json={"user_id": 1, "password": "nope"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_a_good_login_clears_the_count(world):
    async with _client() as c:
        for _ in range(lg.MAX_FAILURES - 1):
            await c.post("/login/app", json={"user_id": 3, "password": "nope"})
        r = await c.post("/login/app", json={"user_id": 3, "password": "a-real-password"})
        assert r.status_code == 200 and "access_token" in r.headers["set-cookie"]
    assert not lg._failures.get(3)


@pytest.mark.asyncio
async def test_initial_password_never_opens_a_session(world):
    session, sent = world
    async with _client() as c:
        r = await c.post("/login", data={"user_id": 1, "password": DEFAULT_PASSWORD, "next": "/lessons/"},
                         follow_redirects=False)
        assert r.status_code == 303 and "set-cookie" not in r.headers
        assert r.headers["location"] == "/login/new-password?user_id=1&next=/lessons/"
        assert len(sent) == 1 and sent[0][0] == 555 and len(sent[0][1]) == 6

        r = await c.post("/login/app", json={"user_id": 1, "password": DEFAULT_PASSWORD})
        assert r.status_code == 403 and "set-cookie" not in r.headers
        assert r.json()["change_password"] == "/login/new-password?user_id=1&next=/lessons/"
        assert len(sent) == 1, "a second code within a minute must not spam the user's Telegram"


@pytest.mark.asyncio
async def test_no_telegram_link_means_ask_the_admin(world):
    session, sent = world
    async with _client() as c:
        r = await c.post("/login", data={"user_id": 2, "password": DEFAULT_PASSWORD}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?error=")
        assert "set-cookie" not in r.headers and not sent
        r = await c.get("/login/new-password?user_id=2", follow_redirects=False)
        assert r.status_code == 303  # no pending code → no form


@pytest.mark.asyncio
async def test_code_plus_a_personal_password_gets_in(world):
    session, sent = world
    async with _client() as c:
        await c.post("/login", data={"user_id": 1, "password": DEFAULT_PASSWORD}, follow_redirects=False)
        code = sent[0][1]
        page = await c.get("/login/new-password?user_id=1")
        assert page.status_code == 200 and 'name="code"' in page.text

        form = {"user_id": 1, "code": code, "new_password": "long-enough-1", "confirm": "long-enough-1"}
        wrong = "000000" if code != "000000" else "111111"
        r = await c.post("/login/new-password", data={**form, "code": wrong}, follow_redirects=False)
        assert "set-cookie" not in r.headers and "/login/new-password" in r.headers["location"]
        for bad in ({"new_password": "short", "confirm": "short"},
                    {"new_password": DEFAULT_PASSWORD * 2, "confirm": "different-one"}):
            r = await c.post("/login/new-password", data={**form, **bad}, follow_redirects=False)
            assert "set-cookie" not in r.headers
        assert session.commits == 0

        r = await c.post("/login/new-password", data=form, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        cookie = r.headers["set-cookie"].lower()
        assert "access_token" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    user = session.users[1]
    assert verify_password("long-enough-1", user.password_hash) and user.password_is_default is False
    assert session.commits == 1 and 1 not in lg._codes  # the code is single-use


@pytest.mark.asyncio
async def test_five_wrong_codes_burn_the_code(world):
    session, sent = world
    async with _client() as c:
        await c.post("/login", data={"user_id": 1, "password": DEFAULT_PASSWORD}, follow_redirects=False)
        code = sent[0][1]
        wrong = "000000" if code != "000000" else "111111"
        form = {"user_id": 1, "code": wrong, "new_password": "long-enough-1", "confirm": "long-enough-1"}
        for _ in range(lg.CODE_MAX_TRIES):
            await c.post("/login/new-password", data=form, follow_redirects=False)
        r = await c.post("/login/new-password", data={**form, "code": code}, follow_redirects=False)
        assert "set-cookie" not in r.headers and r.headers["location"].startswith("/login?error=")
    assert session.commits == 0


def test_the_initial_password_cannot_be_the_new_one():
    assert lg._is_initial_password(DEFAULT_PASSWORD)
    assert lg.MIN_PASSWORD_LENGTH >= 8


def test_login_page_does_not_print_the_initial_password():
    from pathlib import Path
    html = Path("app/templates/login.html").read_text(encoding="utf-8")
    assert "DEFAULT KEY" not in html and f"· {DEFAULT_PASSWORD}" not in html


def test_session_cookie_is_secure_on_https(monkeypatch):
    from fastapi.responses import Response
    from app.config import settings

    monkeypatch.setattr(settings, "RAILWAY_PUBLIC_DOMAIN", "shan.example")
    r = Response()
    lg._set_session(r, _User(3, password="a-real-password"), 7)
    assert "secure" in r.headers["set-cookie"].lower()
