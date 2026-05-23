# Weekly Report v2 — Smart Structured Reports Design

**Date:** 2026-05-23
**Status:** Approved

## Goal

Upgrade the weekly intelligence report from a single free-form text to a structured, role-scoped, historically-aware report with:
- 4 sections + optional delta section
- Per-user history stored in DB
- Manager-controlled recipient selection (Telegram + Dashboard)
- Self-report button accessible to every non-VIEWER user

---

## Data Model

### New table: `report_history`

```python
class ReportHistory(Base):
    __tablename__ = "report_history"
    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sections      = Column(JSONB, nullable=False)
    # sections keys: prologue | decisions | projects | summary | delta (null on first report)
    generated_at  = Column(DateTime, default=datetime.utcnow, index=True)
    triggered_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    sent_via      = Column(String(32))  # "telegram" | "dashboard" | "cron"
```

Migration: `CREATE TABLE report_history (...)` added to `app/main.py` startup via `create_all`.

---

## Report Generation

### Data gathered (role-scoped, same as current service)

| Data | PROJECT_MANAGER | DEPT_MANAGER | DEPUTY / DIVISION |
|------|----------------|--------------|-------------------|
| Decisions | own only | own + direct reports | all |
| Projects | where manager=username | all active | all active |
| Pending approvals | where approver=self | where approver=self | all pending |

### Delta data (structured diff, not text comparison)

Fetch the most recent `ReportHistory` row for the user. Compute:
- `decisions_delta`: count change, new by type, newly approved/rejected
- `projects_delta`: stage changes, new risks, resolved to_handle items
- `approvals_delta`: approvals cleared since last report

Pass as structured JSON to LLM — not text. Delta section is `null` if no prior row exists.

### LLM call

Single `llm_chat("weekly_report", ...)` call. Prompt instructs the model to output **valid JSON only**:

```json
{
  "prologue":   "...",
  "decisions":  "...",
  "projects":   "...",
  "summary":    "...",
  "delta":      "..." | null
}
```

### Section content spec

**① Prologue** — situational awareness header
- User's name, role, date range
- Top 1-2 items requiring immediate action (pending approvals, overdue projects)
- Total counts: decisions this week, active projects, open risks

**② Decisions** — decision intelligence
- Counts by type (INFO / NORMAL / CRITICAL / UNCERTAIN), approval rate %
- Explicit list of decisions awaiting *this user's* approval (stuck > 24h) — with IDs
- Anomaly flag if decision volume > 2× weekly average

**③ Projects** — operational status + personal to-do
- Projects behind schedule (finish date < today or < 7 days away)
- Risks with no owner assigned
- Personal action checklist: numbered `to_handle` items from projects where `manager` matches this user
- Stage changes this week

**④ Summary** — optimistic conclusions
- 2-3 key takeaways: biggest achievement, biggest risk, recommended focus area
- Trend observation if delta exists (e.g., "שני שבועות רצופים עם עלייה ב-CRITICAL")
- One motivating closing sentence

**⑤ Delta** (only when prior report exists)
- Structured comparison: decisions ↑↓ with %, projects changed stage, risks appeared/resolved
- Approval trend, cleared items since last report
- Uses trend arrows: ↑ ↓ → for quick scanning

### Service API

```python
# weekly_report_service.py

async def generate_report_for_user(
    user: User,
    session: AsyncSession,
    triggered_by_id: int | None = None,
    sent_via: str = "telegram",
) -> dict:
    """Generate, persist, and return sections dict for one user."""

async def send_report_to_user(
    bot,
    user: User,
    sections: dict,
) -> None:
    """Send sections as 4-5 sequential Telegram messages (avoids 4096-char limit)."""

async def send_weekly_reports_cron(bot) -> None:
    """Cron entry point — sends self-report to every active non-VIEWER user."""
```

`generate_report_for_user` always saves a `ReportHistory` row before returning.

---

## Telegram UI

### Keyboard changes

**Non-VIEWER users (main keyboard):**
```
Row 1: ["📁 פרוייקטים",  "📋 החלטות"]
Row 2: ["📊 דוח שלי",    "👥 דוח צוות"]   ← "👥 דוח צוות" only for DEPT_MANAGER+
```

**VIEWER keyboard:** unchanged (no report buttons).

### `📊 דוח שלי` flow (all non-VIEWER roles)
1. Show typing indicator
2. Generate report for caller → save `ReportHistory`
3. Send 4-5 sequential messages (one per section) to caller's chat

### `👥 דוח צוות` flow (DEPT_MANAGER / DEPUTY / DIVISION_MANAGER only)
1. Query direct subordinates
2. Show inline keyboard:
   ```
   [👤 שם1]  [👤 שם2]
   [👤 שם3]  [👤 עצמי]
   [👥 כולם (N אנשים)]
   [❌ ביטול]
   ```
3. On selection of one user: generate that user's report → send all sections to **manager's chat**
4. On "כולם": generate all reports sequentially → send each user's sections to manager's chat, separated by `━━━━━━━━━━` divider

### `/report` command
Kept for backward compatibility. Same logic as `📊 דוח שלי`.

### Callback prefix
`report_target:{user_id}` — handled in `handle_callback`.

---

## Dashboard UI

### New page: `/dashboard/reports`

**Access control:**
- DIVISION_MANAGER / DEPUTY / admin: see all users
- DEPARTMENT_MANAGER: see own department (direct reports)
- PROJECT_MANAGER: redirected to own report only

**Page layout:**
```
[Reports header]
[User table]
  Username | Role | Last Report Date | Pending Approvals | Actions
  יוסי     | מנהל | 2026-05-22       | 3                 | [📊 צפה] [📤 שלח לטלגרם]

[Selected user report panel — accordion sections]
  ▶ פתיח
  ▶ החלטות
  ▶ פרויקטים
  ▶ סיכום
  ▶ שינויים מהשבוע הקודם
  [History dropdown: בחר דוח קודם ▼]
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard/reports` | Reports page — lists scoped users |
| GET | `/dashboard/reports/{user_id}` | Regenerate + display latest report for user |
| POST | `/dashboard/reports/{user_id}/send` | Send latest/regenerated report to user's Telegram |
| GET | `/dashboard/reports/{user_id}/history` | JSON list of past ReportHistory rows for user |
| GET | `/dashboard/reports/{user_id}/history/{report_id}` | Display a specific historical report |

### Replace old trigger

Remove the "שלח דוח שבועי עכשיו" button from `dashboard.html` header. Replace with link: "📊 ניהול דוחות →" pointing to `/dashboard/reports`.

---

## Files Changed

| File | Change |
|------|--------|
| `app/models.py` | Add `ReportHistory` model |
| `app/services/weekly_report_service.py` | Full rewrite — structured sections, delta, history persistence |
| `app/services/telegram_polling.py` | Update keyboards, add `report_target:` callback, update `/report` handler |
| `app/routers/dashboard.py` | Add reports endpoints, remove old trigger |
| `app/templates/dashboard.html` | Replace trigger button with reports link |
| `app/templates/reports.html` | New — reports management page |
| `tests/test_weekly_report.py` | Update/extend tests |

---

## Error Handling

- LLM call fails: save `ReportHistory` row with `sections={"error": "..."}`, return fallback Hebrew message
- User has no Telegram ID: skip silently in cron, show error in dashboard
- JSON parse failure from LLM: retry once with stricter prompt; on second failure use fallback
- "כולם" partial failure: continue to next user, collect errors, report summary to manager at end

---

## Self-Review

- No TBDs or vague requirements
- Delta is `null`-safe throughout (first report always works)
- Role scoping is consistent between Telegram and Dashboard
- `send_report_to_user` splits messages to avoid 4096-char Telegram limit
- Old `/report` command preserved for backward compat
- `send_weekly_reports_cron` replaces `send_weekly_reports` — rename keeps cron wiring intact
