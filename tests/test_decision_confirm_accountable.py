"""POST /dashboard/decisions/confirm enforces the one-Accountable rule server-side.

No DB — the fake session fails the test if the route writes anything.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db_session
from app.main import app
from app.models import RoleEnum, User
from app.routers.login import get_current_user
from app.services.raci_service import accountable_error, parse_user_ids

FORM = {
    "problem_description": "בעיה",
    "type": "info",
    "summary": "סיכום",
    "recommended_action": "פעולה",
    "requires_approval": "true",
}


class _NoWriteSession:
    def add(self, *_a, **_k):
        raise AssertionError("an invalid RACI must be rejected before any write")

    async def commit(self):
        raise AssertionError("an invalid RACI must be rejected before any write")


def test_parse_user_ids_drops_blanks_and_junk():
    assert parse_user_ids("12, 34,,x,56 ") == [12, 34, 56]
    assert parse_user_ids("") == []
    assert parse_user_ids(None) == []


@pytest.mark.parametrize("ids,ok", [([], False), ([7], True), ([7, 8], False), ([7, 7], False)])
def test_accountable_error_requires_exactly_one(ids, ok):
    assert (accountable_error(ids) is None) is ok


@pytest.mark.asyncio
@pytest.mark.parametrize("raci_a", ["", "5,6", "abc"])
async def test_confirm_rejects_missing_or_multiple_accountable(raci_a):
    async def fake_user():
        return User(id=9301, username="raci_u", password_hash="", is_admin=False,
                    role=RoleEnum.PROJECT_MANAGER)

    async def fake_session():
        yield _NoWriteSession()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db_session] = fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/dashboard/decisions/confirm", data={**FORM, "raci_a": raci_a})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 400
    assert "(A)" in r.json()["detail"]
