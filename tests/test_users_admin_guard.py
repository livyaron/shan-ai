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
