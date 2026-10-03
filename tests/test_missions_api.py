"""GET /api/missions: read-only JSON, same INVENTORY_API_KEY as inventory.

No DB — a fake session answers the SELECT. Nothing here calls the live site.
"""

import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db_session
from app.main import app

KEY = "test-missions-key"

FIELDS = {
    "id",
    "title",
    "description",
    "status",
    "is_urgent",
    "is_important",
    "quadrant",
    "due_date",
    "owner",
    "parent_id",
    "completed_at",
    "created_at",
    "updated_at",
    "latest_note",
}


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _Session:
    def __init__(self, missions):
        self.missions = missions
        self.statements = []

    async def execute(self, stmt, *_a, **_k):
        sql = str(stmt)
        self.statements.append(sql)
        return _Result(self.missions)


def _update(**over):
    base = dict(
        id=1,
        text="short note",
        created_at=datetime.datetime(2026, 10, 1, 9, 0, 0),
        author_name="Ada",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _mission(**over):
    base = dict(
        id=1,
        title="Alpha",
        description="do the thing",
        status="open",
        is_urgent=True,
        is_important=True,
        due_date=datetime.date(2026, 10, 5),
        owner=SimpleNamespace(
            username="Ada",
            email="ada@example.com",
            telegram_id=999,
            password_hash="secret-hash",
        ),
        parent_id=None,
        completed_at=None,
        created_at=datetime.datetime(2026, 10, 1, 8, 0, 0),
        updated_at=datetime.datetime(2026, 10, 2, 8, 0, 0),
        updates=[_update()],
        weekly_report="LONG WEEKLY",
        risks="LONG RISKS",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _override_session(missions):
    session = _Session(missions)

    async def fake_session():
        yield session

    app.dependency_overrides[get_db_session] = fake_session
    return session


@pytest.fixture
def mission_rows():
    missions = [
        _mission(),
        _mission(
            id=2,
            title="Beta",
            description=None,
            status="done",
            is_urgent=False,
            is_important=True,
            due_date=None,
            owner=None,
            parent_id=1,
            completed_at=datetime.datetime(2026, 10, 3, 15, 0, 0),
            updates=[],
        ),
        _mission(
            id=3,
            title="Gamma",
            status="open",
            is_urgent=True,
            is_important=False,
            owner=SimpleNamespace(username="  "),
            updates=[
                _update(id=1, text="older", created_at=datetime.datetime(2026, 9, 1, 0, 0, 0)),
                _update(
                    id=2,
                    text=("word " * 80).strip(),
                    created_at=datetime.datetime(2026, 10, 2, 12, 0, 0),
                ),
            ],
        ),
        _mission(
            id=4,
            title="Delta",
            status="cancelled",
            is_urgent=False,
            is_important=False,
            updates=[_update(text="   ")],
        ),
    ]
    session = _override_session(missions)
    try:
        yield session
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def configured_key(monkeypatch):
    monkeypatch.setenv("INVENTORY_API_KEY", KEY)
    monkeypatch.setattr(settings, "INVENTORY_API_KEY", KEY)


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_missions_401_without_key(mission_rows, configured_key):
    async with _client() as client:
        response = await client.get("/api/missions")

    assert response.status_code == 401
    assert KEY not in response.text


@pytest.mark.asyncio
async def test_missions_401_with_wrong_key(mission_rows, configured_key):
    async with _client() as client:
        response = await client.get(
            "/api/missions",
            headers={"Authorization": "Bearer wrong-key"},
        )

    assert response.status_code == 401
    assert KEY not in response.text
    assert "wrong-key" not in response.text


@pytest.mark.asyncio
async def test_missions_503_when_env_var_is_empty(monkeypatch, mission_rows):
    monkeypatch.setenv("INVENTORY_API_KEY", "")
    monkeypatch.setattr(settings, "INVENTORY_API_KEY", "")

    async with _client() as client:
        response = await client.get(
            "/api/missions",
            headers={"Authorization": "Bearer something"},
        )

    assert response.status_code == 503
    assert "something" not in response.text
    assert response.json()["detail"] == "Missions API is not configured"


@pytest.mark.asyncio
async def test_missions_200_with_the_right_key(mission_rows, configured_key):
    async with _client() as client:
        response = await client.get(
            "/api/missions",
            headers={"Authorization": f"Bearer {KEY}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"missions"}
    rows = body["missions"]
    assert [m["id"] for m in rows] == [1, 2, 3, 4]
    for row in rows:
        assert set(row) == FIELDS

    assert rows[0] == {
        "id": 1,
        "title": "Alpha",
        "description": "do the thing",
        "status": "open",
        "is_urgent": True,
        "is_important": True,
        "quadrant": "do",
        "due_date": "2026-10-05",
        "owner": "Ada",
        "parent_id": None,
        "completed_at": None,
        "created_at": "2026-10-01T08:00:00Z",
        "updated_at": "2026-10-02T08:00:00Z",
        "latest_note": "short note",
    }
    assert rows[1]["quadrant"] == "plan"
    assert rows[1]["owner"] is None
    assert rows[1]["due_date"] is None
    assert rows[1]["parent_id"] == 1
    assert rows[1]["completed_at"] == "2026-10-03T15:00:00Z"
    assert rows[1]["latest_note"] is None
    assert rows[1]["status"] == "done"
    assert rows[2]["quadrant"] == "delegate"
    assert rows[2]["owner"] is None
    note = rows[2]["latest_note"]
    assert note is not None
    assert note.endswith("...")
    assert len(note) <= 200
    assert "word word word word word word" not in note[180:]
    assert rows[3]["quadrant"] == "backlog"
    assert rows[3]["status"] == "cancelled"
    assert rows[3]["latest_note"] is None

    text = response.text
    assert "ada@example.com" not in text
    assert "secret-hash" not in text
    assert "999" not in text
    assert "LONG WEEKLY" not in text
    assert "LONG RISKS" not in text
    assert KEY not in text

    assert mission_rows.statements, "expected a SELECT"
    sql = " ".join(mission_rows.statements).lower()
    assert sql.startswith("select")
    assert "missions" in sql
    assert "insert" not in sql
    assert "delete" not in sql
    assert "missions.status" in sql
    assert "completed_at" in sql
    assert "order by" in sql
    assert "due_date asc nulls last" in sql
    assert "missions.title asc" in sql

    # Bind parameters hide the status literals in str(stmt). Compile them.
    from sqlalchemy.dialects import postgresql
    from app.routers.missions_api import _missions_query
    compiled = str(
        _missions_query().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "missions.status = 'open'" in compiled
    assert "'done'" in compiled and "'cancelled'" in compiled
    assert "completed_at" in compiled
    assert "insert" not in compiled and "delete" not in compiled


@pytest.mark.asyncio
async def test_missions_rejects_writes(mission_rows, configured_key):
    async with _client() as client:
        for method in ("post", "patch", "delete"):
            response = await getattr(client, method)(
                "/api/missions",
                headers={"Authorization": f"Bearer {KEY}"},
            )
            assert response.status_code == 405
            assert KEY not in response.text
