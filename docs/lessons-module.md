# מערכת לקחים — runbook (P1–P2)

P1: schema `lessons`, the PostgREST service and the `/lessons/api` gateway.
P2: the React app at `/lessons/`. The data import (P3) is not here yet.

## Pieces

| Piece | Where | Notes |
|---|---|---|
| Schema, roles, grants | `app/services/lessons_schema.py` | Runs at startup (`ensure_schema`), idempotent, never raises. Never issues DDL on `public` — tested. |
| Guards (pure) | `app/services/lessons_access.py` | Who you are (`Identity`), which writes pass (`check_request`). |
| Gateway | `app/routers/lessons_gateway.py` | `/lessons/api/*`. Shan-AI session required; API paths answer 401 JSON. |
| SPA serving | `app/routers/lessons_spa.py` | `/lessons/*`: built file as is (public, cached by kind); any other path needs a Shan-AI session → `index.html`, else `/login?next=…`. |
| UI source | `lessons_ui/` | Copied from acumen-spark-hub (no `.env`, `supabase/`, Lovable plugins). Built in the Docker stage `ui` → `static/lessons`. |
| Tests | `tests/test_lessons_module.py` | No DB. In the CI list. |

## UI changes vs. the Lovable app

- `base: /lessons/`, `BrowserRouter basename="/lessons"`, PWA scope `/lessons/`; the service worker never answers `/lessons/api/*`.
- `client.ts` builds `SUPABASE_URL` from `window.location.origin + /lessons/api` at runtime; no env vars.
- Login = `/lessons/api/me`. No session → `/login?next=/lessons/`. Logout → Shan-AI `/logout`.
- No passwords anywhere (user picker, change-password dialog and admin password field removed).
- `UserSwitcher` = "act as": yourself or a referent group from `/me` (kept per tab in `sessionStorage`).
- AI buttons call `/lessons/api/functions/v1/<name>` (see "AI functions" below); a 503 there means "busy".
- `/login` keeps `next` (`login.safe_next`: same-site paths only — never an open redirect).

## Identity

- A Shan-AI user's module profile = the `lessons.profiles` row with `shan_user_id = users.id`
  (a linked person beats a lazy viewer row). None → a viewer `s<user_id>` is inserted on first visit.
- Referent groups: `lessons.referent_members(profile_id, shan_user_id)`; the profile must be
  `role='referent'` (trigger). Not exposed to PostgREST.
- Writes may name as actor only the caller's own profile or a group they belong to; module
  `admin` may act for anyone. Actor fields per table: `lessons_access.ACTOR_FIELDS`.
- Non-admins may PATCH `profiles` only for `assigned_projects` (the SPA syncs it when a PM
  creates a project) and their own `email_preferences`.

## PostgREST service (Railway, project Shan-AI)

Create only after this branch is deployed (the schema must exist first).

| Setting | Value |
|---|---|
| Image | `postgrest/postgrest:v12.2.3` |
| Service name | `postgrest` (→ `postgrest.railway.internal`) |
| Public domain | **none** — private network only |
| `PGRST_DB_URI` | `postgres://lessons_authenticator:<LESSONS_PGRST_PASSWORD>@${{postgres-v2.RAILWAY_PRIVATE_DOMAIN}}:5432/${{postgres-v2.POSTGRES_DB}}` |
| `PGRST_DB_SCHEMAS` | `lessons` |
| `PGRST_DB_ANON_ROLE` | `lessons_anon` |
| `PGRST_DB_EXTRA_SEARCH_PATH` | `lessons` (default is `public`) |
| `PGRST_DB_MAX_ROWS` | `1000` |
| `PGRST_SERVER_PORT` | `3000` |
| `PGRST_OPENAPI_MODE` | `disabled` |

App service variables:

| Variable | Purpose |
|---|---|
| `LESSONS_PGRST_PASSWORD` | 16–128 chars of `[A-Za-z0-9_-]`. Applied at startup to `lessons_authenticator`. Same value in `PGRST_DB_URI`. |
| `LESSONS_POSTGREST_URL` | Default `http://postgrest.railway.internal:3000`. |
| `RESEND_API_KEY` | Optional. Without it the e-mail function answers 503. |
| `LESSONS_EMAIL_SANDBOX` / `LESSONS_EMAIL_SANDBOX_TO` | Sandbox on by default: every mail goes to `…_SANDBOX_TO`. |
| `LESSONS_UPLOAD_DIR` | Default `uploads/lessons` (the uploads volume). |

## Verify (no sandbox access to Railway — open in a browser as a module admin)

`GET /lessons/api/_status` →
`{"schema": true, "roles": true, "leaks": [], "error": null, "postgrest": true}`.

- `roles: false` → the app's DB user lacks CREATEROLE; create the two roles by hand
  (statements in `ROLE_STATEMENTS`).
- `leaks` non-empty → some relation outside `lessons` is granted to PUBLIC and `lessons_anon`
  can reach it. Do not route traffic until it is empty.
- The admin check needs a module admin profile, which exists only after the P3 import + links.
  Until then the startup log line `lessons schema ready; lessons_anon isolated…` is the proof.

## Rollout order (learned 2026-10-10)

1. Deploy the app with `LESSONS_PGRST_PASSWORD` set **first** — the password is applied to
   `lessons_authenticator` at app startup.
2. Only then start PostgREST. Started earlier, it fails auth in a loop until Railway marks it
   CRASHED; a plain redeploy of the `postgrest` service fixes it once the app is up.
3. `PGRST_SERVER_HOST=*` (IPv4 + IPv6 on Railway's private network), `PGRST_LOG_LEVEL=warn`.
   Healthy log: `Successfully connected to PostgreSQL` + `Schema cache loaded 18 Relations`.

## Import from Lovable (P3) — `app/services/lessons_import.py`

- Admin page: `/lessons/api/_import` (Shan-AI `is_admin`). "בדיקה" = dry run (fetch, drift
  check, link preview, no writes); "ייבוא" = replace all module data in ONE transaction.
- Source: `LESSONS_SRC_URL` + `LESSONS_SRC_KEY` (the Lovable site's public anon key) on the app
  service. GETs only; the Lovable site is never written to.
- Aborts before writing on a short read or a source column the target lacks (only
  `profiles.password` is dropped on purpose). Count mismatch after load rolls everything back.
- Re-import keeps module-local state: Shan-AI links, referent group members, project links,
  lazy viewer profiles. Links from `LINKS` apply only when the names are the same word set and
  never override a link an admin set.
- Referent profiles, `מנהל פרויקט - עבר` and `צפייה בלבד` get `is_login = false`.

## Project links — `/lessons/api/_project_links` (PLAN-lessons-projects.md L1)

Admin tab "קישור פרויקטים" (`/lessons/admin?tab=project_links`, Shan-AI admin only). Each lessons
project is linked to an ACTIVE master-file project (rapidfuzz suggestions, nothing applied until
clicked), merged into another lessons project (lessons + implementations move, the duplicate is
deleted, counts checked), or kept local. Then "צור N פרויקטים" creates every remaining active file
project. Rules: `app/services/lessons_project_link.py`. Every action writes `activity_logs`.

## Referent groups — `/lessons/api/_groups` (`app/routers/lessons_admin.py`)

Shan-AI admin page: pick members from the Shan-AI users list per referent group (add / remove).
`referent_members` is closed to PostgREST, so this page is the only way in. Included in
`app.main` before the gateway (its catch-all would 404 these paths). Survives re-import.

## Nightly cleanup — `app/services/lessons_maintenance.py`

00:30 UTC daily: `lessons.notifications` older than 30 days are deleted (owner decision
2026-10-10). Only rows whose `time` starts with a date are compared — Lovable's monthly pg_cron job
failed every month since June on one row holding "לפני שעה". Never raises.

## Daily DB backup — Railway service `db-backup`

Cron `0 0 * * *` (UTC). Start command runs the `DAILY_BACKUP_SCRIPT` variable: read-only
`pg_dump` of `postgres-v2` (`BACKUP_DB_URL`, built from postgres-v2 references) to
`/backups/daily-<ts>.dump` + row counts; keeps 14; never touches the migration dumps.
**Config changes on a cron service only apply on a NEW deployment** (bump a variable, e.g.
`BACKUP_CONFIG_VERSION`) — `redeploy` re-runs the previous config (learned 2026-10-10).

## AI functions — `app/services/lessons_ai.py`

The six Lovable edge functions (summarize-lessons, review-lesson, analyze-lessons,
classify-ai-feedback, suggest-categories, admin-insights) run on Shan-AI's router:
`llm_chat("lessons_ai", …)` — Groq first, Gemma on failure; switch provider on "🤖 מודל AI".
Lovable's `LOVABLE_API_KEY` is a Lovable-internal gateway secret: not readable, not reusable.
- Prompts are the Lovable ones word for word; tool calling became JSON mode (schema in the
  prompt, reply normalised to the exact keys the React code reads).
- Context is packed to `CONTEXT_CHARS` so prompt + max_tokens fits Groq's 8,000 TPM (a test
  builds 400 lessons and checks it).
- The gateway: 12 calls/min per user (shared Groq quota), preferences read only for the caller
  or their groups, feedback filed only as the caller or their groups, lesson/referent ids the
  model invents are dropped. Both providers down → 503 "שירות ה-AI עמוס כרגע".
- Admin HTML pages (`_groups`, `_import`) send a logged-out browser to `/login?next=…`.

## People links — ניהול מערכת → קישור משתמשים (`/lessons/api/_links`)

The module's admin tab (was "משתמשים"). Every existing profile — a person or a role imported
from Lovable — gets the Shan-AI user who now signs in as it; that user inherits its role,
projects and history. Picked from the Shan-AI users list only, never matched by name. A
user may hold one login profile (409 otherwise). Lazy `s<id>` viewer rows and the historical
names (`NO_LOGIN_NAMES`) are not link targets. Users are created only in Shan-AI — the tab no
longer adds or deletes people; ✏️ still sets module role / projects / stages. Shan-AI admin only.
Someone the old system never had is set up in ניהול משתמשים (below), not here.

## People — ניהול מערכת → ניהול משתמשים (`/lessons/api/_people`, 2026-10-17)

The module admin's default tab. EVERY Shan-AI user, with their module role, projects and
referent groups — also people the old system never had (a new PM: nothing to link them to).
An admin sets them up **before** their first visit: saving creates the user's own local row
`s<user_id>` (the same row a first visit would create lazily) or edits their effective profile.

- **Not set up** (owner decision 2026-10-17; `person_problems`, pure): `unset` = no module row,
  or still the automatic viewer (`s<id>`, role viewer, `defined_at` NULL, in no referent group);
  `pm_no_projects`. A missing e-mail is NOT flagged (owner's choice). An explicit "צפייה בלבד"
  saved here stamps `profiles.defined_at` and is set up.
- **Referent groups** (`group_problems`): `no_members` (no member and no login account),
  `no_stages`. Members are edited per person here or on `_groups`; stages via ✏️.
- Projects follow the edit dialog's rule: one manager per project — taking one moves
  `projects.manager_id` and drops it from the other PMs. Admin = every project, viewer = none.
- A local `s<id>` row is never a link target, even with a role (`la.is_local`); a linked
  old-system profile beats it in `pick_profile` (linking = inheriting the old profile).
- Re-import keeps local rows with their role, projects, stages, preferences and `defined_at`.
- **The links tab is a migration tool.** `GET /lessons/api/_links/status` → `{left, done}`:
  people from the old system still unlinked (referent groups and `NO_LOGIN_NAMES` don't count).
  At 0 the tab says "ההעברה הסתיימה ✓" and moves to the end of the menu — it never disappears
  (owner decision 2026-10-17).

## Top bar — `/lessons/api/_navbar`

The SPA draws Shan-AI's own `_navbar.html` (rendered per request for the session user) above
its layout and publishes its height as `--shan-nav-h`. Not injected into `index.html`: the
service worker answers navigations from its precached copy, which would never carry it.
Logout lives in that bar only.

## Referent assignment & drafts — `lessons_ui/src/lib/lessonAssignment.ts` (2026-10-15)

Owner decisions: only the lesson's **creator** picks the referent; the AI never recommends one.

| Created with | `status` / `workflow_status` | Next |
|---|---|---|
| a referent in "אחראי ליישום" | `new` / `חדש` | admin approves → one `apply_lesson_workflow_event` call sets `approved` / `בטיפול רפרנט` + `assigned_to`, event `assigned_to_referent`; a `referent_reviews` row is added only if missing |
| no referent | `draft` / `טיוטה` | visible to its creator only; "שלח לאישור" (enabled once a referent is set) → `new` / `חדש`, event `submitted_for_approval` |

- The "אחראי ליישום" pickers list `role='referent'` profiles only (`referentOptions`); a legacy non-referent value stays shown until changed.
- A legacy lesson without a valid referent keeps the old flow (manual distribution). Distribution never replaces an existing `assigned_to`.
- Draft visibility is a UI filter (`isVisibleLesson`) — the module has no row-level access; PostgREST still returns the row.
- No schema change: both columns are free `text`.

## Phone app login — M0 (PLAN-lessons-mobile.md, 2026-10-16)

An installed PWA must sign in without leaving `/lessons/` (on iOS the out-of-scope `/login`
opens outside the app, and its cookie may never reach it).

- `/lessons/login` is the module's own login page (`pages/Login.tsx`), the ONLY app route that
  opens without a session (`lessons_spa.PUBLIC_ROUTES`). `SHAN_LOGIN_URL` points there; the
  provider never redirects while already on it (it would loop).
- It talks to `GET /login/users` (id, name, job title — what `/login` already shows) and
  `POST /login/app` (JSON; same `_check_credentials` as the HTML form). Remembers the user id
  only, in the same `shan.remembered_user_id` key as Shan-AI's login page.
- **"השאר אותי מחובר"** = `LESSONS_REMEMBER_DAYS` (clamped 7..90; JWT `exp` matches the cookie).
  Default 7 = no change and the checkbox is hidden. **Raise it only after information-security
  approval** — the cookie is Shan-AI's, so it opens every Shan-AI page for that long.
- Opened from the home-screen icon (`lib/standalone.ts`): no Shan-AI navbar; a logout button sits
  in the module's top bar and lands back on `/lessons/login`.

## Phone wizard — M1 (PLAN-lessons-mobile.md, 2026-10-16)

`/lessons/m/new`, `/lessons/m/lessons/<id>/edit`, `/lessons/m/drafts` — full screen (`MobileShell`,
no sidebar/navbar), `add_lessons` permission. Phones get here from "לקח חדש" / "הטיוטות שלי" on the
lessons page (the dialog stays for `md` and up), from `?newLesson=<project>` on a phone, from
"✏️ השלמה בשלבים" on a draft, and from the icon's long-press shortcuts.

- **One rule for what a lesson needs** — `lessonAssignment.ts`: `SAVE_REQUIRED` (title, what
  happened, project, risk — owner decision M-D5) and `SUBMIT_REQUIRED` (title, project, stage,
  category, risk + a referent). `canSubmitDraft` (the computer's "שלח לאישור") uses the same list,
  so a phone draft saved with the minimum cannot be sent without its classification.
- `lessonDraft.ts` (pure, tested): form ⇄ lesson, `selectableProjects` (the dialog's rule, used by
  both), `LESSON_STAGES` (moved out of `Lessons.tsx`), the phone copy in `localStorage`
  (`lessons.wizard.<user>.<lessonId|new>`, kept until the server has the lesson; never a file).
- `addLesson` now **returns the lesson and throws** when the insert is refused (it used to show
  "created" on a failed insert — the dialog now says so). `{ draft: true }` keeps a lesson with its
  creator even with a referent picked. `submitDraftLesson(id, snapshot)` takes the just-saved
  lesson, since the provider's state lags a render. `updateLesson` can change the project.
- Photos are shrunk on the phone before upload (`lessonFiles.ts`: long side 1600 px, JPEG 0.82).
- Only the creator's **draft** opens in the wizard; a lesson already sent is edited on the computer.

## Phone browsing — M2 (PLAN-lessons-mobile.md, 2026-10-16)

`/lessons/m` (home), `/lessons/m/lessons` (list), `/lessons/m/lessons/<id>` (read-only view), inside
`MobileShell` with a bottom tab bar (בית / לקחים / ➕ / טיוטות; never on the wizard — `showsTabBar`).

- **A phone opens `/m`** when it loads `/` (`App.tsx` `HomeRoute`, `opensPhoneHome()` = phone width
  and no `lessons.preferDesktop`). "תצוגת מחשב" on the home sets the preference; "תצוגת טלפון" in the
  computer layout's top bar (phones only) clears it.
- **Who sees which lessons** = `lib/lessonScope.ts` (`myScope` / `scopedLessons` / `canToggleScope`),
  moved out of `Lessons.tsx`; the computer's list and the phone list both call it. Drafts of others
  are already filtered by the provider (`isVisibleLesson`).
- List: search (title / project / description), "שלי / הכול", "ממתינים לי" (`useActionRequired`, the
  same "needs action" as the computer), project; what waits for me first, then newest; 30 more per scroll.
  Filters live in the URL, so "back" from a lesson keeps them.
- View: read only; an image attachment opens full screen; a draft of mine → "✏️ השלמה בשלבים";
  responding / approving stays on the full page (`/lessons/<id>`) until M3.
- Dates people read on the phone are DD/MM/YYYY (`lib/ilDate.ts`).

## Phone handling — M3 (PLAN-lessons-mobile.md, 2026-10-16)

`/lessons/m/tasks` ("משימות" tab, with a count) lists what waits for me; the lesson page carries an
action panel (`components/mobile/LessonActions.tsx`) for exactly those tasks.

- **One rule for "what I must do" and "where it leads"** — `lib/lessonActions.ts`:
  `pendingTasks` (approve_new · admin_final · referent_decide · referent_feedback · pm_feedback ·
  creator_complete), `canReferentDecide` / `canAdminFinalize` / `canCreatorComplete` (the computer's
  cards use them too), `referentDecisionNext` (לא רלוונטי / הלקח נדחה → נדחה, else → ממתין לאישור מנהל
  מערכת), `creatorCompletionTarget` (back to whoever returned it), `aiDistributionTargets` (AI
  pre-selects known projects in the lesson's own stage only).
- `hooks/useLessonWorkflow.ts` — the steps as single calls; `LessonDetail.tsx`'s buttons call them.
- `hooks/useApproveWithAI.ts` — approve + the AI's distribution proposal (owner decision 2026-10-16:
  the phone runs the full flow, not approval only); `Lessons.tsx`'s dialog and the phone's full-screen
  sheet both use it. The sheet lives above the task card: approving ends the task.
- On the phone, "ממתינים לי" (home counter, list filter, card labels) = `pendingTasks`. The computer's
  sidebar badge still uses `useActionRequired`, which does not count the admin's final step or a
  lesson returned to its creator — the two numbers can differ.
- Still on the full page only: editing a returned lesson, manual distribution, AI re-analysis, history.

## Opening screen (owner decisions 2026-10-18)

`components/mobile/IntroSplash.tsx` + `lib/intro.ts` + `.intro-*` in `src/index.css`. Phone screens
only (`MobileShell`): the animation plays **once a local day** (`shouldPlayIntro`; never with "reduce
motion", never when storage is unavailable — a still picture instead of replaying every launch) and
is marked played only when it finishes (a logged-out start leaves for /login midway). A tap skips.
Every other start shows the finished picture (`mode="hold"`) while the data loads — it replaced the
spinner. Text: "מערכת לקחים · רושמים לקחים — סוגרים מעגל · אגף פרויקטי השנאה" (`INTRO_TEXT`).

- Art = a field photo (workers' consent given to the owner), sketched by rules (edge pencil + halftone,
  no image model) in the app's colours; the lift's brand and model lettering were erased before
  sketching. `public/intro/ink.webp` (navy lines + halftone) and `orange.webp` (people + lift), 780×1040,
  ~590 KB together, precached by the service worker (`webp` added to `globPatterns`).
  **NOT COMMITTED (2026-10-18):** the repo is public and built files are served without a login, so
  the photo-based images wait for the owner's decision (public / behind login / vector art). Until
  then this branch carries the code only and must not be merged.
- Title font Karantina is bundled (`public/fonts/karantina-700-hebrew.woff2`, OFL) so it works offline.
- A 6-second MP4 of the same opening was rendered with Remotion for presentations only; it is not in the
  app and not in the repo (Remotion needs a company licence beyond 3 people).
