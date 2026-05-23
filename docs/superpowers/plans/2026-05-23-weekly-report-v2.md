# Weekly Report v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-string weekly report with a 5-section structured report that stores history per user, supports manager-controlled recipient selection in Telegram, and adds a reports management page to the dashboard.

**Architecture:** `ReportHistory` table (JSON sections + raw_data snapshot for delta) ← `weekly_report_service.py` (full rewrite, Option C structured JSON via one LLM call) ← Telegram keyboard buttons + dashboard endpoints. Delta computed from structured data diff, not text. `send_weekly_reports` renamed to `send_weekly_reports_cron` to avoid confusion.

**Tech Stack:** FastAPI, python-telegram-bot v21+, SQLAlchemy async, Jinja2, Groq (llm_router), PostgreSQL

---

## File Map

| File | Change |
|------|--------|
| `app/models.py` | Add `ReportHistory` model at end of file |
| `app/services/weekly_report_service.py` | Full rewrite — structured sections, delta, history, new API |
| `app/services/telegram_polling.py` | Update keyboards, add text handlers, add `rpt:` callback |
| `app/services/eval_cron.py` | `send_weekly_reports` → `send_weekly_reports_cron` |
| `app/routers/dashboard.py` | Add `/reports` endpoints, remove old `/report/trigger` |
| `app/templates/dashboard.html` | Replace trigger button with "📊 ניהול דוחות →" link |
| `app/templates/reports.html` | New — reports management page (list users) |
| `app/templates/report_detail.html` | New — per-user report view with section accordion |
| `tests/test_weekly_report.py` | Full rewrite for new API |

---

## Task 1: Add `ReportHistory` model

**Files:**
- Modify: `app/models.py` (end of file, after `RouteTrace`)
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `tests/test_weekly_report.py` with:

```python
"""Tests for weekly report v2 — ReportHistory model, service API, cron skip."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_report_history_model_importable():
    from app.models import ReportHistory
    row = ReportHistory(user_id=1, sections={"prologue": "hi"}, sent_via="telegram")
    assert row.user_id == 1
    assert row.sections["prologue"] == "hi"
    assert row.raw_data is None  # default
```

- [ ] **Step 2: Run — verify FAIL**

```
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py::test_report_history_model_importable -v
```
Expected: `ImportError: cannot import name 'ReportHistory'`

- [ ] **Step 3: Add `ReportHistory` to `app/models.py`**

Append at the very end of `app/models.py` (after the `RouteTrace` class):

```python


class ReportHistory(Base):
    """Per-user weekly report history. Sections stored as JSON; raw_data snapshot
    enables structured delta computation on next generation."""
    __tablename__ = "report_history"

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sections     = Column(JSON, nullable=False)
    # sections keys: prologue | decisions | projects | summary | delta (null on first run)
    raw_data     = Column(JSON, nullable=True)
    # raw_data: structured snapshot used to compute delta for the next report
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    triggered_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    sent_via     = Column(String(32), nullable=True)  # "telegram" | "dashboard" | "cron"

    user = relationship("User", foreign_keys=[user_id])
```

- [ ] **Step 4: Run — verify PASS**

```
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py::test_report_history_model_importable -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_weekly_report.py
git commit -m "feat(reports): add ReportHistory model with JSON sections + raw_data snapshot"
```

---

## Task 2: Rewrite `weekly_report_service.py`

**Files:**
- Modify: `app/services/weekly_report_service.py` (full rewrite)
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Add tests for the new API**

Append to `tests/test_weekly_report.py`:

```python

# ── Task 2 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_report_returns_sections_dict(db_session):
    """generate_report_for_user returns a dict with 5 keys."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99991
    user.username = "test_gen"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"פתיח","decisions":"החלטות",'
        '"projects":"פרויקטים","summary":"סיכום","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert "prologue" in sections
    assert "decisions" in sections
    assert "projects" in sections
    assert "summary" in sections
    assert "delta" in sections


@pytest.mark.asyncio
async def test_generate_report_saves_history_row(db_session):
    """generate_report_for_user persists a ReportHistory row."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum, ReportHistory
    from sqlalchemy import select

    user = MagicMock(spec=User)
    user.id = 99992
    user.username = "test_save"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"p","decisions":"d","projects":"pr","summary":"s","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        await generate_report_for_user(user, db_session, triggered_by_id=1, sent_via="dashboard")

    row = await db_session.scalar(
        select(ReportHistory).where(ReportHistory.user_id == 99992)
    )
    assert row is not None
    assert row.sent_via == "dashboard"
    assert row.sections["prologue"] == "p"


@pytest.mark.asyncio
async def test_generate_report_fallback_on_llm_error(db_session):
    """When LLM raises, sections has a non-empty prologue and others are None."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99993
    user.username = "test_fallback"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, side_effect=Exception("timeout")):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert sections["prologue"]  # non-empty fallback message


@pytest.mark.asyncio
async def test_send_report_sends_non_empty_sections():
    """send_report_to_user calls bot.send_message once per non-null section."""
    from app.services.weekly_report_service import send_report_to_user

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    sections = {
        "prologue":  "פתיח",
        "decisions": "החלטות",
        "projects":  "פרויקטים",
        "summary":   "סיכום",
        "delta":     None,  # null → no message
    }
    await send_report_to_user(mock_bot, 12345, sections)
    assert mock_bot.send_message.call_count == 4  # delta skipped


@pytest.mark.asyncio
async def test_cron_skips_viewer(db_session):
    """send_weekly_reports_cron does not send to VIEWER users."""
    from app.services.weekly_report_service import send_weekly_reports_cron
    from app.models import User, RoleEnum

    viewer = MagicMock(spec=User)
    viewer.telegram_id = 7777777001
    viewer.role = RoleEnum.VIEWER
    viewer.id = 88801
    viewer.username = "viewer_skip"

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [viewer]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    with patch("app.database.async_session_maker", return_value=mock_session):
        await send_weekly_reports_cron(mock_bot)

    mock_bot.send_message.assert_not_called()
```

- [ ] **Step 2: Run — verify FAIL**

```
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py -v
```
Expected: 5 new tests FAIL (ImportError or AttributeError on new functions).

- [ ] **Step 3: Rewrite `app/services/weekly_report_service.py`**

Replace the entire file with:

```python
"""Weekly intelligence report v2.

generate_report_for_user(user, session, triggered_by_id, sent_via) -> dict
    Role-scoped data gather → single LLM call → JSON sections → persist ReportHistory row.

send_report_to_user(bot, chat_id, sections, recipient_label) -> None
    Sends each non-null section as a separate Telegram message (avoids 4096-char limit).

send_weekly_reports_cron(bot) -> None
    Cron entry point — sends self-report to every active non-VIEWER user.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Decision, Project, RoleEnum, DecisionStatusEnum,
    DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum,
    ReportHistory,
)
import app.database as _app_database

logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    RoleEnum.PROJECT_MANAGER:          "מנהל פרויקט",
    RoleEnum.DEPARTMENT_MANAGER:       "מנהל מחלקה",
    RoleEnum.DEPUTY_DIVISION_MANAGER:  "סגן מנהל אגף",
    RoleEnum.DIVISION_MANAGER:         "מנהל אגף",
}

_MANAGER_ROLES = {
    RoleEnum.DEPARTMENT_MANAGER,
    RoleEnum.DEPUTY_DIVISION_MANAGER,
    RoleEnum.DIVISION_MANAGER,
}

_SECTION_HEADERS = [
    ("prologue",  "📊 פתיח"),
    ("decisions", "📋 החלטות השבוע"),
    ("projects",  "🏗️ מצב פרויקטים"),
    ("summary",   "✅ סיכום ומסקנות"),
    ("delta",     "📈 שינויים מהדוח הקודם"),
]

_REPORT_PROMPT = """\
אתה מנתח BI מומחה לתשתיות חשמל. צור דוח שבועי בעברית עבור {username} (תפקיד: {role_label}).
תאריך: שבוע {date_range}

--- נתוני קלט ---
החלטות השבוע: {decisions_json}
אישורים ממתינים לפעולתך: {pending_json}
פרויקטים באיחור (תאריך סיום עבר): {behind_json}
פרויקטים בסיכון: {risks_json}
פרויקטים לטיפול (to_handle): {handle_json}
{delta_section}
--- הוראות לכל חלק ---
prologue (עד 80 מילה): ברכה בשם, שבוע {date_range}, 1–2 פריטים דחופים לפעולה היום \
(אישורים תקועים >24ש, פרויקטים באיחור), ספירות מהירות.
decisions (עד 120 מילה): ספירה לפי סוג (INFO/NORMAL/CRITICAL/UNCERTAIN), אחוז אישורים, \
רשימה מפורשת של אישורים ממתינים עם מזהה (#ID) ותיאור קצר, דגל אנומליה אם נפח > פי שניים מהרגיל.
projects (עד 120 מילה): פרויקטים באיחור עם תאריך, סיכונים פתוחים ללא בעלים, \
רשימת משימות לביצוע ממוספרת מ-to_handle.
summary (עד 80 מילה): 2–3 הישגים מרכזיים, המלצה אחת לשבוע הבא, משפט עידוד אחרון. אופטימי תמיד.
delta: {has_delta} — אם "true" תאר בעברית את השינויים המחושבים: החלטות ↑↓%, \
שינויי שלב פרויקטים, סיכונים חדשים/שנסגרו, מגמות. אם "false" — החזר null.

--- פורמט תשובה (JSON בלבד, ללא טקסט לפני ואחרי) ---
{{
  "prologue": "...",
  "decisions": "...",
  "projects": "...",
  "summary": "...",
  "delta": "..." | null
}}"""

_FALLBACK_SECTIONS = {
    "prologue":  "‏⚠️ שגיאה בייצור הדוח. נסה שוב מאוחר יותר.",
    "decisions": None,
    "projects":  None,
    "summary":   None,
    "delta":     None,
}


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_report_for_user(
    user: User,
    session: AsyncSession,
    triggered_by_id: int | None = None,
    sent_via: str = "telegram",
) -> dict:
    """Generate, persist, and return sections dict for one user."""
    from app.services.llm_router import llm_chat

    role_label = _ROLE_LABELS.get(user.role, user.role.value if user.role else "משתמש")
    today_str  = datetime.utcnow().strftime("%d/%m/%Y")
    since_str  = (datetime.utcnow() - timedelta(days=7)).strftime("%d/%m/%Y")

    raw = await _gather_raw_data(user, session)

    # Fetch previous report for delta
    prev_row = await session.scalar(
        select(ReportHistory)
        .where(ReportHistory.user_id == user.id)
        .order_by(desc(ReportHistory.generated_at))
        .limit(1)
    )

    delta_section_text = ""
    has_delta = "false"
    if prev_row and prev_row.raw_data:
        delta_input = _compute_delta(raw, prev_row.raw_data)
        prev_date = prev_row.generated_at.strftime("%d/%m/%Y")
        delta_section_text = (
            f"שינויים מהדוח הקודם ({prev_date}):\n"
            f"{json.dumps(delta_input, ensure_ascii=False)}\n"
        )
        has_delta = "true"

    prompt = _REPORT_PROMPT.format(
        role_label=role_label,
        username=user.username or role_label,
        date_range=f"{since_str}–{today_str}",
        decisions_json=json.dumps(raw["decisions"], ensure_ascii=False),
        pending_json=json.dumps(raw["pending_approvals"][:10], ensure_ascii=False),
        behind_json=json.dumps(raw["projects_behind"][:5], ensure_ascii=False),
        risks_json=json.dumps(raw["projects_at_risk"][:5], ensure_ascii=False),
        handle_json=json.dumps(raw["handle_items"][:5], ensure_ascii=False),
        delta_section=delta_section_text,
        has_delta=has_delta,
    )

    sections = dict(_FALLBACK_SECTIONS)
    try:
        raw_response = await llm_chat(
            "weekly_report",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.3,
        )
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        parsed = json.loads(cleaned)
        sections = {
            "prologue":  str(parsed.get("prologue") or ""),
            "decisions": parsed.get("decisions") or None,
            "projects":  parsed.get("projects") or None,
            "summary":   parsed.get("summary") or None,
            "delta":     parsed.get("delta") or None,
        }
        if not sections["prologue"]:
            sections["prologue"] = _FALLBACK_SECTIONS["prologue"]
    except Exception as exc:
        logger.error(f"Weekly report LLM/parse failed for user {user.id}: {exc}")

    row = ReportHistory(
        user_id=user.id,
        sections=sections,
        raw_data=raw,
        triggered_by=triggered_by_id,
        sent_via=sent_via,
    )
    session.add(row)
    await session.flush()

    return sections


async def send_report_to_user(
    bot,
    chat_id: int,
    sections: dict,
    recipient_label: str = "",
) -> None:
    """Send sections as sequential Telegram messages. Skips null sections."""
    for i, (key, header) in enumerate(_SECTION_HEADERS):
        body = sections.get(key)
        if not body:
            continue
        prefix = f"‏👤 דוח עבור: <b>{recipient_label}</b>\n\n" if (i == 0 and recipient_label) else ""
        text = f"‏<b>{header}</b>\n\n{prefix}{body}"[:4000]
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def send_weekly_reports_cron(bot) -> None:
    """Cron entry point — send self-report to every active non-VIEWER user."""
    async with _app_database.async_session_maker() as session:
        stmt = select(User).where(
            User.telegram_id.isnot(None),
            User.role.isnot(None),
        )
        all_users = (await session.execute(stmt)).scalars().all()
        users = [u for u in all_users if u.role != RoleEnum.VIEWER]

        for user in users:
            try:
                sections = await generate_report_for_user(user, session, sent_via="cron")
                await send_report_to_user(bot, user.telegram_id, sections)
                logger.info(f"Weekly cron report sent to user {user.id} ({user.username})")
            except Exception as exc:
                logger.error(f"Weekly cron report failed for user {user.id}: {exc}")


# ── Data gathering ────────────────────────────────────────────────────────────

async def _gather_raw_data(user: User, session: AsyncSession) -> dict:
    since = datetime.utcnow() - timedelta(days=7)
    today = datetime.utcnow().date()

    decisions   = await _decisions_summary(user, session, since)
    pending     = await _pending_approvals(user, session)
    behind      = await _projects_behind_schedule(user, session, today)
    at_risk     = await _risky_projects(user, session)
    handle      = await _handle_projects(user, session)
    stage_map   = await _project_stage_map(user, session)

    return {
        "decisions":        decisions,
        "pending_approvals": pending,
        "projects_behind":  behind,
        "projects_at_risk": at_risk,
        "handle_items":     handle,
        "stage_map":        stage_map,
    }


async def _decisions_summary(user: User, session: AsyncSession, since: datetime) -> dict:
    stmt = select(Decision).where(Decision.created_at >= since)
    if user.role == RoleEnum.PROJECT_MANAGER:
        stmt = stmt.where(Decision.submitter_id == user.id)
    elif user.role == RoleEnum.DEPARTMENT_MANAGER:
        sub_ids = await _subordinate_ids(user, session)
        if sub_ids:
            from sqlalchemy import or_
            stmt = stmt.where(or_(
                Decision.submitter_id == user.id,
                Decision.submitter_id.in_(sub_ids),
            ))
        else:
            stmt = stmt.where(Decision.submitter_id == user.id)
    # DEPUTY / DIVISION_MANAGER: no filter — see all

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return {}

    type_counts: dict[str, int] = {}
    approved = 0
    for d in rows:
        t = d.type.value.upper() if d.type else "UNKNOWN"
        type_counts[t] = type_counts.get(t, 0) + 1
        if d.status == DecisionStatusEnum.APPROVED:
            approved += 1

    return {
        "total":             len(rows),
        "by_type":           type_counts,
        "approval_rate_pct": round(approved / len(rows) * 100),
        "sample": [
            {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
            for d in rows[:8]
        ],
    }


async def _pending_approvals(user: User, session: AsyncSession) -> list[dict]:
    stmt = (
        select(Decision)
        .join(DecisionDistribution, DecisionDistribution.decision_id == Decision.id)
        .where(
            DecisionDistribution.user_id == user.id,
            DecisionDistribution.distribution_type == DistributionTypeEnum.APPROVAL,
            DecisionDistribution.status == DistributionStatusEnum.PENDING,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
        for d in rows
    ]


async def _projects_behind_schedule(user: User, session: AsyncSession, today) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.estimated_finish_date.isnot(None),
        Project.estimated_finish_date <= today,
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(10))).scalars().all()
    return [
        {"identifier": p.project_identifier, "name": p.name or "",
         "finish_date": str(p.estimated_finish_date)}
        for p in rows
    ]


async def _risky_projects(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.risks.isnot(None),
        Project.risks != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(20))).scalars().all()
    return [
        {"identifier": p.project_identifier, "name": p.name or "", "risks": (p.risks or "")[:120]}
        for p in rows
    ]


async def _handle_projects(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active == True,
        Project.to_handle.isnot(None),
        Project.to_handle != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(20))).scalars().all()
    return [
        {"identifier": p.project_identifier, "name": p.name or "", "to_handle": (p.to_handle or "")[:120]}
        for p in rows
    ]


async def _project_stage_map(user: User, session: AsyncSession) -> dict[str, str]:
    stmt = select(Project.project_identifier, Project.stage).where(Project.is_active == True)
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(200))).all()
    return {row[0]: (row[1] or "") for row in rows if row[0]}


async def _subordinate_ids(user: User, session: AsyncSession) -> list[int]:
    rows = (await session.execute(
        select(User.id).where(User.manager_id == user.id)
    )).scalars().all()
    return list(rows)


def _compute_delta(current: dict, prev: dict) -> dict:
    """Compute structured diff between current and previous raw_data snapshots."""
    c_dec = current.get("decisions") or {}
    p_dec = prev.get("decisions") or {}

    curr_total = c_dec.get("total", 0)
    prev_total = p_dec.get("total", 0)

    curr_stages = current.get("stage_map", {})
    prev_stages = prev.get("stage_map", {})
    stage_changes = [
        {"id": k, "from": prev_stages[k], "to": curr_stages[k]}
        for k in curr_stages
        if k in prev_stages and curr_stages[k] != prev_stages[k]
    ]

    curr_risk_ids = {p["identifier"] for p in current.get("projects_at_risk", [])}
    prev_risk_ids = {p["identifier"] for p in prev.get("projects_at_risk", [])}

    return {
        "decisions_change":       curr_total - prev_total,
        "prev_decisions_total":   prev_total,
        "curr_decisions_total":   curr_total,
        "prev_approval_rate_pct": p_dec.get("approval_rate_pct", 0),
        "curr_approval_rate_pct": c_dec.get("approval_rate_pct", 0),
        "pending_approvals_change": (
            len(current.get("pending_approvals", [])) -
            len(prev.get("pending_approvals", []))
        ),
        "stage_changes":          stage_changes,
        "new_risks":              list(curr_risk_ids - prev_risk_ids),
        "resolved_risks":         list(prev_risk_ids - curr_risk_ids),
        "behind_schedule_change": (
            len(current.get("projects_behind", [])) -
            len(prev.get("projects_behind", []))
        ),
    }
```

- [ ] **Step 4: Run — verify all 5 new tests PASS**

```
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py -v
```
Expected: all 6 tests PASS (1 from Task 1 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): rewrite weekly_report_service — structured JSON sections, delta, ReportHistory"
```

---

## Task 3: Update cron to use renamed function

**Files:**
- Modify: `app/services/eval_cron.py:60-65`

- [ ] **Step 1: Update the import in `_weekly_report_run`**

In `app/services/eval_cron.py`, find lines 60-63:

```python
async def _weekly_report_run() -> None:
    """Send weekly reports to all active users (Thursday 17:00 Israel time)."""
    from app.services.weekly_report_service import send_weekly_reports
    from app.services.telegram_polling import telegram_bot
    if telegram_bot.application and telegram_bot.application.bot:
        await send_weekly_reports(telegram_bot.application.bot)
    else:
        logger.warning("weekly_report_run: bot not available, skipping")
```

Replace with:

```python
async def _weekly_report_run() -> None:
    """Send weekly reports to all active users (Thursday 17:00 Israel time)."""
    from app.services.weekly_report_service import send_weekly_reports_cron
    from app.services.telegram_polling import telegram_bot
    if telegram_bot.application and telegram_bot.application.bot:
        await send_weekly_reports_cron(telegram_bot.application.bot)
    else:
        logger.warning("weekly_report_run: bot not available, skipping")
```

- [ ] **Step 2: Restart and verify cron still registers**

```bash
docker-compose restart fastapi
docker-compose logs fastapi 2>&1 | grep weekly_report
```
Expected: `eval_cron: weekly_report job registered (Thu 17:00 Asia/Jerusalem)`

- [ ] **Step 3: Commit**

```bash
git add app/services/eval_cron.py
git commit -m "fix(cron): update import to send_weekly_reports_cron after rename"
```

---

## Task 4: Update Telegram keyboards + "📊 דוח שלי" handler

**Files:**
- Modify: `app/services/telegram_polling.py` lines 31–51 (keyboards), ~583 (handle_message keyword section), ~349 (handle_report)

- [ ] **Step 1: Update `_main_reply_keyboard` and `_keyboard_for_user`**

In `app/services/telegram_polling.py`, replace lines 31–51:

```python
def _main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📁 פרוייקטים", "📋 החלטות"]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _viewer_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📁 פרוייקטים"]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _keyboard_for_user(user) -> ReplyKeyboardMarkup:
    from app.models import RoleEnum
    if user and user.role == RoleEnum.VIEWER:
        return _viewer_reply_keyboard()
    return _main_reply_keyboard()
```

With:

```python
_MANAGER_REPORT_ROLES = frozenset()  # populated after import to avoid circular

def _main_reply_keyboard(user=None) -> ReplyKeyboardMarkup:
    from app.models import RoleEnum
    manager_roles = {RoleEnum.DEPARTMENT_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER, RoleEnum.DIVISION_MANAGER}
    rows = [["📁 פרוייקטים", "📋 החלטות"]]
    if user and user.role in manager_roles:
        rows.append(["📊 דוח שלי", "👥 דוח צוות"])
    else:
        rows.append(["📊 דוח שלי"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _viewer_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📁 פרוייקטים"]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _keyboard_for_user(user) -> ReplyKeyboardMarkup:
    from app.models import RoleEnum
    if user and user.role == RoleEnum.VIEWER:
        return _viewer_reply_keyboard()
    return _main_reply_keyboard(user)
```

- [ ] **Step 2: Add "📊 דוח שלי" keyword handler in `handle_message`**

In `handle_message`, find the block that handles "החלטות" and "פרוייקטים" keywords (around line 589):

```python
            # Decisions menu keyword shortcut
            if "החלטות" in text.strip():
```

Insert a new block **before** this, after the `if user.role == _RE.VIEWER:` early-return:

```python
            # Report shortcut — "📊 דוח שלי"
            if "דוח שלי" in text.strip():
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
                async with async_session_maker() as _rpt_session:
                    sections = await generate_report_for_user(
                        user, _rpt_session,
                        triggered_by_id=user.id,
                        sent_via="telegram",
                    )
                await send_report_to_user(context.bot, update.effective_chat.id, sections)
                return

```

- [ ] **Step 3: Update `handle_report` command to use new API**

Replace the current `handle_report` method (lines 349–364) with:

```python
    async def handle_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/report — generate and send weekly intelligence report for the requesting user."""
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה.")
                return
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await update.message.reply_text("‏🔒 דוח שבועי אינו זמין לצופים.")
                return
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
            sections = await generate_report_for_user(
                user, session, triggered_by_id=user.id, sent_via="telegram"
            )
        await send_report_to_user(context.bot, update.effective_chat.id, sections)
```

- [ ] **Step 4: Restart and smoke-test**

```bash
docker-compose restart fastapi
```

In Telegram, send `📊 דוח שלי` (or press the new button). Verify 4-5 messages arrive with section headers in Hebrew. Verify `test_report_history_model_importable` and `test_generate_report_returns_sections_dict` still pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(telegram): add '📊 דוח שלי' button and update report keyboard layout"
```

---

## Task 5: Add "👥 דוח צוות" handler and `rpt:` callback

**Files:**
- Modify: `app/services/telegram_polling.py` (handle_message keyword section, handle_callback)

- [ ] **Step 1: Add "👥 דוח צוות" keyword handler**

In `handle_message`, immediately after the "דוח שלי" block added in Task 4, add:

```python
            # Team report shortcut — "👥 דוח צוות" (managers only)
            if "דוח צוות" in text.strip():
                from app.models import RoleEnum as _RE2
                _MANAGER_ROLES_RPT = {_RE2.DEPARTMENT_MANAGER, _RE2.DEPUTY_DIVISION_MANAGER, _RE2.DIVISION_MANAGER}
                if user.role not in _MANAGER_ROLES_RPT:
                    await update.message.reply_text("‏🔒 דוח צוות זמין למנהלים בלבד.")
                    return
                async with async_session_maker() as _sub_session:
                    sub_rows = (await _sub_session.execute(
                        select(User).where(User.manager_id == user.id, User.role.isnot(None))
                    )).scalars().all()
                if not sub_rows:
                    await update.message.reply_text("‏📭 אין לך כפופים רשומים במערכת.")
                    return
                buttons = []
                row_buf = []
                # Index-based callback to stay within 64-byte limit
                _awaiting_team_report[telegram_id] = [u.id for u in sub_rows]
                for i, sub in enumerate(sub_rows):
                    label = f"👤 {sub.username or sub.id}"
                    row_buf.append(InlineKeyboardButton(label, callback_data=f"rpt:{i}"))
                    if len(row_buf) == 2:
                        buttons.append(row_buf)
                        row_buf = []
                if row_buf:
                    buttons.append(row_buf)
                buttons.append([
                    InlineKeyboardButton("👥 כולם", callback_data="rpt:all"),
                    InlineKeyboardButton("❌ ביטול", callback_data="rpt:cancel"),
                ])
                await update.message.reply_text(
                    "‏📊 לאיזה חבר צוות לייצר דוח?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

```

- [ ] **Step 2: Add `_awaiting_team_report` state dict**

In `app/services/telegram_state.py`, add at the end of the file:

```python
# { telegram_id (int): [user_id, ...] }  — subordinate list for team-report selection
_awaiting_team_report: dict[int, list[int]] = {}
```

In `telegram_polling.py`, add this import at the top of `handle_message` (near the other telegram_state imports, or inline as a local import inside the keyword block — use inline to match existing pattern):

```python
from app.services.telegram_state import _awaiting_team_report
```

Add this line at the top of the "דוח צוות" handler block:

```python
                from app.services.telegram_state import _awaiting_team_report
```

- [ ] **Step 3: Add `rpt:` callback handler in `handle_callback`**

In `handle_callback`, find the `disambig:` block (around line 827). Add a new block **before** it:

```python
        # Team report — manager selected a recipient
        if data.startswith("rpt:"):
            from app.services.telegram_state import _awaiting_team_report
            from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
            token = data[len("rpt:"):]

            if token == "cancel":
                _awaiting_team_report.pop(telegram_id, None)
                await query.edit_message_text("‏❌ בוטל.")
                return

            async with async_session_maker() as _rpt_cb_session:
                requester = await _rpt_cb_session.scalar(
                    select(User).where(User.telegram_id == telegram_id)
                )
                if not requester:
                    await query.answer("שגיאה — משתמש לא נמצא")
                    return

                sub_ids = _awaiting_team_report.pop(telegram_id, [])

                if token == "all":
                    target_ids = sub_ids if sub_ids else []
                    if not target_ids:
                        await query.edit_message_text("‏📭 לא נמצאו כפופים.")
                        return
                    await query.edit_message_text("‏⏳ מייצר דוחות לכל הצוות…")
                    errors = []
                    for uid in target_ids:
                        try:
                            target = await _rpt_cb_session.scalar(
                                select(User).where(User.id == uid)
                            )
                            if not target:
                                continue
                            sections = await generate_report_for_user(
                                target, _rpt_cb_session,
                                triggered_by_id=requester.id,
                                sent_via="telegram",
                            )
                            await send_report_to_user(
                                context.bot,
                                telegram_id,
                                sections,
                                recipient_label=target.username or str(target.id),
                            )
                        except Exception as _e:
                            errors.append(str(uid))
                            logger.error(f"Team report failed for user {uid}: {_e}")
                    summary = "‏✅ כל הדוחות נשלחו."
                    if errors:
                        summary += f" שגיאות עבור: {', '.join(errors)}"
                    await context.bot.send_message(chat_id=telegram_id, text=summary)
                    return

                # Single user by index
                try:
                    target_id = sub_ids[int(token)]
                except (IndexError, ValueError):
                    await query.answer("שגיאה — נסה שוב")
                    return

                target = await _rpt_cb_session.scalar(
                    select(User).where(User.id == target_id)
                )
                if not target:
                    await query.edit_message_text("‏⚠️ משתמש לא נמצא.")
                    return

                await query.edit_message_text(
                    f"‏⏳ מייצר דוח עבור {target.username or target_id}…"
                )
                sections = await generate_report_for_user(
                    target, _rpt_cb_session,
                    triggered_by_id=requester.id,
                    sent_via="telegram",
                )
            await send_report_to_user(
                context.bot,
                telegram_id,
                sections,
                recipient_label=target.username or str(target_id),
            )
            return

```

- [ ] **Step 4: Restart and smoke-test**

```bash
docker-compose restart fastapi
```

As a manager user, send "👥 דוח צוות". Verify the inline keyboard appears with subordinate buttons. Click one — verify their report (labeled with their name) arrives in your chat.

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py app/services/telegram_state.py
git commit -m "feat(telegram): add '👥 דוח צוות' team report selection with rpt: callback"
```

---

## Task 6: Dashboard reports endpoints

**Files:**
- Modify: `app/routers/dashboard.py` (remove old trigger, add reports endpoints)

- [ ] **Step 1: Remove old trigger endpoint and add reports endpoints**

In `app/routers/dashboard.py`, find and **remove** the entire `trigger_weekly_report` function (lines 2416–2438):

```python
@router.post("/report/trigger", response_class=HTMLResponse)
async def trigger_weekly_report(
    ...
):
    ...
```

Then add the following at the end of `app/routers/dashboard.py`:

```python
# ── Reports management ────────────────────────────────────────────────────────

from app.models import ReportHistory


@router.get("/reports", response_class=HTMLResponse)
async def reports_index(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Reports management — list users scoped to current_user's visibility."""
    from sqlalchemy import desc as _desc

    # Determine visible users
    if current_user.is_admin or current_user.role in (
        RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER
    ):
        stmt = select(User).where(User.role.isnot(None), User.role != RoleEnum.VIEWER)
    elif current_user.role == RoleEnum.DEPARTMENT_MANAGER:
        stmt = select(User).where(
            User.role.isnot(None),
            User.role != RoleEnum.VIEWER,
            User.manager_id == current_user.id,
        )
    else:
        # PROJECT_MANAGER — redirect to own report
        return RedirectResponse(f"/dashboard/reports/{current_user.id}", status_code=302)

    users = (await session.execute(stmt.order_by(User.role, User.username))).scalars().all()

    # Fetch last report date per user
    latest_stmt = (
        select(ReportHistory.user_id, func.max(ReportHistory.generated_at).label("last_report"))
        .group_by(ReportHistory.user_id)
        .subquery()
    )
    latest_map_rows = (await session.execute(select(latest_stmt))).all()
    latest_map = {row[0]: row[1] for row in latest_map_rows}

    users_data = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value if u.role else "",
            "last_report": latest_map.get(u.id),
        }
        for u in users
    ]

    return templates.TemplateResponse("reports.html", {
        "request": request,
        "current_user": current_user,
        "users_data": users_data,
    })


@router.get("/reports/{user_id}", response_class=HTMLResponse)
async def report_view(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """View the latest saved report for a user. Does NOT regenerate."""
    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Access control
    if not (
        current_user.is_admin
        or current_user.id == user_id
        or current_user.role in (RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER)
        or (current_user.role == RoleEnum.DEPARTMENT_MANAGER and target.manager_id == current_user.id)
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    latest = await session.scalar(
        select(ReportHistory)
        .where(ReportHistory.user_id == user_id)
        .order_by(desc(ReportHistory.generated_at))
        .limit(1)
    )

    # History list for dropdown
    history_rows = (await session.execute(
        select(ReportHistory.id, ReportHistory.generated_at, ReportHistory.sent_via)
        .where(ReportHistory.user_id == user_id)
        .order_by(desc(ReportHistory.generated_at))
        .limit(20)
    )).all()

    history = [{"id": r[0], "date": r[1], "via": r[2]} for r in history_rows]

    return templates.TemplateResponse("report_detail.html", {
        "request": request,
        "current_user": current_user,
        "target_user": target,
        "latest": latest,
        "history": history,
    })


@router.post("/reports/{user_id}/generate", response_class=HTMLResponse)
async def report_generate(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Regenerate report for user and redirect to view page."""
    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not (
        current_user.is_admin
        or current_user.id == user_id
        or current_user.role in (RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER)
        or (current_user.role == RoleEnum.DEPARTMENT_MANAGER and target.manager_id == current_user.id)
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.weekly_report_service import generate_report_for_user
    await generate_report_for_user(
        target, session,
        triggered_by_id=current_user.id,
        sent_via="dashboard",
    )
    return RedirectResponse(f"/dashboard/reports/{user_id}", status_code=302)


@router.post("/reports/{user_id}/send", response_class=HTMLResponse)
async def report_send(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Send the latest report for a user to their Telegram."""
    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not (
        current_user.is_admin
        or current_user.id == user_id
        or current_user.role in (RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER)
        or (current_user.role == RoleEnum.DEPARTMENT_MANAGER and target.manager_id == current_user.id)
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.telegram_id:
        return HTMLResponse('<script>alert("למשתמש זה אין Telegram."); window.history.back();</script>')

    latest = await session.scalar(
        select(ReportHistory)
        .where(ReportHistory.user_id == user_id)
        .order_by(desc(ReportHistory.generated_at))
        .limit(1)
    )
    if not latest:
        return HTMLResponse('<script>alert("אין דוח שמור — יש לייצר תחילה."); window.history.back();</script>')

    from app.services.weekly_report_service import send_report_to_user
    from app.services.telegram_polling import telegram_bot
    if not telegram_bot.application or not telegram_bot.application.bot:
        return HTMLResponse('<script>alert("הבוט אינו פעיל."); window.history.back();</script>')

    import asyncio
    asyncio.create_task(send_report_to_user(
        telegram_bot.application.bot,
        target.telegram_id,
        latest.sections,
    ))
    return HTMLResponse(
        f'<script>alert("הדוח נשלח ל-{target.username or user_id}."); '
        f'window.location.href="/dashboard/reports/{user_id}";</script>'
    )


@router.get("/reports/{user_id}/history/{report_id}", response_class=HTMLResponse)
async def report_history_view(
    request: Request,
    user_id: int,
    report_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """View a specific historical report."""
    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not (
        current_user.is_admin
        or current_user.id == user_id
        or current_user.role in (RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER)
        or (current_user.role == RoleEnum.DEPARTMENT_MANAGER and target.manager_id == current_user.id)
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    row = await session.scalar(
        select(ReportHistory).where(
            ReportHistory.id == report_id,
            ReportHistory.user_id == user_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")

    return templates.TemplateResponse("report_detail.html", {
        "request": request,
        "current_user": current_user,
        "target_user": target,
        "latest": row,
        "history": [],
    })
```

- [ ] **Step 2: Fix missing `desc` import**

At the top of `app/routers/dashboard.py`, the `from sqlalchemy import ...` line already imports `func`. Add `desc` to it:

```python
from sqlalchemy import select, func, or_, exists, update, delete, desc
```

- [ ] **Step 3: Restart and verify endpoints load**

```bash
docker-compose restart fastapi
docker-compose logs fastapi 2>&1 | grep -E "ERROR|started"
```
Expected: No import errors. Visit `/dashboard/reports` — should return 200 (or redirect for non-managers).

- [ ] **Step 4: Commit**

```bash
git add app/routers/dashboard.py
git commit -m "feat(dashboard): add /reports management endpoints, remove old trigger"
```

---

## Task 7: Dashboard templates

**Files:**
- Create: `app/templates/reports.html`
- Create: `app/templates/report_detail.html`
- Modify: `app/templates/dashboard.html` lines 865–872 (trigger button)

- [ ] **Step 1: Create `app/templates/reports.html`**

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shan-AI — ניהול דוחות</title>
    <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --bg-deep:#070b12; --bg-surface:#0c1220; --bg-card:#0f1826;
            --border:#1a2d47; --cyan:#00d4ff; --green:#10b981;
            --amber:#f59e0b; --red:#ef4444; --text-1:#e2e8f0; --text-2:#64748b;
        }
        body { background:var(--bg-deep); color:var(--text-1); font-family:'Heebo',sans-serif; min-height:100vh; }
        .navbar { background:var(--bg-surface); border-bottom:1px solid var(--border); padding:12px 24px; }
        .page-title { color:var(--cyan); font-size:1.4rem; font-weight:700; }
        .card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; }
        .table { color:var(--text-1); }
        .table th { color:var(--text-2); border-color:var(--border); font-size:.8rem; text-transform:uppercase; letter-spacing:.05em; }
        .table td { border-color:var(--border); vertical-align:middle; }
        .btn-cyan { background:var(--cyan); color:#000; font-weight:600; border:none; border-radius:6px; padding:4px 14px; font-size:.85rem; }
        .btn-cyan:hover { opacity:.85; }
        .btn-outline-dim { border:1px solid var(--border); color:var(--text-2); background:transparent; border-radius:6px; padding:4px 14px; font-size:.85rem; }
        .btn-outline-dim:hover { color:var(--text-1); border-color:var(--cyan); }
        .badge-role { font-size:.72rem; padding:2px 8px; border-radius:4px; background:rgba(0,212,255,.12); color:var(--cyan); }
        .no-report { color:var(--text-2); font-size:.8rem; }
    </style>
</head>
<body>
<nav class="navbar d-flex justify-content-between align-items-center">
    <span class="page-title">📊 ניהול דוחות שבועיים</span>
    <div class="d-flex gap-2 align-items-center">
        <span style="color:var(--text-2);font-size:.85rem;">{{ current_user.username }}</span>
        <a href="/dashboard" class="btn-outline-dim">← לוח בקרה</a>
        <a href="/logout" class="btn-outline-dim">יציאה</a>
    </div>
</nav>

<div class="container-fluid px-4 py-4">
    <div class="card p-3">
        <table class="table table-hover mb-0">
            <thead>
                <tr>
                    <th>משתמש</th>
                    <th>תפקיד</th>
                    <th>דוח אחרון</th>
                    <th>פעולות</th>
                </tr>
            </thead>
            <tbody>
            {% for u in users_data %}
                <tr>
                    <td><strong>{{ u.username }}</strong></td>
                    <td><span class="badge-role">{{ u.role }}</span></td>
                    <td>
                        {% if u.last_report %}
                            {{ u.last_report.strftime('%d/%m/%Y %H:%M') }}
                        {% else %}
                            <span class="no-report">לא נוצר עדיין</span>
                        {% endif %}
                    </td>
                    <td class="d-flex gap-2">
                        <a href="/dashboard/reports/{{ u.id }}" class="btn-outline-dim">👁 צפה</a>
                        <form method="post" action="/dashboard/reports/{{ u.id }}/generate" style="margin:0;">
                            <button type="submit" class="btn-outline-dim">🔄 צור חדש</button>
                        </form>
                        {% if u.last_report %}
                        <form method="post" action="/dashboard/reports/{{ u.id }}/send"
                              onsubmit="return confirm('לשלוח דוח ל-{{ u.username }} בטלגרם?');" style="margin:0;">
                            <button type="submit" class="btn-cyan">📤 שלח</button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="4" class="text-center" style="color:var(--text-2);">אין משתמשים להצגה</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</div>
</body>
</html>
```

- [ ] **Step 2: Create `app/templates/report_detail.html`**

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shan-AI — דוח {{ target_user.username }}</title>
    <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --bg-deep:#070b12; --bg-surface:#0c1220; --bg-card:#0f1826;
            --border:#1a2d47; --cyan:#00d4ff; --green:#10b981;
            --text-1:#e2e8f0; --text-2:#64748b;
        }
        body { background:var(--bg-deep); color:var(--text-1); font-family:'Heebo',sans-serif; min-height:100vh; }
        .navbar { background:var(--bg-surface); border-bottom:1px solid var(--border); padding:12px 24px; }
        .page-title { color:var(--cyan); font-size:1.3rem; font-weight:700; }
        .section-card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; margin-bottom:12px; }
        .section-header { padding:12px 16px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; font-weight:600; }
        .section-header:hover { background:rgba(0,212,255,.05); border-radius:8px; }
        .section-body { padding:16px; border-top:1px solid var(--border); white-space:pre-wrap; line-height:1.7; font-size:.95rem; }
        .meta { color:var(--text-2); font-size:.82rem; }
        .btn-cyan { background:var(--cyan); color:#000; font-weight:600; border:none; border-radius:6px; padding:6px 18px; }
        .btn-outline-dim { border:1px solid var(--border); color:var(--text-2); background:transparent; border-radius:6px; padding:6px 18px; }
        .btn-outline-dim:hover { color:var(--text-1); border-color:var(--cyan); }
        select.history-sel { background:var(--bg-surface); color:var(--text-1); border:1px solid var(--border); border-radius:6px; padding:4px 10px; font-size:.85rem; }
    </style>
</head>
<body>
<nav class="navbar d-flex justify-content-between align-items-center">
    <span class="page-title">📊 דוח שבועי — {{ target_user.username }}</span>
    <div class="d-flex gap-2 align-items-center">
        <span class="meta">{{ current_user.username }}</span>
        <a href="/dashboard/reports" class="btn-outline-dim">← רשימת דוחות</a>
        <a href="/logout" class="btn-outline-dim">יציאה</a>
    </div>
</nav>

<div class="container-fluid px-4 py-4">

    {% if latest %}
    <!-- Action bar -->
    <div class="d-flex gap-2 mb-3 align-items-center flex-wrap">
        <form method="post" action="/dashboard/reports/{{ target_user.id }}/generate">
            <button type="submit" class="btn-outline-dim">🔄 צור דוח חדש</button>
        </form>
        <form method="post" action="/dashboard/reports/{{ target_user.id }}/send"
              onsubmit="return confirm('לשלוח דוח ל-{{ target_user.username }} בטלגרם?');">
            <button type="submit" class="btn-cyan">📤 שלח לטלגרם</button>
        </form>
        {% if history %}
        <select class="history-sel" onchange="if(this.value) window.location='/dashboard/reports/{{ target_user.id }}/history/'+this.value;">
            <option value="">בחר דוח קודם ▼</option>
            {% for h in history %}
            <option value="{{ h.id }}" {% if h.id == latest.id %}selected{% endif %}>
                {{ h.date.strftime('%d/%m/%Y %H:%M') }} — {{ h.via or '—' }}
            </option>
            {% endfor %}
        </select>
        {% endif %}
        <span class="meta">נוצר: {{ latest.generated_at.strftime('%d/%m/%Y %H:%M') }} | ערוץ: {{ latest.sent_via or '—' }}</span>
    </div>

    <!-- Sections accordion -->
    {% set section_defs = [
        ('prologue',  '📊 פתיח'),
        ('decisions', '📋 החלטות השבוע'),
        ('projects',  '🏗️ מצב פרויקטים'),
        ('summary',   '✅ סיכום ומסקנות'),
        ('delta',     '📈 שינויים מהדוח הקודם'),
    ] %}

    {% for key, label in section_defs %}
        {% set body = latest.sections.get(key) %}
        {% if body %}
        <div class="section-card">
            <div class="section-header" onclick="toggle('{{ key }}')">
                <span>{{ label }}</span>
                <span id="arr-{{ key }}">▼</span>
            </div>
            <div class="section-body" id="sec-{{ key }}">{{ body }}</div>
        </div>
        {% endif %}
    {% endfor %}

    {% else %}
    <div style="color:var(--text-2);text-align:center;padding:60px 0;">
        <p style="font-size:1.1rem;">אין דוח שמור למשתמש זה.</p>
        <form method="post" action="/dashboard/reports/{{ target_user.id }}/generate">
            <button type="submit" class="btn-cyan">🔄 צור דוח ראשון</button>
        </form>
    </div>
    {% endif %}
</div>

<script>
function toggle(key) {
    const sec = document.getElementById('sec-' + key);
    const arr = document.getElementById('arr-' + key);
    if (sec.style.display === 'none') {
        sec.style.display = 'block';
        arr.textContent = '▼';
    } else {
        sec.style.display = 'none';
        arr.textContent = '▶';
    }
}
</script>
</body>
</html>
```

- [ ] **Step 3: Replace trigger button in `dashboard.html`**

In `app/templates/dashboard.html`, find lines 865–872:

```html
        {% if current_user.is_admin %}
        <form method="post" action="/dashboard/report/trigger"
              onsubmit="return confirm('שלוח דוח שבועי לכל המשתמשים עכשיו?');">
          <button type="submit" class="btn btn-outline-primary btn-sm ms-2">
            📊 שלח דוח שבועי עכשיו
          </button>
        </form>
        {% endif %}
```

Replace with:

```html
        <a href="/dashboard/reports" class="btn btn-outline-primary btn-sm ms-2">
            📊 ניהול דוחות →
        </a>
```

(Remove the `{% if current_user.is_admin %}` guard — the link is visible to all, access is enforced server-side.)

- [ ] **Step 4: Restart and verify**

```bash
docker-compose restart fastapi
```

Visit `/dashboard` — verify "📊 ניהול דוחות →" link appears. Click it — verify `/dashboard/reports` loads with user table. Click a user — verify `/dashboard/reports/{id}` shows "no report yet" with a "צור דוח ראשון" button. Click it — verify report is generated and sections accordion appears.

- [ ] **Step 5: Commit**

```bash
git add app/templates/reports.html app/templates/report_detail.html app/templates/dashboard.html
git commit -m "feat(dashboard): add reports management pages with section accordion and history"
```

---

## Task 8: Run full test suite

- [ ] **Step 1: Run all tests**

```bash
docker-compose exec fastapi python -m pytest tests/ -v --timeout=60 2>&1 | tail -40
```
Expected: all tests PASS. Zero failures.

- [ ] **Step 2: If any failures, investigate and fix**

Common failure modes:
- `ImportError` on `desc` in `dashboard.py` — verify `from sqlalchemy import ... desc` is added
- `AttributeError: _awaiting_team_report` — verify it was added to `telegram_state.py`
- Old `send_weekly_reports` test — the old tests were fully replaced in Task 2 Step 1; if old tests remain, delete them

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "test(reports): full test suite green after weekly report v2"
```

---

## Self-Review

**Spec coverage:**
- ✅ ReportHistory table with JSON sections + raw_data → Task 1
- ✅ 5-section structured report (prologue/decisions/projects/summary/delta) → Task 2
- ✅ Delta from structured data diff → Task 2 (`_compute_delta`)
- ✅ `send_report_to_user` splits into separate messages → Task 2
- ✅ `📊 דוח שלי` for all non-VIEWER → Task 4
- ✅ `👥 דוח צוות` for DEPT_MANAGER+ only → Task 5
- ✅ "כולם" sends all reports to manager's chat → Task 5
- ✅ `/report` command updated → Task 4
- ✅ Cron renamed to `send_weekly_reports_cron` → Task 3
- ✅ Dashboard `/reports` list page → Task 6
- ✅ Dashboard `/reports/{id}` detail view with accordion → Tasks 6+7
- ✅ Regenerate + Send to Telegram buttons → Tasks 6+7
- ✅ History dropdown → Task 7
- ✅ Old trigger button replaced → Task 7
- ✅ Access control on all dashboard endpoints → Task 6
- ✅ PROJECT_MANAGER redirected to own report → Task 6

**Placeholder scan:** None found.

**Type consistency:**
- `generate_report_for_user` → returns `dict` throughout (Tasks 2, 4, 5, 6)
- `send_report_to_user(bot, chat_id, sections, recipient_label="")` → consistent (Tasks 2, 4, 5, 6)
- `send_weekly_reports_cron` → consistent (Tasks 2, 3)
- `ReportHistory.sections` → `dict` (JSON column) throughout
- `_awaiting_team_report` → `dict[int, list[int]]` → consistent (Tasks 5)
