"""Integration tests for GET /dashboard/learning/stats."""
import pytest
from datetime import datetime
from sqlalchemy import text
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.models import AnswerFeedback, QueryLog, RepairProposal, User
from app.routers.login import get_current_user


async def _seed_user(db_session, uid=3001, admin=True):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        f"VALUES ({uid}, {900000000 + uid}, 'stats_t', 'DIVISION_MANAGER', '', {str(admin).lower()}) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_stats_returns_metrics_shape(db_session):
    await _seed_user(db_session)
    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        body = r.json()
        assert "pass_rate_7d" in body
        assert "pass_rate_baseline" in body
        assert "rules_applied_7d" in body
        assert "corrections_7d" in body
        assert isinstance(body["rules_applied_7d"], int)
        assert isinstance(body["corrections_7d"], int)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stats_counts_recent_corrections(db_session):
    """Seed 2 AnswerFeedback rows with correction_text + vote='down'. Endpoint counts both."""
    await _seed_user(db_session)
    log = QueryLog(question="stats-q-001", ai_response="x", sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text="correction-1",
    ))
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text="correction-2",
    ))
    # control: vote='down' WITHOUT correction_text → must NOT count
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text=None,
    ))
    await db_session.commit()

    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        body = r.json()
        assert body["corrections_7d"] >= 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stats_counts_applied_proposals(db_session):
    """Seed 1 RepairProposal with status='applied' + applied_at=now. Endpoint counts it."""
    await _seed_user(db_session)
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "stats-test", "project_id": 999},
        status="applied",
        applied_at=datetime.utcnow(),
    )
    db_session.add(proposal)
    await db_session.commit()

    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        body = r.json()
        assert body["rules_applied_7d"] >= 1
    finally:
        app.dependency_overrides.clear()
