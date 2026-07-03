"""Shared test fixtures.

Tests run against the same Postgres container the app uses (docker-compose
service `postgres`). Each test gets a fresh transaction that rolls back at
teardown so we never persist test data — including data written by code-
under-test that opens its own session via app.database.async_session_maker.
"""
import os
from unittest.mock import patch

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool

# Inherit DATABASE_URL from the running app env (loaded via docker-compose).
# A no-credential placeholder is the final fallback — it will fail auth, which
# is the right behavior when env is missing rather than silently leaking creds.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://shan_user:@localhost:5432/shan_ai",
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _test_engine():
    """Session-scoped async engine with NullPool to keep connections per-test
    and avoid leaking across event loops."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


class _AsyncSessionContext:
    """Async context manager that yields an AsyncSession bound to the same
    connection as the test's db_session. Each `async with` opens a nested
    SAVEPOINT so internal commits inside production code don't escape the
    outer test transaction."""
    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        await self._session.begin_nested()
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        await self._session.close()
        return False


@pytest_asyncio.fixture
async def db_session(_test_engine, monkeypatch) -> AsyncSession:
    """Yield an AsyncSession joined to an external transaction (with SAVEPOINTs).

    Also monkey-patches app.database.async_session_maker AND every already-
    imported module that has the name bound (via `from app.database import
    async_session_maker`). All of those bindings are restored on teardown.

    Pattern: SQLAlchemy "joining a session into an external transaction"
    https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites
    """
    async with _test_engine.connect() as conn:
        outer_trans = await conn.begin()

        TestSessionLocal = async_sessionmaker(
            bind=conn, expire_on_commit=False, class_=AsyncSession,
        )

        sess = TestSessionLocal()
        await sess.begin_nested()

        @event.listens_for(sess.sync_session, "after_transaction_end")
        def _restart_savepoint(session_, transaction_):
            # When a SAVEPOINT ends (from an implicit/explicit commit inside
            # code-under-test), open a new SAVEPOINT so subsequent operations
            # remain isolated.
            if transaction_.nested and not transaction_._parent.nested:
                session_.begin_nested()

        # Monkey-patch the app's session maker so production code joins this txn.
        import app.database as _app_db

        def _factory():
            return _AsyncSessionContext(TestSessionLocal())

        # Patch every module that has bound `async_session_maker` as an attribute,
        # not just app.database itself. `from app.database import async_session_maker`
        # creates new module-level bindings that don't update when we patch the
        # source module — so we walk sys.modules to find them all.
        import sys
        original = _app_db.async_session_maker
        monkeypatch.setattr(_app_db, "async_session_maker", _factory)
        for mod_name, mod in list(sys.modules.items()):
            if mod is None or mod is _app_db:
                continue
            try:
                bound = getattr(mod, "async_session_maker", None)
            except Exception:
                continue
            if bound is original:
                monkeypatch.setattr(mod, "async_session_maker", _factory)

        try:
            yield sess
        finally:
            await sess.close()
            await outer_trans.rollback()


@pytest_asyncio.fixture
async def seeded_project_id(db_session) -> int:
    """Insert a minimal active project and return its id. Tests must not rely
    on pre-existing rows in the dev database (fresh DBs have none)."""
    from sqlalchemy import text as _text
    row = await db_session.execute(_text(
        "INSERT INTO projects (project_identifier, name, is_active, last_updated) "
        "VALUES ('TEST-SEED-PROJ', 'פרויקט בדיקה', true, now()) "
        "ON CONFLICT (project_identifier) DO UPDATE SET is_active = true "
        "RETURNING id"
    ))
    return row.scalar()


@pytest_asyncio.fixture
async def mock_llm_chat():
    """Patch app.services.llm_router.llm_chat with a programmable async mock."""
    async def _default(*args, **kwargs):
        return ""
    with patch("app.services.llm_router.llm_chat", side_effect=_default) as m:
        yield m
