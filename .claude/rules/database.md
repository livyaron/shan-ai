---
paths:
  - "app/models.py"
  - "app/main.py"
  - "app/database.py"
  - "scripts/**"
---
# Database: fresh-DB fixes, schema changes, data safety

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

**The fixes below are NOT needed on a fresh, empty DB** (checked 2026-10-26): `telegram_id` is `BigInteger`, and `is_relevant`/`irrelevant_*`, `judged_against_gold`, `failed_questions`, `last_live_*` and `VIEWER` are all in `app/models.py`, so `create_all` creates them. They matter only when an **existing** table predates them — e.g. restoring an old dump. Production already has them.

`Base.metadata.create_all` creates missing tables at startup but never alters an existing one, so every new column on an existing table is an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `app/main.py` startup (or run once on the Railway DB).

- **No local database, anywhere (user instruction 2026-09-26).** Not Docker, and not a throwaway Postgres inside a remote session either. Verify logic with the synthetic-frame tests; real-data checks happen in production only.
- **The "BIGINT" Fix:** On a restored old dump, run:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;"`
- **is_relevant columns:** On a restored old dump:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE decisions ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT, ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP, ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);"`
- **roleenum VIEWER:** DB enum may lack values added in code (`app/models.py` RoleEnum). On a restored old dump, run:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'VIEWER';"`
- **judged_against_gold:** On a restored old dump:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;"`
- **eval_runs.failed_questions:** On a restored old dump:
  `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;` (Railway DB)
- **eval_gold_answers live cols:** On a restored old dump:
  `ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;` (Railway DB)
- **No Data Loss:** NEVER run destructive SQL (DROP/TRUNCATE/DELETE without WHERE) or delete the Railway Postgres volume without explicit confirmation.
