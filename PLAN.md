# PLAN — Pattern & Risk Engine (second brain, analyst layer)

**Status:** P0 + P1 + P2 + P3-web implemented (see §3 notes). P1 backfill loaded in production (8/8 files). P3 Telegram digest and P4 not started.
**Goal:** Turn the weekly master file (דוח שבועי לסמנכ"ל) into specialist-grade answers — patterns, leading indicators, bottlenecks — delivered to the division manager, the PM department and the three sector managers.

> Exploratory analysis was run on 8 historical weekly files (Mar–Sep 2026) outside the repo. **No project data, names or findings are committed** — the repo is public and the files carry an inside-information notice. Numbers below are shapes, not data.

---

## 1. Decisions already made (by the user)

| # | Decision |
|---|---|
| D1 | Primary source = the weekly master file. Everything else is secondary. |
| D2 | Audience = division manager + PM department head + all PMs + 3 sector managers. |
| D3 | PMs see a **full named league table** (option ב), with three guardrails (§6). |
| D4 | Org: PMs + תו"ב all sit in מחלקת ניהול פרויקטים. Sectors are תכנון / פיקוח / ביצוע. A sector owns a project by the **stage** it is in (§5). |
| D5 | Claude (MCP) is the division manager's analysis tool only. PMs and sector managers consume through Shan-AI (Telegram + web). |

## 2. What the exploratory run proved the current sync loses

Verified in `app/services/project_sync.py` and `project_learning_service.save_snapshot`:

| # | Gap | Where | Consequence |
|---|---|---|---|
| G1 | `snapshot_date = date.today()` | `save_snapshot` | Snapshot is dated by upload day, not report day. Backfilling old files would stamp them all "today". |
| G2 | Only the **last** `פירוט שבועי` column is kept | `_extract_weekly_report` | ~30 weeks of per-project narrative history in every file are discarded. |
| G3 | Free-text `יעד חשמול מסתמן` → `_parse_date` returns `None` silently | `_parse_date` | A material share of active projects have text instead of a date ("לא אפשרי, יתקבל יעד חדש…") and vanish from every date metric. |
| G4 | `תו"ב`, `חוסר במשגיחים`, `חוסר בבודקים`, `פרויקטים קריטים` not in `KNOWN_COLUMNS` | `KNOWN_COLUMNS` | Staffing-shortage signals and criticality tier are dropped. |
| G5 | `manager` is a raw string, no link to `users` | `Project.manager` | "My projects" / per-PM delivery is impossible. |
| G6 | Year-1 / out-of-range dates exist in the file | parsing | Crashes naive date arithmetic (hit during the exploratory run). |

## 3. Phases

```
P0 capture fix ─► P1 backfill ─► P2 pattern engine ─► P3 delivery (web+Telegram) ─► P4 MCP (manager only)
```

### P0 — Capture everything the file already has (S)
1. `sync_projects_file(file_path, sheet_name, report_date: date | None)` — `report_date` parsed from the filename (`DD.MM.YYYY` / `D.M.YY`), else from the right-most `פירוט שבועי <date>` header, else today. Passed through to `save_snapshot`.
2. New table `project_weekly_entries` (auto-create via `create_all`):
   `id, project_id FK CASCADE, week_date DATE, text TEXT, text_hash CHAR(16), source_report_date DATE, UNIQUE(project_id, week_date)`.
   Every `פירוט שבועי <date>` column → one row per project. Upsert: a later file overwrites the same `week_date` (reports get corrected).
   Header date parsing must handle `14.1`, `04.02.26`, `04/03/2026`, `19/8/2026`, `26/8/26`, `דוח קודם 7.1`; year inferred from `report_date` when missing.
3. `projects` + `project_snapshots`: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` at startup (`app/main.py`, existing pattern):
   `finish_date_text TEXT` (raw cell when not a date), `controller TEXT` (תו"ב), `short_supervisors BOOLEAN`, `short_testers BOOLEAN`, `critical_tier VARCHAR(32)`.
4. `_parse_date`: reject years outside 2000–2045.
5. Add the four headers to `KNOWN_COLUMNS`.

**P0 as built — deviations from the draft above:**
- An automatic "history mode" replaces the per-call backfill flag: a file whose report date is older than `_report_horizon` (newest `source_report_date` in `project_weekly_entries`, else newest legacy snapshot − 14 days) never touches live rows, returns no identifiers, and fires no briefs/reports. Projects first seen in such a file are created `is_active=False`.
- Draft sheets (`טיוטה`) are skipped entirely — they were creating phantom projects and snapshots.
- Duplicate `זיהוי` inside one sheet: first row wins, later ones logged and skipped.
- String dates are parsed day-first (was month-first: "01/07/2026" read as 7 Jan).
- Verified end-to-end on the 8 historical files against a throwaway local Postgres: re-sync is idempotent; replaying 25.03 / 01.07 / 02.09 after 16.09 leaves the live rows byte-identical and the newer weekly text intact.

### P1 — Backfill (S)
One-off admin endpoint / script that ingests historical files in **date order** with their `report_date`. Idempotent (UNIQUE keys). Must not fire the post-sync report/notification hooks (`_trigger_reports_after_sync`) — add a `backfill=True` flag that skips them.

**P1 as built:** admin-only `POST /dashboard/projects/backfill` (many XLSX at once) + `GET …/backfill/status`, button "📚 טעינת היסטוריה" on the projects page. `project_sync.backfill_files` orders files by report date and syncs each with `force_history=True` — live rows untouched, no identifiers, no briefs/reports, in any upload order. `pick_master_sheet` chooses the "…עדכני" sheet (newer files open with a one-cell "מאקרו1" sheet, so "first sheet" synced nothing) — the plain `/upload` route gets it too. Verified locally: 8 files uploaded in shuffled order → 8 report-dated snapshots, 7,092 weekly entries (14.1–16.9), live rows byte-identical.

### P2 — Pattern engine `app/services/pattern_service.py` (M)
Pure SQL/pandas. **No LLM computes a number.** Every metric returns `{value, n, confidence, caveat}`.

| Metric | Definition |
|---|---|
| `slip_vs_plan` | `estimated_finish_date − dev_plan_date` (months), active projects with both dates |
| `forecast_drift` | change in `estimated_finish_date` between two report dates |
| `baseline_moves` | change in `dev_plan_date` between two report dates — **the gaming detector** |
| `update_waves` | per consecutive snapshot pair: # forecast moves, # baseline moves, # both |
| `stuck` | same `stage` across ≥ N weeks |
| `stale_reporting` | identical `text_hash` for ≥ 4 consecutive `week_date`s |
| `undated` | `finish_date_text IS NOT NULL` |
| `past_due` | forecast < report_date and stage ≠ הסתיים |
| `risk_categories` | regex taxonomy (9 categories, Hebrew-prefix tolerant) over `risks`, `to_handle`, weekly entries |
| `leading_indicator` | category mentioned before T → % drifted after T, vs. baseline, with Fisher p and a **multiple-comparison flag** |
| `slip_attribution` | each drift event attributed to the **stage the project was in** when it moved (needed so ביצוע is not blamed for slips born in תכנון) |

Legacy snapshots (pre-P0) are dated by upload day and may sit 1–7 days from a report-dated snapshot of the same report — collapse them to the nearest report date before computing drift, or they read as extra "weeks".

Confidence rules: n < 5 → "מדגם קטן", not ranked; p reported only with the number of hypotheses tested.

**P2 as built:** `app/services/pattern_service.py` (`load_frames` is the only DB access; `compute_patterns` is pure pandas, tested on synthetic frames) + `app/services/stage_sectors.py` (§5 map). Output via admin-only `GET /dashboard/projects/patterns` (JSON) until P3 decides who sees what. Thresholds are named constants at the top of the module: late = forecast > 1 month past plan; a date "moved" only beyond 15 days; stuck = 12+ weeks in the current stage (`lower_bound` when history starts in that stage); stale = identical weekly text 4 weeks running. Report dates: only full-file syncs (≥ 50% of the median of the 5 largest dates — one inflated pre-P0 date must not disqualify the real reports); dates within 3 days of a cluster's FIRST date collapse onto its latest — never chained (an upload day sits 6 days after the previous week's report, and chaining merged the two weeks — caught by a test). Leading indicators: Fisher exact (own implementation, checked against scipy) + Bonferroni; `weak` = significant only before correction. Slip attribution charges each forecast move to the sector(s) owning the stage at the START of the interval.

### P3 — Delivery (M)
- **Web** `/dashboard/patterns` (reuse dashboard auth + `war_room` template conventions):
  - Division manager: everything.
  - PM dept head: named PM table + all projects.
  - Sector manager: projects currently in their stages + `slip_attribution` for their stages.
  - PM: own projects + the full named table (D3).
- **Telegram**: weekly digest after each sync (piggy-backs on the existing post-sync hook, **not** during backfill), per role.
- Role → view uses existing `RoleEnum` + a new `users.sector VARCHAR(16)` (NULL | planning | supervision | execution | pm_dept), ALTERed at startup.

**P3-web as built:** `insight_access.py` (scope + pure `view_for`), `users.sector`, `manager_aliases`, admin page `/dashboard/projects/insights/access` (name → user links with a rapidfuzz *hint* only, sector per user). The same insights page serves every role, filtered; tiles are computed from the viewer's own projects (for the admin they equal the engine metrics — a test enforces it). Users with no role get a 403 with an explanation; the projects-page button shows only to users with a role. Admin "👁 צפה כ" selector: preview by user, by sector, or by PM name from the file (before linking). Telegram digest: not built yet (user chose web first).

### P4 — MCP server, read-only, manager only (M)
Tools return aggregates only: `get_slip_patterns`, `get_update_waves`, `get_stuck_projects`, `get_leading_indicators`, `get_reporting_quality`, `get_project_history(identifier)`. Bearer token per user, admin-only issuance, audit log row per call. Mounted under the FastAPI app. **Blocked on information-security approval** (see §8).

## 4. Name → user linkage (G5)
New table `manager_aliases(alias TEXT UNIQUE, user_id FK SET NULL)`. On sync, unmatched `מנה"פ` strings are queued; admin confirms matches in a small web page (rapidfuzz suggestion, human confirm — a wrong link shows a PM someone else's projects). `טרם הוקצאה` is a reserved alias → "unassigned" bucket.

## 5. Stage → sector map (single source of truth: `app/services/stage_sectors.py`)

| Stage | Sector |
|---|---|
| תכנון, הקפאת תכולה, הקפאת תצורה, קבלת היתר | תכנון |
| עבודה אזרחית, לקראת ביצוע | פיקוח |
| הרכבה חשמלית, הרכבה חשמלית ובדיקות, בדיקות | ביצוע |
| עבודה אזרחית והרכבות | פיקוח **and** ביצוע (counted in both) |
| בחירת קבלן, הסכם- אגירת אנרגיה, טופס 4 | ניהול פרויקטים |
| הסתיים | — (closed) |
| empty / unknown | "ללא סטטוס" bucket, surfaced as a data-quality item |

Tests enforce: every stage value seen in the file maps somewhere; a new unseen stage raises a data-quality alert, not a silent drop. Because עבודה אזרחית והרכבות counts twice, **sector totals must never be summed into a division total** — division totals come from projects directly.

## 6. League-table guardrails (D3)
1. Each row shows n and stage mix.
2. A `baseline_moves` column next to every lateness metric.
3. n < 5 active projects → shown, unranked, marked "מדגם קטן".

## 7. Edge cases to test
- Same `זיהוי` appearing twice in one file (seen) → keep first, log.
- Project disappears from a later file → mark inactive, never delete history.
- Weekly column header without year; header date after `report_date`.
- Two files for the same report date (e.g. xlsx + Google-Sheet copy) → second is a no-op.
- `יעד חשמול מסתמן` text that *contains* a date ("01/07/2026 לא אפשרי…") → store as text, do **not** extract the date (it is explicitly not the target).
- Sync of an older file after a newer one → must not overwrite `projects` current state; only snapshots/weekly entries.

## 8. Open items / risks
1. **Information security:** files are marked as possible inside information. Confirm Railway hosting of this data is approved before P1 backfill; P4 (external MCP) needs explicit approval.
2. The sync runs only when someone uploads the file — the engine is only as fresh as the uploads. Consider a Gmail/Drive watcher later (out of scope).
3. Regex taxonomy is a v1; validate on a hand-labelled sample of ~50 entries before showing category stats to PMs.
4. Leading-indicator stats on ~130 projects are weak; ship them to the division manager only until they replicate on new data.

## 9. Review ask (per CLAUDE.md §3)
"Review @PLAN.md for logical fallacies and edge cases."
