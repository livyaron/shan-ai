# Viewer Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `VIEWER` role that can browse projects and query the AI via Telegram, but cannot submit decisions or access the decisions workflow.

**Architecture:** Add `VIEWER = "viewer"` to `RoleEnum`; run a one-line Postgres ALTER TYPE migration; add viewer checks to `telegram_polling.py`'s keyboard helper, keyword handlers, and `handle_message`; add the Hebrew label to the dashboard.

**Tech Stack:** Python 3.11, SQLAlchemy async, python-telegram-bot v21+, FastAPI, PostgreSQL, Groq (llama-3.3-70b-versatile).

---

## File Structure

| File | Change |
|---|---|
| `app/models.py` | Add `VIEWER = "viewer"` to `RoleEnum` |
| `app/services/telegram_polling.py` | `_keyboard_for_user()` helper; viewer branch in `handle_message`; block decisions in `handle_decisions` + keyword guard |
| `app/routers/dashboard.py` | Add `"viewer": "צופה"` to `ROLE_LABELS` |
| `tests/test_viewer_role.py` | New — unit tests for viewer access control and keyboard logic |

> **No template change needed.** `users.html` already iterates `[r.value for r in RoleEnum]` passed from the router, so the viewer option appears automatically once `RoleEnum` gains the new value.

---

### Task 0: Add VIEWER to RoleEnum

**Files:**
- Modify: `app/models.py:10-14`

- [ ] **Step 1: Write the failing test**

Create `tests/test_viewer_role.py`:

```python
from app.models import RoleEnum

def test_viewer_role_exists():
    assert RoleEnum.VIEWER == "viewer"

def test_viewer_not_in_superior_hierarchy():
    from app.services.decision_service import SUPERIOR_ROLE
    assert RoleEnum.VIEWER not in SUPERIOR_ROLE
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_viewer_role.py::test_viewer_role_exists -v
```
Expected: `FAILED` — `AttributeError: VIEWER`

- [ ] **Step 3: Add VIEWER to RoleEnum**

In `app/models.py`, change:
```python
class RoleEnum(str, enum.Enum):
    PROJECT_MANAGER = "project_manager"
    DEPARTMENT_MANAGER = "department_manager"
    DEPUTY_DIVISION_MANAGER = "deputy_division_manager"
    DIVISION_MANAGER = "division_manager"
```
to:
```python
class RoleEnum(str, enum.Enum):
    PROJECT_MANAGER = "project_manager"
    DEPARTMENT_MANAGER = "department_manager"
    DEPUTY_DIVISION_MANAGER = "deputy_division_manager"
    DIVISION_MANAGER = "division_manager"
    VIEWER = "viewer"
```

- [ ] **Step 4: Run DB migration**

SQLAlchemy's `create_all` does NOT alter existing Postgres enum types. Run this manually (adjust container name if needed):

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c \
  "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'viewer';"
```

If the command fails with "type roleenum does not exist", find the actual enum name:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "\dT"
```
Use the name shown for the `users.role` column.

For Railway (production), run via psql with the external TCP proxy:
```bash
psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" \
  -c "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'viewer';"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_viewer_role.py -v
```
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_viewer_role.py
git commit -m "feat(viewer): add VIEWER to RoleEnum + DB migration"
```

---

### Task 1: Dashboard — Add Viewer Label

**Files:**
- Modify: `app/routers/dashboard.py:236-241`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_viewer_role.py`:

```python
def test_viewer_in_dashboard_role_labels():
    from app.routers.dashboard import ROLE_LABELS
    assert "viewer" in ROLE_LABELS
    assert ROLE_LABELS["viewer"] == "צופה"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_viewer_role.py::test_viewer_in_dashboard_role_labels -v
```
Expected: `FAILED` — `AssertionError`

- [ ] **Step 3: Add label to ROLE_LABELS**

In `app/routers/dashboard.py`, change:
```python
ROLE_LABELS = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
}
```
to:
```python
ROLE_LABELS = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
    "viewer": "צופה",
}
```

Note: `users.html` passes `"roles": [r.value for r in RoleEnum]` to the template. With `VIEWER` now in `RoleEnum`, the dropdown will automatically include `viewer` → `צופה`. No template edit needed.

Also update the inline `role_labels` dicts scattered in `app/routers/dashboard.py` (lines ~536, ~763, ~879). Search for `"division_manager": "מנהל אגף"` in that file — each occurrence is a local copy. Add `"viewer": "צופה"` to each one.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_viewer_role.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py
git commit -m "feat(viewer): add צופה label to dashboard role lists"
```

---

### Task 2: Viewer Keyboard Helper

**Files:**
- Modify: `app/services/telegram_polling.py:30-35` (the `_main_reply_keyboard` function and its callers)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_viewer_role.py`:

```python
def test_keyboard_for_viewer_has_one_button():
    import sys
    sys.path.insert(0, 'C:/Users/livya/Desktop/SHAN-AI')
    # We import just the helper — no bot connection needed
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    kb = _keyboard_for_user(viewer)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 1
    assert "פרוייקטים" in buttons[0]

def test_keyboard_for_operational_has_two_buttons():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    user = MagicMock()
    user.role = RoleEnum.PROJECT_MANAGER
    kb = _keyboard_for_user(user)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_viewer_role.py::test_keyboard_for_viewer_has_one_button -v
```
Expected: `FAILED` — `ImportError: cannot import name '_keyboard_for_user'`

- [ ] **Step 3: Add `_keyboard_for_user` and update callers**

In `app/services/telegram_polling.py`, replace the existing `_main_reply_keyboard` function (lines 30-35):

```python
def _main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📁 פרוייקטים", "📋 החלטות"]],
        resize_keyboard=True,
        is_persistent=True,
    )
```

with:

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

Then update the three call sites that currently call `_main_reply_keyboard()` with a user available:

**Line ~139** (in `handle_start`):
```python
# Before:
kb = _main_reply_keyboard() if user.role else None
# After:
kb = _keyboard_for_user(user) if user.role else None
```

**Line ~225** (in `handle_register` or equivalent registration success):
```python
# Before:
reply_markup=_main_reply_keyboard(),
# After:
reply_markup=_keyboard_for_user(user),
```

**Line ~314** (in `handle_menu`):
```python
# Before:
reply_markup=_main_reply_keyboard(),
# After:
reply_markup=_keyboard_for_user(user),
```

- [ ] **Step 4: Syntax check and run tests**

```bash
python -c "import ast; ast.parse(open('app/services/telegram_polling.py', encoding='utf-8').read()); print('OK')"
python -m pytest tests/test_viewer_role.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(viewer): add _keyboard_for_user helper, viewer single-button keyboard"
```

---

### Task 3: Block Decisions Access for Viewers

**Files:**
- Modify: `app/services/telegram_polling.py` — `handle_decisions`, `handle_message` decisions keyword block

The blocked message constant (add near the top of `telegram_polling.py`, after the `_viewer_reply_keyboard` function):
```python
_VIEWER_DECISIONS_BLOCKED = "‏🔒 גישה לתפריט ההחלטות אינה זמינה למשתמשי צפייה."
```

- [ ] **Step 1: Write the failing test**

Add to `tests/test_viewer_role.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_handle_decisions_blocks_viewer():
    from app.services.telegram_polling import TelegramPollingBot
    from app.models import RoleEnum

    bot = TelegramPollingBot()

    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER

    update = MagicMock()
    update.effective_user.id = 999
    update.message = AsyncMock()

    context = MagicMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm:
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=viewer)
        mock_sm.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)
        await bot.handle_decisions(update, context)

    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "🔒" in call_text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_viewer_role.py::test_handle_decisions_blocks_viewer -v
```
Expected: `FAILED` — viewer currently passes through to the decisions menu

- [ ] **Step 3: Add viewer block to `handle_decisions`**

In `app/services/telegram_polling.py`, in `handle_decisions` (around line 277), add a viewer check right after the `if not user or not user.role` guard:

```python
async def handle_decisions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/decisions — open the decisions menu."""
    from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text, get_menu_counts
    from app.models import RoleEnum
    telegram_id = update.effective_user.id
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user or not user.role:
            await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
            return
        if user.role == RoleEnum.VIEWER:
            await update.message.reply_text(
                _VIEWER_DECISIONS_BLOCKED,
                reply_markup=_viewer_reply_keyboard(),
            )
            return
        counts = await get_menu_counts(session, user.id)
    await update.message.reply_text(
        get_menu_text(counts),
        parse_mode="HTML",
        reply_markup=get_menu_keyboard(),
    )
```

Also add the block in `handle_message` for the `"החלטות"` keyword (around line 512). Change:

```python
if "החלטות" in text.strip():
    if user.role:
        from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text, get_menu_counts
        async with async_session_maker() as _cnt_s:
            counts = await get_menu_counts(_cnt_s, user.id)
        await update.message.reply_text(
            get_menu_text(counts),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
    return
```

to:

```python
if "החלטות" in text.strip():
    from app.models import RoleEnum
    if user.role == RoleEnum.VIEWER:
        await update.message.reply_text(
            _VIEWER_DECISIONS_BLOCKED,
            reply_markup=_viewer_reply_keyboard(),
        )
        return
    if user.role:
        from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text, get_menu_counts
        async with async_session_maker() as _cnt_s:
            counts = await get_menu_counts(_cnt_s, user.id)
        await update.message.reply_text(
            get_menu_text(counts),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
    return
```

- [ ] **Step 4: Syntax check and run tests**

```bash
python -c "import ast; ast.parse(open('app/services/telegram_polling.py', encoding='utf-8').read()); print('OK')"
python -m pytest tests/test_viewer_role.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(viewer): block decisions menu and החלטות keyword for viewer role"
```

---

### Task 4: Viewer Free-text Handler

**Files:**
- Modify: `app/services/telegram_polling.py` — add `_handle_viewer_message()` async method to `TelegramPollingBot`, call it from `handle_message`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_viewer_role.py`:

```python
@pytest.mark.asyncio
async def test_viewer_projects_keyword_opens_menu():
    from app.services.telegram_polling import TelegramPollingBot
    from app.models import RoleEnum

    bot = TelegramPollingBot()
    bot.application = MagicMock()

    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    viewer.id = 1

    update = MagicMock()
    update.effective_user.id = 42
    update.effective_chat.id = 42
    update.message = AsyncMock()
    update.message.text = "פרוייקטים"

    context = MagicMock()
    context.bot = AsyncMock()

    with patch("app.services.telegram_polling.async_session_maker") as mock_sm, \
         patch("app.services.telegram_polling.TelegramService") as mock_svc:
        mock_session = AsyncMock()
        mock_sm.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_svc_inst = AsyncMock()
        mock_svc_inst._get_or_create_user = AsyncMock(return_value=viewer)
        mock_svc_inst._store_message = AsyncMock()
        mock_svc.return_value = mock_svc_inst

        with patch("app.services.telegram_polling.feedback_service") as mock_fb, \
             patch("app.services.projects_menu_service.get_total_active", new_callable=AsyncMock, return_value=5):
            mock_fb.get_awaiting_feedback.return_value = {}
            await bot.handle_message(update, context)

    # Should have called reply_text at least once (projects menu)
    assert update.message.reply_text.called
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_viewer_role.py::test_viewer_projects_keyword_opens_menu -v
```
Expected: `FAILED`

- [ ] **Step 3: Add `_handle_viewer_message` and wire into `handle_message`**

Add this method to `TelegramPollingBot` in `telegram_polling.py`, before `_handle_decisions_menu`:

```python
async def _handle_viewer_message(
    self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str
) -> None:
    """Handle all free-text from VIEWER role users."""
    from app.models import RoleEnum
    from app.services.projects_menu_service import (
        get_menu_keyboard as pm_kb, get_menu_text as pm_text,
        get_total_active, build_project_card,
    )
    from app.models import Project
    from sqlalchemy import select as _sel

    # ── keyword: projects menu ──────────────────────────────────────────
    if "פרוייקטים" in text or "פרויקטים" in text:
        async with async_session_maker() as _s:
            _total = await get_total_active(_s)
        await update.message.reply_text(
            pm_text(_total),
            parse_mode="HTML",
            reply_markup=pm_kb(),
        )
        return

    # ── keyword: decisions blocked ──────────────────────────────────────
    if "החלטות" in text:
        await update.message.reply_text(
            _VIEWER_DECISIONS_BLOCKED,
            reply_markup=_viewer_reply_keyboard(),
        )
        return

    # ── project name search ─────────────────────────────────────────────
    async with async_session_maker() as _s:
        rows = list((await _s.scalars(
            _sel(Project)
            .where(Project.is_active.is_(True))
            .where(Project.name.ilike(f"%{text}%"))
            .limit(6)
        )).all())

    if len(rows) == 1:
        await update.message.reply_text(
            build_project_card(rows[0]),
            parse_mode="HTML",
            reply_markup=_viewer_reply_keyboard(),
        )
        return

    if 2 <= len(rows) <= 5:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        import html as _h
        btns = [
            [InlineKeyboardButton(
                f"📁 {_h.escape(p.name or str(p.id))}",
                callback_data=f"pm:d:{p.id}:viewer:0",
            )]
            for p in rows
        ]
        await update.message.reply_text(
            "‏נמצאו מספר פרוייקטים — בחר:",
            reply_markup=InlineKeyboardMarkup(btns),
        )
        return

    # ── fallthrough: AI analysis (display-only, no Decision saved) ─────
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )
    from app.services.decision_service import DecisionService
    async with async_session_maker() as _s:
        decision_svc = DecisionService(_s, self.application)
        try:
            pre_result = await decision_svc.analyze_only(user, text)
        except Exception as _err:
            logger.error(f"viewer analyze_only failed: {_err}", exc_info=True)
            await update.message.reply_text(
                "‏⚠️ שגיאה בניתוח. נסה שוב.",
                reply_markup=_viewer_reply_keyboard(),
            )
            return

    import html as _h2
    type_map = {"INFO": "מידע", "NORMAL": "רגיל", "CRITICAL": "קריטי", "UNCERTAIN": "לא ודאי"}
    t = (pre_result.get("type") or "").upper()
    reply = (
        "‏🔍 <b>ניתוח AI (לצפייה בלבד):</b>\n\n"
        f"<b>סוג:</b> {type_map.get(t, t or '—')}\n"
        f"<b>סיכום:</b> {_h2.escape(pre_result.get('summary') or '—')}\n"
        f"<b>פעולה מומלצת:</b> {_h2.escape(pre_result.get('recommended_action') or '—')}\n"
        f"<b>ביטחון:</b> {pre_result.get('confidence', 0):.0%}"
    )
    await update.message.reply_text(
        reply,
        parse_mode="HTML",
        reply_markup=_viewer_reply_keyboard(),
    )
```

Then in `handle_message`, add the viewer branch right after the `if not user.role` guard (around line 504), before the typing indicator:

```python
# If no role assigned yet, redirect to register
if not user.role:
    await update.message.reply_text(
        "⏳ חשבונך ממתין לאישור תפקיד.\n"
        "השתמש ב-/register לבדיקת הסטטוס."
    )
    return

# Viewer: separate read-only pipeline
from app.models import RoleEnum as _RE
if user.role == _RE.VIEWER:
    await self._handle_viewer_message(update, context, user, text.strip())
    return

# Show typing indicator
await context.bot.send_chat_action(
    chat_id=update.effective_chat.id, action="typing"
)
```

- [ ] **Step 4: Syntax check and run tests**

```bash
python -c "import ast; ast.parse(open('app/services/telegram_polling.py', encoding='utf-8').read()); print('OK')"
python -m pytest tests/test_viewer_role.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(viewer): _handle_viewer_message — project search + AI display-only"
```

---

### Task 5: Deploy

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/test_viewer_role.py tests/test_projects_menu_service.py -v 2>&1 | tail -20
```
Expected: all unit tests pass (DB-dependent tests will error on missing connection — that's expected).

- [ ] **Step 2: Run DB migration on Railway**

```bash
psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" \
  -c "ALTER TYPE roleenum ADD VALUE IF NOT EXISTS 'viewer';"
```

- [ ] **Step 3: Push and deploy**

```bash
git push origin master

TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"
SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"
ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { serviceInstanceDeploy(serviceId: \\\"$SVC_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}"
```

- [ ] **Step 4: Verify in dashboard**

Open `https://easygoing-endurance-production-df54.up.railway.app/dashboard/users` → edit any user → confirm `צופה` appears in the role dropdown.

- [ ] **Step 5: Smoke test in Telegram**

1. Assign a test user the `viewer` role via dashboard.
2. User sends `/decisions` → should receive `🔒 גישה לתפריט ההחלטות...`
3. User sends `פרוייקטים` → should open projects menu with single-button keyboard.
4. User sends a project name → should see a project card.
5. User sends a decision-like text → should see AI analysis without any "document decision?" prompt.
