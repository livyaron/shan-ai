---
paths:
  - "app/services/missions_*.py"
  - "app/services/war_room_*.py"
  - "app/routers/war_room.py"
  - "app/routers/missions_api.py"
  - "app/templates/war_room*.html"
  - "app/templates/_war_room*.html"
  - "tests/test_war_room*.py"
  - "tests/test_missions*.py"
---
# Missions / חדר מבצעים

Condensed 2026-11-16. Full text with the reasoning: `docs/rules-history/missions-war-room.md` — read it before changing a rule's behaviour.

## Schema
- `missions`, `mission_updates`, `mission_due_changes`, `mission_report_cache` auto-create via `create_all`; new columns on an existing table → `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `app/main.py` startup (as for `mission_updates.kind`, `missions.parent_id`, `users.war_room_style`).
- `missions.status` stays VARCHAR — never a PG enum. Deleting a user reassigns their missions to the deleting admin. `mission_updates.author_id` is nullable, `author_name` a snapshot.

## Single sources — import, never re-spell (in `missions_menu_service.py` unless noted)
- Status is open/closed only (`in_progress` removed from `MissionStatusEnum`; legacy rows → `open` at startup). `ACTIVE_STATUSES` = "is it live?" (templates get `active_statuses`).
- `NEW_MISSION_HOURS` + `is_new()` = "just opened" for every layout, the wall, Telegram and the XLSX. A new mission is exempt from `is_silent`. On the wall "new" is a badge + ring, never a tone (colour = time-to-target only).
- `created_at` is naive UTC; `format_created_il` renders `DD/MM/YYYY HH:MM` Israel time.
- Styles: `war_room_styles.py` / `STYLES`. NULL = default. Every layout gets the same context and posts to the same endpoints; a new layout = one template + one `STYLES` row. `?style=<key>` overrides for one request; `&tv=1` drops navbar and switcher (wall URL).
- Wall: `war_room_wall.py` owns tones, worst-first order and page size; rotates through every active mission (undated too), reloads only after a full cycle.
- KPI cards: `war_room_kpis.py` owns the cards, their stat and the rows `?kpi=<key>` selects. A KPI owns the status while on (status select disabled). Counters stay board-wide. Only `cyber` and `table` carry KPIs (tested).
- Follow-ups: `missions.parent_id` is **SET NULL, never CASCADE**. `war_room_chain.py` (`load_chains`, cycle-guarded, `MAX_DEPTH`) is the only chain walker. A closed parent is hidden by the status rule alone — add nothing. Children are created through the ordinary `/create` with `parent_id`.
- Postponement: `oms.needs_due_reason(m, new_date)` is the ONE definition (an existing target moved; a first date is not). Web, batch bar and Telegram ask it; `oms.update_mission` asks again before writing history. `requested_by` is a snapshot string, `requested_by_id` only for a picked user. Counter = `len(rows)` via `oms.postpone_label`; re-saving the same date never writes a row.

## Closing
- `set_status` stamps `completed_at` for both `done` and `cancelled`; anything counting "done" filters `status == DONE` first.
- `missions_report_service._closed_at()` falls back to the close note, then `updated_at` — the closed sheet's `נסגרה בתאריך` and its sort read that, never `completed_at` raw (which keeps its own `הושלמה בתאריך` column).
- `_close_update()` writes the close note (`kind == 'close'`); `_update_text` marks it 🔒.
- Changing `OPEN_HEADERS` / `CLOSED_HEADERS` → bump `KIND_REPORT` (`report_v4`), or the day-cache serves the old layout.

## Board UX
- Every board write reloads through `wrReload(id)` in `_war_room_actions.html`, never bare `location.reload()` (tested): restores window + inner scroll boxes (`.wr-quad-body` / `data-wr-scroll`) and flashes the touched card (`data-mission="<id>"`).
- Owner field: `_war_room_owner_field.html` (all layouts) — search narrows the `<select>`; the select is what posts.

## Wall quotes — `war_room_quotes.py` is the ONLY place quotes live
- **Never let a model generate a quote**; the LLM writes only the connecting sentence. Author-only attribution, Hebrew sources quoted not translated, disputed attributions excluded, no straight `"` — tests enforce all four.
- `pick_quote` shows every quote once per shuffled cycle. Add quotes by appending to `QUOTES`.
- The quote holds for the hour; the sentence under it alternates every `SWAP_MINUTES` (5) between the AI line and the computed one, opening on the computed one. Every `_FALLBACKS` shelf holds an ODD number of lines (tested).

## Report cache — `mission_report_cache`
- Day-cache for the XLSX + 🧠 AI summary so it survives a redeploy: warmed 04:10, restored at startup, hourly watchdog repairs a missing summary. Never a source of truth; every read/write is best-effort and swallows its errors.
