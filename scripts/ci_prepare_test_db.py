"""Build the app's schema on CI's throwaway Postgres, for the `db` tests.

Runs only in .github/workflows/db-tests.yml against the job's own
`services: postgres` container — never against Railway. It calls the real
`app.main.startup()` (so the schema is exactly what production builds:
create_all + every ALTER + the lessons schema) with the Telegram bot and the
cron scheduler stubbed out: CI has no bot token and must never touch the
production webhook.
"""
import asyncio
import os
import sys


async def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if "localhost" not in url and "127.0.0.1" not in url:
        print(f"refusing: DATABASE_URL is not a local CI database ({url.split('@')[-1]})")
        return 2

    import app.main as m
    from app.services import eval_cron

    async def _no_bot() -> bool:
        return True

    m._start_telegram_bot = _no_bot
    eval_cron.start_scheduler = lambda: None

    await m.startup()
    await asyncio.sleep(3)  # let startup's background tasks finish against the empty DB

    from sqlalchemy import text
    from app.database import engine
    async with engine.connect() as conn:
        n = (await conn.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ))).scalar_one()
    print(f"schema ready: {n} tables in public")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
