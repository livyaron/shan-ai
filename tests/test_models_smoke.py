"""Confirm new tables exist after Base.metadata.create_all runs.

These are smoke tests, not behavior tests — they only check schema presence.
"""
from sqlalchemy import text


async def test_project_aliases_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.project_aliases')"
    ))
    assert res.scalar() is not None, "project_aliases table missing"


async def test_intent_overrides_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.intent_overrides')"
    ))
    assert res.scalar() is not None, "intent_overrides table missing"


async def test_repair_proposals_has_applied_artifact_id(db_session):
    res = await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='repair_proposals' AND column_name='applied_artifact_id'"
    ))
    assert res.scalar() == "applied_artifact_id"
