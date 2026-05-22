# Spec: Telegram Feedback Quick-Access Menu

**Date:** 2026-05-22  
**Status:** Approved

---

## Context

Decisions get a 48-hour automated feedback reminder (score + notes) sent to the submitter. But users who are involved in a decision as recipients or RACI members have no Telegram path to rate decisions. This spec adds a `⭐ ממתין למשוב` shortcut to the decisions menu — a list of completed decisions the current user has not yet rated, with an inline flow to add score + optional text.

---

## What We're Building

A new shortcut in the Telegram decisions menu that shows the user all completed decisions (status=EXECUTED or APPROVED) they are involved in (submitted, distributed to, or RACI member) where they have not yet submitted a `DecisionFeedback` row. From the list, the user taps a decision to rate it (1–5 + optional text).

---

## Data Model

No schema changes needed. The existing `DecisionFeedback` table is used for all ratings:
- `decision_id` FK + `user_id` FK + `score` (1–5) + `notes` (optional text)
- One row per user per decision (upsert logic already exists in dashboard)
- "No feedback yet" = absence of a `DecisionFeedback` row for this user

The existing 48h scheduler (`feedback_service.send_feedback_requests`) is **not changed** — it continues writing to `Decision.feedback_score` directly as the submitter's personal post-mortem.

---

## Menu Layout

`get_menu_keyboard(feedback_count: int = 0)` updated to:

```
Row 1: [🕐 אחרונות | 🚨 קריטיות | ⏳ ממתינות]
Row 2: [📥 שקיבלתי | 📤 שהגשתי  | 🔍 סינון  ]
Row 3: [      ⭐ ממתין למשוב (N)              ]
```

- `N` is omitted when count = 0
- `feedback_count` param defaults to 0 so existing call sites need no changes
- Only `dm:menu` handler and `handle_decisions()` pass the live count

`get_menu_counts()` gains a `"feedback"` key:

```python
SELECT COUNT(DISTINCT d.id)
FROM decisions d
LEFT JOIN decision_feedbacks df
    ON df.decision_id = d.id AND df.user_id = :user_id
WHERE d.status IN ('executed', 'approved')
  AND df.id IS NULL
  AND (
    d.submitter_id = :user_id
    OR d.id IN (SELECT decision_id FROM decision_distributions WHERE user_id = :user_id)
    OR d.id IN (SELECT decision_id FROM decision_raci_roles WHERE user_id = :user_id)
  )
```

---

## Interaction Flow

```
1. User taps ⭐ ממתין למשוב (N)
   Callback: dm:feedback:0

2. Bot shows paginated list of decisions (10/page)
   Each decision is a clickable inline button:
     "✅ #42 — החלפת מפסק 33 קו ⚙️ 19/05 [📤]"
   Callback: dm:fbsel:42:0
   Navigation: ◀/▶ pagination + 🔙 תפריט

3. User taps a decision
   Callback: dm:fbsel:42:0
   Bot edits message to show decision card + score keyboard:
     ⭐ משוב — #42 — החלפת מפסק 33 קו
     📋 סיכום: ...
     🎯 פעולה: ...
     📅 בוצע: 19/05
   Keyboard: [1️⃣ כישלון | 2️⃣ לא טוב | 3️⃣ בסדר | 4️⃣ טוב | 5️⃣ מצוין]
             [🔙 חזרה לרשימה]  ← dm:feedback:0

4. User picks score
   Callback: dm:fbsc:4:42:0
   Upserts DecisionFeedback(decision_id=42, user_id=<db_user.id>, score=4)
   Recalculates Decision.feedback_score = round(avg(all DecisionFeedback.score for this decision))
   Bot sends new message: "✅ ציון 4 — טוב נשמר. רוצה להוסיף הערה? שלח טקסט, או /skip לדילוג."
   State: _awaiting_fb_menu_text[telegram_id] = (42, 0)

5. User sends text OR /skip
   Saves DecisionFeedback.notes (or leaves null)
   Clears state
   Bot: "✅ הפידבק שלך נשמר." + re-shows feedback list (dm:feedback:0)
```

---

## Callback Routing

New callbacks handled in `_handle_decisions_menu()`:

| Callback | Action |
|----------|--------|
| `dm:feedback:{page}` | Query pending feedback, show clickable list |
| `dm:fbsel:{decision_id}:{page}` | Show decision card + score buttons |
| `dm:fbsc:{score}:{decision_id}:{page}` | Save score, ask for text |

Handled **before** the generic `dm:{shortcut}:{page}` branch to avoid false matches.

---

## State

New dict in `telegram_state.py`:
```python
_awaiting_fb_menu_text: dict[int, tuple[int, int]] = {}
# telegram_id → (decision_id, back_page)
```

In `handle_message()`, check `_awaiting_fb_menu_text` **before** the existing `_awaiting_feedback_text` check.

---

## Files to Modify

| File | Changes |
|------|---------|
| `app/services/decisions_menu_service.py` | `get_menu_keyboard(feedback_count)`, `get_menu_counts()` + `"feedback"` key, `query_pending_feedback(session, user_id, page)`, `build_feedback_results_keyboard(decisions, page, total)` |
| `app/services/feedback_service.py` | `save_telegram_feedback_score(user_id, decision_id, score)`, `save_telegram_feedback_text(user_id, decision_id, notes)` — both write to `DecisionFeedback` table |
| `app/services/telegram_polling.py` | Handle `dm:feedback:`, `dm:fbsel:`, `dm:fbsc:` in `_handle_decisions_menu()`; check `_awaiting_fb_menu_text` in `handle_message()` |
| `app/services/telegram_state.py` | Add `_awaiting_fb_menu_text: dict[int, tuple[int, int]] = {}` |

No migrations required.

---

## Verification

1. Open decisions menu → `⭐ ממתין למשוב (N)` appears with correct count
2. Tap the button → list of pending-feedback decisions, paginated
3. Tap a decision → detail card + score buttons appear
4. Pick score → confirmation message + text prompt
5. Send text → `DecisionFeedback` row saved, `Decision.feedback_score` recalculated
6. Send `/skip` → `DecisionFeedback.notes` stays null, still saved
7. After rating, decision no longer appears in the pending list
8. Count on menu button updates when returning to menu
9. `/decisions` command from keyboard also shows updated count
10. Existing 48h scheduler still fires unaffected
