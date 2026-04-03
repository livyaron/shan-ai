from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
from app.database import engine, get_db_session
from app.models import Base
from app.routers import auth, telegram, dashboard
from app.routers.dashboard import profile_router
from fastapi.templating import Jinja2Templates
from app.services.telegram_polling import telegram_bot
from app.services.feedback_service import run_feedback_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    docs_url="/docs",
)

# Register routers
app.include_router(auth.router)
app.include_router(telegram.router)
app.include_router(dashboard.router)
app.include_router(profile_router)

@app.on_event("startup")
async def startup():
    """Initialize database tables and start Telegram bot polling."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables initialized.")

    # Start Telegram bot polling in the background
    try:
        await telegram_bot.initialize()
        print("Telegram bot initialized.")
        # Start polling in background task
        import asyncio
        asyncio.create_task(telegram_bot.start_polling())
        print("Telegram bot polling started in background.")

        # Start 48-hour feedback scheduler
        asyncio.create_task(run_feedback_scheduler(telegram_bot.application.bot))
        print("Feedback scheduler started.")
    except Exception as e:
        print(f"Warning: Telegram bot polling failed to start: {e}")
        logger.error(f"Telegram bot startup error: {e}")

@app.get("/")
async def root():
    return {
        "message": "Shan-AI Decision Intelligence Platform",
        "status": "running",
        "version": settings.PROJECT_VERSION
    }

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
