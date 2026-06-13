"""Telegram /gold: role gate, candidate queue, keyboard structure."""
import pytest

from app.models import QueryLog, RoleEnum
from app.services import gold_telegram_service as gts_tg


def test_is_manager():
    class U:
        def __init__(self, role): self.role = role
    assert gts_tg.is_manager(U(RoleEnum.DEPARTMENT_MANAGER)) is True
    assert gts_tg.is_manager(U(RoleEnum.DIVISION_MANAGER)) is True
    assert gts_tg.is_manager(U(RoleEnum.PROJECT_MANAGER)) is False
    assert gts_tg.is_manager(U(RoleEnum.VIEWER)) is False
    assert gts_tg.is_manager(None) is False


def test_gold_keyboard_callbacks():
    kb = gts_tg.gold_keyboard(candidate_id=7)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "gold:approve:7" in datas
    assert "gold:edit:7" in datas
    assert "gold:skip:7" in datas
    assert "gold:stop:7" in datas


@pytest.mark.asyncio
async def test_next_candidate_skips_questions_with_gold(db_session):
    from sqlalchemy import delete
    from app.models import EvalGoldAnswer, AnswerFeedback
    from app.services.gold_truth_service import save_gold
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()
    db_session.add_all([
        QueryLog(question="שאלה עם זהב", ai_response="a"),
        QueryLog(question="שאלה בלי זהב", ai_response="b"),
    ])
    await db_session.commit()
    await save_gold(db_session, question="שאלה עם זהב", gold_answer="g", user_id=None, source="manual")

    cand = await gts_tg.next_candidate(db_session, exclude_questions=set())
    assert cand is not None
    assert cand["question"] == "שאלה בלי זהב"
