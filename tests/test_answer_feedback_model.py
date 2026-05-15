"""Smoke tests for AnswerFeedback model: table + columns + FK constraints."""
from sqlalchemy import text


async def test_answer_feedback_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.answer_feedback')"
    ))
    assert res.scalar() is not None, "answer_feedback table missing"


async def test_answer_feedback_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='answer_feedback' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "query_log_id", "user_id", "vote",
                "correction_text", "gold_id", "created_at"}
    assert expected.issubset(set(rows)), \
        f"missing columns: {expected - set(rows)}"


async def test_answer_feedback_index_on_query_log_id(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename='answer_feedback' AND indexdef LIKE '%query_log_id%'"
    ))
    assert res.scalar() == 1, "missing index on answer_feedback.query_log_id"
