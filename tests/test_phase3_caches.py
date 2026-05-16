"""Verify correction_pins + __field_aliases__ rows populate the in-memory caches."""
import pytest
from sqlalchemy import text

from app.services import knowledge_service as ks


@pytest.mark.asyncio
async def test_correction_pins_loaded_into_cache(db_session):
    await db_session.execute(text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source) "
        "VALUES ('hash-cp-001', 'pinned-answer-text', 'manual')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "hash-cp-001" in ks._DB_CORRECTION_PINS_CACHE
    assert ks._DB_CORRECTION_PINS_CACHE["hash-cp-001"]["pinned_answer"] == "pinned-answer-text"


@pytest.mark.asyncio
async def test_field_aliases_loaded_from_sentinel(db_session):
    """__field_aliases__ sentinel row stored as ['alias=field', ...] — mirrors
    the existing __hebrew_abbrevs__ pattern."""
    await db_session.execute(text(
        "INSERT INTO query_synonyms (original, synonyms, source) "
        "VALUES ('__field_aliases__', CAST(:syn AS jsonb), 'ai')"
    ), {"syn": '["מנה\\"פ=manager", "תכנון=stage"]'})
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert ks._DB_FIELD_ALIASES_CACHE.get('מנה"פ') == "manager"
    assert ks._DB_FIELD_ALIASES_CACHE.get("תכנון") == "stage"


@pytest.mark.asyncio
async def test_expired_correction_pin_not_loaded(db_session):
    """Pins with expires_at < now must NOT load into the cache."""
    await db_session.execute(text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source, expires_at) "
        "VALUES ('hash-expired', 'old-pin', 'manual', NOW() - INTERVAL '1 day')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "hash-expired" not in ks._DB_CORRECTION_PINS_CACHE
