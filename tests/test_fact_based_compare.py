"""compare_to_gold uses deterministic project-id matching for id-bearing gold."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import gold_truth_service as gts


@pytest.mark.asyncio
async def test_right_id_passes_no_llm():
    gold = '‏WBE-252 | חולה - החלפת שנאי | מנה"פ: יעקבי, ניר | שלב: עבודה אזרחית'
    ans = '📁 מזהה: WBE-252\nשם: חולה\nמנה"פ: יעקבי, ניר'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM for id-bearing gold"))):
        assert await gts.compare_to_gold("חולה", ans, gold) == 1.0


@pytest.mark.asyncio
async def test_wrong_id_fails_no_llm():
    gold = '‏WBE-252 | חולה | מנה"פ: יעקבי'
    ans = '📁 מזהה: WBE-999\nשם: משהו אחר'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        assert await gts.compare_to_gold("חולה", ans, gold) == 0.0


@pytest.mark.asyncio
async def test_raw_json_with_right_id_passes():
    gold = '‏WBE-195 | עתלית | מנה"פ: כהן | שלב: תכנון'
    ans = '{"id": 394, "project_identifier": "WBE-195", "name": "עתלית- התקנת שנאי"}'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        assert await gts.compare_to_gold("עתלית", ans, gold) == 1.0


@pytest.mark.asyncio
async def test_multi_id_all_present_passes():
    gold = '‏WBE-204 | אשלים א\n‏WBE-180 | אשלים ב'
    ans = 'WBE-204 ... WBE-180 ...'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        assert await gts.compare_to_gold("אשלים", ans, gold) == 1.0


@pytest.mark.asyncio
async def test_no_id_gold_defers_to_existing():
    gold = '‏מנהל הפרויקט: יעקבי, ניר'
    ans = '‏מנהל הפרויקט: יעקבי, ניר'
    with patch.object(gts, "llm_chat", new=AsyncMock(return_value="YES")):
        assert await gts.compare_to_gold("מי המנהל", ans, gold) == 1.0
