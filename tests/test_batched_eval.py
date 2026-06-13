"""run_cycle batch mode: selects oldest-checked N, writes last_live_* per question."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services import per_question_loop_service as pq
from app.models import EvalGoldAnswer
from app.services.per_question_loop_service import QuestionResult


async def _seed(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.commit()
    base = datetime(2026, 6, 1)
    db_session.add_all([
        EvalGoldAnswer(question_hash="h1", question="q1", gold_answer="g", source="manual", last_live_at=base),
        EvalGoldAnswer(question_hash="h2", question="q2", gold_answer="g", source="manual", last_live_at=base + timedelta(days=2)),
        EvalGoldAnswer(question_hash="h3", question="q3", gold_answer="g", source="manual", last_live_at=None),
    ])
    await db_session.commit()


@pytest.mark.asyncio
async def test_batch_selects_nulls_then_oldest(db_session):
    await _seed(db_session)
    seen = []

    async def fake_one(session, g, *a, **k):
        seen.append(g.question)
        return QuestionResult(
            question=g.question,
            question_hash=g.question_hash,
            gold_answer=g.gold_answer,
            status="passed_first_try",
            score_initial=1.0,
            score_final=1.0,
        )

    with patch.object(pq, "run_one_question", new=fake_one):
        await pq.run_cycle(db_session, user_id=None, repair=False, batch=2)
    assert set(seen) == {"q3", "q1"}


@pytest.mark.asyncio
async def test_batch_persists_verdict(db_session):
    await _seed(db_session)

    async def fake_one(session, g, *a, **k):
        bad = g.question == "q1"
        return QuestionResult(
            question=g.question,
            question_hash=g.question_hash,
            gold_answer=g.gold_answer,
            status=("unfixable" if bad else "passed_first_try"),
            score_initial=(0.0 if bad else 1.0),
            score_final=(0.0 if bad else 1.0),
        )

    with patch.object(pq, "run_one_question", new=fake_one):
        await pq.run_cycle(db_session, user_id=None, repair=False, batch=2)

    from sqlalchemy import select
    rows = {r.question_hash: r for r in (await db_session.execute(select(EvalGoldAnswer))).scalars()}
    assert rows["h3"].last_live_verdict == "PASS"
    assert rows["h1"].last_live_verdict == "FAIL"
    assert rows["h1"].last_live_at is not None
    assert rows["h2"].last_live_verdict is None
