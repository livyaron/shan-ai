---
paths:
  - "app/services/project_sync.py"
  - "app/services/exec_summary.py"
  - "app/services/contracts_sheet.py"
  - "app/services/target_reading.py"
  - "app/services/project_kpis.py"
  - "app/services/project_learning_service.py"
  - "app/routers/projects.py"
  - "app/templates/projects.html"
  - "app/templates/project_contracts.html"
  - "app/templates/project_kpi_list.html"
  - "tests/test_project_sync*.py"
  - "tests/test_exec_summary.py"
  - "tests/test_contracts_sheet.py"
  - "tests/test_target_reading.py"
---
# Weekly master file → projects (sync, exec summary, contracts, targets)

Condensed 2026-11-16. Full text with history and examples: `docs/rules-history/projects-master-file.md` — read it before changing a rule's behaviour.

## Sync — `project_sync.py`
- The upload syncs ONLY the record sheet: `pick_master_sheet` → the `…עדכני` sheet (never "first sheet" — newer workbooks open with a macro sheet). Other tabs once became ~550 phantom projects. `דוח שבועי טיוטה` sheets are skipped.
- `deactivate_missing` (guard `MIN_INVENTORY`) is the only deactivation; both upload paths call it.
- Snapshots carry the **report date** (`resolve_report_date`: filename → newest weekly column → today), never the upload day.
- A file older than `_report_horizon` is **replayed as history**: adds snapshots + weekly entries, never touches live `projects`, returns no `identifiers` (else it would deactivate newer projects), fires no briefs/reports. Backfill: admin-only `POST /dashboard/projects/backfill`, `force_history=True`, oldest first.
- `project_weekly_entries` (one row per project per `פירוט שבועי <date>` column) keeps the newest `source_report_date` — an older file never overwrites newer text. New columns → `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `app/main.py` startup (as for `finish_date_text`, `controller`, `short_supervisors`, `short_testers`, `critical_tier`, `approved_date`/`approved_text`).

## KPI cards — `project_kpis.py` is the ONLY definition (user, 2026-10-16)
- Dashboard tile + view, patterns page and every card's list count the SAME rows: the engine's live projects (`pattern_service.compute_cached`), filtered by `insight_access.view_for` for "mine". `insight_access.summarize` / `_PROJECT_TESTS` read `ENGINE_KPIS` from here.
- Population = ACTIVE projects; finished ones shown apart ("263 בקובץ · 41 הסתיימו").
- A card's number is always `len(rows_for(key))` (tested). Dashboard shows cards only; lists are behind a click: `GET /dashboard/projects/kpi/<key>[?scope=mine]`, summary tables at `GET /dashboard/projects/summary/<i>`.
- Dashboard numbers use `--num` (Heebo), never Karantina (6 and 8 look alike).

## Exec summary — `exec_summary.py`
- Parses the `תקציר הנהלה <date>` sheet generically; stored in `project_exec_summaries` after a LIVE sync only.
- `resolve` re-runs each card's Excel formula (COUNTA/COUNTIF/COUNTIFS) over the record sheet into identifiers. Structured refs (`טבלה13[[col ]]`) resolve via `_table_column`: header row = the row with "זיהוי", trimmed match, name must occur exactly once; `[#All]` / `[@…]` aren't re-counted.
- A card that is a typed number (no formula) → `rule_ids(label, value, record_rows, ident_col)` (only when `resolve` returns None). Definitions come from the file's own past formulas, rewritten by **column name** (letters shift). "דחייה מול תכנית פיתוח / יעד מאושר" were never formulas: pick the candidate definition whose count equals the file's number, else the first, and show "בקובץ X". Definition stored in `k["rule"]`, shown above the list. **No definition → no list; add one, never guess** (user: always build the list in code).
- A formula that can't be re-run exactly keeps the file's number, marked, with no list. `reread_if_unresolved` re-reads the file after upload and saves only if more cards open. Log line `EXEC_SUMMARY_CARDS` shows formula/definition and count per card.

## Contracts — `contracts_sheet.py` is the only reader
- Sheet `סטטוס התקשרויות קבלנים ותיחורים` → `GET /dashboard/projects/contracts` (any logged-in user). `parse_rows` is pure, maps columns BY HEADER; `analyse` makes every card a count of payload rows. Stored in `project_contract_sheets` after a LIVE sync only. No model reads this sheet.
- Stage = furthest step the diary says already HAPPENED (`LADDER`); a plan ("צפי ל…", "נקבע ל…") is not a step; a postponed step counts at its last date, a cancelled tour never. Past/plan judged against the sheet's OWN date; ground-entry runway against the REPORT date — show the gap. Only colours the sheet's legend names are read.
- **No money on this page (user, 2026-10-17):** `_clean` replaces every amount with `REDACTED` on read; `latest()` runs `redact_payload` on stored payloads. Never add an amount field, card or column.

## Three targets — `target_reading.py` (pure, rules only; user decision 2026-10-16)
- Prose in a target cell is READ, never shown raw alone: plain date · staged (by its LAST date) · "לא מאושר" (forecast, flagged) · missed ("לא אפשרי" — that date is NEVER the forecast; a later date in the text is) · energized (date it WAS energized, not a forecast) · partly energized · no target (+ reason: נגה / תכנון / היתר / קבלן). Month/year only = last day, `approx`.
- The sync stores the read date in `estimated_finish_date` (text stays in `finish_date_text`); `canonical_snaps` applies the same reading to old snapshots. Approved target (`יעד חשמול מאושר ע"י מנהל האגף`) → `approved_date` / `approved_text`.
- `compare` is the ONE definition: `over_approved` = forecast > approved + `OVER_APPROVED_DAYS` (15); an earlier forecast is not flagged; `approved_lost` = approved target, no forecast. Shown on the dashboard, project page (three side by side) and patterns page (🟠 triage rule).
