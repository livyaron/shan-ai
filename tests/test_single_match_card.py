"""by_identifier single match returns a formatted card, never raw JSON, without an LLM call."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import project_tools as pt


@pytest.mark.asyncio
async def test_single_match_returns_card_no_llm(db_session):
    one = [{"id": 1, "project_identifier": "WBE-178", "name": "רמת חובב",
            "manager": "כהן", "stage": "תכנון", "project_type": "הרחבה",
            "estimated_finish_date": "", "dev_plan_date": "", "to_handle": "",
            "weekly_report": "", "risks": "", "delay_months": None,
            "last_updated": ""}]
    user_data: dict = {}
    with patch.object(pt, "find_projects_by_identifier", new=AsyncMock(return_value=one)), \
         patch.object(pt, "llm_chat", new=AsyncMock(side_effect=AssertionError("LLM must not be called for single match"))), \
         patch.object(pt, "_log_query", new=AsyncMock(return_value=123)):
        answer, log_id = await pt.answer_project_query(
            "WBE-178", db_session, user_data,
            user_id=3,
            precomputed_intent="by_identifier",
            precomputed_param="WBE-178",
        )

    assert "WBE-178" in answer
    assert '{"id"' not in answer          # not raw JSON
    assert "\U0001f4c1" in answer          # 📁 card header
    assert log_id == 123
