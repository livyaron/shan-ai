"""Verify compare_to_gold's entity-guard suppresses _rule_check's 1.0 when
the question's entity token is missing from the AI answer."""
import pytest
from unittest.mock import patch, AsyncMock

from app.services.gold_truth_service import compare_to_gold


@pytest.mark.asyncio
async def test_compare_to_gold_rejects_wrong_entity():
    """Question asks about 'בת ים', gold mentions manager 'יהודר בכר'.
    AI returns project 'תל השומר' (wrong entity!) with the same manager.
    Substring containment of 'יהודר בכר' alone must NOT yield score 1.0
    when the question's entity token ('ים') is missing from the answer.
    The guard suppresses _rule_check 1.0 and defers to the LLM judge — we
    mock the judge to return NO so the test confirms the guard fired."""
    question = "מי המנהל של פרויקט בת ים?"
    gold     = "מנהל הפרויקט: יהודר בכר"
    ai       = "📌 שם הפרויקט: תל השומר 📌 מנהל הפרויקט: יהודר בכר, אורית"

    # Without the guard, _rule_check would return 1.0 from substring containment.
    # With the guard, we defer to the LLM judge. Mock it to return NO.
    with patch("app.services.gold_truth_service.llm_chat",
               new=AsyncMock(return_value="NO")):
        score = await compare_to_gold(question, ai, gold)
    assert score < 1.0, f"expected sub-1.0 (different entity), got {score}"


@pytest.mark.asyncio
async def test_compare_to_gold_passes_when_entity_matches():
    """Same-manager scenario but AI answer DOES mention 'ים' in the project
    name. Should score 1.0 via substring containment (no entity-guard trigger)."""
    question = "מי המנהל של פרויקט בת ים?"
    gold     = "מנהל הפרויקט: יהודר בכר"
    ai       = "📌 שם הפרויקט: בת ים תחמ\"ש 📌 מנהל הפרויקט: יהודר בכר, אורית"

    score = await compare_to_gold(question, ai, gold)
    assert score >= 1.0, f"expected full match, got {score}"
