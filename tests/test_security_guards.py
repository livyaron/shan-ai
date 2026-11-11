"""Server-side guards added in the 2026-11-11 security pass. No database:
every guard answers before the endpoint touches the session.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.models import RoleEnum, User
from app.routers import files as files_router
from app.routers import lessons_gateway as lg
from app.routers.login import get_current_user


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _as(user):
    async def fake():
        return user
    app.dependency_overrides[get_current_user] = fake


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ open routes that are gone

@pytest.mark.asyncio
async def test_unauthenticated_role_assignment_no_longer_exists():
    async with _client() as c:
        for path, body in (("/api/v1/auth/approve-role", {"user_id": 1, "role": "division_manager"}),
                           ("/api/v1/auth/register", {"username": "xxx", "telegram_id": 5, "email": "a@b.co"})):
            assert (await c.post(path, json=body)).status_code == 404
        assert (await c.get("/api/v1/auth/users/5")).status_code == 404
        assert (await c.get("/telegram/_diag_timing")).status_code == 404


# ------------------------------------------------------------------ telegram webhook

def test_webhook_secret_is_never_empty_while_a_bot_token_exists(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:abc")
    derived = settings.webhook_secret
    assert len(derived) == 64 and "123:abc" not in derived
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "explicit")
    assert settings.webhook_secret == "explicit"


@pytest.mark.asyncio
async def test_webhook_refuses_an_update_without_the_secret(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:abc")
    forged = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 9, "type": "private"},
                                          "from": {"id": 9, "is_bot": False, "first_name": "x"}, "text": "hi"}}
    async with _client() as c:
        assert (await c.post("/telegram/webhook", json=forged)).status_code == 403
        r = await c.post("/telegram/webhook", json=forged,
                         headers={"X-Telegram-Bot-Api-Secret-Token": "guess"})
        assert r.status_code == 403


def test_set_webhook_registers_the_same_secret():
    import inspect
    from app.services.telegram_polling import telegram_bot
    assert "secret_token=settings.webhook_secret" in inspect.getsource(type(telegram_bot).set_webhook)


# ------------------------------------------------------------------ user management is the admin's

@pytest.mark.asyncio
@pytest.mark.parametrize("path,data", [
    ("/dashboard/users/create", {"username": "new one", "role": "division_manager"}),
    ("/dashboard/users/1/set-role", {"role": "division_manager"}),
    ("/dashboard/users/1/delete", {}),
    ("/dashboard/users/1/regen-code", {}),
])
async def test_non_admin_cannot_manage_users(path, data):
    _as(User(id=9102, username="plain", password_hash="", is_admin=False, role=RoleEnum.PROJECT_MANAGER))
    async with _client() as c:
        r = await c.post(path, data=data, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


@pytest.mark.asyncio
async def test_registration_qr_is_for_the_admin_or_the_user_themself():
    _as(User(id=9102, username="plain", password_hash="", is_admin=False))
    async with _client() as c:
        assert (await c.get("/dashboard/users/1/qr")).status_code == 403


def test_a_user_cannot_change_their_own_role():
    import inspect
    from app.routers import dashboard
    src = inspect.getsource(dashboard.edit_user)
    assert src.index("if not current_user.is_admin:\n        role = \"\"") < src.index("user.role = RoleEnum(role)")


# ------------------------------------------------------------------ knowledge files

@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_master", "unset_master", "reprocess", "delete"])
async def test_viewer_cannot_change_knowledge_files(action):
    _as(User(id=9103, username="viewer", password_hash="", is_admin=False, role=RoleEnum.VIEWER))
    async with _client() as c:
        r = await c.post(f"/dashboard/files/1/{action}", follow_redirects=False)
    assert r.status_code == 403


def test_every_file_mutation_checks_the_writer():
    import inspect
    for fn in (files_router.upload_file, files_router.set_master_file, files_router.unset_master_file,
               files_router.edit_file, files_router.replace_file, files_router.reprocess_file,
               files_router.delete_file):
        assert "_require_writer(current_user)" in inspect.getsource(fn), fn.__name__


@pytest.mark.parametrize("given,stored", [
    ("דוח שבועי.xlsx", "דוח שבועי.xlsx"),
    ("../../app/main.py", "main.py"),
    ("..\\..\\evil.xlsx", "evil.xlsx"),
    ("/etc/passwd", "passwd"),
    ("", "file"), (None, "file"),
])
def test_upload_name_cannot_leave_the_upload_dir(given, stored):
    assert files_router._stored_name(given) == stored


# ------------------------------------------------------------------ lessons uploads

@pytest.mark.asyncio
async def test_only_pictures_and_pdfs_open_in_the_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LESSONS_UPLOAD_DIR", str(tmp_path))
    (tmp_path / "a.html").write_text("<script>alert(1)</script>")
    (tmp_path / "b.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "c.png").write_bytes(b"\x89PNG")
    for name, inline in (("a.html", False), ("b.svg", False), ("c.png", True)):
        r = await lg.storage_read(lg.BUCKET, name, identity=None)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert ("attachment" not in r.headers.get("content-disposition", "")) is inline, name


# ------------------------------------------------------------------ response headers

@pytest.mark.asyncio
async def test_security_headers_on_every_response():
    async with _client() as c:
        r = await c.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "scheduler_running" in r.json()
