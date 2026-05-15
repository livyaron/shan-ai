"""Integration test for POST /dashboard/ask/correct."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from app.main import app
from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog, User
from app.routers.login import get_current_user


async def _seed_log(db_session, question="corr-q-001", answer="wrong"):
    log = QueryLog(question=question, ai_response=answer, sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


async def _seed_user(db_session, uid=1001):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        f"VALUES ({uid}, {900000000 + uid}, 'corr_t', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_ask_correct_writes_gold_and_returns_ids(db_session, monkeypatch):
    await _seed_user(db_session, uid=1001)
    log = await _seed_log(db_session, question="באיזה שלב נמצא פרויקט בית X?")

    # Stub the background-repair entry so the test doesn't actually run the loop
    scheduled = {}
    async def fake_run(gold_id, user_id):
        scheduled["called"] = True
        scheduled["gold_id"] = gold_id
        scheduled["user_id"] = user_id
    monkeypatch.setattr("app.routers.ask._schedule_repair_for_gold", fake_run)

    async def fake_user():
        return User(id=1001, telegram_id=900001001, username="corr_t",
                    role="PROJECT_MANAGER", password_hash="", is_admin=False)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/ask/correct",
                json={"log_id": log.id,
                      "correction_text": "הפרויקט בשלב תכנון"},
            )
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        body = r.json()
        assert body["status"] == "learning"
        assert body.get("gold_id") is not None
        assert body.get("feedback_id") is not None
    finally:
        app.dependency_overrides.clear()

    # Verify side-effects
    fb = (await db_session.execute(
        select(AnswerFeedback).where(AnswerFeedback.query_log_id == log.id)
    )).scalar_one_or_none()
    assert fb is not None
    assert fb.vote == "down"
    assert fb.correction_text == "הפרויקט בשלב תכנון"
    assert fb.gold_id is not None

    gold = await db_session.get(EvalGoldAnswer, fb.gold_id)
    assert gold is not None
    assert gold.gold_answer == "הפרויקט בשלב תכנון"
    assert gold.source == "user_correction"

    # BackgroundTasks were scheduled
    # NOTE: FastAPI runs BackgroundTasks AFTER returning the response. With
    # ASGITransport, the response is awaited so background tasks have a chance
    # to run within the `async with client` block. If `scheduled` is empty,
    # the patching target may have been incorrect.
    assert scheduled.get("called") is True, \
        f"_schedule_repair_for_gold not called; got scheduled={scheduled!r}"


@pytest.mark.asyncio
async def test_ask_correct_rejects_empty_correction(db_session):
    await _seed_user(db_session, uid=1002)
    log = await _seed_log(db_session, question="empty-corr-q")
    async def fake_user():
        return User(id=1002, telegram_id=900001002, username="corr_e",
                    role="PROJECT_MANAGER", password_hash="", is_admin=False)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/ask/correct",
                json={"log_id": log.id, "correction_text": ""},
            )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
