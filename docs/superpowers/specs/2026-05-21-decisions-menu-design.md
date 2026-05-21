# Decisions Menu — Design Spec
**Date:** 2026-05-21  
**Feature:** Telegram inline decisions browser with filters and pagination

---

## Overview

A menu-driven interface in Telegram for browsing decisions — both submitted and received — without requiring free-text input. Replaces the need to ask open-ended questions to find past decisions.

---

## Entry Points (3)

1. `/decisions` slash command
2. User types the keyword **"החלטות"** — detected early in `handle_message` before LLM routing
3. After a decision is submitted — confirmation message includes an inline button **"📋 ההחלטות שלי"** pre-filtered to `owner=my`

All three display the same main menu message.

---

## Main Menu

A single Telegram message with 6 inline buttons (2 rows of 3):

```
[🕐 אחרונות]  [🚨 קריטיות]  [⏳ ממתינות]
[📥 שקיבלתי] [📤 שהגשתי]   [🔍 סינון]
```

Shortcuts are **stateless** — tapping fires a callback with the preset filter encoded in `callback_data`. No session state created.

---

## Shortcut Presets

| Button | owner | type | status | date_days |
|--------|-------|------|--------|-----------|
| 🕐 אחרונות | all | None | None | 30 |
| 🚨 קריטיות | all | critical | None | 0 |
| ⏳ ממתינות | all | None | pending | 0 |
| 📥 שקיבלתי | recv | None | None | 0 |
| 📤 שהגשתי | my | None | None | 0 |

---

## Results List

- **10 decisions per page**, ordered `created_at DESC`
- Each line: `{type_emoji} #{id} — {summary[:40]}…  {status_emoji} {status_label} · {DD/MM}`
- Type emojis: `🚨 critical · ✅ normal · ℹ️ info · ❓ uncertain`
- Status emojis: `⏳ pending · ✔️ approved · ❌ rejected · ⚙️ executed`
- Header: `{title} ({total_count})\nמציג {from}–{to} מתוך {total}`
- Pagination row: `[◀ הקודם] [עמוד N/M] [הבא ▶] [🔙 תפריט]`
- If ≤10 results: no pagination row, just `[🔙 תפריט]`

---

## Custom Filter (stateful session)

Triggered by **"🔍 סינון"** button. Creates a session in `_decisions_menu_state`.

### State shape

```python
_decisions_menu_state: dict[int, dict] = {}

# value per telegram_id:
{
    "owner": "all" | "my" | "recv",
    "type":  None | "critical" | "normal" | "info" | "uncertain",
    "status": None | "pending" | "approved" | "rejected" | "executed",
    "date_days": 0 | 7 | 30,   # 0 = all time
    "page": 0
}
```

Default state: `owner=all, type=None, status=None, date_days=30, page=0`

### Filter panel message

One message, edited in-place as user toggles. Selected option shows `✓` and blue highlight.

```
🔍 סינון מותאם אישית

👤 מקור:   [הכל ✓]  [שלי]  [שקיבלתי]
🏷️ סוג:   [הכל ✓]  [🚨 קריטי]  [✅ רגיל]  [ℹ️ מידע]
📌 סטטוס: [הכל ✓]  [⏳ ממתין]  [✔️ אושר]  [❌ נדחה]
📅 תקופה: [7 ימים]  [30 יום ✓]  [הכל]

[🔍 הצג תוצאות]  [🔙 תפריט]
```

### Session lifecycle

- Created: on `dm:custom` callback
- Updated: on each `dm_cf:*` toggle callback (edits message in-place)
- Deleted: when user taps "🔍 הצג תוצאות" or "🔙 תפריט"
- No TTL — abandoned sessions are garbage-collected on next menu open by same user

---

## Callback Data Format

All values stay well under Telegram's 64-byte `callback_data` limit.

| Action | Format | Example |
|--------|--------|---------|
| Shortcut result | `dm:{shortcut}:{page}` | `dm:critical:0` |
| Shortcut paginate | `dm:{shortcut}:{page}` | `dm:critical:2` |
| Open custom filter | `dm:custom` | `dm:custom` |
| Custom toggle owner | `dm_cf:o:{val}` | `dm_cf:o:my` |
| Custom toggle type | `dm_cf:t:{val}` | `dm_cf:t:C` |
| Custom toggle status | `dm_cf:s:{val}` | `dm_cf:s:P` |
| Custom toggle date | `dm_cf:d:{days}` | `dm_cf:d:7` |
| Custom show results | `dm_cf:show` | `dm_cf:show` |
| Custom paginate | `dm_cf:pg:{page}` | `dm_cf:pg:1` |
| Back to menu | `dm:menu` | `dm:menu` |

---

## DB Query Logic

Implemented in `decisions_menu_service.py`:

```python
async def query_decisions(
    session, user_id, owner, type_, status, date_days, page
) -> tuple[list[Decision], int]:
    ...
```

- `owner="my"` → `Decision.submitter_id == user_id`
- `owner="recv"` → `Decision.id IN (SELECT decision_id FROM decision_distributions WHERE user_id = user_id)`
- `owner="all"` → `Decision.submitter_id == user_id OR Decision.id IN (subquery above)` — single query, no duplicates
- Type filter → `Decision.type == DecisionTypeEnum(type_)` (skipped if `None`)
- Status filter → `Decision.status == DecisionStatusEnum(status)` (skipped if `None`)
- Date filter → `Decision.created_at >= utcnow() - timedelta(days=date_days)` (skipped if `0`)
- Order → `Decision.created_at.desc()`
- Pagination → `.offset(page * 10).limit(10)` + separate scalar `COUNT(*)`

---

## Files Changed

| File | Change |
|------|--------|
| `app/services/decisions_menu_service.py` | **New** — query logic, message formatting, keyboard builders |
| `app/services/telegram_state.py` | Add `_decisions_menu_state: dict[int, dict] = {}` |
| `app/services/telegram_polling.py` | Add `/decisions` handler, "החלטות" keyword detection, `dm:*` / `dm_cf:*` callback branches |
| `app/services/decision_service.py` | Append "📋 ההחלטות שלי" button to `process()` reply |

No schema changes. No new DB models. No new routes.

---

## Out of Scope

- Drill-down to full decision detail from the list (future)
- Project-based filter (future)
- Export / share decision list (future)
