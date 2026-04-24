import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://shan_user:@localhost:5432/shan_ai"

    # FastAPI
    PROJECT_NAME: str = "Shan-AI - מערכת ניהול החלטות"
    PROJECT_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "FILELLSBOT")
    TELEGRAM_WEBHOOK_URL: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    WEBHOOK_SECRET_TOKEN: str = os.getenv("WEBHOOK_SECRET_TOKEN", "")
    USE_POLLING: bool = os.getenv("USE_POLLING", "").lower() in ("1", "true", "yes")

    # Claude API
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Gemini API
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Groq API
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # Google AI Studio (Gemma 4)
    GOOGLE_AI_API_KEY: str = os.getenv("GOOGLE_AI_API_KEY", "")

    # Public base URL (for profile links etc.)
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

    class Config:
        env_file = ".env"

settings = Settings()
