"""run_cycle records FAIL questions into EvalRun.failed_questions."""
import pytest
from unittest.mock import patch

from app.services import per_question_loop_service as pq
from app.models import EvalGoldAnswer, EvalRun


@pytest.mark.asyncio
async def test_run_cycle_records_failed_questions(db_session):
    from sqlalchemy import delete, select
    from app.models import AnswerFeedback
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.commit()
    db_session.add_all([
        EvalGoldAnswer(question_hash="h1", question="שאלה טובה", gold_answer="ת", source="manual"),
        EvalGoldAnswer(question_hash="h2", question="שאלה רעה", gold_answer="ת", source="manual"),
    ])
    await db_session.commit()

    from app.services.per_question_loop_service import QuestionResult

    async def fake_run_one(session, g, *a, **k):
        ok = g.question == "שאלה טובה"
        return QuestionResult(
            question=g.question,
            question_hash=g.question_hash,
            gold_answer=g.gold_answer,
            status=("passed_first_try" if ok else "unfixable"),
            score_initial=(1.0 if ok else 0.0),
            score_final=(1.0 if ok else 0.0),
        )

    with patch.object(pq, "run_one_question", new=fake_run_one):
        await pq.run_cycle(db_session, user_id=None, repair=False)

    er = (await db_session.execute(select(EvalRun).order_by(EvalRun.id.desc()).limit(1))).scalar_one()
    fq = er.failed_questions or []
    assert any(item["question"] == "שאלה רעה" for item in fq)
    assert all(item["question"] != "שאלה טובה" for item in fq)
