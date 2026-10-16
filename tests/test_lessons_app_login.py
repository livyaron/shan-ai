"""מערכת לקחים כאפליקציית טלפון — M0 (PLAN-lessons-mobile.md). No database.

The installed app signs in without leaving /lessons/: /lessons/login opens
without a session, and two JSON routes give it the same user list and the same
password check as Shan-AI's /login. The long session is off (7 days) until
LESSONS_REMEMBER_DAYS is raised after information-security approval.
"""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db_session
from app.main import app
from app.routers import lessons_spa as spa
from app.routers import login as login_router
from app.utils.session import verify_token


class _User:
    def __init__(self, id_, name, title=None, password_hash="h"):
        self.id, self.username, self.job_title, self.password_hash = id_, name, title, password_hash
        self.role = "project_manager"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, users):
        self.users = {u.id: u for u in users}

    async def get(self, model, key):
        return self.users.get(key)

    async def execute(self, stmt):
        return _Result(list(self.users.values()))


@pytest.fixture
def db(monkeypatch):
    users = [_User(1, "ירון ליב", "מנהל אגף"), _User(2, "דנה כהן"), _User(3, "בלי סיסמה", password_hash=None)]

    async def fake():
        yield _Session(users)

    import app.utils.auth as auth
    monkeypatch.setattr(auth, "verify_password", lambda plain, hashed: plain == "right")
    app.dependency_overrides[get_db_session] = fake
    yield
    app.dependency_overrides.clear()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _max_age(r) -> int:
    cookie = r.headers["set-cookie"]
    return int(cookie.split("Max-Age=")[1].split(";")[0])


@pytest.mark.parametrize("configured,expected", [(7, 7), (1, 7), (0, 7), (30, 30), (90, 90), (365, 90)])
def test_remember_days_is_clamped(monkeypatch, configured, expected):
    monkeypatch.setattr(settings, "LESSONS_REMEMBER_DAYS", configured)
    assert login_router.remember_days() == expected


def test_default_is_the_normal_session_until_approved():
    # No env var → nothing changes for anyone (PLAN-lessons-mobile.md M0.3).
    src = Path("app/config.py").read_text(encoding="utf-8")
    assert 'os.getenv("LESSONS_REMEMBER_DAYS", "7")' in src
    assert login_router.ACCESS_TOKEN_EXPIRE_DAYS == 7


@pytest.mark.asyncio
async def test_users_list_is_names_and_titles_only(db, monkeypatch):
    monkeypatch.setattr(settings, "LESSONS_REMEMBER_DAYS", 90)
    async with _client() as c:
        r = await c.get("/login/users")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["remember_days"] == 90
    assert body["users"][0] == {"id": 1, "name": "ירון ליב", "title": "מנהל אגף"}
    # Never a hash, a role or anything the HTML login page does not already show.
    assert all(set(u) == {"id", "name", "title"} for u in body["users"])


@pytest.mark.asyncio
async def test_wrong_password_is_401_json_and_no_cookie(db):
    async with _client() as c:
        r = await c.post("/login/app", json={"user_id": 1, "password": "wrong"})
        assert r.status_code == 401 and r.json() == {"ok": False, "error": "סיסמה שגויה"}
        assert "set-cookie" not in r.headers
        r = await c.post("/login/app", json={"user_id": 99, "password": "right"})
        assert r.status_code == 401 and r.json()["error"] == "משתמש לא נמצא"
        r = await c.post("/login/app", json={"user_id": 3, "password": "right"})
        assert r.status_code == 401 and "סיסמה לא הוגדרה" in r.json()["error"]


@pytest.mark.asyncio
async def test_login_sets_the_normal_session_without_remember(db, monkeypatch):
    monkeypatch.setattr(settings, "LESSONS_REMEMBER_DAYS", 90)
    async with _client() as c:
        r = await c.post("/login/app", json={"user_id": 2, "password": "right"})
    assert r.status_code == 200 and r.json() == {"ok": True, "days": 7}
    assert _max_age(r) == 7 * 86400
    assert "httponly" in r.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_remember_gives_the_approved_length_and_the_token_matches(db, monkeypatch):
    monkeypatch.setattr(settings, "LESSONS_REMEMBER_DAYS", 90)
    async with _client() as c:
        r = await c.post("/login/app", json={"user_id": 2, "password": "right", "remember": True})
    assert r.json() == {"ok": True, "days": 90}
    assert _max_age(r) == 90 * 86400
    # The JWT inside must live as long as the cookie, or the cookie outlives the session.
    from jose import jwt
    from app.utils.session import ALGORITHM, SECRET_KEY
    token = r.cookies["access_token"]
    claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert round((claims["exp"] - claims["iat"]) / 86400) == 90
    assert verify_token(token) == {"user_id": 2, "username": "דנה כהן"}


@pytest.mark.asyncio
async def test_remember_is_a_no_op_until_raised(db, monkeypatch):
    monkeypatch.setattr(settings, "LESSONS_REMEMBER_DAYS", 7)
    async with _client() as c:
        r = await c.post("/login/app", json={"user_id": 2, "password": "right", "remember": True})
    assert r.json()["days"] == 7 and _max_age(r) == 7 * 86400


@pytest.mark.asyncio
async def test_html_login_keeps_its_behaviour(db):
    async with _client() as c:
        r = await c.post("/login", data={"user_id": 1, "password": "wrong", "next": "/lessons/"},
                         follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("&next=/lessons/")
        r = await c.post("/login", data={"user_id": 1, "password": "right", "next": "/lessons/"},
                         follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/lessons/"
    assert _max_age(r) == 7 * 86400


# ------------------------------------------------------------------ /lessons/login opens without a session

@pytest.fixture
def built(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>APP</html>")
    monkeypatch.setattr(spa, "SPA_DIR", tmp_path)
    import app.utils.session as sess
    monkeypatch.setattr(sess, "verify_token", lambda t: None)
    return tmp_path


@pytest.mark.asyncio
async def test_in_app_login_needs_no_session_but_nothing_else_opens(built):
    async with _client() as c:
        for path in ("/lessons/login", "/lessons/login/"):
            r = await c.get(path, follow_redirects=False)
            assert r.status_code == 200 and r.text == "<html>APP</html>", path
        for path in ("/lessons/", "/lessons/lessons/3", "/lessons/login/x", "/lessons/loginx", "/lessons/admin"):
            r = await c.get(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path


@pytest.mark.asyncio
async def test_precached_shell_is_the_app_never_the_login_page(built):
    # The service worker fetches /lessons/index.html without a session; a 303 there
    # was stored as the shell and every login "bounced" back to the form.
    async with _client() as c:
        r = await c.get("/lessons/index.html?__WB_REVISION__=abc", follow_redirects=False)
    assert r.status_code == 200 and r.text == "<html>APP</html>"


def test_shell_revision_marker_is_present():
    # Its bump is what evicts a poisoned precache from phones already in the field.
    assert '<meta name="shell-revision"' in Path("lessons_ui/index.html").read_text(encoding="utf-8")


def test_spa_sends_a_missing_session_to_its_own_login():
    client = Path("lessons_ui/src/integrations/supabase/client.ts").read_text(encoding="utf-8")
    assert 'export const SHAN_LOGIN_URL = "/lessons/login";' in client
    ctx = Path("lessons_ui/src/context/UserContext.tsx").read_text(encoding="utf-8")
    # The login page loads the provider too: redirecting there would loop forever.
    assert "if (!isLoginPath(window.location.pathname)) window.location.href = SHAN_LOGIN_URL;" in ctx


def test_app_login_stores_only_the_user_id():
    src = Path("lessons_ui/src/pages/Login.tsx").read_text(encoding="utf-8")
    assert 'const REMEMBERED_KEY = "shan.remembered_user_id";' in src
    assert "localStorage.setItem(REMEMBERED_KEY, v)" in src
    assert "store.set(String(picked.id))" in src
    assert "sessionStorage" not in src and "document.cookie" not in src
    assert src.count("localStorage.setItem") == 1


def test_installed_app_hides_the_shan_navbar_and_keeps_a_logout():
    layout = Path("lessons_ui/src/components/AppLayout.tsx").read_text(encoding="utf-8")
    assert "{!standalone && <ShanNavbar />}" in layout
    assert "onClick={logout}" in layout
