"""find_projects_by_identifier strips leading substation/station prefixes as a fallback."""
import pytest
from sqlalchemy import delete

from app.models import Project
from app.services.project_tools import find_projects_by_identifier

# Tables that FK-reference projects — must be cleared before deleting projects.
_FK_TABLES = ("project_aliases", "project_snapshots", "correction_pins")


async def _clear(db_session):
    from sqlalchemy import text
    for tbl in _FK_TABLES:
        await db_session.execute(text(f"DELETE FROM {tbl}"))
    await db_session.execute(delete(Project))
    await db_session.commit()


async def _seed(db_session, name, ident):
    p = Project(project_identifier=ident, name=name, is_active=True)
    db_session.add(p)
    await db_session.commit()


@pytest.mark.asyncio
async def test_strips_tachmash_prefix(db_session):
    await _clear(db_session)
    await _seed(db_session, "ניר יצחק - הקמת תחנה", "WBE-700")

    direct = await find_projects_by_identifier('תחמ"ש ניר יצחק', db_session)
    assert any(m["project_identifier"] == "WBE-700" for m in direct)


@pytest.mark.asyncio
async def test_strips_tachanat_prefix(db_session):
    await _clear(db_session)
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    res = await find_projects_by_identifier("תחנת נתניה", db_session)
    assert any(m["project_identifier"] == "WBE-701" for m in res)


@pytest.mark.asyncio
async def test_prefix_only_returns_nothing(db_session):
    await _clear(db_session)
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    res = await find_projects_by_identifier('תחמ"ש', db_session)
    assert res == []


@pytest.mark.asyncio
async def test_existing_match_unchanged(db_session):
    await _clear(db_session)
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    res = await find_projects_by_identifier("נתניה", db_session)
    assert any(m["project_identifier"] == "WBE-701" for m in res)
