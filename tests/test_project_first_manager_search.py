"""Tests for project-first manager search fix."""
import json
import pytest
from unittest.mock import patch

from app.services.project_tools import search_by_manager, find_projects_by_identifier


@pytest.mark.asyncio
async def test_search_by_manager_multi_token_requires_all_tokens(db_session):
    """Multi-token query must match ALL tokens (AND), not any token (OR)."""
    from app.models import Project

    # Manager "ניר יעקבי" — has "ניר" but NOT "יצחק"
    mgr = Project(
        name="פרויקט בדיקה",
        project_identifier="TEST-01",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    # "ניר יצחק" should NOT match "ניר יעקבי" with AND logic
    results = await search_by_manager("ניר יצחק", db_session)
    names = [r["manager"] for r in results]
    assert "ניר יעקבי" not in names, (
        "AND logic should not match 'ניר יעקבי' when searching 'ניר יצחק'"
    )


@pytest.mark.asyncio
async def test_search_by_manager_multi_token_matches_correct_manager(db_session):
    """Multi-token query must still match the correct manager with AND logic."""
    from app.models import Project

    mgr = Project(
        name="פרויקט בדיקה 2",
        project_identifier="TEST-02",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    # "ניר יעקבי" should match "ניר יעקבי"
    results = await search_by_manager("ניר יעקבי", db_session)
    assert len(results) >= 1
    assert any(r["manager"] == "ניר יעקבי" for r in results)


@pytest.mark.asyncio
async def test_search_by_manager_single_token_unchanged(db_session):
    """Single-token queries must still work (no regression)."""
    from app.models import Project

    mgr = Project(
        name="פרויקט בדיקה 3",
        project_identifier="TEST-03",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    results = await search_by_manager("ניר", db_session)
    assert any(r["manager"] == "ניר יעקבי" for r in results)


@pytest.mark.asyncio
async def test_by_manager_intent_prefers_project_when_name_matches(db_session):
    """When intent is by_manager but the param matches a project name, return project card."""
    from app.models import Project
    from app.services.project_tools import answer_project_query

    # Seed a project named "ניר יצחק"
    proj = Project(
        name="ניר יצחק",
        project_identifier="NIR-01",
        stage="תכנון",
        manager="כלשהו מנהל",
        is_active=True,
    )
    db_session.add(proj)
    await db_session.commit()

    async def fake_llm(*args, **kwargs):
        return "פרויקט ניר יצחק נמצא בשלב תכנון."

    with patch("app.services.project_tools.llm_chat", side_effect=fake_llm):
        answer, _ = await answer_project_query(
            text="ניר יצחק",
            session=db_session,
            user_data={},
            user_id=None,
            precomputed_intent="by_manager",
            precomputed_param="ניר יצחק",
        )

    assert "NIR-01" in answer or "ניר יצחק" in answer, (
        f"Expected project card, got: {answer!r}"
    )
    assert "פרויקטים של" not in answer, (
        f"Should not return manager project list, got: {answer!r}"
    )
