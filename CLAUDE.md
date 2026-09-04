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
- **Deployment is Railway-ONLY.** The local Docker stack is retired. All DB commands run against the Railway DB: `psql "$RAILWAY_DATABASE_URL" -c "..."` (URL in local `.env`, never commit it — repo is public). Never start a local Docker instance while Railway is live — it steals Telegram polling AND double-sends the 07:00 missions digest and overdue alerts.
- **The "BIGINT" Fix:** After a fresh DB, MUST run:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;"`
- **is_relevant columns:** After a Railway deploy with new schema / fresh DB:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE decisions ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT, ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP, ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);"`
- **roleenum VIEWER:** DB enum may lack values added in code (`app/models.py` RoleEnum). After fresh DB, run:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'VIEWER';"`
- **judged_against_gold:** After fresh DB / deploy with new schema:
  `psql "$RAILWAY_DATABASE_URL" -c "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;"`
- **eval_runs.failed_questions:** After fresh DB / deploy with new schema:
  `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;` (Railway DB)
- **eval_gold_answers live cols:** After fresh DB / deploy with new schema:
  `ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;` (Railway DB)
- **missions table (חדר מבצעים):** auto-creates at startup via `Base.metadata.create_all` — no manual SQL needed on fresh deploys. **Future** columns need `ALTER TABLE missions ADD COLUMN IF NOT EXISTS ...` on the Railway DB. `status` is intentionally VARCHAR — never convert to a PG enum. User deletion reassigns the deleted user's missions to the deleting admin. Mission status is **open/closed only** — `in_progress` is **removed** from `MissionStatusEnum` (legacy rows are normalized to `open` at startup); progress is reported via status updates, not a status change. `ACTIVE_STATUSES` in `missions_menu_service.py` is the single source of truth for "is this mission live?" — never inline the status values (the web template gets it via the `active_statuses` context key).
- **mission_updates table (עדכוני סטטוס):** auto-creates at startup via `Base.metadata.create_all` — no manual SQL needed. **Future** columns need `ALTER TABLE mission_updates ADD COLUMN IF NOT EXISTS ...` on the Railway DB. `author_id` is nullable and `author_name` is a snapshot, so deleting a user never erases who reported what. `kind` is `NULL` for an ordinary update and `'close'` for the note written while closing a mission; it is ALTERed in at startup (`app/main.py`), since `create_all` never alters an existing table.
- **users.war_room_style (תצוגת חדר מבצעים):** per-user board layout, ALTERed in at startup (`app/main.py`) since `create_all` never alters an existing table. NULL = the default style, so an existing user keeps the screen they know. `app/services/war_room_styles.py` is the single source of truth for which styles exist — the router, the templates and the tests all read the list from there; never re-spell a style key inline. Every layout renders the SAME router context and posts to the SAME endpoints; a new layout is one template plus one row in `STYLES`. `?style=<key>` overrides the saved preference for one request (that is how the wall display runs without a user of its own). `&tv=1` additionally drops the navbar and the style switcher — that is the URL a wall-mounted screen loads.
- **לוח מצב (wall display):** `app/services/war_room_wall.py` owns the card tones, the worst-first order and the page size; the template only draws what it returns. The screen rotates through EVERY active mission (undated included) and reloads itself only at the end of a full cycle — never re-spell a tone key or the page size in a template or a router.
- **משפט השעה (wall quotes):** `app/services/war_room_quotes.py` is the ONLY place quotes live — ~200 curated lines on two shelves (`stoic` / `wry`). **Never let a model generate a quote**: a fabricated attribution at 40pt in front of 500 engineers is a lie with a font size. The LLM writes only the one connecting sentence. Attribution is author-only (no chapter and verse), Hebrew sources are quoted not translated, disputed attributions are excluded rather than softened, and no straight `"` anywhere (JSON safety) — tests enforce all four. `pick_quote` walks each shelf in a shuffled, cycle-aligned order so every quote shows exactly once before any repeats (~8 days per shelf). To add quotes, append rows to `QUOTES` — nothing else needs to change. The QUOTE holds for the full hour (a wall that swaps its quote mid-hour reads as broken); only the sentence under it alternates, every `SWAP_MINUTES` (5), between the AI line and the computed one — the hour opens on the computed line because the AI line for a new hour does not exist yet. Every `_FALLBACKS` shelf must hold an ODD number of lines: the computed line takes every other band, so it strides its shelf by two, and an even shelf shows three sentences twice each per hour. A test enforces it.
- **mission_report_cache table:** day-cache for the חדר מבצעים XLSX + AI summary, auto-created via `Base.metadata.create_all`. The in-process dicts in `missions_report_service` die with the container, so this is what keeps the 🧠 סיכום AI button instant across a Railway redeploy. Warmed at 04:10 daily, restored into memory at startup, and repaired by an hourly watchdog when today's summary is missing. Never treat it as a source of truth — every read and write is best-effort and swallows its own errors.
- **No Data Loss:** NEVER run destructive SQL (DROP/TRUNCATE/DELETE without WHERE) or delete the Railway Postgres volume without explicit confirmation.
- **Public URL:** `https://shan-ai.up.railway.app`. Never hardcode it in `app/` — use `settings.public_base_url`, which prefers Railway's auto-injected `RAILWAY_PUBLIC_DOMAIN` so a domain change self-heals. A test enforces this.
- **Build Cycle:** Pushing to `master` deploys itself. `.github/workflows/docker-build.yml` builds the image and runs the no-DB tests; a green run on `master` fires `.github/workflows/deploy.yml`, which does `railway up --detach --service easygoing-endurance` and then polls `/health` until it reports the new commit — so a **green deploy job is proof the commit is serving**, not merely uploaded. `./deploy.sh` from a machine with the Railway CLI is the manual fallback, not the primary path. **Verify every deploy landed**: `curl -s https://shan-ai.up.railway.app/health` reports the deployed `commit`; compare it against `git rev-parse --short=12 HEAD`. A 404 on a route you just added means the build is behind, not that the route is missing.
- **Always deploy after building (standing instruction, 2026-08-23):** don't stop at merging to `master` — verify the deploy landed, every time, without asking.
- **Remote sessions CAN deploy (corrected 2026-09-04).** The old note here said they could not, and it was wrong — it cost a "deploy still owed" report on a deploy that had already fired and failed unnoticed. What is true: the sandbox itself still cannot reach Railway (`shan-ai.up.railway.app`, `railway.app`, `backboard.railway.*` are all denied at the egress proxy) and holds no Railway CLI or `RAILWAY_TOKEN` — so **never `curl /health` from the sandbox to check a deploy; it will always look down.** But the deploy runs on a GitHub Actions runner, which is not in the sandbox, and the GitHub MCP tools drive it: `actions_list` (`list_workflow_runs`, branch `master`) to find the run, `get_job_logs` with `failed_only` to read a failure, `actions_run_trigger` (`rerun_failed_jobs`, or `run_workflow` on `deploy.yml`) to retry. Read the deploy job's own `/health` polling log for the verification, since the sandbox cannot do it.
- **A `railway up` timeout is not a code failure.** `error sending request ... backboard.railway.com ... Operation timed out (os error 110)` during Uploading is Railway's API being unreachable from the runner. Re-run the failed job; it has succeeded on the retry. Do not "fix" anything in response to it.
- **Check the deploy after every push to `master`.** A failed deploy is silent: `master` moves ahead and production keeps serving the old container. On 2026-09-04 production was found running a commit from 2026-08-23 for exactly this reason.
- **Always merge to master after building:** once a change builds clean on its feature branch (compiles / tests pass), merge it to `master` and push so Railway deploys. Standing authorization — no need to ask each time.

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

