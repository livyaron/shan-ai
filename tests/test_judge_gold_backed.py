"""judge_one prefers real gold and reports whether it was gold-backed."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog
from app.services import judge_backfill_service as jbs


@pytest.mark.asyncio
async def test_judge_one_uses_gold_when_present():
    log = QueryLog(question="מי המנהל של חולה?", ai_response="יעקבי, ניר")

    class _Gold:
        gold_answer = "המנהל: יעקבי, ניר"

    with patch.object(jbs, "get_gold", new=AsyncMock(return_value=_Gold())) as g, \
         patch.object(jbs, "propose_gold", new=AsyncMock()) as p, \
         patch.object(jbs, "compare_to_gold", new=AsyncMock(return_value=1.0)):
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)

    assert verdict == "PASS"
    assert failure is None
    assert gold_backed is True
    g.assert_awaited_once()
    p.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_one_falls_back_to_propose_when_no_gold():
    log = QueryLog(question="שאלה נדירה", ai_response="תשובה")

    with patch.object(jbs, "get_gold", new=AsyncMock(return_value=None)), \
         patch.object(jbs, "propose_gold", new=AsyncMock(return_value={"answer": "ref"})), \
         patch.object(jbs, "compare_to_gold", new=AsyncMock(return_value=0.0)), \
         patch.object(jbs, "_classify_failure", new=AsyncMock(return_value="MISSING_DATA")):
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)

    assert verdict == "FAIL"
    assert failure == "MISSING_DATA"
    assert gold_backed is False


@pytest.mark.asyncio
async def test_judge_one_empty_answer_is_refused_and_not_gold_backed():
    log = QueryLog(question="ש", ai_response="")
    with patch.object(jbs, "get_gold", new=AsyncMock()) as g:
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)
    assert (verdict, failure) == ("FAIL", "REFUSED")
    assert gold_backed is False
    g.assert_not_awaited()


from app.services.gold_truth_service import save_gold


@pytest.mark.asyncio
async def test_rejudge_only_touches_gold_covered(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    covered = QueryLog(question="מי המנהל של חולה?", ai_response="יעקבי, ניר", judge_verdict="FAIL")
    uncovered = QueryLog(question="שאלה ללא זהב", ai_response="משהו", judge_verdict="FAIL")
    db_session.add_all([covered, uncovered])
    await db_session.commit()

    await save_gold(db_session, question="מי המנהל של חולה?", gold_answer="יעקבי, ניר",
                    user_id=None, source="db_lookup")

    with patch.object(jbs, "judge_one",
                      new=AsyncMock(return_value=("PASS", None, True))) as j:
        stats = await jbs.rejudge_gold_covered(db_session, limit=100)

    assert j.await_count == 1
    await db_session.refresh(covered)
    await db_session.refresh(uncovered)
    assert covered.judge_verdict == "PASS"
    assert covered.judged_against_gold is True
    assert uncovered.judge_verdict == "FAIL"
    assert stats["judged"] == 1


@pytest.mark.asyncio
async def test_rejudge_distinct_judges_one_per_question(db_session):
    from sqlalchemy import delete
    from datetime import datetime, timedelta
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        QueryLog(question="ש1", ai_response="a", judge_verdict="FAIL", timestamp=base),
        QueryLog(question="ש1", ai_response="b", judge_verdict="FAIL", timestamp=base + timedelta(hours=1)),
        QueryLog(question="ש2", ai_response="c", judge_verdict="FAIL", timestamp=base),
    ])
    await db_session.commit()

    with patch.object(jbs, "judge_one",
                      new=AsyncMock(return_value=("PASS", None, True))) as j:
        stats = await jbs.rejudge_distinct(db_session)

    assert j.await_count == 2
    assert stats["judged"] == 2
