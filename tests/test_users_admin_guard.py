"""ניהול מנהלי מערכת: הצפייה פתוחה לכולם, השינוי רק למנהל מערכת.

No DB needed — the guard returns before the endpoint touches the session.
"""
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models import User
from app.routers.login import get_current_user


def _user(is_admin: bool) -> User:
    return User(id=9101 if is_admin else 9102,
                username="admin_g" if is_admin else "plain_g",
                password_hash="", is_admin=is_admin)


@pytest.mark.asyncio
async def test_non_admin_cannot_toggle_admin():
    async def fake_user():
        return _user(is_admin=False)

    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/dashboard/users/1/toggle-admin",
                                  follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    from urllib.parse import unquote

    assert r.status_code == 303
    location = unquote(r.headers["location"])
    assert "error=" in location
    assert "רק+מנהל+מערכת" in location


def test_template_hides_toggle_form_from_non_admin():
    """The button is admin-only, but the 👑 badge stays visible to everyone."""
    from pathlib import Path
    html = Path("app/templates/users.html").read_text(encoding="utf-8")
    assert "{% if current_user.is_admin %}" in html
    # the POST form sits inside the admin-only branch
    idx_form = html.index("/toggle-admin")
    idx_guard = html.rindex("{% if current_user.is_admin %}", 0, idx_form)
    assert idx_form - idx_guard < 400, "toggle form is not behind the admin guard"
    # read-only fallback still renders the crown
    assert "רק מנהל מערכת יכול לשנות הרשאה זו" in html


# -----------------------------------------------------------------------
# סיסמאות: bcrypt חד-כיווני — אין "הצג קיימת", יש "אפס וקבל" + חיווי ברירת מחדל
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_cannot_reset_password():
    async def fake_user():
        return _user(is_admin=False)

    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/dashboard/users/1/reset-password")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 403
    assert r.json()["ok"] is False


def test_generated_password_is_unguessable_and_readable():
    from app.utils.auth import generate_password

    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50, "generated passwords must not repeat"
    for pw in passwords:
        # no 0/O/1/l/I — these get dictated over the phone
        assert not (set(pw) & set("0O1lI")), pw
        assert "-" in pw and len(pw) >= 10


def test_default_password_flag_answers_only_the_known_question():
    from app.utils.auth import (
        DEFAULT_PASSWORD, get_default_password_hash, hash_password,
        is_default_password,
    )

    assert is_default_password(get_default_password_hash()) is True
    assert is_default_password(hash_password("something else")) is False
    # a missing hash is not "still on the default"
    assert is_default_password("") is False
    assert is_default_password(None) is False
    # bcrypt salts, so the same password never yields the same hash
    assert get_default_password_hash() != get_default_password_hash()
    assert DEFAULT_PASSWORD == "1234"


def test_password_is_never_returned_through_a_redirect_url():
    """The clear-text password lives in a JSON body only — never in a URL."""
    import inspect
    from app.routers import dashboard

    src = inspect.getsource(dashboard.reset_password)
    assert "JSONResponse" in src
    assert "RedirectResponse" not in src


def test_template_shows_default_badge_to_admins_only():
    from pathlib import Path
    html = Path("app/templates/users.html").read_text(encoding="utf-8")
    assert "{% if current_user.is_admin and user.password_is_default %}" in html
    # the modal states plainly why the existing password cannot be shown
    assert "לא ניתנת לשחזור" in html
