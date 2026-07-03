"""Smoke tests for the dashboard router (2,800+ lines, previously ~13%
covered). Shallow by design: authenticated GETs must return 200 and render,
POST guards must reject non-admins. Catches import/template/query breakage."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import Decision, DecisionStatusEnum, DecisionTypeEnum, RoleEnum, User
from app.routers.login import get_current_user


async def _seed_user(db_session, uid, username, role=RoleEnum.DIVISION_MANAGER,
                     is_admin=True):
    user = User(id=uid, telegram_id=904000000 + uid, username=username,
                role=role, password_hash="", is_admin=is_admin)
    db_session.add(user)
    await db_session.commit()
    return user


def _override(user):
    async def fake_user():
        return user
    app.dependency_overrides[get_current_user] = fake_user


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dashboard_home_renders(db_session, client):
    user = await _seed_user(db_session, 3001, "dash_admin")
    _override(user)
    async with client as c:
        r = await c.get("/dashboard/")
    assert r.status_code == 200
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


@pytest.mark.asyncio
async def test_dashboard_users_page_lists_users(db_session, client):
    user = await _seed_user(db_session, 3002, "dash_users_admin")
    _override(user)
    async with client as c:
        r = await c.get("/dashboard/users")
    assert r.status_code == 200
    assert "dash_users_admin" in r.text


@pytest.mark.asyncio
async def test_dashboard_decisions_page_renders_seeded_decision(db_session, client):
    user = await _seed_user(db_session, 3003, "dash_dec_admin")
    db_session.add(Decision(
        submitter_id=user.id,
        problem_description="נדרש להחליף שנאי בתחנת בדיקה",
        summary="החלפת שנאי בתחנת בדיקה",
        type=DecisionTypeEnum.NORMAL,
        status=DecisionStatusEnum.PENDING,
    ))
    await db_session.commit()
    _override(user)
    async with client as c:
        r = await c.get("/dashboard/decisions")
    assert r.status_code == 200
    assert "החלפת שנאי" in r.text


@pytest.mark.asyncio
async def test_get_all_users_returns_json(db_session, client):
    user = await _seed_user(db_session, 3004, "dash_json_admin")
    _override(user)
    async with client as c:
        r = await c.get("/dashboard/get-all-users")
    assert r.status_code == 200
    payload = r.json()
    users = payload if isinstance(payload, list) else payload.get("users", [])
    assert any(u.get("username") == "dash_json_admin" for u in users)


@pytest.mark.asyncio
async def test_raci_intelligence_page_renders(db_session, client):
    user = await _seed_user(db_session, 3005, "dash_raci_admin")
    _override(user)
    async with client as c:
        r = await c.get("/dashboard/raci-intelligence")
    assert r.status_code == 200


# ── Admin-only guards on user management ─────────────────────────────────────
# Regression tests: these endpoints accepted ANY authenticated user (even a
# VIEWER) before the _admin_guard fix — privilege escalation via toggle-admin.

@pytest.mark.asyncio
async def test_non_admin_cannot_create_user(db_session, client):
    from sqlalchemy import select
    user = await _seed_user(db_session, 3006, "dash_plain",
                            role=RoleEnum.PROJECT_MANAGER, is_admin=False)
    _override(user)
    async with client as c:
        r = await c.post("/dashboard/users/create", data={
            "username": "sneaky", "role": "project_manager",
        })
    assert r.status_code == 303
    assert "error" in r.headers["location"]
    assert await db_session.scalar(
        select(User).where(User.username == "sneaky")) is None


@pytest.mark.asyncio
async def test_non_admin_cannot_grant_self_admin(db_session, client):
    from sqlalchemy import select
    user = await _seed_user(db_session, 3007, "dash_escalate",
                            role=RoleEnum.PROJECT_MANAGER, is_admin=False)
    uid = user.id
    _override(user)
    async with client as c:
        r = await c.post(f"/dashboard/users/{uid}/toggle-admin")
    assert r.status_code == 303
    assert "error" in r.headers["location"]
    db_session.expire_all()
    refreshed = await db_session.scalar(select(User).where(User.id == uid))
    assert refreshed.is_admin is False


@pytest.mark.asyncio
async def test_non_admin_cannot_delete_user(db_session, client):
    from sqlalchemy import select
    victim = await _seed_user(db_session, 3008, "dash_victim",
                              role=RoleEnum.PROJECT_MANAGER, is_admin=False)
    attacker = await _seed_user(db_session, 3009, "dash_attacker",
                                role=RoleEnum.PROJECT_MANAGER, is_admin=False)
    victim_id = victim.id
    _override(attacker)
    async with client as c:
        r = await c.post(f"/dashboard/users/{victim_id}/delete")
    assert r.status_code == 303
    assert "error" in r.headers["location"]
    db_session.expire_all()
    assert await db_session.scalar(
        select(User).where(User.id == victim_id)) is not None


@pytest.mark.asyncio
async def test_non_admin_cannot_set_role(db_session, client):
    from sqlalchemy import select
    user = await _seed_user(db_session, 3010, "dash_setrole",
                            role=RoleEnum.VIEWER, is_admin=False)
    uid = user.id
    _override(user)
    async with client as c:
        r = await c.post(f"/dashboard/users/{uid}/set-role",
                         data={"role": "division_manager"})
    assert r.status_code == 303
    assert "error" in r.headers["location"]
    db_session.expire_all()
    refreshed = await db_session.scalar(select(User).where(User.id == uid))
    assert refreshed.role == RoleEnum.VIEWER


@pytest.mark.asyncio
async def test_admin_can_create_user(db_session, client):
    from sqlalchemy import select
    admin = await _seed_user(db_session, 3011, "dash_creator", is_admin=True)
    _override(admin)
    async with client as c:
        r = await c.post("/dashboard/users/create", data={
            "username": "created_by_admin", "role": "project_manager",
        })
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]
    created = await db_session.scalar(
        select(User).where(User.username == "created_by_admin"))
    assert created is not None
    assert created.role == RoleEnum.PROJECT_MANAGER
    assert created.registration_code  # QR onboarding code generated
