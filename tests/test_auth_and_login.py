"""Tests for the auth surface: bcrypt utils, JWT session tokens, and the
/login endpoints. Security-relevant and previously at 0–43% coverage."""
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from app.main import app
from app.models import RoleEnum, User
from app.utils.auth import get_default_password_hash, hash_password, verify_password
from app.utils.session import create_access_token, verify_token


# ── Password hashing ─────────────────────────────────────────────────────────

def test_hash_and_verify_roundtrip():
    h = hash_password("s0d-gadol")
    assert h != "s0d-gadol"
    assert verify_password("s0d-gadol", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_garbage_hash_returns_false():
    # Must not raise on malformed hashes
    assert verify_password("x", "not-a-bcrypt-hash") is False
    assert verify_password("x", "") is False


def test_default_password_hash_is_1234():
    assert verify_password("1234", get_default_password_hash()) is True


# ── JWT session tokens ───────────────────────────────────────────────────────

def test_token_roundtrip():
    token = create_access_token(17, "yaron")
    payload = verify_token(token)
    assert payload == {"user_id": 17, "username": "yaron"}


def test_verify_token_rejects_tampered():
    token = create_access_token(17, "yaron")
    assert verify_token(token + "x") is None
    assert verify_token("definitely.not.a-jwt") is None


# ── /login endpoints ─────────────────────────────────────────────────────────

async def _seed_login_user(db_session, username="login_t", password="1234"):
    user = User(username=username, role=RoleEnum.PROJECT_MANAGER,
                telegram_id=903000001, password_hash=hash_password(password))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_login_success_sets_cookie_and_redirects(db_session):
    user = await _seed_login_user(db_session)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        r = await client.post("/login", data={"user_id": user.id, "password": "1234"})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "access_token=" in r.headers.get("set-cookie", "")
    # Cookie must be a valid token for this user
    token = r.cookies.get("access_token")
    assert verify_token(token)["user_id"] == user.id


@pytest.mark.asyncio
async def test_login_wrong_password_redirects_with_error(db_session):
    user = await _seed_login_user(db_session, username="login_wrong")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        r = await client.post("/login", data={"user_id": user.id, "password": "hacked"})
    assert r.status_code == 303
    assert "/login?error=" in r.headers["location"]
    assert "access_token" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_login_unknown_user_redirects(db_session):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        r = await client.post("/login", data={"user_id": 999999, "password": "1234"})
    assert r.status_code == 303
    assert "/login?error=" in r.headers["location"]


@pytest.mark.asyncio
async def test_logout_clears_cookie():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        r = await client.get("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert 'access_token="";' in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_dashboard_requires_auth(db_session):
    """Unauthenticated dashboard access must bounce to /login, never render."""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        r = await client.get("/dashboard/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
