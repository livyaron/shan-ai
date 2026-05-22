# Shan-AI Improvements Design
**Date:** 2026-05-22  
**Status:** Approved  
**Scope:** 4 features across AI intelligence, UX, and proactive engagement

---

## Overview

Four targeted improvements to Shan-AI, built sequentially in order of complexity:

| # | Feature | Track | Complexity |
|---|---------|-------|------------|
| 1 | B3 — Typing Indicator + Progress Status | UX | ~2h |
| 2 | A4 — Smart Project Disambiguation | AI | ~4h |
| 3 | A2 — Multi-turn Conversation Context | AI | ~6h |
| 4 | C4 — Weekly Intelligence Report | Proactive | ~12h |

---

## Feature 1 — B3: Typing Indicator + Progress Status

### What it does
- Shows a typing indicator while the bot is processing any heavy operation (AI calls, DB queries).
- Sends a one-time static status message after a decision is routed to a superior for approval.

### Implementation

**File:** `app/services/telegram_polling.py`

1. Add `await context.bot.send_chat_action(chat_id, ChatAction.TYPING)` at the top of `handle_message`, before any AI or DB call. Telegram auto-cancels it when a reply is sent.
2. Also add before project queries (`_is_project_query` branch) and `/ask` answers.
3. After routing a CRITICAL/UNCERTAIN decision to a superior, append: `"⏳ ממתין לאישור מנהל מחלקה — תקבל הודעה כשיאושר."` to the confirmation reply.

### Constraints
- No new files. No new state. ~10 lines of changes.
- `ChatAction.TYPING` is imported from `telegram` (already a dependency).
- Status message is static — no auto-edit on approval/rejection (by design).

---

## Feature 2 — A4: Smart Project Disambiguation

### What it does
When a project identifier query matches 2+ projects within 15% fuzzy similarity of each other, the bot pauses and asks the user to confirm which project they meant via inline keyboard buttons, instead of guessing.

### Flow
1. `ask_router.py` routes to `project_tools` with `intent="by_identifier"`.
2. `project_tools.answer_project_query` performs fuzzy name lookup.
3. If 2+ candidates exist where top scores are within 15% of each other → return `AnswerResult(path="disambiguation", answer="", ...)` with candidate project names in `sources_used`.
4. `telegram_polling.py` detects `path="disambiguation"` → sends inline keyboard: one button per candidate (max 4) + `[❌ ביטול]`.
5. Callback data format: `"disambig:<project_identifier>"`.
6. `handle_callback` resolves the selection → re-runs the original query with the exact identifier.

### State
Add to `app/services/telegram_state.py`:
```python
# { telegram_id: original_question (str) }
_awaiting_disambiguation: dict[int, str] = {}
```

### Files changed
- `app/services/ask_router.py` — detect disambiguation result, pass through
- `app/services/project_tools.py` — return disambiguation signal when 2+ candidates within threshold
- `app/services/telegram_polling.py` — handle `path="disambiguation"`, send keyboard, handle `disambig:` callback
- `app/services/telegram_state.py` — add `_awaiting_disambiguation`

---

## Feature 3 — A2: Multi-turn Conversation Context

### What it does
Bot retains the last 5 message exchanges per user in memory. Follow-up messages like "המשך", "שנה ל-CRITICAL", or "תן לי עוד פרטים" resolve correctly without the user re-explaining the topic.

### State
Add to `app/services/telegram_state.py`:
```python
from collections import deque
# { telegram_id: deque of {"role": "user"|"assistant", "content": str, "ts": float} }
_conversation_context: dict[int, deque] = {}
```
`deque` initialized with `maxlen=5`.

### Lifecycle
- **Append user message** at the start of `handle_message`, after auth checks pass.
- **Append assistant response** after the bot reply is sent.
- **Clear context** on: `/start`, `/menu`, `/clear` commands, or if gap between `ts` values exceeds 30 minutes.

### Injection points (3 places)

5 exchanges are stored in memory; only the last 3 are injected per call to limit token usage. The full 5 are available but not passed to the routing or RAG prompts.

**1. `telegram_routing._ai_route_message`** — prepend last 3 exchanges to the routing prompt:
```
Conversation history:
User: <prev msg 1>
Assistant: <prev reply 1>
User: <prev msg 2>
---
Current message: <current>
```

**2. `app/services/claude_service.ClaudeService.analyze_only`** — prepend last 3 exchanges to the Groq system prompt so decision analysis has prior context (e.g., user confirmed "yes this is a decision" in previous message).

**3. `app/services/knowledge_service`** — prepend context to the question passed to the RAG pipeline so "תן לי עוד פרטים" resolves to the previous topic rather than treating it as a new query.

### Files changed
- `app/services/telegram_state.py` — add `_conversation_context`
- `app/services/telegram_polling.py` — append/clear context, pass to routing
- `app/services/telegram_routing.py` — inject context into routing prompt
- `app/services/claude_service.py` — inject context into decision analysis prompt
- `app/services/knowledge_service.py` — prepend context to RAG question

---

## Feature 4 — C4: Weekly Intelligence Report

### What it does
Every Thursday at 17:00 Israel time, each active user receives a personalized Hebrew summary of the week: decisions by type, approval rate, notable risks, and any anomalies. Division managers and department managers receive team-scoped summaries; project managers receive their own activity. Manual trigger available from Telegram and web dashboard.

### New file: `app/services/weekly_report_service.py`

**`generate_report_for_user(user, session) -> str`**
- Queries `decisions` table: past 7 days, filtered by role scope (see below).
- Queries `projects` table: projects with risks or `to_handle` items, scoped by manager name where applicable.
- Sends structured data + report prompt to Groq (llama-3.3-70b-versatile).
- Returns Hebrew report text prefixed with `‏`.

**`send_weekly_reports(bot, session) -> None`**
- Loads all active users where `telegram_id IS NOT NULL` and `role IS NOT NULL`.
- Skips `VIEWER` role.
- Calls `generate_report_for_user` per user, sends via `bot.send_message`.
- Logs failures per user without aborting the batch.

### Role scoping

| Role | Decisions scope | Projects scope |
|------|----------------|----------------|
| PROJECT_MANAGER | Only `submitter_id = user.id` | Only `manager = user.username` |
| DEPARTMENT_MANAGER | All PMs managed by this user (via `manager_id`) | All projects managed by those PMs |
| DEPUTY_DIVISION_MANAGER | All decisions in past 7 days | All active projects |
| DIVISION_MANAGER | All decisions in past 7 days | All active projects |

### Report prompt structure
```
אתה עוזר BI לצוות תשתיות חשמל. צור סיכום שבועי קצר בעברית (עד 300 מילה).

נתונים:
- החלטות השבוע: {json_decisions}
- פרויקטים עם סיכונים: {json_risks}
- פרויקטים לטיפול: {json_to_handle}

כלול: ספירה לפי סוג, אחוז אישורים, 2-3 ממצאים בולטים, אנומליות אם קיימות.
סיים עם משפט עידוד קצר.
```

### Scheduler
Add job to existing `app/services/eval_cron.py`:
```python
sch.add_job(
    _weekly_report_run,
    CronTrigger(day_of_week="thu", hour=17, minute=0, timezone="Asia/Jerusalem"),
    id="weekly_report",
    replace_existing=True,
)
```

### Manual triggers

**Telegram `/report` command:**
- Register `CommandHandler("report", handle_report_command)` in `telegram_polling.py`.
- Handler: generate report for requesting user, send immediately.
- All roles except VIEWER can use it.

**Web dashboard button:**
- New endpoint `POST /dashboard/report/trigger` in `app/routers/dashboard.py` (admin-only, requires JWT auth).
- Calls `send_weekly_reports` for all users.
- New button in `app/templates/dashboard.html`: `"📊 שלח דוח שבועי עכשיו"` — visible to admin users only.

### Files changed / created
- `app/services/weekly_report_service.py` ← new file
- `app/services/eval_cron.py` — add Thursday cron job
- `app/services/telegram_polling.py` — register `/report` command handler
- `app/routers/dashboard.py` — add `/dashboard/report/trigger` endpoint
- `app/templates/dashboard.html` — add trigger button

---

## Build Order

1. **B3** — `telegram_polling.py` only. Ship immediately after implementation.
2. **A4** — `project_tools.py` + `ask_router.py` + `telegram_polling.py` + `telegram_state.py`. Test with ambiguous project names.
3. **A2** — `telegram_state.py` + 4 service files. Test with follow-up message sequences.
4. **C4** — New service file + 4 existing files. Test manual `/report` before enabling cron.

## Non-goals (explicitly out of scope)
- Persisting conversation context to DB (in-memory only by design)
- Auto-editing pending approval messages (static by design)
- Notifications for VIEWER role users
- Disambiguation for decision text (only project name lookup)
