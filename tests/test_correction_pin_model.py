"""Smoke tests for CorrectionPin model."""
from sqlalchemy import text


async def test_correction_pins_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.correction_pins')"
    ))
    assert res.scalar() is not None


async def test_correction_pins_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='correction_pins' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "question_hash", "pinned_answer", "scope_project_id",
                "expires_at", "source", "created_by_id", "created_at"}
    assert expected.issubset(set(rows)), f"missing: {expected - set(rows)}"


async def test_correction_pins_unique_question_hash(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename='correction_pins' "
        "AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%question_hash%'"
    ))
    assert res.scalar() == 1, "missing unique index on question_hash"
