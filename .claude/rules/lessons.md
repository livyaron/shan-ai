---
paths:
  - "app/services/lessons_*.py"
  - "app/routers/lessons_*.py"
  - "app/services/project_lessons.py"
  - "lessons_ui/**"
  - "tests/test_lessons_*.py"
  - "tests/test_project_lessons.py"
  - "docs/lessons-module.md"
---
# Lessons module (schema `lessons`)

Condensed 2026-11-16. Full text with history: `docs/rules-history/lessons.md` — read it before changing a rule's behaviour. Runbook: `docs/lessons-module.md`.

## Isolation and access
- Lives ONLY in schema `lessons`. `lessons_schema.py` is its DDL (startup, idempotent, never raises); no DDL against `public` beyond the two `REFERENCES public.users(id)` (a test greps for it).
- PostgREST runs as `lessons_anon`, granted schema `lessons` only; `GET /lessons/api/_status` → `leaks` must be `[]`. Server-only tables (`referent_members`, `ai_summary_cache`, `sync_runs`, `master_overrides`) are not in `TABLES` and `REVOKE ALL` from `lessons_anon`.
- Every write passes `lessons_access.check_request` (actor = caller's profile or own referent group; module admin acts for anyone). Identity only from `/lessons/api/me`; no passwords in the module.
- Module lessons and `lessons_learned` stay separate. Never create Shan-AI users; never link people or projects by name.
- **Ship: PR, merged automatically once CI is green** (user 2026-10-16); the standard deploy-verify rules apply.
- UI: built in the Docker stage `ui`, served by `lessons_spa.py` at `/lessons/`; never commit `dist/`.
- Phone app: `/lessons/login` is the module's own login and the only app route open without a session besides `index.html` (gating `index.html` caused the Android login loop). `LESSONS_REMEMBER_DAYS` (default 7) is raised only after information-security approval.

## People
- `/lessons/api/_people` lists EVERY Shan-AI user and sets module role / projects / referent groups before a first visit. `lessons_admin.person_problems` / `group_problems` are the ONLY definition of "not set up". The links tab is migration-only (stays, marked done, once `migration_left` = 0).

## Projects — `lessons_project_link.py` is the ONLY bridge from the master file
- Owns `STAGE_MAP`, `TYPE_MAP`, `RISK_BY_LEVEL` (pattern engine's level, never the model), and the manager chain: confirmed `manager_aliases` → user → lessons profile; unresolved = `pm_unassigned` ("טרם שויך", in `NO_LOGIN_NAMES`). Source = ACTIVE `public.projects` only.
- The file owns name/manager/stage/type/risk; the module owns station type/site/equipment (`station_type_set`).
- **Every live upload** runs `sync_after_upload()` (both paths; own transaction; never fails the upload); each project in its own savepoint (`shan_sync_error`); report in `lessons.sync_runs`. `sync_plan` (pure):
  - fills a manager only on a project held by `pm_unassigned`; a different real manager waits as `manager_differs` for the admin;
  - projects gone from the file → `shan_active=false`, never deleted;
  - creates new file projects only when the engine answered (`_levels` None = engine down → no risk written, nothing created), and not when `possible_duplicates` says it may be an undecided lessons project.
- `EXCLUDED_TYPES` (ניידות): never created or offered; `remove_excluded` deletes existing ones only if they hold no lessons and no distributions.
- `dismiss` ("מחק"): removes one file project from the module via `master_overrides.excluded`, only if nothing is linked; reversible; the master file is never touched.
- No unfounded defaults: missing stage/type holds the project (`missing_fields`) until an admin completes it (`complete` → `master_overrides`); a value the file gives always wins.
- `link_issues` is the ONLY definition of "ממתין לטיפול" (undecided / manager_differs / manager_unresolved / manager_placeholder / sync_error). Admin suggestions are hints only; "create the missing" is refused while any lessons project is undecided; never create on an unknown stage. Lovable re-import refuses to write while a module-only project exists (`local_only_projects`).
- Excel completion: `lessons_link_sheet.py` (pure), `GET/POST /lessons/api/_project_links/completion(.xlsx)` — preview before apply, empty cell = no change, stale value refused, users by `#id` only.
- Trigger `lessons.lock_master_fields` refuses `lessons_anon` edits of file-owned fields on a linked project — never SECURITY DEFINER.
- "חסר נתונים" (no station type / no site; equipment optional) is defined ONLY in `lessons_ui/src/lib/projectProblems.ts`. UI project writes throw on server refusal and never show a change the server didn't keep.

## Lessons on the Shan-AI project page — `project_lessons.py`
- The ONLY reader: read-only over `lessons`, bridged only by `lessons.projects.shan_project_identifier`. Lists: written on the project, and distributed to it. Never another user's draft. Best-effort in a savepoint (`{"ok": False}` on failure); access = the project page's gate.

## Dashboard AI and pending
- `summarize-lessons` saves only COMPLETE answers (`lessons_ai.summary_complete`: reached its `ציון ביטחון` line) in `ai_summary_cache`, keyed by `lessons_ai.prompt_key` (sha256 of the exact prompt; project lists sorted). Same prompt → saved answer, no model call; `cacheOnly: true` never reaches the model (204 when nothing saved).
- "ממתינים לאישור" = `lessonActions.awaitsAdminApproval` (status `new` or `ממתין לאישור מנהל מערכת`) — the same set `pendingTasks` gives the admin (a test holds them equal).

## Single-source lists (keep both sides in step)
- App start: `GET /lessons/api/_bootstrap`. `lessons_gateway.BOOTSTRAP_READS` = `STARTUP_TABLES` in `lessons_ui/src/lib/startupData.ts` (tested equal). Each read passes `la.check_request` + `la.forward_headers`; a failed table comes back under `errors` and only it is re-fetched; no route/network error = old path; 401 = login page. A new startup table goes in both places.
- Form fields: `lessons_ui/src/lib/fieldConfigs.ts` is the only reader of `form_field_configs` for screens (`findField` / `labelsFor`); any screen that writes the table calls `fieldConfigs.invalidate()`.

## Process metrics — `lessons_process_metrics.py`
- Read-only; `load_rows` is the only DB access, `compute` pure. **Numbers only — no names, titles or lesson text** (tested), so it may be logged: `LESSONS_PROCESS_METRICS {json}` ~45 s after each upload, and `GET /lessons/api/_process_metrics` (module admins). It is how a remote session measures the process — never add a free query or identifying detail.
