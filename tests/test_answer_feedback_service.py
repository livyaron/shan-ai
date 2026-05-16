"""Unit tests for answer_feedback_service."""
import pytest
from sqlalchemy import select, text

from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog
from app.services.answer_feedback_service import (
    record_thumbs_up, record_thumbs_down, _is_rate_limited,
)


async def _seed_log(db_session, question="שאלת בדיקה", answer="תשובה"):
    log = QueryLog(question=question, ai_response=answer, sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.asyncio
async def test_thumbs_up_writes_feedback_row(db_session):
    log = await _seed_log(db_session, question="up-q-001")
    fb = await record_thumbs_up(db_session, log.id, user_id=None)
    assert fb.vote == "up"
    assert fb.query_log_id == log.id
    assert fb.correction_text is None


@pytest.mark.asyncio
async def test_thumbs_up_creates_auto_gold_when_none_exists(db_session):
    log = await _seed_log(db_session, question="auto-gold-q-002", answer="auto-gold-answer")
    fb = await record_thumbs_up(db_session, log.id, user_id=None)

    from app.services.gold_truth_service import question_hash
    h = question_hash("auto-gold-q-002")
    gold = await db_session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    assert gold is not None
    assert gold.source == "auto_user_confirmed"
    assert gold.gold_answer == "auto-gold-answer"
    assert fb.gold_id == gold.id


@pytest.mark.asyncio
async def test_thumbs_up_does_not_overwrite_existing_gold(db_session):
    log = await _seed_log(db_session, question="noclobber-q-003", answer="new-ai-answer")
    from app.services.gold_truth_service import save_gold
    original_gold = await save_gold(
        db_session, question="noclobber-q-003",
        gold_answer="manual-gold-text", user_id=None, source="manual",
    )

    fb = await record_thumbs_up(db_session, log.id, user_id=None)

    from app.services.gold_truth_service import question_hash
    h = question_hash("noclobber-q-003")
    rows = (await db_session.execute(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h)
    )).scalars().all()
    assert len(rows) == 1, "must not insert a second gold row for same question"
    assert rows[0].gold_answer == "manual-gold-text"
    assert rows[0].source == "manual"
    assert fb.gold_id == original_gold.id


@pytest.mark.asyncio
async def test_thumbs_down_writes_feedback_row_and_gold(db_session):
    log = await _seed_log(db_session, question="down-q-004", answer="wrong-answer")

    fb, gold = await record_thumbs_down(
        db_session, log.id, user_id=None,
        correction_text="the correct answer",
    )
    assert fb.vote == "down"
    assert fb.correction_text == "the correct answer"
    assert fb.gold_id == gold.id
    assert gold.gold_answer == "the correct answer"
    assert gold.source == "user_correction"


@pytest.mark.asyncio
async def test_rate_limit_skips_auto_gold_after_5_in_60s(db_session):
    """6th 👍 within a minute must skip the auto-gold conversion (the row
    still inserts; only the gold side-effect is suppressed). We need a real
    user row to exist because AnswerFeedback.user_id has FK to users.id."""
    # Seed a user row so the FK on user_id resolves
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (42, 99999042, 'rl_user', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()

    log = await _seed_log(db_session, question="burst-q-005")
    for i in range(5):
        db_session.add(AnswerFeedback(
            query_log_id=log.id, user_id=42, vote="up",
        ))
    await db_session.commit()

    log6 = await _seed_log(db_session, question="burst-q-006", answer="ans-006")
    fb = await record_thumbs_up(db_session, log6.id, user_id=42)
    assert fb.gold_id is None, "expected auto-gold skipped under rate limit"

    from app.services.gold_truth_service import question_hash
    h = question_hash("burst-q-006")
    gold = await db_session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    assert gold is None


@pytest.mark.asyncio
async def test_is_rate_limited_returns_false_below_threshold(db_session):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (99, 99999099, 'under_user', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()
    log = await _seed_log(db_session, question="under-q-007")
    for _ in range(3):
        db_session.add(AnswerFeedback(query_log_id=log.id, user_id=99, vote="up"))
    await db_session.commit()
    assert await _is_rate_limited(db_session, user_id=99) is False


@pytest.mark.asyncio
async def test_legacy_logs_feedback_endpoint_writes_answer_feedback(db_session):
    """POST /api/logs/feedback with feedback=1 must create both
    QueryLog.user_feedback AND an AnswerFeedback row (vote='up')."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.routers.login import get_current_user
    from app.models import User

    # Seed a real user (the auth dependency stub needs a User object,
    # but the FK in AnswerFeedback also expects user_id to exist in DB)
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (501, 999000501, 'fb_t', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()

    log = await _seed_log(db_session, question="legacy-q-008")

    async def fake_user():
        return User(id=501, username="fb_t", role="PROJECT_MANAGER",
                    telegram_id=999000501, password_hash="", is_admin=False)

    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/logs/feedback",
                                  json={"log_id": log.id, "feedback": 1})
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()

    fb = (await db_session.execute(
        select(AnswerFeedback).where(AnswerFeedback.query_log_id == log.id)
    )).scalar_one_or_none()
    assert fb is not None
    assert fb.vote == "up"


@pytest.mark.asyncio
async def test_legacy_logs_feedback_endpoint_bare_thumbs_down(db_session):
    """POST /api/logs/feedback with feedback=-1 must create a bare AnswerFeedback
    row (vote='down', no correction_text, no gold). save_gold + repair are
    only triggered by /dashboard/ask/correct in Task 2.3."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.routers.login import get_current_user
    from app.models import User

    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (502, 999000502, 'fb_d', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()

    log = await _seed_log(db_session, question="legacy-down-q-009")

    async def fake_user():
        return User(id=502, username="fb_d", role="PROJECT_MANAGER",
                    telegram_id=999000502, password_hash="", is_admin=False)

    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/logs/feedback",
                                  json={"log_id": log.id, "feedback": -1})
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()

    fb = (await db_session.execute(
        select(AnswerFeedback).where(AnswerFeedback.query_log_id == log.id)
    )).scalar_one_or_none()
    assert fb is not None
    assert fb.vote == "down"
    assert fb.correction_text is None
    assert fb.gold_id is None
