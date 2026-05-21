# Decisions Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram menu that lets users browse submitted and received decisions with shortcut buttons, custom filters, and pagination — triggered by `/decisions`, typing "החלטות", or an inline button after decision submission.

**Architecture:** New `decisions_menu_service.py` owns all query/formatting/keyboard logic. `telegram_polling.py` routes `dm:*` and `dm_cf:*` callbacks to a new `_handle_decisions_menu()` method before the existing int-based callback parsing. Shortcuts are stateless (filter encoded in callback_data); custom filter uses a session dict in `telegram_state.py` that persists until the user returns to the main menu.

**Tech Stack:** python-telegram-bot v21+, SQLAlchemy async, pytest + pytest-asyncio (already in requirements)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app/services/decisions_menu_service.py` | Create | All menu logic: keyboards, formatters, DB query |
| `app/services/telegram_state.py` | Modify | Add `_decisions_menu_state` dict |
| `app/services/telegram_polling.py` | Modify | `/decisions` handler, "החלטות" keyword, callback routing, menu button on process() replies |
| `tests/test_decisions_menu_service.py` | Create | Tests for pure functions + query |

---

## Task 1: Create `decisions_menu_service.py` — pure functions, keyboards, formatters

**Files:**
- Create: `app/services/decisions_menu_service.py`
- Create: `tests/test_decisions_menu_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_decisions_menu_service.py
import pytest
from datetime import datetime
from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum
from app.services.decisions_menu_service import (
    format_result_line,
    format_results_message,
    build_custom_filter_keyboard,
    get_menu_keyboard,
    SHORTCUT_PRESETS,
)


def _make_decision(**kwargs):
    defaults = dict(
        id=1,
        type=DecisionTypeEnum.NORMAL,
        status=DecisionStatusEnum.PENDING,
        summary="בדיקה",
        created_at=datetime(2026, 5, 20),
    )
    defaults.update(kwargs)
    d = Decision.__new__(Decision)
    for k, v in defaults.items():
        setattr(d, k, v)
    return d


def test_format_result_line_critical_pending():
    d = _make_decision(id=42, type=DecisionTypeEnum.CRITICAL, status=DecisionStatusEnum.PENDING)
    line = format_result_line(d)
    assert "🚨" in line
    assert "#42" in line
    assert "⏳" in line
    assert "20/05" in line


def test_format_result_line_truncates_long_summary():
    d = _make_decision(summary="א" * 50)
    line = format_result_line(d)
    assert "…" in line


def test_format_result_line_does_not_truncate_short_summary():
    d = _make_decision(summary="קצר")
    line = format_result_line(d)
    assert "…" not in line


def test_format_results_message_empty():
    msg = format_results_message("📋 כל ההחלטות", [], 0, 0)
    assert "לא נמצאו" in msg


def test_format_results_message_header_counts():
    decisions = [_make_decision(id=i) for i in range(3)]
    msg = format_results_message("📋 תוצאות", decisions, 3, 0)
    assert "3" in msg
    assert "1–3" in msg


def test_build_custom_filter_keyboard_marks_active_owner():
    state = {"owner": "my", "type": None, "status": None, "date_days": 30, "page": 0}
    kb = build_custom_filter_keyboard(state)
    flat = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("שלי" in b and "✓" in b for b in flat)
    assert not any("שקיבלתי" in b and "✓" in b for b in flat)


def test_build_custom_filter_keyboard_marks_active_status():
    state = {"owner": "all", "type": None, "status": "pending", "date_days": 7, "page": 0}
    kb = build_custom_filter_keyboard(state)
    flat = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("ממתין" in b and "✓" in b for b in flat)
    assert any("7" in b and "✓" in b for b in flat)


def test_get_menu_keyboard_has_six_buttons():
    kb = get_menu_keyboard()
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(all_buttons) == 6


def test_shortcut_presets_all_keys_present():
    for key in ("recent", "critical", "pending", "recv", "my"):
        assert key in SHORTCUT_PRESETS
        p = SHORTCUT_PRESETS[key]
        assert "owner" in p and "type" in p and "status" in p
        assert "date_days" in p and "title" in p
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_decisions_menu_service.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.services.decisions_menu_service'`

- [ ] **Step 3: Create `app/services/decisions_menu_service.py`**

```python
"""Decisions menu — keyboards, formatters, and DB query."""

import html as _html
from datetime import datetime, timedelta

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Decision, DecisionDistribution, DecisionTypeEnum, DecisionStatusEnum

# ── Constants ──────────────────────────────────────────────────────────────

TYPE_EMOJI = {
    DecisionTypeEnum.CRITICAL:  "🚨",
    DecisionTypeEnum.NORMAL:    "✅",
    DecisionTypeEnum.INFO:      "ℹ️",
    DecisionTypeEnum.UNCERTAIN: "❓",
}
STATUS_EMOJI = {
    DecisionStatusEnum.PENDING:  "⏳",
    DecisionStatusEnum.APPROVED: "✔️",
    DecisionStatusEnum.REJECTED: "❌",
    DecisionStatusEnum.EXECUTED: "⚙️",
}
STATUS_LABEL = {
    DecisionStatusEnum.PENDING:  "ממתין",
    DecisionStatusEnum.APPROVED: "אושר",
    DecisionStatusEnum.REJECTED: "נדחה",
    DecisionStatusEnum.EXECUTED: "בוצע",
}

SHORTCUT_PRESETS: dict[str, dict] = {
    "recent":   {"owner": "all",  "type": None,       "status": None,      "date_days": 30, "title": "📋 החלטות אחרונות"},
    "critical": {"owner": "all",  "type": "critical",  "status": None,      "date_days": 0,  "title": "🚨 החלטות קריטיות"},
    "pending":  {"owner": "all",  "type": None,        "status": "pending", "date_days": 0,  "title": "⏳ החלטות ממתינות"},
    "recv":     {"owner": "recv", "type": None,        "status": None,      "date_days": 0,  "title": "📥 שקיבלתי"},
    "my":       {"owner": "my",   "type": None,        "status": None,      "date_days": 0,  "title": "📤 שהגשתי"},
}

_MENU_TEXT = "‏📋 <b>ההחלטות שלי</b>\n\nבחר תצוגה מהירה או סינון מותאם:"

# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕐 אחרונות",  callback_data="dm:recent:0"),
            InlineKeyboardButton("🚨 קריטיות",  callback_data="dm:critical:0"),
            InlineKeyboardButton("⏳ ממתינות",  callback_data="dm:pending:0"),
        ],
        [
            InlineKeyboardButton("📥 שקיבלתי", callback_data="dm:recv:0"),
            InlineKeyboardButton("📤 שהגשתי",  callback_data="dm:my:0"),
            InlineKeyboardButton("🔍 סינון",    callback_data="dm:custom"),
        ],
    ])


def get_menu_shortcut_keyboard() -> InlineKeyboardMarkup:
    """Single-button keyboard appended to process() confirmation messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 ההחלטות שלי", callback_data="dm:my:0"),
    ]])


def build_results_keyboard(shortcut: str, page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"dm:{shortcut}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="dm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"dm:{shortcut}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu")])
    return InlineKeyboardMarkup(rows)


def build_custom_filter_keyboard(state: dict) -> InlineKeyboardMarkup:
    def _btn(label: str, cd: str, active: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{label} ✓" if active else label, callback_data=cd)

    return InlineKeyboardMarkup([
        [
            _btn("הכל",      "dm_cf:o:all",  state["owner"] == "all"),
            _btn("שלי",      "dm_cf:o:my",   state["owner"] == "my"),
            _btn("שקיבלתי", "dm_cf:o:recv", state["owner"] == "recv"),
        ],
        [
            _btn("הכל",       "dm_cf:t:all",      state["type"] is None),
            _btn("🚨 קריטי",  "dm_cf:t:critical",  state["type"] == "critical"),
            _btn("✅ רגיל",   "dm_cf:t:normal",    state["type"] == "normal"),
            _btn("ℹ️ מידע",  "dm_cf:t:info",      state["type"] == "info"),
        ],
        [
            _btn("הכל",      "dm_cf:s:all",      state["status"] is None),
            _btn("⏳ ממתין", "dm_cf:s:pending",  state["status"] == "pending"),
            _btn("✔️ אושר",  "dm_cf:s:approved", state["status"] == "approved"),
            _btn("❌ נדחה",  "dm_cf:s:rejected", state["status"] == "rejected"),
        ],
        [
            _btn("7 ימים", "dm_cf:d:7",  state["date_days"] == 7),
            _btn("30 יום",  "dm_cf:d:30", state["date_days"] == 30),
            _btn("הכל",    "dm_cf:d:0",  state["date_days"] == 0),
        ],
        [
            InlineKeyboardButton("🔍 הצג תוצאות", callback_data="dm_cf:show"),
            InlineKeyboardButton("🔙 תפריט",       callback_data="dm:menu"),
        ],
    ])


def build_custom_results_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"dm_cf:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="dm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"dm_cf:pg:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu")])
    return InlineKeyboardMarkup(rows)


# ── Formatters ─────────────────────────────────────────────────────────────

def format_result_line(d: Decision) -> str:
    t_emoji  = TYPE_EMOJI.get(d.type, "❓")
    s_emoji  = STATUS_EMOJI.get(d.status, "")
    s_label  = STATUS_LABEL.get(d.status, "")
    summary  = d.summary or ""
    if len(summary) > 40:
        summary = summary[:40] + "…"
    date_str = d.created_at.strftime("%d/%m") if d.created_at else ""
    return f"{t_emoji} <b>#{d.id}</b> — {_html.escape(summary)}  {s_emoji} {s_label} · {date_str}"


def format_results_message(title: str, decisions: list, total: int, page: int) -> str:
    if not decisions:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו החלטות."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(decisions)
    lines  = [
        f"‏<b>{_html.escape(title)}</b> ({total})",
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>",
        "──────────────────",
    ]
    lines.extend(format_result_line(d) for d in decisions)
    return "\n".join(lines)


def build_custom_filter_message() -> str:
    return (
        "‏🔍 <b>סינון מותאם אישית</b>\n\n"
        "בחר פילטרים ולחץ הצג:\n"
        "──────────────────\n"
        "👤 <b>מקור</b> · 🏷️ <b>סוג</b> · 📌 <b>סטטוס</b> · 📅 <b>תקופה</b>"
    )


def get_menu_text() -> str:
    return _MENU_TEXT
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_decisions_menu_service.py -v
```
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/decisions_menu_service.py tests/test_decisions_menu_service.py
git commit -m "feat(decisions-menu): add service with keyboards, formatters, presets"
```

---

## Task 2: Add `query_decisions()` to `decisions_menu_service.py`

**Files:**
- Modify: `app/services/decisions_menu_service.py`
- Modify: `tests/test_decisions_menu_service.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_decisions_menu_service.py`)

```python
import pytest
import pytest_asyncio
from app.models import (
    User, Decision, DecisionDistribution,
    DecisionTypeEnum, DecisionStatusEnum, RoleEnum, DistributionTypeEnum,
)
from app.services.decisions_menu_service import query_decisions


@pytest.mark.asyncio
async def test_query_decisions_my_only(db_session):
    u1 = User(telegram_id=9001, username="qd_u1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u2 = User(telegram_id=9002, username="qd_u2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([u1, u2])
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.APPROVED, summary="mine")
    d2 = Decision(submitter_id=u2.id, type=DecisionTypeEnum.CRITICAL,
                  status=DecisionStatusEnum.PENDING, summary="theirs")
    db_session.add_all([d1, d2])
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "my", None, None, 0, 0)
    assert total == 1
    assert results[0].summary == "mine"


@pytest.mark.asyncio
async def test_query_decisions_recv(db_session):
    u1 = User(telegram_id=9003, username="qd_u3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u2 = User(telegram_id=9004, username="qd_u4", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([u1, u2])
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.PENDING, summary="recv_test")
    db_session.add(d1)
    await db_session.flush()

    dist = DecisionDistribution(
        decision_id=d1.id, user_id=u2.id,
        distribution_type=DistributionTypeEnum.INFO,
    )
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_decisions(db_session, u2.id, "recv", None, None, 0, 0)
    assert total == 1
    assert results[0].summary == "recv_test"


@pytest.mark.asyncio
async def test_query_decisions_all_no_duplicates(db_session):
    """Decision submitted by user AND distributed to same user must appear once."""
    u1 = User(telegram_id=9005, username="qd_u5", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.PENDING, summary="no_dup")
    db_session.add(d1)
    await db_session.flush()

    dist = DecisionDistribution(
        decision_id=d1.id, user_id=u1.id,
        distribution_type=DistributionTypeEnum.INFO,
    )
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "all", None, None, 0, 0)
    assert total == 1


@pytest.mark.asyncio
async def test_query_decisions_type_filter(db_session):
    u1 = User(telegram_id=9006, username="qd_u6", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    d_crit = Decision(submitter_id=u1.id, type=DecisionTypeEnum.CRITICAL,
                      status=DecisionStatusEnum.PENDING, summary="crit")
    d_norm = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                      status=DecisionStatusEnum.PENDING, summary="norm")
    db_session.add_all([d_crit, d_norm])
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "my", "critical", None, 0, 0)
    assert total == 1
    assert results[0].summary == "crit"


@pytest.mark.asyncio
async def test_query_decisions_pagination(db_session):
    u1 = User(telegram_id=9007, username="qd_u7", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    for i in range(12):
        db_session.add(Decision(
            submitter_id=u1.id,
            type=DecisionTypeEnum.NORMAL,
            status=DecisionStatusEnum.PENDING,
            summary=f"page_test_{i}",
        ))
    await db_session.flush()

    results_p0, total = await query_decisions(db_session, u1.id, "my", None, None, 0, 0)
    results_p1, _ = await query_decisions(db_session, u1.id, "my", None, None, 0, 1)

    assert total == 12
    assert len(results_p0) == 10
    assert len(results_p1) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_decisions_menu_service.py -k "query" -v
```
Expected: `ImportError` — `query_decisions` not yet defined

- [ ] **Step 3: Add `query_decisions` to `app/services/decisions_menu_service.py`** (append after the formatter functions)

```python
# ── DB Query ───────────────────────────────────────────────────────────────

async def query_decisions(
    session: AsyncSession,
    user_id: int,
    owner: str,        # "my" | "recv" | "all"
    type_: str | None, # "critical" | "normal" | "info" | "uncertain" | None
    status: str | None, # "pending" | "approved" | "rejected" | "executed" | None
    date_days: int,    # 0 = all time
    page: int,
) -> tuple[list[Decision], int]:
    recv_subq = (
        select(DecisionDistribution.decision_id)
        .where(DecisionDistribution.user_id == user_id)
        .scalar_subquery()
    )

    if owner == "my":
        base = select(Decision).where(Decision.submitter_id == user_id)
    elif owner == "recv":
        base = select(Decision).where(Decision.id.in_(recv_subq))
    else:  # "all" — no duplicates via OR
        base = select(Decision).where(
            or_(Decision.submitter_id == user_id, Decision.id.in_(recv_subq))
        )

    if type_:
        base = base.where(Decision.type == DecisionTypeEnum(type_))
    if status:
        base = base.where(Decision.status == DecisionStatusEnum(status))
    if date_days:
        cutoff = datetime.utcnow() - timedelta(days=date_days)
        base = base.where(Decision.created_at >= cutoff)

    count_q = select(func.count()).select_from(base.subquery())
    total: int = await session.scalar(count_q) or 0

    rows_q = base.order_by(Decision.created_at.desc()).offset(page * 10).limit(10)
    decisions = list((await session.scalars(rows_q)).all())

    return decisions, total
```

- [ ] **Step 4: Run all decisions menu tests**

```
pytest tests/test_decisions_menu_service.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/decisions_menu_service.py tests/test_decisions_menu_service.py
git commit -m "feat(decisions-menu): add query_decisions with owner/type/status/date filters"
```

---

## Task 3: Add `_decisions_menu_state` to `telegram_state.py`

**Files:**
- Modify: `app/services/telegram_state.py`

- [ ] **Step 1: Append the new dict to `app/services/telegram_state.py`**

```python
# { telegram_id (int): filter state dict }  — active custom-filter session
# value: { "owner": str, "type": str|None, "status": str|None, "date_days": int, "page": int }
_decisions_menu_state: dict[int, dict] = {}
```

- [ ] **Step 2: Verify the module still imports cleanly**

```
python -c "from app.services.telegram_state import _decisions_menu_state; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/services/telegram_state.py
git commit -m "feat(decisions-menu): add _decisions_menu_state to telegram_state"
```

---

## Task 4: Add `/decisions` command and "החלטות" keyword

**Files:**
- Modify: `app/services/telegram_polling.py`

- [ ] **Step 1: Add `handle_decisions` command method** to `TelegramPollingBot`

Add this method after `handle_status` (around line 255):

```python
async def handle_decisions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/decisions — open the decisions menu."""
    from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text
    telegram_id = update.effective_user.id
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user or not user.role:
        await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
        return
    await update.message.reply_text(
        get_menu_text(),
        parse_mode="HTML",
        reply_markup=get_menu_keyboard(),
    )
```

- [ ] **Step 2: Register the handler** in `initialize()`, after the `/status` handler line:

```python
self.application.add_handler(CommandHandler("decisions", self.handle_decisions))
```

- [ ] **Step 3: Add keyword detection** in `handle_message`, before the `_NON_WORK_WORDS` check (around line 451). Add this block:

```python
# Decisions menu keyword shortcut
if text.strip() == "החלטות":
    if user.role:
        from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text
        await update.message.reply_text(
            get_menu_text(),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
    return
```

- [ ] **Step 4: Restart Docker and smoke-test**

```
docker-compose restart fastapi
```

Send `/decisions` and the word "החלטות" to the bot. Expect the menu keyboard to appear.

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(decisions-menu): add /decisions command and החלטות keyword trigger"
```

---

## Task 5: Add `_handle_decisions_menu()` to `TelegramPollingBot`

**Files:**
- Modify: `app/services/telegram_polling.py`

- [ ] **Step 1: Route `dm:*` / `dm_cf:*` callbacks at the top of `handle_callback`**

In `handle_callback`, right after `telegram_id = update.effective_user.id` and before the `try:` block that parses `decision_id`, add:

```python
# Decisions menu — handle before int-based action parsing
if data.startswith("dm:") or data.startswith("dm_cf:"):
    async with async_session_maker() as _dm_session:
        _dm_user = await _dm_session.scalar(select(User).where(User.telegram_id == telegram_id))
    if _dm_user:
        await self._handle_decisions_menu(query, context, data, telegram_id, _dm_user)
    return
```

- [ ] **Step 2: Add `_handle_decisions_menu` method** to `TelegramPollingBot` (add before the `start` lifecycle method):

```python
async def _handle_decisions_menu(
    self,
    query,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
    telegram_id: int,
    user,
) -> None:
    """Handle all dm:* and dm_cf:* callback actions."""
    from app.services.decisions_menu_service import (
        get_menu_keyboard, get_menu_text,
        build_custom_filter_keyboard, build_custom_filter_message,
        build_results_keyboard, build_custom_results_keyboard,
        format_results_message, query_decisions, SHORTCUT_PRESETS,
    )
    from app.services.telegram_state import _decisions_menu_state

    # ── dm:noop — page indicator button, do nothing ──────────────────────
    if data == "dm:noop":
        return

    # ── dm:menu — return to main menu ────────────────────────────────────
    if data == "dm:menu":
        _decisions_menu_state.pop(telegram_id, None)
        await query.edit_message_text(
            get_menu_text(),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
        return

    # ── dm:custom — open stateful custom filter panel ────────────────────
    if data == "dm:custom":
        _decisions_menu_state[telegram_id] = {
            "owner": "all", "type": None, "status": None, "date_days": 30, "page": 0,
        }
        await query.edit_message_text(
            build_custom_filter_message(),
            parse_mode="HTML",
            reply_markup=build_custom_filter_keyboard(_decisions_menu_state[telegram_id]),
        )
        return

    # ── dm:{shortcut}:{page} — stateless shortcut results ────────────────
    if data.startswith("dm:"):
        parts = data.split(":")
        shortcut = parts[1] if len(parts) > 1 else ""
        page = int(parts[2]) if len(parts) > 2 else 0
        preset = SHORTCUT_PRESETS.get(shortcut)
        if not preset:
            return
        async with async_session_maker() as session:
            decisions, total = await query_decisions(
                session, user.id,
                preset["owner"], preset["type"], preset["status"], preset["date_days"],
                page,
            )
        await query.edit_message_text(
            format_results_message(preset["title"], decisions, total, page),
            parse_mode="HTML",
            reply_markup=build_results_keyboard(shortcut, page, total),
        )
        return

    # ── dm_cf:* — custom filter session callbacks ─────────────────────────
    if data.startswith("dm_cf:"):
        parts = data.split(":")
        sub = parts[1]
        state = _decisions_menu_state.get(telegram_id)

        if sub == "o" and state:
            state["owner"] = parts[2]
            await query.edit_message_reply_markup(
                reply_markup=build_custom_filter_keyboard(state)
            )
            return

        if sub == "t" and state:
            val = parts[2]
            state["type"] = None if val == "all" else val
            await query.edit_message_reply_markup(
                reply_markup=build_custom_filter_keyboard(state)
            )
            return

        if sub == "s" and state:
            val = parts[2]
            state["status"] = None if val == "all" else val
            await query.edit_message_reply_markup(
                reply_markup=build_custom_filter_keyboard(state)
            )
            return

        if sub == "d" and state:
            state["date_days"] = int(parts[2])
            await query.edit_message_reply_markup(
                reply_markup=build_custom_filter_keyboard(state)
            )
            return

        if sub == "show":
            if not state:
                await query.edit_message_text(
                    "‏⚠️ סשן הסינון פג. פתח את תפריט ההחלטות מחדש.",
                    reply_markup=get_menu_keyboard(),
                )
                return
            async with async_session_maker() as session:
                decisions, total = await query_decisions(
                    session, user.id,
                    state["owner"], state["type"], state["status"], state["date_days"],
                    0,
                )
            # Keep state in _decisions_menu_state for pagination (cleared on dm:menu)
            await query.edit_message_text(
                format_results_message("🔍 תוצאות סינון מותאם", decisions, total, 0),
                parse_mode="HTML",
                reply_markup=build_custom_results_keyboard(0, total),
            )
            return

        if sub == "pg":
            page = int(parts[2]) if len(parts) > 2 else 0
            if not state:
                await query.edit_message_text(
                    "‏⚠️ סשן הסינון פג.",
                    reply_markup=get_menu_keyboard(),
                )
                return
            async with async_session_maker() as session:
                decisions, total = await query_decisions(
                    session, user.id,
                    state["owner"], state["type"], state["status"], state["date_days"],
                    page,
                )
            await query.edit_message_text(
                format_results_message("🔍 תוצאות סינון מותאם", decisions, total, page),
                parse_mode="HTML",
                reply_markup=build_custom_results_keyboard(page, total),
            )
            return
```

- [ ] **Step 3: Restart Docker and test all keyboard paths**

```
docker-compose restart fastapi
```

Test the following flows manually:
1. `/decisions` → menu appears with 6 buttons
2. Tap "🚨 קריטיות" → results list (or "לא נמצאו" if no critical decisions)
3. Tap "🔙 תפריט" → back to menu
4. Tap "🔍 סינון" → custom filter panel with toggles
5. Toggle "שלי" under מקור → button shows ✓
6. Tap "🔍 הצג תוצאות" → results
7. Tap "🔙 תפריט" → back to menu
8. If >10 results exist: test pagination ◀ / ▶

- [ ] **Step 4: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(decisions-menu): add _handle_decisions_menu with shortcut and custom filter flows"
```

---

## Task 6: Add menu button to `process()` confirmation messages

**Files:**
- Modify: `app/services/telegram_polling.py`

The goal: after every decision is confirmed and a reply is sent to the user, append a "📋 ההחלטות שלי" inline button. There are 4 `send_message` / `reply_text` call sites that send the result of `decision_svc.process()`.

- [ ] **Step 1: Add import at top of `_handle_decisions_menu` usage area**

In `telegram_polling.py`, in the `handle_message` method and the callback handler, add this import where `process()` results are sent. You can add a module-level import at the top of the file:

```python
from app.services.decisions_menu_service import get_menu_shortcut_keyboard
```

- [ ] **Step 2: Update call site 1** — in `handle_message`, the clarification path (look for `reply = await decision_svc.process(user, combined_text)` followed by `reply_text`):

```python
reply = await decision_svc.process(user, combined_text)
await update.message.reply_text(reply, parse_mode="HTML",
                                reply_markup=get_menu_shortcut_keyboard())
```

- [ ] **Step 3: Update call site 2** — in `handle_callback`, the `dec_prev_y` path (look for `reply = await decision_svc.process(approver, original_text, pre_result=pre_result)` followed by `send_message`):

```python
reply = await decision_svc.process(approver, original_text, pre_result=pre_result)
await context.bot.send_message(
    chat_id=update.effective_chat.id, text=reply, parse_mode="HTML",
    reply_markup=get_menu_shortcut_keyboard(),
)
```

- [ ] **Step 4: Update call site 3** — in `handle_callback`, the `mgr_yes/mgr_no` path (look for `reply = await decision_svc.process(approver, original_text, force_approval=..., pre_result=pre_result)` followed by `send_message`):

```python
reply = await decision_svc.process(
    approver, original_text,
    force_approval=(action == "mgr_yes"),
    pre_result=pre_result,
)
await context.bot.send_message(
    chat_id=update.effective_chat.id,
    text=reply,
    parse_mode="HTML",
    reply_markup=get_menu_shortcut_keyboard(),
)
```

- [ ] **Step 5: Update call site 4** — in `handle_callback`, the `dec_conf_y` fallback path (look for `reply = await decision_svc.process(approver, original_text)` in the `except` block of `dec_conf_y`):

```python
reply = await decision_svc.process(approver, original_text)
await context.bot.send_message(
    chat_id=update.effective_chat.id, text=reply, parse_mode="HTML",
    reply_markup=get_menu_shortcut_keyboard(),
)
```

- [ ] **Step 6: Restart Docker and verify**

```
docker-compose restart fastapi
```

Submit a decision through the bot. After the confirmation message appears, verify a "📋 ההחלטות שלי" button is visible below it. Tapping it should show the decisions list filtered to "שהגשתי".

- [ ] **Step 7: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(decisions-menu): append menu shortcut button to all decision confirmation messages"
```

---

## Final Smoke Test

- [ ] Run full test suite

```
pytest tests/ -v
```
Expected: all existing tests pass + new decisions menu tests pass

- [ ] End-to-end flow check:
  1. Type "החלטות" → menu appears
  2. `/decisions` → menu appears
  3. Submit a decision → confirmation has "📋 ההחלטות שלי" button
  4. Tap "📋 ההחלטות שלי" → shows submitted decisions
  5. Tap "🔙 תפריט" → back to menu
  6. Tap "🔍 סינון" → custom filter panel
  7. Toggle filters → each toggle updates the keyboard in-place
  8. "🔍 הצג תוצאות" → results with correct filters applied
  9. If 11+ decisions: paginate with ◀ ▶
  10. "🔙 תפריט" from any results screen → returns to menu
