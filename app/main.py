from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import asyncio
from app.config import settings

from fastapi.staticfiles import StaticFiles


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
from app.database import engine, get_db_session
from app.models import Base
from app.routers import auth, telegram, dashboard, login
from app.routers.dashboard import profile_router
from app.routers import files as files_router
from app.routers import ask as ask_router
from app.routers import logs as logs_router
from fastapi.templating import Jinja2Templates
from app.services.telegram_polling import telegram_bot
from app.services.feedback_service import run_feedback_scheduler
import asyncio

logger = logging.getLogger(__name__)

# Global state to prevent multiple polling instances
_polling_task = None

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    docs_url="/docs",
)

# Add this line after your app = FastAPI() definition
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register routers
app.include_router(auth.router)
app.include_router(login.router)
app.include_router(telegram.router)
app.include_router(dashboard.router)
app.include_router(files_router.router)
app.include_router(ask_router.router)
app.include_router(logs_router.router)
app.include_router(profile_router)

@app.on_event("startup")
async def startup():
    """Initialize database tables and start Telegram bot polling."""
    global _polling_task
    from app.utils.migrations import migrate_user_passwords

    # Retry DB connection up to 10 times (Railway internal DNS may take a moment)
    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)
                from sqlalchemy import text as _text

                # Lessons learned table
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS lessons_learned (
                        id            SERIAL PRIMARY KEY,
                        decision_id   INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
                        lesson_text   TEXT NOT NULL,
                        decision_type VARCHAR(20),
                        tags          TEXT,
                        embedding     vector(384),
                        created_at    TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_lessons_decision ON lessons_learned (decision_id)"
                ))

                # Phase 4 — knowledge summaries
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS knowledge_summaries (
                        id            SERIAL PRIMARY KEY,
                        decision_type VARCHAR(20) NOT NULL UNIQUE,
                        summary_text  TEXT NOT NULL,
                        lesson_count  INTEGER DEFAULT 0,
                        updated_at    TIMESTAMP DEFAULT NOW()
                    )
                """))

                # Phase A — ensure RACI table exists on already-running DBs
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS decision_raci_roles (
                        id             SERIAL PRIMARY KEY,
                        decision_id    INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
                        user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        role           VARCHAR(1) NOT NULL,
                        assigned_by_ai BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at     TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_raci_decision ON decision_raci_roles (decision_id)"
                ))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_raci_user ON decision_raci_roles (user_id)"
                ))
                await conn.execute(_text(
                    "ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS is_master BOOLEAN NOT NULL DEFAULT FALSE;"
                ))
            print("Database tables initialized.")
            break
        except Exception as e:
            if attempt < 9:
                print(f"DB connection attempt {attempt + 1}/10 failed: {e}. Retrying in 5s...")
                await asyncio.sleep(5)
            else:
                print(f"DB connection failed after 10 attempts: {e}")
                raise

    # Ensure uploads directory exists
    from pathlib import Path
    Path("uploads").mkdir(exist_ok=True)

    # Migrate user passwords (set default for users without password_hash)
    try:
        from app.database import async_session_maker
        async with async_session_maker() as session:
            await migrate_user_passwords(session)
    except Exception as e:
        print(f"Warning: User password migration failed: {e}")

    # Start Telegram bot polling in the background
    try:
        await telegram_bot.initialize()
        print("Telegram bot initialized.")

        # Start polling in background task
        _polling_task = asyncio.create_task(telegram_bot.start_polling())
        print("Telegram bot polling started in background.")

        # Start 48-hour feedback scheduler
        asyncio.create_task(run_feedback_scheduler(telegram_bot.application.bot))
        print("Feedback scheduler started.")
    except Exception as e:
        print(f"Warning: Telegram bot polling failed to start: {e}")
        logger.error(f"Telegram bot startup error: {e}")

@app.on_event("shutdown")
async def shutdown():
    """Gracefully stop the Telegram bot polling."""
    global _polling_task
    try:
        # Signal polling to stop
        await telegram_bot.stop_polling()

        # Wait for polling task to finish (with timeout)
        if _polling_task and not _polling_task.done():
            try:
                await asyncio.wait_for(_polling_task, timeout=5)
            except asyncio.TimeoutError:
                print("Polling task did not stop in 5 seconds, cancelling...")
                _polling_task.cancel()
                try:
                    await _polling_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

        print("Telegram bot stopped.")
    except Exception as e:
        print(f"Error stopping bot: {e}")
        logger.error(f"Telegram bot shutdown error: {e}")


@app.get("/")
async def root(request: Request):
    """Redirect to login or dashboard depending on authentication."""
    from app.utils.session import verify_token

    token = request.cookies.get("access_token")
    if token and verify_token(token):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "shan-ai-api"
    }

@app.get("/api/v1/status")
async def api_status():
    return {
        "api": "operational",
        "database": "configured",
        "pgvector": "available"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
