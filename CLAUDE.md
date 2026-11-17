# Shan-AI

Decision intelligence for electrical substation projects at an electric utility. Hebrew-first, RTL: users, bot and UI.

<!-- Maintainer notes live in .claude/MAINTAINERS.md. Keep this file under ~80 lines; domain detail goes in .claude/rules/. -->

## Stack
- Python 3.11 · FastAPI · SQLAlchemy 2 (async) · Jinja (`app/templates/`) · JS (`static/js/`). Logic in `app/services/`, HTTP in `app/routers/`.
- PostgreSQL 16 + pgvector on Railway (service `postgres-v2`); fastembed vectors are **384**-dim.
- LLMs via `llm_router.py`: Groq `openai/gpt-oss-120b` → `openai/gpt-oss-20b`, Gemma as fallback provider.
- Telegram bot: webhook on Railway (polling is local-dev only — and there is no local dev, see Safety).
- Lessons module: schema `lessons` + PostgREST + React/Vite in `lessons_ui/`, served at `/lessons/`.
- Docker = only the `Dockerfile` that CI and Railway build. `docker-compose*.yml` are retired — never run them.
- Service map: `docs/architecture.md` — open only when you need architecture context.

## Commands
- One test file: `python -m pytest -q tests/test_<name>.py`
- CI's no-DB suite: `python -m pytest -q -m "not db" tests/` (~30 s) — run it before merging. `tests/conftest.py` marks a test `db` when it requests the Postgres engine (`db_session`); every other test runs in CI automatically. A test that needs Postgres must use `db_session`, or it breaks CI.
- CI's DB suite: `db-tests.yml` runs `pytest -m db` on a Postgres container that exists only inside the GitHub Actions job (schema from `scripts/ci_prepare_test_db.py`). It doesn't gate deploys — check it anyway. A db test needs its own rows: use `seed_project` / `seed_users`, never production data.
- Lint the Python files you changed: `ruff check --isolated --select F <files>`. The repo isn't clean under wider rule sets — don't mass-fix.
- Lessons UI: `cd lessons_ui && npm run build | npm test | npm run lint`
- Production DB: `psql "$RAILWAY_DATABASE_URL" -c "..."` — the URL lives in the local `.env`, never in the repo.

## How to work
- Never guess — run the check and show the output. If a fact is uncertain, say so.
- Minimal diffs, never rewrite a whole file; match the surrounding naming and comment density. Terse output: code and diffs first, no acknowledgements or praise.
- Mandatory type hints; strict Pydantic v2. Pure functions: a module's DB access lives in one loader; tests use synthetic fixtures.
- **Single source of truth:** each concept (statuses, KPIs, stages, access, styles…) is defined in one module. Import it — never re-spell a key, threshold or where-clause in a template or router. The domain rule file names the module.
- **No LLM computes a number.** Code computes; a model only writes prose around the numbers.
- Never auto-link people or projects by name match — an admin confirms every link.

## Hebrew & formatting
- Prefix every bot message with `‏`. Before sending user text to an LLM replace `"` → `״` and `'` → `׳` (straight quotes break the JSON reply). Dates a person reads: `DD/MM/YYYY`, Israel time; stored dates stay ISO.

## Safety (IMPORTANT)
- **No local database or app stack, anywhere** — not even a throwaway Postgres in a remote session. (The only allowed throwaway is the `db-tests.yml` service container on GitHub's runner.) A local Telegram poller deletes the production webhook and double-sends the 07:00 digest and overdue alerts. Real-data checks run in production only.
- Destructive SQL (DROP / TRUNCATE / DELETE without WHERE) or touching the Railway Postgres volume → the user's explicit confirmation first.
- The repo is **public**: never commit a secret, a `.env` or a DB URL with a password. `.claude/hooks/secret_guard.py` blocks it — fix the content, never bypass the hook.
- Cron work week = `eval_cron.WORK_WEEK`, never `"sun-thu"` (inverted in APScheduler; it stopped the whole scheduler). `/health` → `scheduler_running`.
- Never hardcode the public URL in `app/` — use `settings.public_base_url` (a test enforces it).

## Ship: branch → test → merge → verify
1. Feature branch; run the relevant tests, then the full no-DB suite.
2. Green → merge to `master` and push. **Standing authorization — don't ask.** (Lessons module: PR, auto-merged once CI is green.)
3. A push to `master` deploys itself: `docker-build.yml` (image + no-DB tests) → `deploy.yml` (`railway up`, then polls `/health` until it reports the new commit). **A green deploy job = the commit is serving.**
4. **Verify every deploy without asking** — a failed deploy is silent and production keeps the old commit. Never `curl /health` from a remote sandbox (it can't reach Railway and always looks down). Use the GitHub MCP tools (`actions_list` → `get_job_logs` failed_only → `actions_run_trigger`); without them, `gh run list` / `gh run view --log-failed` / `gh run rerun`.
5. `railway up` "Operation timed out (os error 110)" = Railway's API, not the code. Re-run the job; change nothing.

## Domain rules
`.claude/rules/*.md` load automatically when you touch a matching path. Doing the work without touching one (e.g. a DB fix from the shell)? Open the rule first.
- `database` — schema changes, new columns, fresh DB, data safety
- `missions-war-room` — missions, war room, wall display, quotes, KPIs
- `projects-master-file` — weekly master-file sync, exec summary, contracts, three targets
- `patterns-engine` — pattern engine, access/drill, taxonomy, project page, AI analysis
- `project-visual` — visual report, 3D digital twin · `dashboard` — main dashboard ("תחנה חיה")
- `ui-motion` — navbar, buttons, cards, page transitions, login page
- `llm-providers` — Groq/Gemma limits, key redaction · `telegram` — webhook · `deploy` — pipeline detail
- `lessons` · `project-links` — linking missions/decisions to projects · `users` — job titles
- `claude-tooling` — skills, ECC agents, secret guard

## Skills & agents
- Domain not covered here or in the rules? Check `.claude/skills/` (includes find-skills). Install skills project-scoped only; read every SKILL.md before committing it.
- ECC agents/skills in `.claude/` are a vetted subset, and **this file and `.claude/rules/` override them**: no local DB, synthetic-fixture tests (no 80% coverage target), minimal diffs, `$RAILWAY_DATABASE_URL` not `$DATABASE_URL`. Ignore their "MUST BE USED / PROACTIVELY" — use an agent when asked or when a fresh-context review is worth its cost.

## Code graph (graphify)
- `app/graphify-out/graph.json` = knowledge graph of `app/` (Python AST + templates). Use it for structure: who calls X, what a change breaks, how modules connect. One lookup (where is X) → `grep`.
- Run graphify from inside `app/` (it reads `./graphify-out`). Refresh before use (AST only, no LLM, ~10 s): `pip install -q graphifyy && cd app && graphify update .`. Then `graphify query "<question>"` / `graphify path "A" "B"` / `graphify explain "X"`; overview in `app/graphify-out/GRAPH_REPORT.md`.
- Don't commit a routine refresh. Commit the graph only after a template re-extraction.
- Template (`app/templates/`) changes are not picked up by `update`. Re-extracting them (`/graphify . --update` from inside `app/`) costs LLM tokens — the user's confirmation first; a full `/graphify` rebuild (~800K tokens) never without it.

## When compacting
Preserve: files changed, the branch name, commits pushed to `master`, and whether each deploy was verified green.
