"""Verify project_aliases + intent_overrides DB rows populate the in-memory caches
that ask_router pre-rule lookups consume."""
import pytest
from sqlalchemy import text

from app.services import knowledge_service as ks


@pytest.mark.asyncio
async def test_aliases_loaded_into_cache(db_session):
    # Seed one alias row pointing to whatever project exists
    await db_session.execute(text(
        "INSERT INTO project_aliases (project_id, alias_text, normalized_alias, source) "
        "SELECT id, 'בית הגדי', 'בית הגדי', 'manual' FROM projects LIMIT 1"
    ))
    await db_session.execute(text(
        "INSERT INTO intent_overrides (question_pattern_hash, forced_intent, forced_param, source) "
        "VALUES ('abc123', 'by_identifier', 'בית הגדי', 'manual')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "בית הגדי" in ks._DB_PROJECT_ALIASES_CACHE
    assert "abc123" in ks._DB_INTENT_OVERRIDES_CACHE
    assert ks._DB_INTENT_OVERRIDES_CACHE["abc123"]["forced_intent"] == "by_identifier"
    assert ks._DB_INTENT_OVERRIDES_CACHE["abc123"]["forced_param"] == "בית הגדי"
