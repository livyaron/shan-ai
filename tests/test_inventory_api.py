"""GET /api/inventory: read-only JSON, closed unless INVENTORY_API_KEY is set.

No DB — a fake session answers the two SELECTs. Nothing here calls the live site.
"""

import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db_session
from app.main import app

KEY = "test-inventory-key"


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
    def __init__(self, projects, master):
        self.projects = projects
        self.master = master
        self.statements = []

    async def execute(self, stmt, *_a, **_k):
        sql = str(stmt)
        self.statements.append(sql)
        if "knowledge_files" in sql.lower():
            return _Result(self.master)
        return _Result(self.projects)


def _project(**over):
    base = dict(
        project_identifier="P-1",
        name="Alpha",
        manager="Ada",
        stage="build",
        last_updated=datetime.datetime(2026, 10, 3, 12, 0, 0),
        weekly_report="LONG WEEKLY",
        risks="LONG RISKS",
        is_active=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _override_session(projects, master):
    session = _Session(projects, master)

    async def fake_session():
        yield session

    app.dependency_overrides[get_db_session] = fake_session
    return session


@pytest.fixture
def inventory_rows():
    projects = [
        _project(project_identifier="P-1", name="Alpha"),
        _project(
            project_identifier="P-2",
            name="Beta",
            manager=None,
            stage=None,
            last_updated=None,
        ),
    ]
    master = [
        SimpleNamespace(
            created_at=datetime.datetime(2026, 9, 1, 8, 30, 0),
            original_name="master.xlsx",
            is_master=True,
        )
    ]
    session = _override_session(projects, master)
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
async def test_inventory_401_without_key(inventory_rows, configured_key):
    async with _client() as client:
        response = await client.get("/api/inventory")

    assert response.status_code == 401
    assert KEY not in response.text


@pytest.mark.asyncio
async def test_inventory_401_with_wrong_key(inventory_rows, configured_key):
    async with _client() as client:
        response = await client.get(
            "/api/inventory",
            headers={"Authorization": "Bearer wrong-key"},
        )

    assert response.status_code == 401
    assert KEY not in response.text
    assert "wrong-key" not in response.text


@pytest.mark.asyncio
async def test_inventory_200_with_the_right_key(inventory_rows, configured_key):
    async with _client() as client:
        response = await client.get(
            "/api/inventory",
            headers={"Authorization": f"Bearer {KEY}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"master", "projects"}
    assert body["master"] == {
        "uploaded_at": "2026-09-01T08:30:00Z",
        "filename": "master.xlsx",
    }
    assert [p["project_identifier"] for p in body["projects"]] == ["P-1", "P-2"]
    assert body["projects"][0] == {
        "project_identifier": "P-1",
        "name": "Alpha",
        "manager": "Ada",
        "stage": "build",
        "last_updated": "2026-10-03T12:00:00Z",
    }
    assert body["projects"][1]["name"] == "Beta"
    assert body["projects"][1]["manager"] is None
    assert body["projects"][1]["last_updated"] is None
    for row in body["projects"]:
        assert "status" not in row
        assert "weekly_report" not in row
        assert "risks" not in row
    assert "LONG WEEKLY" not in response.text
    assert KEY not in response.text

    project_sql = [s.lower() for s in inventory_rows.statements if "knowledge_files" not in s.lower()]
    assert project_sql, inventory_rows.statements
    assert "is_active" in project_sql[0]
    assert "order by" in project_sql[0] and "name" in project_sql[0]
    master_sql = [s.lower() for s in inventory_rows.statements if "knowledge_files" in s.lower()]
    assert master_sql and "is_master" in master_sql[0]


@pytest.mark.asyncio
async def test_inventory_503_when_env_var_is_empty(monkeypatch, inventory_rows):
    monkeypatch.setenv("INVENTORY_API_KEY", "")
    monkeypatch.setattr(settings, "INVENTORY_API_KEY", "")

    async with _client() as client:
        response = await client.get(
            "/api/inventory",
            headers={"Authorization": "Bearer something"},
        )

    assert response.status_code == 503
    assert "something" not in response.text
    assert response.json()["detail"] == "Inventory API is not configured"
