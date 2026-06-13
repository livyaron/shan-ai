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
