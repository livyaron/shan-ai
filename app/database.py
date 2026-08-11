from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings

# Create async engine.
# Real pooling (not NullPool): the bot opens several short-lived sessions per
# Telegram message, and NullPool forced a fresh TCP+TLS handshake to the Railway
# Postgres on every one — hundreds of ms of pure connection overhead per reply.
# pool_pre_ping transparently discards connections dropped by Railway restarts,
# and pool_recycle caps connection age so we never hand out a stale socket.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True
)

# Create async session factory
async_session_maker = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def get_db_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session
