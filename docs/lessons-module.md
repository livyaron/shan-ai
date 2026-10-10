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
- AI buttons get the gateway's 503 and show "שירות ה-AI אינו מחובר כרגע".
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

## Referent groups — `/lessons/api/_groups` (`app/routers/lessons_admin.py`)

Shan-AI admin page: pick members from the Shan-AI users list per referent group (add / remove).
`referent_members` is closed to PostgREST, so this page is the only way in. Included in
`app.main` before the gateway (its catch-all would 404 these paths). Survives re-import.
