"""Shared test fixtures.

Tests run against the same Postgres container the app uses (docker-compose
service `postgres`). Each test gets a fresh transaction that rolls back at
teardown so we never persist test data.
"""
import asyncio
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Allow tests to override DATABASE_URL via env; default to docker host.
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://shan_user:shan_secure_pass_2025@localhost:5432/shan_ai",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Yield a session bound to a transaction that always rolls back."""
    engine = create_async_engine(TEST_DB_URL, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        async_sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield async_sess
        finally:
            await async_sess.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def mock_llm_chat():
    """Patch app.services.llm_router.llm_chat with a programmable async mock."""
    async def _default(*args, **kwargs):
        return ""
    with patch("app.services.llm_router.llm_chat", side_effect=_default) as m:
        yield m
