"""Endpoint integration tests for /dashboard/learning/rules CRUD."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from app.main import app
from app.models import ProjectAlias, User
from app.routers.login import get_current_user


async def _seed_admin(db_session, uid=2001):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        f"VALUES ({uid}, {900000000 + uid}, 'admin_t', 'DIVISION_MANAGER', '', true) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_rules_page_returns_html(db_session):
    await _seed_admin(db_session)
    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/rules")
        assert r.status_code == 200
        assert "אליאסים" in r.text or "כללי למידה" in r.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_project_alias(db_session, seeded_project_id):
    await _seed_admin(db_session)
    pid = seeded_project_id

    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/learning/rules/aliases",
                json={"alias_text": "TestAlias-Z9", "project_id": pid},
            )
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()

    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-Z9"))
    assert row is not None
    assert row.source == "manual"


@pytest.mark.asyncio
async def test_delete_project_alias(db_session, seeded_project_id):
    await _seed_admin(db_session)
    pid = seeded_project_id
    from app.services.knowledge_service import normalize_hebrew
    db_session.add(ProjectAlias(
        project_id=pid, alias_text="TestAlias-Del",
        normalized_alias=normalize_hebrew("TestAlias-Del"),
        source="manual",
    ))
    await db_session.commit()
    alias = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-Del"))

    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.delete(f"/dashboard/learning/rules/aliases/{alias.id}")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()

    still = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.id == alias.id))
    assert still is None


@pytest.mark.asyncio
async def test_admin_only(db_session):
    """Non-admin user gets 403 on CRUD endpoints."""
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (2099, 902002099, 'non_admin', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()

    async def fake_user():
        return User(id=2099, telegram_id=902002099, username="non_admin",
                    role="PROJECT_MANAGER", password_hash="", is_admin=False)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/learning/rules/aliases",
                json={"alias_text": "X", "project_id": 1},
            )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
