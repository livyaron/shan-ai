"""seed_from_production saves DB-derivable gold, leaves LLM-needed questions for humans."""
import pytest
from unittest.mock import patch

from app.models import QueryLog
from app.services import gold_seed_service as gss


@pytest.mark.asyncio
async def test_seed_saves_db_lookup_skips_llm_needed(db_session):
    from sqlalchemy import delete
    from app.models import EvalGoldAnswer, AnswerFeedback
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()
    db_session.add_all([
        QueryLog(question="מי המנהל של חולה?", ai_response="x"),
        QueryLog(question="שאלה עמומה", ai_response="y"),
    ])
    await db_session.commit()

    async def fake_propose(session, q, *, use_llm=True):
        if "חולה" in q:
            return {"answer": "המנהל: יעקבי, ניר", "source": "db_lookup",
                    "target_project": "WBE-252", "target_field": "manager"}
        return {"answer": "", "source": "manual", "target_project": None, "target_field": None}

    with patch.object(gss, "propose_gold", new=fake_propose):
        result = await gss.seed_from_production(db_session, user_id=None)

    assert result["seeded"] == 1
    assert result["needs_manual"] == 1
    from app.services.gold_truth_service import get_gold
    g = await get_gold(db_session, "מי המנהל של חולה?")
    assert g is not None and g.source == "db_lookup"
    assert await get_gold(db_session, "שאלה עמומה") is None
