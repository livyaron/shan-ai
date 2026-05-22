# Viewer Role Design Spec

## Overview

Add a `VIEWER` role to Shan-AI — a read-only Telegram user who can browse projects and query the AI but cannot submit decisions or access the decisions workflow.

---

## Role Definition

Add `VIEWER = "viewer"` to `UserRoleEnum` in `app/models.py`. No new DB columns required. Postgres enum type requires an `ALTER TYPE` migration to add the new value.

---

## Assignment

Admin assigns the role via the web dashboard (`/dashboard/users`), the same way all other roles are assigned. Viewer appears as a selectable option in the role dropdown. No separate registration flow.

---

## Permissions Matrix

| Capability | Viewer | Operational roles |
|---|---|---|
| `/projects`, `פרוייקטים`, `פרויקטים` → projects menu | ✅ full access | ✅ |
| Project name free-text search → project card | ✅ | ✅ (via decision engine) |
| `/ask <question>` → RAG answer | ✅ | ✅ |
| Free text → Groq AI analysis (display-only) | ✅ shown inline, not saved | ✅ saved as Decision, routed |
| `/decisions`, `החלטות` → decisions menu | ❌ blocked message | ✅ |
| Decision submission / approval / rejection | ❌ | ✅ |
| RACI assignment | ❌ | ✅ |

---

## Telegram Keyboard

Viewers get a separate persistent `ReplyKeyboardMarkup` with a single button:

```
[ 📁 פרוייקטים ]
```

`_main_reply_keyboard()` in `telegram_polling.py` checks `user.role == RoleEnum.VIEWER` and returns this single-button keyboard instead of the two-button keyboard. The viewer keyboard is re-sent as `reply_markup` on every bot response to keep it visible.

---

## Keyword Triggers

Both spellings trigger the projects menu for viewers (same as all users):
- `פרוייקטים` (double yod)
- `פרויקטים` (single yod)

The `החלטות` keyword returns the blocked message (same as `/decisions`).

---

## Free-text Handling (`handle_message`)

For viewers, the message handler follows this order:

1. **Feedback / rejection-note flow** — unchanged (existing early-return guards handle these by `telegram_id` state; viewer is unlikely to be in these states, but guards remain for safety).
2. **Keyword: `פרוייקטים` / `פרויקטים`** → open projects menu.
3. **Keyword: `החלטות`** → blocked message.
4. **Project name search** — `ILIKE %text%` on `Project.name` where `is_active = True`:
   - 1 match → full project card (same as `build_project_card()`)
   - 2–5 matches → list with inline buttons (`pm:d:{id}:viewer:0` detail callback)
   - 0 matches → fall through to AI pipeline
5. **AI pipeline** — run full Groq analysis on the text (same call as operational users). Display result inline as a formatted message. **Do not create a `Decision` row. Do not route. Do not propose RACI.**

Blocked message text: `‏🔒 גישה לתפריט ההחלטות אינה זמינה למשתמשי צפייה.`

---

## Blocked Message (Decisions)

Shown when viewer calls `/decisions`, types `החלטות`, or taps the החלטות button (if somehow seen). Reply includes the persistent viewer keyboard so it stays visible.

---

## Web Dashboard

- Role dropdown in `/dashboard/users` gains a `צופה` option mapping to `"viewer"`.
- Role label in Hebrew: `צופה`.
- No other dashboard changes — viewer is not a supervisor, has no superior in the hierarchy, is excluded from `SUPERIOR_ROLE` map in `decision_service.py`.

---

## Files Changed

| File | Change |
|---|---|
| `app/models.py` | Add `VIEWER = "viewer"` to `UserRoleEnum` |
| `app/services/telegram_polling.py` | `_main_reply_keyboard()` → viewer single-button keyboard; `handle_message` → viewer branch; `handle_decisions`, `handle_ask`, keyword guards |
| `app/routers/dashboard.py` | Add `צופה` / `viewer` to role label and dropdown |
| `app/templates/users.html` | Add viewer option to role `<select>` |
| `alembic/versions/` (or raw SQL) | `ALTER TYPE userrole ADD VALUE 'viewer'` |

---

## Out of Scope

- Viewer cannot receive decision notifications (not in `DecisionDistribution`)
- Viewer has no superior in the RACI / approval hierarchy
- No viewer-specific dashboard page
- No self-registration as viewer (admin assigns only)
