"""propose_gold project-name branch: 1 card / multi-card / narrowing / non-project."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import gold_truth_service as gts


@pytest.mark.asyncio
async def test_single_match_returns_rich_card(db_session):
    from app.models import Project
    proj = Project(project_identifier="WBE-999", name="טסטוביל", manager="כהן, דנה",
                   stage="הרכבה חשמלית", is_active=True)
    db_session.add(proj)
    await db_session.commit()
    await db_session.refresh(proj)

    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier",
                      new=AsyncMock(return_value=[{"id": proj.id, "project_identifier": "WBE-999", "name": "טסטוביל", "manager": "כהן, דנה", "stage": "הרכבה חשמלית"}])):
        res = await gts.propose_gold(db_session, "טסטוביל", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-999" in res["answer"]
    assert "טסטוביל" in res["answer"]
    assert "מנה" in res["answer"]
    assert "📁" not in res["answer"]
    assert res["target_project"] == "WBE-999"


@pytest.mark.asyncio
async def test_two_matches_returns_combined_multicard(db_session):
    matches = [
        {"id": 1, "project_identifier": "WBE-204", "name": "אשלים-התקנת 2 שנאים", "manager": "א", "stage": "תכנון"},
        {"id": 2, "project_identifier": "WBE-180", "name": "אשלים-PV3", "manager": "ב", "stage": "ביצוע"},
    ]
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=matches)):
        res = await gts.propose_gold(db_session, "אשלים", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-204" in res["answer"] and "WBE-180" in res["answer"]
    assert "\n" in res["answer"]
    assert "📁" not in res["answer"]
    assert res["target_project"] is None


@pytest.mark.asyncio
async def test_too_many_matches_returns_narrowing(db_session):
    matches = [{"id": i, "project_identifier": f"WBE-{i}", "name": f"בית {i}"} for i in range(1, 8)]
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=matches)):
        res = await gts.propose_gold(db_session, "בית", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-1" in res["answer"]
    assert res["answer"]
    assert res["source"] != "manual"


@pytest.mark.asyncio
async def test_no_project_match_stays_manual(db_session):
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=[])):
        res = await gts.propose_gold(db_session, "שלום", use_llm=False)

    assert res["source"] == "manual"
    assert (res["answer"] or "") == ""
