# Decision `is_relevant` Attribute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `is_relevant` boolean flag to every decision so stale/cancelled decisions can be hidden from all searches by default, and toggled from the Telegram bot.

**Architecture:** New columns on `decisions` table (`is_relevant`, `irrelevant_reason`, `irrelevant_at`, `irrelevant_by_id`). All existing search paths default to `is_relevant=True`. Telegram adds two new callbacks (`dec_irrel:` / `dec_rel:`) following the existing reject-with-reason pattern. Dashboard adds a filter dropdown and a `⛔` badge.

**Tech Stack:** SQLAlchemy (async), python-telegram-bot v21+, PostgreSQL (manual ALTER TABLE, no Alembic), FastAPI/Jinja2

---

## File Map

| File | Change |
|------|--------|
| `app/models.py` | +4 columns + 1 relationship on `Decision` |
| `app/services/telegram_state.py` | +1 state dict |
| `app/services/decision_service.py` | +`set_decision_relevance()` function |
| `app/services/decisions_menu_service.py` | filter param in `query_decisions()` + `get_menu_counts()`, relevance row in custom filter keyboard, `⛔` in `format_result_line()` |
| `app/services/embedding_service.py` | +`is_relevant==True` filter in `get_similar_decisions()` |
| `app/services/telegram_polling.py` | +2 callback handlers + state check in `handle_message()` |
| `app/routers/dashboard.py` | +`filter_relevant` param + `_can_toggle_relevance()` + toggle endpoint |
| `app/templates/decisions.html` | +relevance filter dropdown + `⛔` badge |
| `app/services/weekly_report_service.py` | +`is_relevant==True` filter |
| `CLAUDE.md` | Document the new ALTER TABLE in section 4 |

---

## Task 1: Database Migration

**Files:** Modify nothing yet — run SQL directly against running DB.

- [ ] **Step 1: Run ALTER TABLE**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "
ALTER TABLE decisions
  ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT,
  ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);
"
```

Expected output: `ALTER TABLE`

- [ ] **Step 2: Verify columns exist**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "\d decisions" | grep irrelevant
```

Expected: 3 lines showing `irrelevant_reason`, `irrelevant_at`, `irrelevant_by_id`

---

## Task 2: Add Columns to SQLAlchemy Model

**Files:**
- Modify: `app/models.py` (Decision class, after `completed_at` column ~line 114)

- [ ] **Step 1: Write failing test**

```python
# tests/test_is_relevant.py
import pytest
from app.models import Decision

def test_decision_has_is_relevant_column():
    cols = {c.key for c in Decision.__table__.columns}
    assert "is_relevant" in cols
    assert "irrelevant_reason" in cols
    assert "irrelevant_at" in cols
    assert "irrelevant_by_id" in cols

def test_is_relevant_defaults_true():
    d = Decision.__new__(Decision)
    # SQLAlchemy Column default value
    col = Decision.__table__.c["is_relevant"]
    assert col.default.arg is True or col.server_default is not None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
docker exec shan-ai-fastapi pytest tests/test_is_relevant.py -v
```

Expected: FAIL — `assert "is_relevant" in cols`

- [ ] **Step 3: Add columns to Decision class in `app/models.py`**

After `completed_at` and before the `submitter` relationship, add:

```python
    is_relevant        = Column(Boolean, nullable=False, default=True, server_default="true")
    irrelevant_reason  = Column(Text, nullable=True)
    irrelevant_at      = Column(DateTime, nullable=True)
    irrelevant_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
```

After the `submitter` relationship, add:

```python
    irrelevant_by = relationship("User", foreign_keys=[irrelevant_by_id])
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
docker exec shan-ai-fastapi pytest tests/test_is_relevant.py -v
```

Expected: PASS

- [ ] **Step 5: Restart FastAPI and confirm no startup errors**

```bash
docker-compose restart fastapi && docker logs shan-ai-fastapi --tail 20
```

Expected: `Application startup complete.`

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_is_relevant.py
git commit -m "feat(decisions): add is_relevant columns to Decision model"
```

---

## Task 3: Service Function `set_decision_relevance`

**Files:**
- Modify: `app/services/decision_service.py` (add after `reject_decision()` method)

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_is_relevant.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.decision_service import DecisionService

@pytest.mark.asyncio
async def test_set_irrelevant_updates_fields():
    session = AsyncMock()
    svc = DecisionService.__new__(DecisionService)
    svc.session = session
    d = MagicMock()
    d.is_relevant = True
    d.submitter_id = 1
    session.get.return_value = d
    session.scalar.return_value = None  # no RACI A

    actor = MagicMock()
    actor.id = 1
    actor.is_admin = False

    success, msg = await svc.set_decision_relevance(session, 99, actor, is_relevant=False, reason="בוטל")
    assert success
    assert d.is_relevant is False
    assert d.irrelevant_reason == "בוטל"
    assert d.irrelevant_by_id == 1

@pytest.mark.asyncio
async def test_restore_relevant_clears_fields():
    session = AsyncMock()
    svc = DecisionService.__new__(DecisionService)
    svc.session = session
    d = MagicMock()
    d.is_relevant = False
    d.submitter_id = 1
    session.get.return_value = d
    session.scalar.return_value = None

    actor = MagicMock()
    actor.id = 1
    actor.is_admin = False

    success, msg = await svc.set_decision_relevance(session, 99, actor, is_relevant=True)
    assert success
    assert d.is_relevant is True
    assert d.irrelevant_reason is None
    assert d.irrelevant_at is None
    assert d.irrelevant_by_id is None
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_is_relevant.py::test_set_irrelevant_updates_fields -v
```

Expected: FAIL — ImportError or AttributeError

- [ ] **Step 3: Add `set_decision_relevance()` to `app/services/decision_service.py`**

Add at the end of the `DecisionService` class (after `reject_decision`):

```python
    async def set_decision_relevance(
        self,
        session: AsyncSession,
        decision_id: int,
        actor,
        is_relevant: bool,
        reason: str = "",
    ) -> tuple[bool, str]:
        """Toggle is_relevant. Returns (success, hebrew_message)."""
        from datetime import datetime
        from sqlalchemy import select as _select
        from app.models import DecisionRaciRole, RaciRoleEnum

        decision = await session.get(Decision, decision_id)
        if not decision:
            return False, f"‏החלטה #{decision_id} לא נמצאה."

        # Permission: submitter, admin, or RACI Accountable
        is_accountable = False
        if not getattr(actor, "is_admin", False) and decision.submitter_id != actor.id:
            accountable_id = await session.scalar(
                _select(DecisionRaciRole.user_id).where(
                    DecisionRaciRole.decision_id == decision_id,
                    DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE,
                )
            )
            is_accountable = (accountable_id == actor.id)
            if not is_accountable:
                return False, "‏אין לך הרשאה לשנות את הרלוונטיות של החלטה זו."

        decision.is_relevant = is_relevant
        if not is_relevant:
            decision.irrelevant_reason = reason.strip() or None
            decision.irrelevant_at = datetime.utcnow()
            decision.irrelevant_by_id = actor.id
        else:
            decision.irrelevant_reason = None
            decision.irrelevant_at = None
            decision.irrelevant_by_id = None

        await session.commit()
        label = "סומנה כלא רלוונטית ⛔" if not is_relevant else "שוחזרה כרלוונטית ♻️"
        return True, f"‏החלטה #{decision_id} {label}."
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_is_relevant.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/decision_service.py tests/test_is_relevant.py
git commit -m "feat(decisions): add set_decision_relevance service method"
```

---

## Task 4: Filter Searches by `is_relevant`

**Files:**
- Modify: `app/services/decisions_menu_service.py`
- Modify: `app/services/embedding_service.py`
- Modify: `app/services/weekly_report_service.py`

### 4a. `query_decisions()` in `decisions_menu_service.py`

The function signature currently (line ~315):
```python
async def query_decisions(session, user_id, owner, type_, status, date_days, page, raci):
```

- [ ] **Step 1: Write failing test for relevance filter**

```python
# Add to tests/test_decisions_menu_service.py (or test_is_relevant.py)
@pytest.mark.asyncio
async def test_query_decisions_hides_irrelevant_by_default(async_session):
    user = await _make_user(async_session, role="EMPLOYEE")
    d_relevant = await _make_decision(async_session, user.id, is_relevant=True)
    d_irrelevant = await _make_decision(async_session, user.id, is_relevant=False)
    results, total = await query_decisions(async_session, user.id, "my", None, None, 0, 0, None)
    ids = [d.id for d in results]
    assert d_relevant.id in ids
    assert d_irrelevant.id not in ids

@pytest.mark.asyncio
async def test_query_decisions_shows_irrelevant_when_requested(async_session):
    user = await _make_user(async_session, role="EMPLOYEE")
    d_irrelevant = await _make_decision(async_session, user.id, is_relevant=False)
    results, total = await query_decisions(async_session, user.id, "my", None, None, 0, 0, None, show_irrelevant=True)
    ids = [d.id for d in results]
    assert d_irrelevant.id in ids
```

- [ ] **Step 2: Confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_decisions_menu_service.py::test_query_decisions_hides_irrelevant_by_default -v
```

- [ ] **Step 3: Edit `query_decisions()` in `decisions_menu_service.py`**

Change the signature to add `show_irrelevant: bool = False` as the last parameter. In the body, after `base_stmt` is assembled but before pagination, add:

```python
    if not show_irrelevant:
        stmt = stmt.where(Decision.is_relevant == True)
```

Also add to `get_menu_counts()` (after line 204 where `my_count` is defined):
```python
    # counts only include relevant decisions
    my_count = await session.scalar(
        select(func.count(Decision.id)).where(
            Decision.submitter_id == user_id,
            Decision.is_relevant == True,
        )
    ) or 0
    recv_count = await session.scalar(
        select(func.count(Decision.id)).where(
            Decision.id.in_(recv_subq),
            Decision.is_relevant == True,
        )
    ) or 0
```

And update the `pending` count similarly by adding `Decision.is_relevant == True` to its `where()`.

- [ ] **Step 4: Confirm tests PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_decisions_menu_service.py -v
```

### 4b. `embedding_service.py` — RAG search

- [ ] **Step 5: Add filter to `get_similar_decisions()`**

Find the `stmt` in `get_similar_decisions()` (around line 46–53). The existing filters are:
```python
.where(Decision.embedding.isnot(None))
.where(Decision.status.in_([...]))
.where(Decision.feedback_score.isnot(None))
```

Add after those:
```python
.where(Decision.is_relevant == True)
```

### 4c. `weekly_report_service.py`

- [ ] **Step 6: Add filter to weekly report query**

Find the base `stmt` in `_decisions_summary()` at line ~283:
```python
select(Decision).where(Decision.created_at >= since)
```

Change to:
```python
select(Decision).where(Decision.created_at >= since, Decision.is_relevant == True)
```

- [ ] **Step 7: Restart and smoke test**

```bash
docker-compose restart fastapi && docker logs shan-ai-fastapi --tail 20
```

- [ ] **Step 8: Commit**

```bash
git add app/services/decisions_menu_service.py app/services/embedding_service.py app/services/weekly_report_service.py tests/
git commit -m "feat(decisions): default all searches to is_relevant=True"
```

---

## Task 5: Telegram Bot — Toggle Handlers

**Files:**
- Modify: `app/services/telegram_state.py`
- Modify: `app/services/telegram_polling.py`
- Modify: `app/services/decisions_menu_service.py` (keyboard + formatter)

### 5a. State dict

- [ ] **Step 1: Add `_awaiting_irrelevant_reason` to `telegram_state.py`**

After line 8 (`_awaiting_rejection_note`), add:

```python
# { telegram_id (int): decision_id (int) }  — waiting for irrelevance reason
_awaiting_irrelevant_reason: dict[int, int] = {}
```

### 5b. Custom filter keyboard — relevance row

- [ ] **Step 2: Add relevance row to `build_custom_filter_keyboard()` in `decisions_menu_service.py`**

In `build_custom_filter_keyboard()` (lines 89–130), after the RACI row and before the action row, add:

```python
        [
            _btn("✅ רלוונטיות בלבד", "dm_cf:rel:yes", not state.get("show_irrelevant")),
            _btn("⛔ לא רלוונטיות",   "dm_cf:rel:no",  state.get("show_irrelevant") is True),
            _btn("🔄 הכל",            "dm_cf:rel:all", state.get("show_irrelevant") is None),
        ],
```

Also update `build_custom_filter_message()` to include: `"🔄 <b>רלוונטיות</b>"` in the label line.

### 5c. `format_result_line()` — irrelevant badge

- [ ] **Step 3: Add ⛔ prefix to irrelevant decisions in `format_result_line()`**

Change line 160 of `decisions_menu_service.py`:
```python
    return f"{t_emoji} <b>#{d.id}</b> — {_html.escape(summary)}  {s_emoji} {s_label}{date_part}{raci_part}"
```
To:
```python
    irrel = "⛔ " if not getattr(d, "is_relevant", True) else ""
    return f"{irrel}{t_emoji} <b>#{d.id}</b> — {_html.escape(summary)}  {s_emoji} {s_label}{date_part}{raci_part}"
```

### 5d. Callback handlers in `telegram_polling.py`

- [ ] **Step 4: Add import of new state dict at the top of `telegram_polling.py`**

Find the import from `telegram_state` (around line 15–21). Add `_awaiting_irrelevant_reason` to the import list.

- [ ] **Step 5: Add `dec_irrel` handler block in `handle_callback()` (lines 854–1483)**

Add after the `dist_*` block (around line 1124) and before the `raci_*` block:

```python
        elif action == "dec_irrel":
            # Mark decision irrelevant — prompt for reason
            decision_id = int(parts[1]) if len(parts) > 1 else 0
            _awaiting_irrelevant_reason[update.effective_user.id] = decision_id
            await query.edit_message_text(
                "‏⛔ <b>סמן כלא רלוונטי</b>\n\n"
                f"שלח סיבה קצרה להחלטה #{decision_id}\n"
                "(או שלח <code>ללא</code> לדילוג)",
                parse_mode="HTML",
            )

        elif action == "dec_rel":
            # Restore decision relevance immediately
            decision_id = int(parts[1]) if len(parts) > 1 else 0
            async with get_db() as session:
                user = await _get_user_by_telegram_id(session, update.effective_user.id)
                svc = DecisionService(session, context.application)
                success, msg = await svc.set_decision_relevance(session, decision_id, user, is_relevant=True)
            await query.edit_message_text(f"‏{msg}", parse_mode="HTML")
```

- [ ] **Step 6: Add state check in `handle_message()` (before the role/routing check)**

Find the block that checks `_awaiting_rejection_note` (around line 1181). Add a parallel block immediately before it:

```python
        if telegram_id in _awaiting_irrelevant_reason:
            decision_id = _awaiting_irrelevant_reason.pop(telegram_id)
            reason = "" if text.strip() in ("ללא", "/skip") else text.strip()
            async with get_db() as session:
                user = await _get_user_by_telegram_id(session, telegram_id)
                svc = DecisionService(session, context.application)
                success, msg = await svc.set_decision_relevance(
                    session, decision_id, user, is_relevant=False, reason=reason
                )
            await update.message.reply_text(f"‏{msg}", parse_mode="HTML")
            return
```

- [ ] **Step 7: Handle `dm_cf:rel:{value}` in `_handle_decisions_menu()` in `telegram_polling.py`**

Find the block that handles `dm_cf:d:{days}` (around line 1730–1822). Add a parallel block for the relevance filter:

```python
        elif sub == "rel":
            if val == "yes":
                state["show_irrelevant"] = False
            elif val == "no":
                state["show_irrelevant"] = True
            else:  # "all"
                state["show_irrelevant"] = None
            await query.edit_message_reply_markup(
                reply_markup=build_custom_filter_keyboard(state)
            )
            return
```

- [ ] **Step 8: Pass `show_irrelevant` to `query_decisions()` when using custom filter**

Find the call to `query_decisions()` inside the `dm_cf:show` handler (around line 1822). Change to pass `show_irrelevant=state.get("show_irrelevant", False)`.

Also update the initial state dict for `_decisions_menu_state` to include:
```python
{"owner": "all", "type": None, "status": None, "date_days": 30, "page": 0, "raci": None, "show_irrelevant": False}
```

- [ ] **Step 9: Restart and test in Telegram**

```bash
docker-compose restart fastapi
```

- Open Telegram bot → `/decisions` → Custom filter → verify new relevance row appears
- Mark a decision irrelevant via `dm_cf:rel:no` → "הצג תוצאות" → verify only irrelevant appear
- Send `dec_irrel:{id}` callback (manually or via shell) → bot asks for reason → send reason → confirm

- [ ] **Step 10: Commit**

```bash
git add app/services/telegram_state.py app/services/decisions_menu_service.py app/services/telegram_polling.py
git commit -m "feat(telegram): add dec_irrel/dec_rel callbacks and relevance filter in decisions menu"
```

---

## Task 6: Dashboard Filter and Badge

**Files:**
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/decisions.html`

### 6a. Route changes in `dashboard.py`

- [ ] **Step 1: Add `_can_toggle_relevance()` helper** (near the top of `dashboard.py` after other `_can_*` helpers)

```python
def _can_toggle_relevance(decision, user, my_raci_roles: dict | None = None) -> bool:
    if getattr(user, "is_admin", False):
        return True
    if decision.submitter_id == user.id:
        return True
    if my_raci_roles and my_raci_roles.get(decision.id) == "A":
        return True
    return False
```

- [ ] **Step 2: Add `filter_relevant` param to `decisions_page()` endpoint**

Find the `decisions_page` function (~line 1098). Add `filter_relevant: str = "yes"` to its parameters. In the query building block (after `filter_type` is applied, ~line 1132):

```python
    if filter_relevant == "yes":
        q = q.where(Decision.is_relevant == True)
    elif filter_relevant == "no":
        q = q.where(Decision.is_relevant == False)
    # else: show all
```

- [ ] **Step 3: Add relevance fields to each decision dict in the template context**

In the data-enrichment loop (~line 1136–1226), add to each decision dict:
```python
"is_relevant":          d.is_relevant,
"irrelevant_reason":    d.irrelevant_reason or "",
"irrelevant_at":        d.irrelevant_at.strftime("%d/%m/%Y") if d.irrelevant_at else "",
"can_toggle_relevance": _can_toggle_relevance(d, current_user, my_raci_roles),
```

Pass `filter_relevant=filter_relevant` into the template context.

- [ ] **Step 4: Add toggle endpoint**

Add after the existing `status` endpoint:

```python
@router.post("/decisions/{decision_id}/relevance")
async def toggle_decision_relevance(
    decision_id: int,
    is_relevant: bool = Form(...),
    reason: str = Form(default=""),
    request: Request = None,
    session: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    svc = DecisionService(session, None)
    success, msg = await svc.set_decision_relevance(session, decision_id, current_user, is_relevant, reason)
    if not success:
        raise HTTPException(status_code=403, detail=msg)
    return RedirectResponse(url="/dashboard/decisions", status_code=303)
```

### 6b. Template changes in `decisions.html`

- [ ] **Step 5: Add relevance filter dropdown to filter bar**

Find the status filter `<select>` element. After it, add:

```html
<select name="filter_relevant" class="form-select form-select-sm" style="min-width:140px;">
  <option value="yes" {% if filter_relevant == "yes" %}selected{% endif %}>✅ רלוונטיות בלבד</option>
  <option value="no"  {% if filter_relevant == "no"  %}selected{% endif %}>⛔ לא רלוונטיות</option>
  <option value=""    {% if filter_relevant == ""    %}selected{% endif %}>הכל</option>
</select>
```

- [ ] **Step 6: Add `⛔` irrelevant badge to decision rows**

Find where the status badge is rendered in the decisions table. After the status badge, add:

```html
{% if not decision.is_relevant %}
  <span class="badge bg-secondary ms-1" title="{{ decision.irrelevant_reason }}">⛔ לא רלוונטי</span>
{% endif %}
```

- [ ] **Step 7: Add toggle button in action column**

In the decision row action buttons (where edit/delete are), add:

```html
{% if decision.can_toggle_relevance %}
  {% if decision.is_relevant %}
    <form method="post" action="/dashboard/decisions/{{ decision.id }}/relevance" class="d-inline">
      <input type="hidden" name="is_relevant" value="false">
      <button type="submit" class="btn btn-sm btn-outline-secondary" 
              onclick="return confirm('סמן כלא רלוונטי?')">⛔</button>
    </form>
  {% else %}
    <form method="post" action="/dashboard/decisions/{{ decision.id }}/relevance" class="d-inline">
      <input type="hidden" name="is_relevant" value="true">
      <button type="submit" class="btn btn-sm btn-outline-success">♻️</button>
    </form>
  {% endif %}
{% endif %}
```

- [ ] **Step 8: Restart and verify in browser**

```bash
docker-compose restart fastapi
```

- Navigate to `/dashboard/decisions` — verify only relevant decisions shown by default
- Change filter to "⛔ לא רלוונטיות" — verify only irrelevant shown
- Change filter to "הכל" — verify all shown
- Click ⛔ on a decision → confirm dialog → verify `⛔ לא רלוונטי` badge appears

- [ ] **Step 9: Commit**

```bash
git add app/routers/dashboard.py app/templates/decisions.html
git commit -m "feat(dashboard): add is_relevant filter and toggle button to decisions page"
```

---

## Task 7: Document in CLAUDE.md

**Files:** Modify `CLAUDE.md`

- [ ] **Step 1: Update section 4 "Critical Operational Guardrails"**

Append to the existing BIGINT fix block:

```
- **is_relevant columns:** After any Docker rebuild, also run:
  `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE decisions ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN IF NOT EXISTS irrelevant_reason TEXT, ADD COLUMN IF NOT EXISTS irrelevant_at TIMESTAMP, ADD COLUMN IF NOT EXISTS irrelevant_by_id INTEGER REFERENCES users(id);"`
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add is_relevant ALTER TABLE to post-rebuild checklist"
```

---

## Verification Checklist

**Database:**
- [ ] `\d decisions` shows all 4 new columns
- [ ] All existing decisions have `is_relevant = true`

**Telegram:**
- [ ] `/decisions` → shortcut lists hide irrelevant decisions
- [ ] Custom filter → relevance row shows, `✅ רלוונטיות בלבד ✓` is default
- [ ] Mark a decision irrelevant: custom filter `⛔` + "הצג תוצאות" shows `⛔` badge in list
- [ ] `dec_irrel:{id}` callback → bot asks for reason → message sent → decision marked
- [ ] `dec_rel:{id}` callback → decision restored immediately
- [ ] Non-authorized user gets permission error message

**Dashboard:**
- [ ] Default view shows only `is_relevant=true` decisions
- [ ] Filter "הכל" shows all including irrelevant (with `⛔ לא רלוונטי` badge)
- [ ] ⛔ button marks irrelevant; ♻️ button restores; badge disappears
- [ ] `reason` shows in badge `title` tooltip on hover

**RAG:**
- [ ] Mark a decision irrelevant → send a similar question via `/ask` → irrelevant decision NOT cited in similar decisions
