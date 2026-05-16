"""Smoke tests for RouteTrace model."""
from sqlalchemy import text


async def test_route_traces_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.route_traces')"
    ))
    assert res.scalar() is not None


async def test_route_traces_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='route_traces' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "query_log_id", "path", "intent", "param",
                "applied_rule_ids", "ms_total", "ms_llm", "created_at"}
    assert expected.issubset(set(rows)), f"missing: {expected - set(rows)}"


async def test_route_traces_index_on_query_log_id(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename='route_traces' "
        "AND indexdef LIKE '%query_log_id%'"
    ))
    assert res.scalar() == 1
