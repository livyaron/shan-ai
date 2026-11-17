---
paths:
  - "app/services/pattern_service.py"
  - "app/services/stage_sectors.py"
  - "app/services/insight_*.py"
  - "app/services/project_chart.py"
  - "app/services/il_format.py"
  - "app/services/forecast_reliability.py"
  - "app/services/blockers.py"
  - "app/routers/projects.py"
  - "app/templates/project_insights*.html"
  - "app/templates/project_page.html"
  - "app/templates/project_delays.html"
  - "app/templates/project_blockers.html"
  - "app/templates/_project_charts.js"
  - "tests/test_pattern_service.py"
  - "tests/test_insight_*.py"
  - "tests/test_forecast_reliability.py"
  - "tests/test_blockers.py"
---
# Pattern engine, access, taxonomy, project page, AI analysis, delays, blockers

Condensed 2026-11-16. The full text, with the history and measurements behind each rule, is in `docs/rules-history/patterns-engine.md` — read it before changing a rule's behaviour.

## Engine — `pattern_service.py`
- No LLM computes a number. Every metric is `{value, n, confidence, caveat}`; thresholds are named constants at the top of the module, nowhere else.
- `load_frames` is the only DB access; `compute_patterns` and `project_history` are pure, tested on synthetic frames. `frames_and_patterns(session)` returns `(frames, p)` from the `compute_cached` cache — `_visible_project` (project page, visual report, project AI) and the delays page use it (read-only); never reload snapshots per page.
- `stage_sectors.py` is the ONLY stage → sector map. `עבודה אזרחית והרכבות` sits in two sectors, so sector totals are never summed into a division total. Assignable sectors: `ASSIGNABLE_SECTORS` (no `unknown`). Stage ladder: `STAGE_RANK`.
- Report-date clustering is measured from a cluster's first date, never chained. A weekly report repeats by `near_same` (≥95 similarity, punctuation ignored), never by exact hash.
- Move events are measured against the last DATED report (`undated_between`); waves count consecutive pairs only; their drill filters on `from` and `to`. Drift (`_own_drift`) runs from each project's OWN first dated report to its last (needs ≥2). Weeks in stage with history starting in that stage carry `weeks_lower_bound` → shown "לפחות".
- Rule flags (`frozen`, `escalation`, `stage_gap`) are computed once in `_rule_flags`. Slip/baseline moves stay in the output as `events` — drills read them.

## Topic taxonomy — `RISK_CATEGORIES`
- Ambiguous stems are anchored on Hebrew word edges (`_B`/`_E`). **Before adding a stem, list what it actually matches in the weekly texts**; a test holds the known traps (הרכבת≠train, מוקדם, הגורמים/כרמיאל≠רמ"י, לעלות/בעלות≠cost, bare נתי = a person).
- `קרקע` alone is not land — only land-rights phrases count. `ציוד/אספקה` = supply only, not equipment nouns. `נגה` IS an outside party (user decision 2026-09-26).
- `FROZEN` (הוקפא / בהקפאה / לבטל את הפרויקט / בחינת נחיצות) is a high flag; a cancelled outage ("בוטל עקב") is not. A target cell starting `חושמל` = `ENERGIZED`, not undated. `חסם לטיפול <who>` in לטיפול is a flag (סמנכ"ל = high); the risk-column history never prints that prefix.
- **Stage vs. report** (`stage_contradiction`): a milestone the latest report says happened beyond the file's stage is a WARNING only (≥2 stages = medium, 1 = info). Never change a stage from free text (user decision 2026-09-26). "ע\"י היזם" is not ours; a future phrase (צפי, טרם…) cancels a match.

## Access — `insight_access.py` is the only place that decides
- `scope_for` reads the role (admin / `users.sector` / confirmed `manager_aliases`); `view_for` is pure; templates only draw `view`. Admin: all. `pm_dept`: all projects + league. Sector manager: projects whose current stage is in the sector + that sector's row. PM: own projects + named league. Leading indicators, risk topics, update waves, data health and lists are admin-only.
- Links are set only by an admin at `/dashboard/projects/insights/access` — **never auto-link a name**.
- No role (not admin, no sector, no confirmed name) → 403 with `insight_access.NO_ACCESS` (the one string); `app/main.py` `_analysis_refusal_as_page` draws it as a Hebrew page for a browser, JSON for scripts. The analysis buttons (חסמים · עיכובים · דפוסים · התקשרויות) sit at the TOP of the dashboard's projects view (`.zv-analysis`) and of `/dashboard/projects` (`.pj-analysis`, insights pages only when `insights_allowed`) — tested (user, 2026-11-17).
- View-as: `?as_user=` / `?as_sector=` / `?as_manager=` (`preview_scope`) — admin only, never carries admin power; ignored for non-admins (tested).
- Drill: every number, label, stage and manager cell links to `?drill=<kind>&dv=<value>&dm=<metric>` (`&ds=<stage>`). `drill_for` only filters what `view` allows — a drill never widens access; each drill's row count equals its number (tested).
- AI levels: `ai_kinds_for` only (level 3 = whoever may see the project page). Delays/blockers: `sees_project`; blockers also `owns_blocker` (sector → the תכנון/ביצוע/פיקוח/מחלק word in the owner, via `OWNER_WORD`; sector assignment is admin-set, not a name match) — `sees_blocker` = either.

## Pages
- Insights `GET /dashboard/projects/insights`; raw JSON `/dashboard/projects/patterns` admin-only.
- Project page `GET /dashboard/projects/p/<identifier>`: visible only if in `view`. Every risk line is a rule (no LLM). Charts from `project_chart` (pure), drawn by `_project_charts.js`: delay in months on ONE axis (never dates and months together); risks as topic × week matrix. Palette dark slots #3987e5 / #d95926 / #199e70 on #0f1826. Every chart has a table twin.
- Delays `GET /dashboard/projects/delays`: `forecast_reliability.analyse(frames, allowed, with_projects=True)`; every number computed only on projects the viewer may see (`visible_ids` via `sees_project`; admin and pm_dept = all). `compute` (the log) never returns project rows; names (`by_manager`) never reach the log. Colour scale once, in `heat()`. Scale order travels as lists in `order` — Jinja `tojson` sorts keys.
- Blockers `GET /dashboard/projects/blockers`: see below.
- All three pages: DOM via `createElement`/`textContent` only, never `innerHTML` (tested); every group number equals the row count behind it (tested); `prefers-reduced-motion` = no motion, instant cuts, no autoplay. Motion is decoration — data must be correct without it.

## Forecast reliability — `forecast_reliability.py`
- Built ON the engine (`canonical_snaps`, `MOVE_DAYS`, `MONTH_DAYS`, `stage_sectors`) — no constant re-spelled (tested). A move is between two consecutive dated reports and belongs to the stage of the first; finished projects don't count; sectors aren't summed.
- All figures are a **lower bound** (vs. last forecast, not actual energization). `slip_months_per_year` = net slip months per project-year; `realism_factor` only when that rate is under 12. Numbers only → logged as `FORECAST_RELIABILITY {json}` after each upload.

## Blockers — `blockers.py`
- A blocker = a run of weekly reports where "פירוט סיכונים וחסמים עיקריים" is filled (`episodes`); closes at the first empty cell or when the project ends/leaves the file. Present in the first report we hold → `lower_bound`, shown "לפחות".
- Owner = לטיפול without the "חסם לטיפול" prefix — **a role, not a person**; display names via `OWNER_NAMES`, order via `HIERARCHY` / `by_hierarchy`. `?owner=` narrows to one owner.
- Priority is a rule, not the model: `insight_triage` level → escalation to סמנכ"ל → target slip since the blocker opened → age.
- AI: `recommend` — one call, on request only, on the first `AI_BLOCKERS`; passes `insight_ai.validate`; stored in `pattern_ai_analyses` as `blockers:<scope>:<owner hash>`; PM names never sent.
- Page is a scene "film" over one canvas where each blocker is a particle. Canvas and DOM must share one coordinate system (`rectOf` subtracts the canvas offset; fixed `body`, `touch-action: none` — iPad Safari bug); the frame loop is wrapped in try/catch; the "who holds" scene is dropped with ≤1 owner. Scene details: history doc.

## AI analysis — `insight_triage.py` + `insight_ai.py`
- 3 zoom levels (division / sector / project) × 4 action levels (🔴 VP · 🟠 division head this week · 🟡 sector/PM follow-up · 🟢 FYI). **The level is a rule, never the model** — thresholds are constants in `insight_triage.py` (user-approved 2026-09-28).
- The model writes prose only. `validate` runs on every answer: number not in context → `[?]`, quote not found verbatim → removed, foreign project id → removed.
- PM names go out as `מנה״פ #n`, restored only on display (`restore_names`). `name_projects` (display only) writes "short name (WBE-087)" via `SHORT_NAME_CHARS`; stored answers and `projects` lists stay ids.
- Table `pattern_ai_analyses` (kind, as_of) self-creates. Levels 1–2 are generated after a live sync (not history replay) and on page entry when missing; `claim_autostart` limits to once per 30 min per report date. usage: `pattern_analysis`.

## Dates — `il_format.py` (user instruction 2026-10-07)
- The engine stores ISO; only what a person reads is converted: `il_date`, `il_dates` (ISO inside text), `il_datetime` (naive UTC → Israel). Registered on `routers/projects.py`'s `templates.env`; in tests `il_format.template_env()`.
- **No global finalize:** drill links (`dv=`) and chart data (`tojson`) stay ISO — a test checks both.
- "נכון לדוח" = the report's date (file name, else the last `פירוט שבועי` column), not the upload day; show the last master-file load time beside it.
