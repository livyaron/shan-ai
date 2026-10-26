# Shan-AI

Decision intelligence for electrical substation infrastructure (the projects division of an electric utility). Hebrew-first: users, bot and UI are in Hebrew, RTL.

<!--
Maintainer notes (stripped before Claude sees this file — they cost no tokens):
- Domain rules live in .claude/rules/*.md with `paths:` frontmatter; each loads only when Claude touches a matching file.
  Moved there verbatim from this file on 2026-10-26 (55 KB → this root). Keep this file under ~150 lines.
- Model strategy for the human driving sessions: Sonnet for routine coding; Opus for schema redesigns and
  multi-file refactors of the decision flow. Before an Opus pass: search for existing utilities, draft PLAN.md,
  then ask "Review @PLAN.md for logical fallacies and edge cases."
- Run /clear between unrelated tasks; run /doctor prompt-audit after editing instruction files.
-->

## Stack
- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2 (async), Jinja templates in `app/templates/`, static JS in `static/js/`.
- **DB:** PostgreSQL 16 + pgvector on Railway (service `postgres-v2`). Embeddings: fastembed, vector size **384**.
- **LLMs:** Groq `openai/gpt-oss-120b` → `openai/gpt-oss-20b` (`app/services/groq_client.py`), Google Gemma as fallback provider (`gemma_client.py`), routed by `llm_router.py`.
- **Telegram bot:** webhook on Railway (polling is the local-dev path only).
- **Lessons module:** separate schema `lessons` + PostgREST + React/Vite UI in `lessons_ui/`, served at `/lessons/`.
- **Layout:** `app/routers/` (HTTP), `app/services/` (all logic), `tests/`, `docs/architecture.md` (service map — read only when you need architecture context).

## Commands
- One test file: `python -m pytest -q tests/test_<name>.py`
- CI's no-DB suite = the explicit file list in `.github/workflows/docker-build.yml`. A new test file must be added to that list or CI never runs it.
- Lessons UI: `cd lessons_ui && npm run build | npm test | npm run lint` (vite / vitest / eslint).
- DB (production only): `psql "$RAILWAY_DATABASE_URL" -c "..."` — URL lives in the local `.env`, never in the repo.

## How to work
- Never lie, never guess, always verify — run the check, show the output. If a fact is uncertain, say so.
- Minimal diffs: targeted edits, never rewrite a whole file. Match the surrounding code's naming and comment density.
- Terse output: no flattery, no "I understand". Code, diffs and the technical notes that matter.
- Tests use synthetic frames/fixtures. Pure functions are the norm: a module's DB access lives in one loader, the rest is pure and tested.
- Mandatory type hints; strict Pydantic v2 schemas.
- **Single source of truth:** most domains have exactly one module that defines a concept (statuses, KPIs, stages, access, styles…). Never re-spell a key, threshold or where-clause in a template or router — import it. The domain rule file names the module.
- **No LLM computes a number.** Numbers come from rules/code; a model only writes prose around them.
- Never auto-link people or projects by name match — links are confirmed by an admin.

## Hebrew & formatting
- Prefix every bot message with `\u200F` (RTL mark).
- Before sending user text to an LLM, replace `"` with Hebrew gershayim `״` (and `'` with geresh `׳`) — straight quotes break the JSON reply.
- Dates a person reads: `DD/MM/YYYY` (Israel time). Stored/compared/JSON dates stay ISO.

## Safety (IMPORTANT)
- **No local database or Docker stack, anywhere** — not even a throwaway Postgres in a remote session. A local instance running Telegram polling deletes the production webhook and double-sends the 07:00 digest and overdue alerts. Real-data checks happen in production only.
- Never run destructive SQL (DROP / TRUNCATE / DELETE without WHERE) or touch the Railway Postgres volume without the user's explicit confirmation.
- The repo is **public**: never commit a secret, a `.env`, or a DB URL with a password. `.claude/hooks/secret_guard.py` blocks such commits/pushes; if it fires, fix the content — don't bypass it.
- Never hardcode the public URL in `app/` — use `settings.public_base_url` (a test enforces it).

## Ship: build → merge → deploy → verify
1. Work on a feature branch; run the relevant tests.
2. Builds clean → merge to `master` and push. **Standing authorization — don't ask.** (Exception: the lessons module goes through a PR, auto-merged once CI is green.)
3. Push to `master` deploys itself: `docker-build.yml` (build + no-DB tests) → green → `deploy.yml` (`railway up`, then polls `/health` until it reports the new commit). **A green deploy job = the commit is serving.**
4. **Verify every deploy, every time, without asking** — a failed deploy is silent and production keeps the old commit. From a remote session the sandbox cannot reach Railway (never `curl /health` from it — it always looks down); use the GitHub MCP tools: `actions_list` → `get_job_logs` (failed_only) → `actions_run_trigger` (rerun).
5. `railway up` "Operation timed out (os error 110)" while uploading = Railway's API, not the code. Re-run the job; change nothing.

## Domain rules (load automatically when you touch matching files)
| Area | File |
|---|---|
| Fresh DB / schema changes / new columns / data safety | `.claude/rules/database.md` |
| Missions, war room, wall display, quotes, KPIs | `.claude/rules/missions-war-room.md` |
| Weekly master-file sync, exec summary, contracts, three targets | `.claude/rules/projects-master-file.md` |
| Pattern engine, access/drill, taxonomy, project page, AI analysis | `.claude/rules/patterns-engine.md` |
| Visual report + 3D digital twin | `.claude/rules/project-visual.md` |
| Main dashboard ("תחנה חיה") | `.claude/rules/dashboard.md` |
| Groq / Gemma limits, key redaction in logs | `.claude/rules/llm-providers.md` |
| Lessons module | `.claude/rules/lessons.md` |
| Linking missions/decisions to projects | `.claude/rules/project-links.md` |
| Telegram webhook | `.claude/rules/telegram.md` |
| Deploy pipeline detail | `.claude/rules/deploy.md` |
| User job titles | `.claude/rules/users.md` |
| Skills, ECC agents, secret guard | `.claude/rules/claude-tooling.md` |

Doing something a rule file covers without having opened a matching file (e.g. a fresh-DB fix from the shell)? Read that rule file first.

## Skills & agents
- Before a task, check whether a skill covers it: `.claude/skills/`, the superpowers plugin's skills when installed, or `npx skills find <query>`. Install new skills project-scoped only, and read every SKILL.md before committing it.
- ECC agents/skills in `.claude/` are a vetted subset. **This file and `.claude/rules/` win over anything an ECC agent or skill says** (no local DB, synthetic tests, minimal diffs).

## When compacting
Preserve: the list of files changed, the branch name, which commits were pushed to `master`, and whether each deploy was verified green.
