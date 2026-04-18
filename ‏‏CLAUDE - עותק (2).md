 # Shan-AI Root Controller

## 1. Project Identity & Context
- **Project:** Shan-AI (שנ"ל - שיתוף, נסיון, למידה).
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
- **Polling Conflict:** Local Docker and Railway **cannot** run simultaneously. Stop local before Railway is live.
- **No Data Loss:** NEVER run `docker-compose down -v` without explicit confirmation.
- **Build Cycle:** After code changes, run `docker-compose restart fastapi`.

## 5. Development Standards (Hebrew & Logic)
- **Hebrew RTL:** Prefix ALL bot messages with `\u200F` (RTL Mark).
- **JSON Safety:** Replace straight quotes `"` with Hebrew gershayim `״` in user inputs before Groq processing.
- **Type Safety:** Strict Pydantic v2 schemas and mandatory Python Type Hinting.
- **Vector Specs:** pgvector size is 384 (FastEmbed default).

## 6. Project Knowledge Map
- **Knowledge Base:** Refer to `@docs/gotchas.md` for fixed bugs (e.g., Hebrew quote breaking JSON).
- **Service Map:** Refer to `@docs/architecture.md` for `app/services/` responsibilities.