 # Shan-AI Root Controller

## 1. Project Identity & Context
- **Project:** Shan-AI (��"� - �����, �����, �����).
- **Domain:** Decision intelligence for electrical substation infrastructure.
- **Tech Stack:** FastAPI, Telegram (Polling), Groq (Llama-3.3-70b), PostgreSQL + pgvector.
- **Reference:** For detailed service maps and schemas, see `@docs/architecture.md`.

## 2. Token & Resource Efficiency (MANDATORY)
- **Lazy Loading:** Do NOT read `@docs/` unless the task requires specific architectural context.
- **Terse Output:** No flattery. No "I understand." Provide only code/diffs and essential technical notes.
- **Minimal Diffs:** Never rewrite a whole file. Use targeted edits.
- **Session Hygiene:** Use `/clear` after major features to reset context overhead.

## 3. The Opus Escalation Strategy (Planning)
- **Implementation:** Use Sonnet for 90% of coding, bug fixes, and boilerplate.
- **Opus Trigger:** Use Opus ONLY for:
    1. Initial database schema redesigns.
    2. Complex multi-file refactors (e.g., changing the Decision Logic flow).
- **Mandatory Pre-Opus Research:** 1. Search codebase for existing utilities (`grep` or `find`).
    2. Draft a `PLAN.md` using Sonnet.
    3. Ask Opus: "Review @PLAN.md for logical fallacies and edge cases."

## 4. Critical Operational Guardrails
- **The "BIGINT" Fix:** After any Docker rebuild, MUST run:
  `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;"`
- **is_relevant columns:** After any Docker rebuild OR Railway deploy with new schema, run on BOTH local and Railway DB:
  Local: `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE decisions ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT, ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP, ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);"`
  Railway: `docker exec shan-ai-postgres psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE decisions ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT, ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP, ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);"` (URL in local `.env`, never commit it — repo is public)
- **roleenum VIEWER:** DB enum may lack values added in code (`app/models.py` RoleEnum). After rebuild/fresh DB, run:
  `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'VIEWER';"`
- **judged_against_gold:** After rebuild/fresh DB or Railway deploy, run on both:
  `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;"`
- **eval_runs.failed_questions:** After rebuild/fresh DB or Railway deploy:
  `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;` (run local + Railway)
- **eval_gold_answers live cols:** After rebuild/Railway deploy:
  `ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;` (run local + Railway)
- **missions table (חדר מבצעים):** auto-creates at startup via `Base.metadata.create_all` — no manual SQL needed on fresh deploys. **Future** columns need `ALTER TABLE missions ADD COLUMN IF NOT EXISTS ...` on BOTH local and Railway. `status` is intentionally VARCHAR — never convert to a PG enum. Warning: a forgotten local Docker container running while Railway is live will **double-send** the 07:00 missions digest and overdue alerts (outbound sends work from both instances even though polling conflicts). User deletion reassigns the deleted user's missions to the deleting admin.
- **Polling Conflict:** Local Docker and Railway **cannot** run simultaneously. Stop local before Railway is live.
- **No Data Loss:** NEVER run `docker-compose down -v` without explicit confirmation.
- **Build Cycle:** After code changes, run `docker-compose restart fastapi`.

## 5. Development Standards (Hebrew & Logic)
- **Hebrew RTL:** Prefix ALL bot messages with `\u200F` (RTL Mark).
- **JSON Safety:** Replace straight quotes `"` with Hebrew gershayim `�` in user inputs before Groq processing.
- **Type Safety:** Strict Pydantic v2 schemas and mandatory Python Type Hinting.
- **Vector Specs:** pgvector size is 384 (FastEmbed default).

## 6. Project Knowledge Map
- **Knowledge Base:** Refer to `@docs/gotchas.md` for fixed bugs (e.g., Hebrew quote breaking JSON).
- **Service Map:** Refer to `@docs/architecture.md` for `app/services/` responsibilities.

## 7. IMPORTANT
- **NEVER lie**
- **NEVER guess**
- **ALWAYS verify**

## 8. superpowers
- in every response, try to use supwerpowers skills if possible.

