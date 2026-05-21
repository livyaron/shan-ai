# Projects Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/projects` Telegram command (+ "פרוייקטים" keyword) that lets users browse, filter, and drill into project cards via inline keyboards — mirroring the existing `/decisions` menu pattern.

**Architecture:** New `app/services/projects_menu_service.py` handles all text formatting, keyboard building, and DB queries (mirrors `decisions_menu_service.py`). Two new state dicts added to `telegram_state.py` for filter state and back-nav origin. `telegram_polling.py` wires the command, keyword, and `pm:` / `pm_cf:` callback routing into a new `_handle_projects_menu()` method.

**Tech Stack:** python-telegram-bot v21 InlineKeyboardMarkup, SQLAlchemy async, PostgreSQL. All tests use the existing `db_session` pytest-asyncio fixture (connects to the running Docker postgres).

---

## File Map

| File | Change |
|------|--------|
| `app/services/decisions_menu_service.py` | Update `get_menu_shortcut_keyboard()` — add "📁 פרוייקטים" button |
| `app/services/telegram_state.py` | Add `_projects_menu_state` + `_projects_detail_origin` |
| `app/services/projects_menu_service.py` | Create — keyboards, formatters, DB queries |
| `tests/test_projects_menu_service.py` | Create — pure-function + DB query tests |
| `app/services/telegram_polling.py` | Add command handler, keyword, callback routing, `_handle_projects_menu()` |

---

## Project model fields (read-only reference, `app/models.py:298`)

```python
id                    # int PK
project_identifier    # str — unique code (= מזהה)
name                  # str
project_type          # str — סוג (הקמה / הרחבה / ניידת / קדם ניידות / שוש)
stage                 # str — שלב
manager               # str — מנה"פ
weekly_report_brief   # str — סיכום שבועי (short version)
to_handle             # str — לטיפול
dev_plan_date         # date — תאריך ת"פ
estimated_finish_date # date — תאריך חישמול
is_active             # bool
```

---

## Callback data reference (all ≤ 64 bytes)

| Pattern | Meaning |
|---------|---------|
| `pm:menu` | Open / return to main menu |
| `pm:noop` | Page indicator button (no-op) |
| `pm:{shortcut}:{page}` | Results list — shortcut: late/handle/quarter/all/active |
| `pm:d:{id}:{shortcut}:{page}` | Project card — `d` keeps callback short |
| `pm_cf:open` | Open custom filter panel |
| `pm_cf:stage:{idx\|all}` | Toggle stage by list index |
| `pm_cf:type:{idx\|all}` | Toggle type by list index |
| `pm_cf:mgr:{idx\|all}` | Toggle manager by list index |
| `pm_cf:th:{idx\|all}` | Toggle to_handle by list index |
| `pm_cf:date:{val\|all}` | Toggle date: late/q_cur/q_next/2026/2027 |
| `pm_cf:show` | Apply filters → results |
| `pm_cf:pg:{page}` | Custom filter pagination |
| `pm_cf:back` | Clear state → main menu |

---

## Task 0: Add "📁 פרוייקטים" to the shortcut keyboard

The `get_menu_shortcut_keyboard()` in `decisions_menu_service.py` is attached to decision process notifications. Add a "📁 פרוייקטים" button alongside "📋 ההחלטות שלי" so users can jump to either menu from any notification.

**Files:**
- Modify: `app/services/decisions_menu_service.py:60-64`
- Test: `tests/test_decisions_menu_service.py` (append one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_decisions_menu_service.py`:

```python
def test_get_menu_shortcut_keyboard_has_projects_button():
    kb = get_menu_shortcut_keyboard()
    all_btns = [btn for row in kb.inline_keyboard for btn in row]
    assert any("פרוייקטים" in b.text for b in all_btns)
    assert any("pm:menu" == b.callback_data for b in all_btns)
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_decisions_menu_service.py::test_get_menu_shortcut_keyboard_has_projects_button -v
```

Expected: FAIL — no projects button yet.

- [ ] **Step 3: Update `get_menu_shortcut_keyboard()` in `app/services/decisions_menu_service.py`**

Current (line 60-64):
```python
def get_menu_shortcut_keyboard() -> InlineKeyboardMarkup:
    """Single-button keyboard appended to process() confirmation messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 ההחלטות שלי", callback_data="dm:my:0"),
    ]])
```

Replace with:
```python
def get_menu_shortcut_keyboard() -> InlineKeyboardMarkup:
    """Two-button keyboard appended to process() confirmation messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 ההחלטות שלי", callback_data="dm:my:0"),
        InlineKeyboardButton("📁 פרוייקטים",   callback_data="pm:menu"),
    ]])
```

- [ ] **Step 4: Run test**

```bash
python -m pytest tests/test_decisions_menu_service.py::test_get_menu_shortcut_keyboard_has_projects_button -v
```

Expected: PASS.

- [ ] **Step 5: Run full decisions test suite to check for regressions**

```bash
python -m pytest tests/test_decisions_menu_service.py -v 2>&1 | tail -20
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/decisions_menu_service.py tests/test_decisions_menu_service.py
git commit -m "feat(projects): add 📁 פרוייקטים button to shortcut keyboard"
```

---

## Task 1: Add state dicts to telegram_state.py

**Files:**
- Modify: `app/services/telegram_state.py:35` (append after `_decisions_menu_state`)

- [ ] **Step 1: Append the two new dicts**

Open `app/services/telegram_state.py` and add at the end:

```python
# { telegram_id (int): filter state dict }  — active projects custom-filter session
# value: { "stage": str|None, "type": str|None, "mgr": str|None, "th": str|None, "date": str|None }
_projects_menu_state: dict[int, dict] = {}

# { telegram_id (int): (shortcut_key, page) }  — origin for back-nav from detail card
# shortcut_key is one of: "late"|"handle"|"quarter"|"all"|"active"|"cf"
_projects_detail_origin: dict[int, tuple[str, int]] = {}
```

- [ ] **Step 2: Verify import works**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -c "from app.services.telegram_state import _projects_menu_state, _projects_detail_origin; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/services/telegram_state.py
git commit -m "feat(projects): add _projects_menu_state and _projects_detail_origin to telegram_state"
```

---

## Task 2: Create projects_menu_service.py — keyboards + text formatters

**Files:**
- Create: `app/services/projects_menu_service.py`
- Test: `tests/test_projects_menu_service.py`

The pure functions (no DB) go in first. DB queries are added in Task 3.

- [ ] **Step 1: Write failing tests for pure functions**

Create `tests/test_projects_menu_service.py`:

```python
import pytest
from datetime import date
from sqlalchemy.orm import configure_mappers, class_mapper
from app.models import Project

configure_mappers()
_project_mgr = class_mapper(Project).class_manager


def _make_project(**kwargs):
    defaults = dict(
        id=1,
        project_identifier="TEST-001",
        name="פרוייקט בדיקה",
        project_type="הקמה",
        stage="הרכבה חשמלית",
        manager="כוכבה כהן",
        weekly_report_brief="הרכבת הלוח הושלמה.",
        to_handle=None,
        dev_plan_date=date(2025, 1, 1),
        estimated_finish_date=date(2025, 3, 1),
        is_active=True,
    )
    defaults.update(kwargs)
    p = _project_mgr.new_instance()
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


def test_format_project_line_truncates_name():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(name="א" * 50, estimated_finish_date=date(2025, 6, 1))
    line = format_project_line(p)
    assert "…" in line


def test_format_project_line_short_name_no_ellipsis():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(name="קצר", estimated_finish_date=date(2025, 6, 1))
    line = format_project_line(p)
    assert "…" not in line


def test_format_project_line_includes_stage_and_date():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(stage="בדיקות", estimated_finish_date=date(2026, 3, 15))
    line = format_project_line(p)
    assert "בדיקות" in line
    assert "03/26" in line


def test_format_project_line_no_date():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(estimated_finish_date=None)
    line = format_project_line(p)
    assert "📁" in line  # still renders without crashing


def test_format_results_message_empty():
    from app.services.projects_menu_service import format_results_message
    msg = format_results_message("📋 כל הפרוייקטים", [], 0, 0)
    assert "לא נמצאו" in msg


def test_format_results_message_header():
    from app.services.projects_menu_service import format_results_message, _make_project
    projects = [_make_project(id=i + 1) for i in range(3)]
    msg = format_results_message("📋 תוצאות", projects, 3, 0)
    assert "3" in msg
    assert "1–3" in msg


def test_build_project_card_overdue():
    from app.services.projects_menu_service import build_project_card
    import datetime
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    p = _make_project(estimated_finish_date=yesterday)
    card = build_project_card(p)
    assert "🔴" in card


def test_build_project_card_not_overdue():
    from app.services.projects_menu_service import build_project_card
    import datetime
    future = datetime.date.today() + datetime.timedelta(days=30)
    p = _make_project(estimated_finish_date=future)
    card = build_project_card(p)
    # Should not have overdue indicator on the date line
    assert "🔴 באיחור" not in card


def test_build_project_card_no_to_handle():
    from app.services.projects_menu_service import build_project_card
    p = _make_project(to_handle=None)
    card = build_project_card(p)
    assert "—" in card


def test_get_menu_keyboard_six_buttons():
    from app.services.projects_menu_service import get_menu_keyboard
    kb = get_menu_keyboard()
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert len(all_btns) == 6


def test_build_results_keyboard_no_nav_single_page():
    from app.services.projects_menu_service import build_results_keyboard
    kb = build_results_keyboard("late", 0, 5)
    rows = kb.inline_keyboard
    # Only menu row, no nav row
    assert len(rows) == 1


def test_build_results_keyboard_has_nav_multipage():
    from app.services.projects_menu_service import build_results_keyboard
    kb = build_results_keyboard("late", 0, 15)
    rows = kb.inline_keyboard
    assert len(rows) == 2  # nav row + menu row


def test_build_custom_filter_keyboard_marks_active_stage():
    from app.services.projects_menu_service import build_custom_filter_keyboard
    state = {"stage": "בדיקות", "type": None, "mgr": None, "th": None, "date": None}
    filter_options = {
        "stage": ["תכנון", "בדיקות"],
        "type": ["הקמה"],
        "mgr": ["כוכבה כהן"],
        "th": ["חסם לטיפול מנהל אגף"],
    }
    kb = build_custom_filter_keyboard(state, filter_options)
    flat = [b.text for row in kb.inline_keyboard for b in row]
    assert any("בדיקות" in t and "✓" in t for t in flat)
    assert not any("תכנון" in t and "✓" in t for t in flat)


def test_build_custom_filter_keyboard_th_strips_prefix():
    from app.services.projects_menu_service import build_custom_filter_keyboard
    state = {"stage": None, "type": None, "mgr": None, "th": None, "date": None}
    filter_options = {
        "stage": [],
        "type": [],
        "mgr": [],
        "th": ["חסם לטיפול מנהל אגף", "חסם לטיפול מנהל מגזר ביצוע"],
    }
    kb = build_custom_filter_keyboard(state, filter_options)
    flat = [b.text for row in kb.inline_keyboard for b in row]
    assert any("מנהל אגף" in t for t in flat)
    assert not any("חסם לטיפול מנהל אגף" in t for t in flat)


def test_shortcut_presets_keys():
    from app.services.projects_menu_service import SHORTCUT_PRESETS
    for key in ("late", "handle", "quarter", "all", "active"):
        assert key in SHORTCUT_PRESETS
        assert "title" in SHORTCUT_PRESETS[key]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_projects_menu_service.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `projects_menu_service` doesn't exist yet.

- [ ] **Step 3: Create `app/services/projects_menu_service.py` with pure functions**

```python
"""Projects menu — keyboards, formatters, and DB queries."""

import html as _html
import datetime

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Project

# ── Constants ──────────────────────────────────────────────────────────────

ACTIVE_STAGES = [
    "עבודה אזרחית",
    "הרכבה חשמלית",
    "הרכבה חשמלית ובדיקות",
    "בדיקות",
]

DATE_OPTIONS = [
    ("late",   "🔴 באיחור"),
    ("q_cur",  "רבעון נוכחי"),
    ("q_next", "רבעון הבא"),
    ("2026",   "2026"),
    ("2027",   "2027"),
]

SHORTCUT_PRESETS: dict[str, dict] = {
    "late":    {"title": "🔴 פרוייקטים באיחור",  "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": "late"},
    "handle":  {"title": "📌 לטיפול",             "stages": None, "type_": None, "mgr": None, "th": "__any__", "date_filter": None},
    "quarter": {"title": "📅 פרוייקטי הרבעון",    "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": "q_cur"},
    "all":     {"title": "📋 כל הפרוייקטים",      "stages": None, "type_": None, "mgr": None, "th": None, "date_filter": None},
    "active":  {"title": "🏗️ פרוייקטים בביצוע",  "stages": ACTIVE_STAGES, "type_": None, "mgr": None, "th": None, "date_filter": None},
}


def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# ── Keyboards ──────────────────────────────────────────────────────────────

def get_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 באיחור",  callback_data="pm:late:0"),
            InlineKeyboardButton("📌 לטיפול",  callback_data="pm:handle:0"),
            InlineKeyboardButton("📅 הרבעון",  callback_data="pm:quarter:0"),
        ],
        [
            InlineKeyboardButton("📋 הכל",     callback_data="pm:all:0"),
            InlineKeyboardButton("🏗️ בביצוע", callback_data="pm:active:0"),
            InlineKeyboardButton("🔍 סינון",   callback_data="pm_cf:open"),
        ],
    ])


def build_results_keyboard(shortcut: str, page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm:{shortcut}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm:{shortcut}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_custom_results_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ הקודם", callback_data=f"pm_cf:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(f"עמוד {page + 1}/{total_pages}", callback_data="pm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("הבא ▶", callback_data=f"pm_cf:pg:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
    return InlineKeyboardMarkup(rows)


def build_detail_back_keyboard(shortcut: str, page: int) -> InlineKeyboardMarkup:
    if shortcut == "cf":
        back_cd = "pm_cf:open"
    else:
        back_cd = f"pm:{shortcut}:{page}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 חזרה לרשימה", callback_data=back_cd),
            InlineKeyboardButton("🏠 תפריט",        callback_data="pm:menu"),
        ]
    ])


def build_custom_filter_keyboard(state: dict, filter_options: dict) -> InlineKeyboardMarkup:
    def _btn(label: str, cd: str, active: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{label} ✓" if active else label, callback_data=cd)

    rows = []

    # Stage — wrap at 3 per row
    stage_btns = [_btn("הכל", "pm_cf:stage:all", state["stage"] is None)]
    for idx, val in enumerate(filter_options.get("stage", [])):
        stage_btns.append(_btn(val, f"pm_cf:stage:{idx}", state["stage"] == val))
    for chunk in _chunk(stage_btns, 3):
        rows.append(chunk)

    # Type — wrap at 4 per row
    type_btns = [_btn("הכל", "pm_cf:type:all", state["type"] is None)]
    for idx, val in enumerate(filter_options.get("type", [])):
        type_btns.append(_btn(val, f"pm_cf:type:{idx}", state["type"] == val))
    for chunk in _chunk(type_btns, 4):
        rows.append(chunk)

    # Manager — "הכל" alone, then 2 per row
    rows.append([_btn("הכל", "pm_cf:mgr:all", state["mgr"] is None)])
    mgr_btns = []
    for idx, val in enumerate(filter_options.get("mgr", [])):
        mgr_btns.append(_btn(val, f"pm_cf:mgr:{idx}", state["mgr"] == val))
    for chunk in _chunk(mgr_btns, 2):
        rows.append(chunk)

    # to_handle — strip "חסם לטיפול " prefix for display, wrap at 2 per row
    th_btns = [_btn("הכל", "pm_cf:th:all", state["th"] is None)]
    for idx, val in enumerate(filter_options.get("th", [])):
        label = val.replace("חסם לטיפול ", "")
        th_btns.append(_btn(label, f"pm_cf:th:{idx}", state["th"] == val))
    for chunk in _chunk(th_btns, 2):
        rows.append(chunk)

    # Date — wrap at 3
    date_btns = [_btn("הכל", "pm_cf:date:all", state["date"] is None)]
    for key, label in DATE_OPTIONS:
        date_btns.append(_btn(label, f"pm_cf:date:{key}", state["date"] == key))
    for chunk in _chunk(date_btns, 3):
        rows.append(chunk)

    rows.append([
        InlineKeyboardButton("🔍 הצג תוצאות", callback_data="pm_cf:show"),
        InlineKeyboardButton("🔙 תפריט",       callback_data="pm_cf:back"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Formatters ─────────────────────────────────────────────────────────────

def get_menu_text(total: int | None = None) -> str:
    header = "‏📁 <b>פרוייקטים</b>"
    if total is not None:
        header += f"\n<i>סה\"כ: {total} פרוייקטים פעילים</i>"
    return header + "\n\nבחר תצוגה מהירה:"


def format_project_line(p: Project) -> str:
    name = p.name or ""
    if len(name) > 35:
        name = name[:35] + "…"
    date_str = ""
    if p.estimated_finish_date:
        date_str = p.estimated_finish_date.strftime("%m/%y")
    stage_part = p.stage or ""
    tail = f"  |  {stage_part} · {date_str}" if date_str else f"  |  {stage_part}"
    return f"📁 <b>#{p.id}</b> · {_html.escape(name)}{tail}"


def format_results_message(title: str, projects: list, total: int, page: int) -> str:
    if not projects:
        return f"‏<b>{_html.escape(title)}</b>\n\nלא נמצאו פרוייקטים."
    from_n = page * 10 + 1
    to_n   = page * 10 + len(projects)
    lines  = [
        f"‏<b>{_html.escape(title)}</b> ({total})",
        f"<i>מציג {from_n}–{to_n} מתוך {total}</i>",
        "──────────────────",
    ]
    lines.extend(format_project_line(p) for p in projects)
    return "\n".join(lines)


def build_project_card(p: Project) -> str:
    today = datetime.date.today()
    finish_str = ""
    overdue = False
    if p.estimated_finish_date:
        finish_str = p.estimated_finish_date.strftime("%m/%Y")
        overdue = p.estimated_finish_date < today

    dev_str = p.dev_plan_date.strftime("%m/%Y") if p.dev_plan_date else "—"
    to_handle = p.to_handle or "—"
    summary = p.weekly_report_brief or "אין"

    date_line = finish_str
    if overdue:
        date_line += " 🔴 באיחור"

    return (
        f"‏📁 <b>פרוייקט #{p.id}</b>\n"
        f"<b>{_html.escape(p.name or '')}</b>\n"
        "──────────────────\n"
        f"🆔 <b>מזהה:</b> {_html.escape(p.project_identifier or '')}\n"
        f"🏷️ <b>סוג:</b> {_html.escape(p.project_type or '—')}\n"
        f"🏗️ <b>שלב:</b> {_html.escape(p.stage or '—')}\n"
        f"🧑‍💼 <b>מנה\"פ:</b> {_html.escape(p.manager or '—')}\n"
        f"📅 <b>תאריך חישמול:</b> {date_line or '—'}\n"
        f"📅 <b>תאריך ת\"פ:</b> {dev_str}\n"
        "──────────────────\n"
        f"📌 <b>לטיפול:</b>\n"
        f"{_html.escape(to_handle)}\n"
        "──────────────────\n"
        f"📋 <b>סיכום שבועי:</b>\n"
        f"<i>{_html.escape(summary)}</i>"
    )


def build_custom_filter_message() -> str:
    return (
        "‏🔍 <b>סינון פרוייקטים</b>\n\n"
        "בחר פילטרים ולחץ הצג:\n"
        "──────────────────\n"
        "🏗️ <b>שלב</b> · 🏷️ <b>סוג</b> · 🧑‍💼 <b>מנהל</b> · 📌 <b>לטיפול</b> · 📅 <b>תאריך</b>"
    )
```

- [ ] **Step 4: Run the pure-function tests**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_projects_menu_service.py -v -k "not db_session and not query" 2>&1 | tail -20
```

Expected: All pure-function tests pass. The `test_format_results_message_header` test imports `_make_project` from the module — remove that import from the test file since it's defined locally in the test file itself:

Check test line `from app.services.projects_menu_service import format_results_message, _make_project` — the test file defines its own `_make_project`, so that import line should just be `from app.services.projects_menu_service import format_results_message`. The test as written above is already correct (no such import).

Run:

```bash
python -m pytest tests/test_projects_menu_service.py::test_get_menu_keyboard_six_buttons tests/test_projects_menu_service.py::test_format_project_line_truncates_name tests/test_projects_menu_service.py::test_build_project_card_overdue tests/test_projects_menu_service.py::test_build_custom_filter_keyboard_marks_active_stage tests/test_projects_menu_service.py::test_build_custom_filter_keyboard_th_strips_prefix tests/test_projects_menu_service.py::test_shortcut_presets_keys -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/services/projects_menu_service.py tests/test_projects_menu_service.py
git commit -m "feat(projects): keyboards and text formatters for projects menu"
```

---

## Task 3: Add DB query functions to projects_menu_service.py

**Files:**
- Modify: `app/services/projects_menu_service.py` (append DB section)
- Test: `tests/test_projects_menu_service.py` (append DB tests)

- [ ] **Step 1: Write failing DB tests (append to test file)**

Append to `tests/test_projects_menu_service.py`:

```python
import pytest
from datetime import date, timedelta
from app.models import Project
from app.services.projects_menu_service import (
    get_filter_options, get_total_active, query_projects,
)


def _db_project(db_session, **kwargs):
    defaults = dict(
        project_identifier=f"TEST-{id(kwargs)}",
        name="פרוייקט",
        project_type="הקמה",
        stage="תכנון",
        manager="מנהל",
        is_active=True,
        estimated_finish_date=None,
        to_handle=None,
    )
    defaults.update(kwargs)
    p = Project(**defaults)
    db_session.add(p)
    return p


@pytest.mark.asyncio
async def test_get_total_active(db_session):
    _db_project(db_session, project_identifier="ACT-1", is_active=True)
    _db_project(db_session, project_identifier="ACT-2", is_active=True)
    _db_project(db_session, project_identifier="INACT-1", is_active=False)
    await db_session.flush()
    total = await get_total_active(db_session)
    assert total >= 2  # at least our 2 active ones


@pytest.mark.asyncio
async def test_get_filter_options_returns_distinct(db_session):
    _db_project(db_session, project_identifier="FO-1", stage="תכנון", project_type="הקמה", manager="א", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="FO-2", stage="תכנון", project_type="הרחבה", manager="ב", to_handle=None)
    _db_project(db_session, project_identifier="FO-3", stage="בדיקות", project_type="הקמה", manager="א", to_handle="חסם לטיפול מנהל אגף")
    await db_session.flush()
    opts = await get_filter_options(db_session)
    assert "תכנון" in opts["stage"]
    assert "בדיקות" in opts["stage"]
    assert len([s for s in opts["stage"] if s == "תכנון"]) == 1  # deduped
    assert "הקמה" in opts["type"]
    assert "הרחבה" in opts["type"]
    assert "א" in opts["mgr"]
    assert "ב" in opts["mgr"]
    assert "חסם לטיפול מנהל אגף" in opts["th"]
    assert len([t for t in opts["th"] if t == "חסם לטיפול מנהל אגף"]) == 1


@pytest.mark.asyncio
async def test_query_projects_all(db_session):
    _db_project(db_session, project_identifier="QP-1")
    _db_project(db_session, project_identifier="QP-2")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th=None, date_filter=None, page=0)
    assert total >= 2


@pytest.mark.asyncio
async def test_query_projects_late_filter(db_session):
    yesterday = date.today() - timedelta(days=1)
    future = date.today() + timedelta(days=30)
    _db_project(db_session, project_identifier="LATE-1", estimated_finish_date=yesterday)
    _db_project(db_session, project_identifier="FUTURE-1", estimated_finish_date=future)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th=None, date_filter="late", page=0)
    ids = [p.project_identifier for p in results]
    assert "LATE-1" in ids
    assert "FUTURE-1" not in ids


@pytest.mark.asyncio
async def test_query_projects_stage_filter(db_session):
    _db_project(db_session, project_identifier="STG-1", stage="הרכבה חשמלית")
    _db_project(db_session, project_identifier="STG-2", stage="תכנון")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=["הרכבה חשמלית"], type_=None, mgr=None, th=None, date_filter=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "STG-1" in ids
    assert "STG-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_handle_any(db_session):
    _db_project(db_session, project_identifier="TH-1", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="TH-2", to_handle=None)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th="__any__", date_filter=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "TH-1" in ids
    assert "TH-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_pagination(db_session):
    for i in range(12):
        _db_project(db_session, project_identifier=f"PAG-{i}", stage="תכנון")
    await db_session.flush()
    results_p0, total = await query_projects(db_session, stages=["תכנון"], type_=None, mgr=None, th=None, date_filter=None, page=0)
    results_p1, _ = await query_projects(db_session, stages=["תכנון"], type_=None, mgr=None, th=None, date_filter=None, page=1)
    assert total >= 12
    assert len(results_p0) == 10
    assert len(results_p1) >= 2
```

- [ ] **Step 2: Run DB tests to confirm they fail**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_projects_menu_service.py::test_get_total_active -v
```

Expected: `ImportError: cannot import name 'get_filter_options' from 'app.services.projects_menu_service'`

- [ ] **Step 3: Append DB query functions to `app/services/projects_menu_service.py`**

Append after the last line of the file:

```python

# ── DB Queries ─────────────────────────────────────────────────────────────

async def get_total_active(session: AsyncSession) -> int:
    result = await session.scalar(
        select(func.count(Project.id)).where(Project.is_active.is_(True))
    )
    return result or 0


async def get_filter_options(session: AsyncSession) -> dict:
    """Return distinct non-null values for each filter dimension."""
    async def _distinct(col):
        rows = await session.scalars(
            select(distinct(col)).where(col.isnot(None)).order_by(col)
        )
        return list(rows.all())

    return {
        "stage": await _distinct(Project.stage),
        "type":  await _distinct(Project.project_type),
        "mgr":   await _distinct(Project.manager),
        "th":    await _distinct(Project.to_handle),
    }


async def query_projects(
    session: AsyncSession,
    stages: list[str] | None,
    type_: str | None,
    mgr: str | None,
    th: str | None,
    date_filter: str | None,
    page: int,
) -> tuple[list[Project], int]:
    """Query active projects with optional filters. Returns (rows, total)."""
    base = select(Project).where(Project.is_active.is_(True))

    if stages is not None:
        base = base.where(Project.stage.in_(stages))
    if type_ is not None:
        base = base.where(Project.project_type == type_)
    if mgr is not None:
        base = base.where(Project.manager == mgr)
    if th == "__any__":
        base = base.where(Project.to_handle.isnot(None), Project.to_handle != "")
    elif th is not None:
        base = base.where(Project.to_handle == th)

    if date_filter == "late":
        base = base.where(Project.estimated_finish_date < datetime.date.today())
    elif date_filter == "q_cur":
        today = datetime.date.today()
        q_start = datetime.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        q_month_end = ((today.month - 1) // 3) * 3 + 3
        q_end = datetime.date(today.year, q_month_end, 1) + datetime.timedelta(days=31)
        q_end = q_end.replace(day=1) - datetime.timedelta(days=1)
        base = base.where(
            Project.estimated_finish_date >= q_start,
            Project.estimated_finish_date <= q_end,
        )
    elif date_filter == "q_next":
        today = datetime.date.today()
        cur_q = (today.month - 1) // 3
        next_q = cur_q + 1
        if next_q > 3:
            next_q = 0
            year = today.year + 1
        else:
            year = today.year
        nq_start = datetime.date(year, next_q * 3 + 1, 1)
        nq_month_end = next_q * 3 + 3
        if nq_month_end > 12:
            nq_end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            nq_end = datetime.date(year, nq_month_end, 1) + datetime.timedelta(days=31)
            nq_end = nq_end.replace(day=1) - datetime.timedelta(days=1)
        base = base.where(
            Project.estimated_finish_date >= nq_start,
            Project.estimated_finish_date <= nq_end,
        )
    elif date_filter in ("2026", "2027"):
        yr = int(date_filter)
        base = base.where(
            Project.estimated_finish_date >= datetime.date(yr, 1, 1),
            Project.estimated_finish_date <= datetime.date(yr, 12, 31),
        )

    count_q = select(func.count()).select_from(base.subquery())
    total: int = await session.scalar(count_q) or 0

    rows_q = base.order_by(Project.id.desc()).offset(page * 10).limit(10)
    projects = list((await session.scalars(rows_q)).all())

    return projects, total
```

- [ ] **Step 4: Run all DB tests**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_projects_menu_service.py -v 2>&1 | tail -30
```

Expected: All tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/services/projects_menu_service.py tests/test_projects_menu_service.py
git commit -m "feat(projects): DB query functions — get_filter_options, get_total_active, query_projects"
```

---

## Task 4: Wire up handlers in telegram_polling.py

**Files:**
- Modify: `app/services/telegram_polling.py`

Four separate edits — do them in order.

### 4a: Register the `/projects` command handler

- [ ] **Step 1: Find the `add_handler` block** (around line 97)

Current code at ~line 97:
```python
self.application.add_handler(CommandHandler("decisions", self.handle_decisions))
self.application.add_handler(CommandHandler("ask", self.handle_ask))
```

Replace with:
```python
self.application.add_handler(CommandHandler("decisions", self.handle_decisions))
self.application.add_handler(CommandHandler("projects", self.handle_projects))
self.application.add_handler(CommandHandler("ask", self.handle_ask))
```

### 4b: Add `handle_projects()` command method

- [ ] **Step 2: Find `handle_decisions()` method** (around line 258). Add `handle_projects()` immediately after the closing `return` of `handle_decisions()`:

```python
    async def handle_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/projects — open the projects menu."""
        from app.services.projects_menu_service import get_menu_keyboard, get_menu_text, get_total_active
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
                return
            total = await get_total_active(session)
        await update.message.reply_text(
            get_menu_text(total),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
```

### 4c: Add "פרוייקטים" keyword trigger in `handle_message()`

- [ ] **Step 3: Find the "החלטות" keyword block** (around line 469):

```python
            if text.strip() == "החלטות":
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

Add the projects keyword immediately after the `return` of that block:

```python
            if text.strip() == "פרוייקטים":
                if user.role:
                    from app.services.projects_menu_service import get_menu_keyboard as _pm_kb, get_menu_text as _pm_txt, get_total_active
                    async with async_session_maker() as _pm_s:
                        _pm_total = await get_total_active(_pm_s)
                    await update.message.reply_text(
                        _pm_txt(_pm_total),
                        parse_mode="HTML",
                        reply_markup=_pm_kb(),
                    )
                return
```

### 4d: Add `pm:` / `pm_cf:` routing in `handle_callback()`

- [ ] **Step 4: Find the `dm:` routing block** (around line 645):

```python
        # Decisions menu — handle before int-based action parsing
        if data.startswith("dm:") or data.startswith("dm_cf:"):
            async with async_session_maker() as _dm_session:
                _dm_user = await _dm_session.scalar(select(User).where(User.telegram_id == telegram_id))
            if _dm_user:
                await self._handle_decisions_menu(query, context, data, telegram_id, _dm_user)
            return
```

Add the projects routing immediately after that `return`:

```python
        # Projects menu
        if data.startswith("pm:") or data.startswith("pm_cf:"):
            async with async_session_maker() as _pm_session:
                _pm_user = await _pm_session.scalar(select(User).where(User.telegram_id == telegram_id))
            if _pm_user:
                await self._handle_projects_menu(query, context, data, telegram_id, _pm_user)
            return
```

### 4e: Add `_handle_projects_menu()` method

- [ ] **Step 5: Add the method after `_handle_decisions_menu()`** (around line 1287, before the `# Webhook lifecycle` section):

```python
    async def _handle_projects_menu(
        self,
        query,
        context: ContextTypes.DEFAULT_TYPE,
        data: str,
        telegram_id: int,
        user,
    ) -> None:
        """Handle all pm:* and pm_cf:* callback actions."""
        from app.services.projects_menu_service import (
            get_menu_keyboard, get_menu_text, get_total_active,
            build_results_keyboard, build_custom_results_keyboard,
            build_custom_filter_keyboard, build_custom_filter_message,
            build_detail_back_keyboard, build_project_card,
            format_results_message, format_project_line,
            query_projects, get_filter_options, SHORTCUT_PRESETS,
        )
        from app.services.telegram_state import _projects_menu_state, _projects_detail_origin

        # ── pm:noop ────────────────────────────────────────────────────────
        if data == "pm:noop":
            return

        # ── pm:menu ────────────────────────────────────────────────────────
        if data == "pm:menu":
            _projects_menu_state.pop(telegram_id, None)
            _projects_detail_origin.pop(telegram_id, None)
            async with async_session_maker() as session:
                total = await get_total_active(session)
            await query.edit_message_text(
                get_menu_text(total),
                parse_mode="HTML",
                reply_markup=get_menu_keyboard(),
            )
            return

        # ── pm:{shortcut}:{page} ───────────────────────────────────────────
        if data.startswith("pm:") and not data.startswith("pm:d:"):
            parts = data.split(":")
            shortcut = parts[1] if len(parts) > 1 else ""
            try:
                page = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                page = 0
            preset = SHORTCUT_PRESETS.get(shortcut)
            if not preset:
                return
            async with async_session_maker() as session:
                projects, total = await query_projects(
                    session,
                    stages=preset["stages"],
                    type_=preset["type_"],
                    mgr=preset["mgr"],
                    th=preset["th"],
                    date_filter=preset["date_filter"],
                    page=page,
                )
            # Build result rows as buttons for drill-down
            item_rows = []
            for p in projects:
                line = format_project_line(p)
                # strip HTML tags for button label
                import re
                label = re.sub(r"<[^>]+>", "", line)[:60]
                item_rows.append([InlineKeyboardButton(label, callback_data=f"pm:d:{p.id}:{shortcut}:{page}")])
            kb = build_results_keyboard(shortcut, page, total)
            # Prepend item rows to nav keyboard
            full_kb = InlineKeyboardMarkup(item_rows + list(kb.inline_keyboard))
            _projects_detail_origin[telegram_id] = (shortcut, page)
            await query.edit_message_text(
                format_results_message(preset["title"], projects, total, page),
                parse_mode="HTML",
                reply_markup=full_kb,
            )
            return

        # ── pm:d:{id}:{shortcut}:{page} — project detail card ─────────────
        if data.startswith("pm:d:"):
            parts = data.split(":")
            try:
                project_id = int(parts[2])
            except (ValueError, IndexError):
                return
            shortcut = parts[3] if len(parts) > 3 else "all"
            try:
                page = int(parts[4]) if len(parts) > 4 else 0
            except ValueError:
                page = 0
            async with async_session_maker() as session:
                p = await session.get(Project, project_id)
            if not p:
                await query.edit_message_text("‏⚠️ פרוייקט לא נמצא.", reply_markup=get_menu_keyboard())
                return
            _projects_detail_origin[telegram_id] = (shortcut, page)
            await query.edit_message_text(
                build_project_card(p),
                parse_mode="HTML",
                reply_markup=build_detail_back_keyboard(shortcut, page),
            )
            return

        # ── pm_cf:open ─────────────────────────────────────────────────────
        if data == "pm_cf:open":
            _projects_menu_state[telegram_id] = {
                "stage": None, "type": None, "mgr": None, "th": None, "date": None,
            }
            async with async_session_maker() as session:
                filter_options = await get_filter_options(session)
            await query.edit_message_text(
                build_custom_filter_message(),
                parse_mode="HTML",
                reply_markup=build_custom_filter_keyboard(_projects_menu_state[telegram_id], filter_options),
            )
            return

        # ── pm_cf:back ─────────────────────────────────────────────────────
        if data == "pm_cf:back":
            _projects_menu_state.pop(telegram_id, None)
            _projects_detail_origin.pop(telegram_id, None)
            async with async_session_maker() as session:
                total = await get_total_active(session)
            await query.edit_message_text(
                get_menu_text(total),
                parse_mode="HTML",
                reply_markup=get_menu_keyboard(),
            )
            return

        # ── pm_cf:* toggle callbacks ───────────────────────────────────────
        if data.startswith("pm_cf:"):
            parts = data.split(":")
            sub = parts[1] if len(parts) > 1 else ""
            state = _projects_menu_state.get(telegram_id)

            async def _rerender_filter():
                async with async_session_maker() as _s:
                    _opts = await get_filter_options(_s)
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state, _opts)
                )

            if sub in ("stage", "type", "mgr", "th") and state is not None and len(parts) > 2:
                val_raw = parts[2]
                if val_raw == "all":
                    state[sub] = None
                else:
                    try:
                        idx = int(val_raw)
                    except ValueError:
                        return
                    async with async_session_maker() as _s:
                        opts = await get_filter_options(_s)
                    key_map = {"stage": "stage", "type": "type", "mgr": "mgr", "th": "th"}
                    opt_list = opts.get(key_map[sub], [])
                    if idx >= len(opt_list):
                        return
                    state[sub] = opt_list[idx]
                await _rerender_filter()
                return

            if sub == "date" and state is not None and len(parts) > 2:
                val = parts[2]
                state["date"] = None if val == "all" else val
                await _rerender_filter()
                return

            if sub == "show":
                if state is None:
                    await query.edit_message_text(
                        "‏⚠️ סשן הסינון פג. פתח את תפריט הפרוייקטים מחדש.",
                        reply_markup=get_menu_keyboard(),
                    )
                    return
                async with async_session_maker() as session:
                    projects, total = await query_projects(
                        session,
                        stages=[state["stage"]] if state["stage"] else None,
                        type_=state["type"],
                        mgr=state["mgr"],
                        th=state["th"],
                        date_filter=state["date"],
                        page=0,
                    )
                _projects_detail_origin[telegram_id] = ("cf", 0)
                item_rows = []
                for p in projects:
                    import re
                    line = format_project_line(p)
                    label = re.sub(r"<[^>]+>", "", line)[:60]
                    item_rows.append([InlineKeyboardButton(label, callback_data=f"pm:d:{p.id}:cf:0")])
                cf_kb = build_custom_results_keyboard(0, total)
                full_kb = InlineKeyboardMarkup(item_rows + list(cf_kb.inline_keyboard))
                await query.edit_message_text(
                    format_results_message("🔍 תוצאות סינון מותאם", projects, total, 0),
                    parse_mode="HTML",
                    reply_markup=full_kb,
                )
                return

            if sub == "pg":
                try:
                    page = int(parts[2]) if len(parts) > 2 else 0
                except ValueError:
                    page = 0
                if state is None:
                    await query.edit_message_text(
                        "‏⚠️ סשן הסינון פג.",
                        reply_markup=get_menu_keyboard(),
                    )
                    return
                async with async_session_maker() as session:
                    projects, total = await query_projects(
                        session,
                        stages=[state["stage"]] if state["stage"] else None,
                        type_=state["type"],
                        mgr=state["mgr"],
                        th=state["th"],
                        date_filter=state["date"],
                        page=page,
                    )
                item_rows = []
                for p in projects:
                    import re
                    line = format_project_line(p)
                    label = re.sub(r"<[^>]+>", "", line)[:60]
                    item_rows.append([InlineKeyboardButton(label, callback_data=f"pm:d:{p.id}:cf:{page}")])
                cf_kb = build_custom_results_keyboard(page, total)
                full_kb = InlineKeyboardMarkup(item_rows + list(cf_kb.inline_keyboard))
                await query.edit_message_text(
                    format_results_message("🔍 תוצאות סינון מותאם", projects, total, page),
                    parse_mode="HTML",
                    reply_markup=full_kb,
                )
                return
```

Note: `InlineKeyboardButton` and `Project` are already imported at the top of `telegram_polling.py`. Check and add any missing imports.

- [ ] **Step 6: Add `Project` to models import in `telegram_polling.py`**

Find line 13 of `app/services/telegram_polling.py`:
```python
from app.models import User
```

Replace with:
```python
from app.models import User, Project
```

- [ ] **Step 7: Run the full test suite**

```bash
cd C:/Users/livya/Desktop/SHAN-AI
python -m pytest tests/test_projects_menu_service.py tests/test_decisions_menu_service.py -v 2>&1 | tail -30
```

Expected: All tests PASS.

- [ ] **Step 8: Smoke-test the import chain**

```bash
python -c "
from app.services.telegram_polling import TelegramPollingService
print('import ok')
"
```

Expected: `import ok` (no errors).

- [ ] **Step 9: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(projects): wire /projects command, keyword, and pm:/pm_cf: callback routing"
```

---

## Task 5: Docker smoke test + restart

**Files:** none (operational)

- [ ] **Step 1: Restart Docker container**

```bash
docker-compose restart fastapi
```

- [ ] **Step 2: Check logs for startup errors**

```bash
docker-compose logs fastapi --tail=30
```

Expected: No `ImportError` or `AttributeError`. Last line should be `Telegram bot polling started`.

- [ ] **Step 3: Manual smoke test in Telegram**

Send the bot:
1. `/projects` → should receive menu with 6 buttons
2. Tap "📋 הכל" → should receive results list
3. Tap a project row → should receive project card with back button
4. Tap "🔙 חזרה לרשימה" → should return to results
5. Tap "🔙 תפריט" → should return to main menu
6. Tap "🔍 סינון" → should receive filter panel
7. Tap a stage button → checkmark should appear on that button
8. Tap "🔍 הצג תוצאות" → should receive filtered results
9. Send text "פרוייקטים" → should open menu

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore(projects): smoke tested, projects menu fully operational"
```
