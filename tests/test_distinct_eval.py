"""Distinct-question aggregation: one verdict per question_hash, latest row wins."""
import pytest
from datetime import datetime, timedelta

from app.models import QueryLog
from app.services import distinct_eval_service as des


@pytest.mark.asyncio
async def test_distinct_groups_by_question_latest_wins(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback, EvalGoldAnswer
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        QueryLog(question="כמה פרויקטים?", ai_response="a", judge_verdict="FAIL",
                 judged_against_gold=True, timestamp=base),
        QueryLog(question="כמה פרויקטים?", ai_response="b", judge_verdict="PASS",
                 judged_against_gold=True, timestamp=base + timedelta(hours=1)),
        QueryLog(question="מי המנהל?", ai_response="c", judge_verdict="FAIL",
                 failure_type="WRONG_PROJECT", judged_against_gold=False, timestamp=base),
    ])
    await db_session.commit()

    rows = await des.distinct_question_eval(db_session)
    by_q = {r["question"]: r for r in rows}

    assert len(rows) == 2
    assert by_q["כמה פרויקטים?"]["verdict"] == "PASS"
    assert by_q["כמה פרויקטים?"]["count"] == 2
    assert by_q["מי המנהל?"]["verdict"] == "FAIL"
    assert by_q["מי המנהל?"]["count"] == 1


@pytest.mark.asyncio
async def test_distinct_summary_counts_each_question_once(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback, EvalGoldAnswer
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base),
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base + timedelta(minutes=1)),
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base + timedelta(minutes=2)),
        QueryLog(question="q2", ai_response="y", judge_verdict="FAIL", judged_against_gold=True, timestamp=base),
    ])
    await db_session.commit()

    s = await des.distinct_summary(db_session)
    assert s["distinct_total"] == 2
    assert s["distinct_pass"] == 1
    assert s["distinct_fail"] == 1
    assert s["pass_rate"] == 50
    assert s["gold_backed"] == 2
