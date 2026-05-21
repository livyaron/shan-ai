# Projects Menu Design Spec

## Overview

A Telegram inline projects browser for Shan-AI, mirroring the decisions menu pattern. Users can browse, filter, and drill into project cards from within Telegram.

---

## Trigger

- **Command:** `/projects`
- **Keyword:** any message containing "פרוייקטים"
- **Button:** "📁 פרוייקטים" added to the main menu keyboard (alongside existing "📋 ההחלטות שלי")

---

## Main Menu

Sent in response to `/projects` or keyword trigger.

**Text:**
```
‏📁 פרוייקטים
סה"כ: {N} פרוייקטים פעילים

בחר תצוגה מהירה:
```

Total count = all projects with `active=True` (or equivalent non-terminated stage).

**Inline keyboard (2×3 grid):**

| Button | Callback | Filter logic |
|--------|----------|--------------|
| 🔴 באיחור | `pm:late:0` | `estimated_finish_date < today` |
| 📌 לטיפול | `pm:handle:0` | `to_handle IS NOT NULL AND to_handle != ''` |
| 📅 הרבעון | `pm:quarter:0` | `estimated_finish_date` in current quarter |
| 📋 הכל | `pm:all:0` | no filter |
| 🏗️ בביצוע | `pm:active:0` | `stage IN [עבודה אזרחית, הרכבה חשמלית, הרכבה חשמלית ובדיקות, בדיקות]` |
| 🔍 סינון | `pm_cf:open` | opens custom filter panel |

---

## Results List

Triggered by any `pm:{shortcut}:{page}` callback.

**Text format:**
```
‏{emoji} פרוייקטים {label} ({total})
מציג {start}–{end} מתוך {total}
──────────────────
📁 #{id} · {name[:35]}…  |  {stage} · {MM/YY}
...
──────────────────
```

- Each project row = one InlineKeyboardButton → `pm:detail:{id}:{origin_shortcut}:{origin_page}`
- Date shown = `estimated_finish_date` in `MM/YY` format; red indicator in card only (list row is plain text)
- Name truncated to 35 chars with `…`
- 10 results per page

**Navigation buttons:**
- `◀ הקודם` → `pm:{shortcut}:{page-1}` (disabled/missing on page 0)
- `1/N` — plain label button (no action)
- `הבא ▶` → `pm:{shortcut}:{page+1}` (disabled/missing on last page)
- `🔙 תפריט` → `pm:menu`

---

## Project Card (Detail View)

Triggered by `pm:d:{id}:{origin}:{origin_page}`.

**Text format:**
```
‏📁 פרוייקט #{id}
{full_project_name}
──────────────────
🆔 מזהה: {project_code}
🏷️ סוג: {type}
🏗️ שלב: {stage}
🧑‍💼 מנה"פ: {manager}
📅 תאריך חישמול: {estimated_finish_date} [🔴 באיחור] (if overdue)
📅 תאריך ת"פ: {dev_plan_date}
──────────────────
📌 לטיפול:
{to_handle or "—"}
──────────────────
📋 סיכום שבועי:
{weekly_summary or "אין"}
──────────────────
```

**Navigation buttons:**
- `🔙 חזרה לרשימה` → reconstructs origin results page using stored origin context
- `🏠 תפריט` → `pm:menu`

---

## Custom Filter Panel

Triggered by `pm_cf:open`. State stored in `_projects_menu_state[telegram_id]` dict.

**State schema:**
```python
{
    "stage": None,   # str or None (None = all)
    "type": None,    # str or None
    "mgr": None,     # str or None
    "th": None,      # str or None (to_handle, displayed truncated)
    "date": None,    # str: "late"|"q_current"|"q_next"|"2026"|"2027"|None
}
```

**Filter rows and options:**

| Row | Callback prefix | Options |
|-----|----------------|---------|
| 🏗️ שלב | `pm_cf:stage:{val}` | Dynamic from `DISTINCT stage` in DB |
| 🏷️ סוג | `pm_cf:type:{val}` | Dynamic from `DISTINCT type` in DB |
| 🧑‍💼 מנהל | `pm_cf:mgr:{val}` | Dynamic from `DISTINCT manager` in DB, 2-col grid |
| 📌 לטיפול | `pm_cf:th:{val}` | Dynamic from `DISTINCT to_handle` in DB, labels strip "חסם לטיפול " prefix |
| 📅 תאריך | `pm_cf:date:{val}` | Fixed: late / q_current / q_next / 2026 / 2027 |

Active selection shown with `✓` suffix and highlighted (blue) button.

**Footer buttons:**
- `🔍 הצג תוצאות` → `pm_cf:show` — applies all active filters, returns results list
- `🔙 תפריט` → `pm_cf:back` — clears state, returns to main menu

**Dynamic loading:** `get_filter_options(session)` queries `DISTINCT` on `manager`, `to_handle`, `stage`, `type` at render time. Adding/removing a project manager auto-updates the filter panel.

---

## Callback Data Format

All callbacks ≤ 64 bytes (Telegram limit).

| Pattern | Meaning |
|---------|---------|
| `pm:menu` | Open main menu |
| `pm:{shortcut}:{page}` | Results list — shortcut: `late/handle/quarter/all/active`, page: int |
| `pm:d:{id}:{shortcut}:{page}` | Project card — `d` prefix keeps total ≤ 64 bytes |
| `pm_cf:open` | Open custom filter panel |
| `pm_cf:stage:{val}` | Toggle stage filter |
| `pm_cf:type:{val}` | Toggle type filter |
| `pm_cf:mgr:{val}` | Toggle manager filter |
| `pm_cf:th:{val}` | Toggle to_handle filter |
| `pm_cf:date:{val}` | Toggle date filter |
| `pm_cf:show` | Apply custom filters → results list |
| `pm_cf:back` | Clear state → main menu |

**Stage values that may exceed 64 bytes when combined:** callback values are shortened via index if needed (e.g., `pm_cf:stage:6` maps to index in `get_filter_options()` result). Implementation decision: use full Hebrew value only if total callback ≤ 64 bytes; otherwise use positional index.

---

## Navigation State

Two module-level dicts in `telegram_state.py`:

```python
_projects_menu_state: dict[int, dict]  # telegram_id → filter state
_projects_detail_origin: dict[int, tuple[str, int]]  # telegram_id → (shortcut_or_"cf", page)
```

State cleared on `pm_cf:back` or `pm:menu`. Custom-filter results use `"cf"` as origin key — back-nav re-opens filter panel with existing state.

---

## File Structure

| File | Change |
|------|--------|
| `app/services/projects_menu_service.py` | New — all menu text builders, filter option queries, project queries |
| `app/services/telegram_state.py` | Add `_projects_menu_state` + `_projects_detail_origin` dicts + accessors |
| `app/services/telegram_polling.py` | Add `/projects` command handler, "פרוייקטים" keyword, `pm:*`/`pm_cf:*` callback routing |
| `app/models.py` | Read-only — `Project` model already exists |
| `tests/test_projects_menu_service.py` | New — unit tests for query builders and text formatters |

---

## Data Model Assumptions

Uses existing `Project` model with fields:
- `id`, `name`, `project_code` (= מזהה)
- `type` (= סוג)
- `stage` (= שלב)
- `manager` (= מנה"פ)
- `estimated_finish_date` (= תאריך חישמול)
- `dev_plan_date` (= תאריך ת"פ)
- `to_handle` (= לטיפול)
- `weekly_summary` (= סיכום שבועי)

"Active" projects: all stages except `הסתיים` for display purposes (all stages filterable).

---

## Out of Scope

- No "שלי" filter (projects have no user ownership)
- No risk filter
- No create/edit project from Telegram
- No multi-select filters (one value per filter dimension at a time)
