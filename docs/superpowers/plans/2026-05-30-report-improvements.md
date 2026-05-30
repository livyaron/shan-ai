# Report Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 report deficiencies — CRITICAL decisions always visible, projects sorted by type importance with summary table, delta shows named project changes, summary has actionable owners, language is conversational Hebrew.

**Architecture:** All changes in `app/services/weekly_report_service.py`. Data layer fixed first (Tasks 1–6), then wiring (Task 7), then prompt (Task 8). No DB schema changes. Existing `tests/test_weekly_report.py` extended throughout.

**Tech Stack:** Python 3.11, SQLAlchemy async, `case()`/`func` from sqlalchemy, `TYPE_ORDER` imported from `app.services.projects_menu_service`.

---

## File Map

| File | What changes |
|------|-------------|
| `app/services/weekly_report_service.py` | All logic changes |
| `tests/test_weekly_report.py` | New tests added per task |

---

### Task 1: Split `_decisions_summary` by severity + delete `_trim_decisions`

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 1 (new) ─────────────────────────────────────────────────────────────

def test_decisions_summary_splits_critical_from_sample():
    """CRITICAL and UNCERTAIN go to critical_urgent; INFO/NORMAL go to sample."""
    from app.services.weekly_report_service import _decisions_summary
    from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum
    from unittest.mock import MagicMock, AsyncMock, patch
    from datetime import datetime
    import asyncio

    def _make_decision(id_, dtype):
        d = MagicMock(spec=Decision)
        d.id = id_
        d.type = dtype
        d.status = DecisionStatusEnum.PENDING
        d.summary = f"summary {id_}"
        d.recommended_action = f"action {id_}"
        d.created_at = datetime(2026, 5, 1)
        d.is_relevant = True
        return d

    decisions = [
        _make_decision(1, DecisionTypeEnum.CRITICAL),
        _make_decision(2, DecisionTypeEnum.INFO),
        _make_decision(3, DecisionTypeEnum.UNCERTAIN),
        _make_decision(4, DecisionTypeEnum.NORMAL),
        _make_decision(5, DecisionTypeEnum.CRITICAL),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = decisions
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = MagicMock()
    user.role.__eq__ = lambda s, o: False  # not PROJECT_MANAGER or DEPARTMENT_MANAGER

    from app.models import RoleEnum
    user.role = RoleEnum.DIVISION_MANAGER

    result = asyncio.get_event_loop().run_until_complete(
        _decisions_summary(user, mock_session, datetime(2026, 4, 24))
    )

    cu_ids = {d["id"] for d in result["critical_urgent"]}
    sample_ids = {d["id"] for d in result["sample"]}

    assert cu_ids == {1, 3, 5}            # CRITICAL + UNCERTAIN
    assert sample_ids == {2, 4}           # INFO + NORMAL
    assert all("recommended_action" in d for d in result["critical_urgent"])
    assert "sample" not in {k: None for k in result["critical_urgent"]}


def test_decisions_summary_critical_urgent_capped_at_8():
    """critical_urgent never exceeds 8 entries."""
    from app.services.weekly_report_service import _decisions_summary
    from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    from datetime import datetime
    import asyncio

    decisions = []
    for i in range(12):
        d = MagicMock(spec=Decision)
        d.id = i
        d.type = DecisionTypeEnum.CRITICAL
        d.status = DecisionStatusEnum.PENDING
        d.summary = f"s{i}"
        d.recommended_action = f"a{i}"
        d.created_at = datetime(2026, 5, i % 28 + 1)
        d.is_relevant = True
        decisions.append(d)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = decisions
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER

    result = asyncio.get_event_loop().run_until_complete(
        _decisions_summary(user, mock_session, datetime(2026, 4, 24))
    )

    assert len(result["critical_urgent"]) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_weekly_report.py::test_decisions_summary_splits_critical_from_sample tests/test_weekly_report.py::test_decisions_summary_capped_at_8 -v
```

Expected: FAIL — `_decisions_summary` doesn't return `critical_urgent` key yet.

- [ ] **Step 3: Replace `_decisions_summary` in `weekly_report_service.py`**

Replace the entire `_decisions_summary` function (lines ~282–318) with:

```python
async def _decisions_summary(user: User, session: AsyncSession, since: datetime) -> dict:
    stmt = select(Decision).where(Decision.created_at >= since, Decision.is_relevant == True)
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

    _critical_types = {DecisionTypeEnum.CRITICAL, DecisionTypeEnum.UNCERTAIN}
    critical_urgent = sorted(
        [d for d in rows if d.type in _critical_types],
        key=lambda d: d.created_at or datetime.min,
        reverse=True,
    )[:8]
    sample = [d for d in rows if d.type not in _critical_types][:5]

    return {
        "total":             len(rows),
        "by_type":           type_counts,
        "approval_rate_pct": round(approved / len(rows) * 100),
        "critical_urgent": [
            {
                "id":                 d.id,
                "type":               d.type.value if d.type else "",
                "summary":            (d.summary or "")[:80],
                "recommended_action": (d.recommended_action or "")[:120],
            }
            for d in critical_urgent
        ],
        "sample": [
            {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
            for d in sample
        ],
    }
```

Also add `DecisionTypeEnum` to the imports at the top of the file (it's used in the `_critical_types` set):

```python
from app.models import (
    User, Decision, Project, RoleEnum, DecisionStatusEnum, DecisionTypeEnum,
    DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum,
    ReportHistory,
)
```

- [ ] **Step 4: Delete `_trim_decisions` helper**

Remove the entire `_trim_decisions` function (~lines 124–130). It is no longer called anywhere.

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_weekly_report.py::test_decisions_summary_splits_critical_from_sample tests/test_weekly_report.py::test_decisions_summary_capped_at_8 -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite to verify nothing regressed**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass (note: `generate_report_for_user` tests may still pass because `_trim_decisions` was only called inside the old `_decisions_summary`).

- [ ] **Step 7: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): split decisions_summary by severity, always surface CRITICAL/UNCERTAIN"
```

---

### Task 2: `_project_stage_map` → return `(stage_map, name_map)` tuple

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 2 (new) ─────────────────────────────────────────────────────────────

def test_project_stage_map_returns_tuple_with_name_map():
    """_project_stage_map returns (stage_map, name_map) both keyed by identifier."""
    from app.services.weekly_report_service import _project_stage_map
    from app.models import Project, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    p1 = MagicMock(spec=Project)
    p1.project_identifier = "P001"
    p1.stage = "תכנון"
    p1.name = "פרויקט ראשון"

    p2 = MagicMock(spec=Project)
    p2.project_identifier = "P002"
    p2.stage = "ביצוע"
    p2.name = None  # name_map should fall back to identifier

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (p1.project_identifier, p1.stage, p1.name),
        (p2.project_identifier, p2.stage, p2.name),
    ]
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    stage_map, name_map = asyncio.get_event_loop().run_until_complete(
        _project_stage_map(user, mock_session)
    )

    assert stage_map == {"P001": "תכנון", "P002": "ביצוע"}
    assert name_map["P001"] == "פרויקט ראשון"
    assert name_map["P002"] == "P002"   # fallback to identifier when name is None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_weekly_report.py::test_project_stage_map_returns_tuple_with_name_map -v
```

Expected: FAIL — function returns a `dict`, not a tuple.

- [ ] **Step 3: Modify `_project_stage_map`**

Replace the function body (currently ~lines 398–403):

```python
async def _project_stage_map(user: User, session: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    stmt = select(Project.project_identifier, Project.stage, Project.name).where(Project.is_active == True)
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))
    rows = (await session.execute(stmt.limit(200))).all()
    stage_map = {row[0]: (row[1] or "") for row in rows if row[0]}
    name_map  = {row[0]: (row[2] or row[0]) for row in rows if row[0]}
    return stage_map, name_map
```

- [ ] **Step 4: Fix `_gather_raw_data` to unpack the tuple**

In `_gather_raw_data`, find the line:

```python
stage_map   = await _project_stage_map(user, session)
```

Replace with:

```python
stage_map, name_map = await _project_stage_map(user, session)
```

And update the return dict (currently only has `"stage_map"`):

```python
return {
    "decisions":         decisions,
    "pending_approvals": pending,
    "projects_behind":   behind,
    "projects_at_risk":  at_risk,
    "handle_items":      handle,
    "stage_map":         stage_map,
    "name_map":          name_map,
}
```

(The `project_type_summary` key is added in Task 5.)

- [ ] **Step 5: Run tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass including the new test.

- [ ] **Step 6: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): project_stage_map returns (stage_map, name_map) tuple"
```

---

### Task 3: `_projects_behind_schedule` — sort by TYPE_ORDER + days overdue

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 3 (new) ─────────────────────────────────────────────────────────────

def test_projects_behind_schedule_sorted_by_type_order():
    """הקמה projects appear before ניידות even if ניידות is more overdue."""
    from app.services.weekly_report_service import _projects_behind_schedule
    from app.models import Project, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    from datetime import date
    import asyncio

    today = date(2026, 5, 30)

    def _make_proj(identifier, name, ptype, finish_date):
        p = MagicMock(spec=Project)
        p.project_identifier = identifier
        p.name = name
        p.project_type = ptype
        p.stage = "ביצוע"
        p.estimated_finish_date = finish_date
        p.weekly_report_brief = ""
        p.manager = "מנהל"
        return p

    # ניידות project is 100 days behind; הקמה project is only 5 days behind
    nadut  = _make_proj("N001", "פרויקט ניידות", "ניידות",  date(2026, 2, 19))  # 100 days
    hakama = _make_proj("H001", "פרויקט הקמה",   "הקמה",    date(2026, 5, 25))  # 5 days

    # DB returns them in any order — the function must order correctly
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [nadut, hakama]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.get_event_loop().run_until_complete(
        _projects_behind_schedule(user, mock_session, today)
    )

    # הקמה must come first despite fewer days behind
    assert result[0]["project"].startswith("פרויקט הקמה")
    assert result[1]["project"].startswith("פרויקט ניידות")
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_weekly_report.py::test_projects_behind_schedule_sorted_by_type_order -v
```

Expected: FAIL — current function has no type ordering.

- [ ] **Step 3: Update `_projects_behind_schedule`**

Replace the entire function:

```python
async def _projects_behind_schedule(user: User, session: AsyncSession, today) -> list[dict]:
    from app.services.projects_menu_service import TYPE_ORDER
    from sqlalchemy import case as sa_case

    stmt = select(Project).where(
        Project.is_active == True,
        Project.estimated_finish_date.isnot(None),
        Project.estimated_finish_date <= today,
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    type_order_expr = sa_case(
        *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
        else_=len(TYPE_ORDER),
    )
    # Earlier estimated_finish_date = more days behind = higher urgency within a type group
    stmt = stmt.order_by(type_order_expr, Project.estimated_finish_date.asc())
    rows = (await session.execute(stmt.limit(15))).scalars().all()

    result = []
    for p in rows:
        days_behind = (today - p.estimated_finish_date).days
        health = "🔴 קריטי" if days_behind > 30 else "🟡 באיחור"
        result.append({
            "project":     f"{p.name or p.project_identifier} ({p.project_identifier})",
            "stage":       p.stage or "",
            "finish_date": str(p.estimated_finish_date),
            "days_behind": days_behind,
            "health":      health,
            "brief":       (p.weekly_report_brief or "")[:200],
            "manager":     p.manager or "",
        })
    return result
```

- [ ] **Step 4: Update `generate_report_for_user` prompt call**

The prompt still references `raw["projects_behind"][:4]`. Change that slice to `[:8]`:

```python
behind_json=json.dumps(raw["projects_behind"][:8], ensure_ascii=False),
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): sort behind-schedule projects by type importance then days overdue"
```

---

### Task 4: `_risky_projects` — sort by TYPE_ORDER + raise limit

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 4 (new) ─────────────────────────────────────────────────────────────

def test_risky_projects_sorted_by_type_order():
    """הרחבה risk project appears before שוש risk project."""
    from app.services.weekly_report_service import _risky_projects
    from app.models import Project, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    def _make_risky(identifier, name, ptype):
        p = MagicMock(spec=Project)
        p.project_identifier = identifier
        p.name = name
        p.project_type = ptype
        p.stage = "ביצוע"
        p.risks = "סיכון כלשהו"
        p.weekly_report_brief = ""
        return p

    shoresh  = _make_risky("S001", "פרויקט שוש",   "שוש")
    harchava = _make_risky("HR01", "פרויקט הרחבה", "הרחבה")

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [shoresh, harchava]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.get_event_loop().run_until_complete(
        _risky_projects(user, mock_session)
    )

    assert result[0]["project"].startswith("פרויקט הרחבה")
    assert result[1]["project"].startswith("פרויקט שוש")
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_weekly_report.py::test_risky_projects_sorted_by_type_order -v
```

Expected: FAIL.

- [ ] **Step 3: Update `_risky_projects`**

Replace the entire function:

```python
async def _risky_projects(user: User, session: AsyncSession) -> list[dict]:
    from app.services.projects_menu_service import TYPE_ORDER
    from sqlalchemy import case as sa_case

    stmt = select(Project).where(
        Project.is_active == True,
        Project.risks.isnot(None),
        Project.risks != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    type_order_expr = sa_case(
        *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
        else_=len(TYPE_ORDER),
    )
    stmt = stmt.order_by(type_order_expr)
    rows = (await session.execute(stmt.limit(12))).scalars().all()

    return [
        {
            "project": f"{p.name or p.project_identifier} ({p.project_identifier})",
            "stage":   p.stage or "",
            "risks":   (p.risks or "")[:100],
            "brief":   (p.weekly_report_brief or "")[:150],
        }
        for p in rows
    ]
```

Also update the prompt call slice from `[:4]` to `[:8]`:

```python
risks_json=json.dumps(raw["projects_at_risk"][:8], ensure_ascii=False),
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): sort risky projects by type importance, raise cap to 12"
```

---

### Task 5: New `_project_type_summary` function

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Consolidate imports at module level**

At the top of `weekly_report_service.py`, change:

```python
from sqlalchemy import select, desc
```

to:

```python
from sqlalchemy import select, desc, func, and_, case as sa_case
from app.services.projects_menu_service import TYPE_ORDER
```

Then remove ALL local imports inside `_projects_behind_schedule` and `_risky_projects` that were added in Tasks 3 and 4:
- `from app.services.projects_menu_service import TYPE_ORDER`
- `from sqlalchemy import case as sa_case`

Both are now at module level.

- [ ] **Step 2: Write failing test**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 5 (new) ─────────────────────────────────────────────────────────────

def test_project_type_summary_structure():
    """_project_type_summary returns dict keyed by all 4 TYPE_ORDER types."""
    from app.services.weekly_report_service import _project_type_summary
    from app.models import RoleEnum
    from app.services.projects_menu_service import TYPE_ORDER
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    # Simulate DB returning 2 rows: הקמה with counts, הרחבה with counts
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("הקמה",  10, 3, 2),
        ("הרחבה", 5,  1, 0),
    ]
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.get_event_loop().run_until_complete(
        _project_type_summary(user, mock_session)
    )

    # All 4 types must be present
    assert set(result.keys()) == set(TYPE_ORDER)
    # Known values
    assert result["הקמה"]  == {"active": 10, "delayed": 3, "at_risk": 2}
    assert result["הרחבה"] == {"active": 5,  "delayed": 1, "at_risk": 0}
    # Missing types default to zeros
    assert result["שוש"]    == {"active": 0, "delayed": 0, "at_risk": 0}
    assert result["ניידות"] == {"active": 0, "delayed": 0, "at_risk": 0}
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest tests/test_weekly_report.py::test_project_type_summary_structure -v
```

Expected: FAIL — function doesn't exist yet.

- [ ] **Step 4: Add `_project_type_summary` to `weekly_report_service.py`**

Add this function after `_handle_projects`:

```python
async def _project_type_summary(user: User, session: AsyncSession) -> dict:
    """Count active/delayed/at_risk projects per TYPE_ORDER type. Role-scoped."""
    today = datetime.utcnow().date()

    base_filters = [Project.is_active.is_(True)]
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        base_filters.append(Project.manager.ilike(f"%{user.username}%"))

    stmt = select(
        Project.project_type,
        func.count().label("active"),
        func.sum(
            sa_case(
                (
                    and_(
                        Project.estimated_finish_date.isnot(None),
                        Project.estimated_finish_date <= today,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("delayed"),
        func.sum(
            sa_case(
                (
                    and_(
                        Project.risks.isnot(None),
                        Project.risks != "",
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("at_risk"),
    ).where(*base_filters).group_by(Project.project_type)

    rows = (await session.execute(stmt)).all()
    counts = {
        row[0]: {"active": row[1], "delayed": int(row[2] or 0), "at_risk": int(row[3] or 0)}
        for row in rows
        if row[0]
    }
    return {t: counts.get(t, {"active": 0, "delayed": 0, "at_risk": 0}) for t in TYPE_ORDER}
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): add _project_type_summary — active/delayed/at_risk counts per type"
```

---

### Task 6: Enrich `_compute_delta` with project names + overdue transitions

**Files:**
- Modify: `app/services/weekly_report_service.py`
- Test: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_weekly_report.py`:

```python
# ── Task 6 (new) ─────────────────────────────────────────────────────────────

def test_compute_delta_stage_changes_include_project_name():
    """stage_changes entries have a 'name' field from current name_map."""
    from app.services.weekly_report_service import _compute_delta

    current = {
        "decisions": {"total": 5, "approval_rate_pct": 60},
        "pending_approvals": [],
        "projects_behind": [],
        "projects_at_risk": [],
        "stage_map": {"P001": "ביצוע"},
        "name_map":  {"P001": "פרויקט ראשון"},
    }
    prev = {
        "decisions": {"total": 3, "approval_rate_pct": 50},
        "pending_approvals": [],
        "projects_behind": [],
        "projects_at_risk": [],
        "stage_map": {"P001": "תכנון"},
        "name_map":  {"P001": "פרויקט ראשון"},
    }

    delta = _compute_delta(current, prev)

    assert len(delta["stage_changes"]) == 1
    sc = delta["stage_changes"][0]
    assert sc["id"]   == "P001"
    assert sc["name"] == "פרויקט ראשון"
    assert sc["from"] == "תכנון"
    assert sc["to"]   == "ביצוע"


def test_compute_delta_overdue_entered_and_resolved():
    """overdue_entered contains projects new to behind list; overdue_resolved contains ones that left."""
    from app.services.weekly_report_service import _compute_delta

    current = {
        "decisions": {},
        "pending_approvals": [],
        "projects_behind": [
            {"project": "פרויקט חדש (P002)", "days_behind": 10},
        ],
        "projects_at_risk": [],
        "stage_map": {},
        "name_map":  {},
    }
    prev = {
        "decisions": {},
        "pending_approvals": [],
        "projects_behind": [
            {"project": "פרויקט ישן (P001)", "days_behind": 20},
        ],
        "projects_at_risk": [],
        "stage_map": {},
        "name_map":  {},
    }

    delta = _compute_delta(current, prev)

    entered_names = [e["name"] for e in delta["overdue_entered"]]
    assert "פרויקט חדש (P002)" in entered_names

    assert "פרויקט ישן (P001)" in delta["overdue_resolved"]


def test_compute_delta_backward_compat_missing_name_map():
    """_compute_delta works when prev raw_data has no name_map (old report row)."""
    from app.services.weekly_report_service import _compute_delta

    current = {
        "decisions": {"total": 2, "approval_rate_pct": 50},
        "pending_approvals": [],
        "projects_behind": [],
        "projects_at_risk": [],
        "stage_map": {"P001": "ביצוע"},
        "name_map":  {"P001": "פרויקט ראשון"},
    }
    prev = {
        "decisions": {"total": 1, "approval_rate_pct": 40},
        "pending_approvals": [],
        "projects_behind": [],
        "projects_at_risk": [],
        "stage_map": {"P001": "תכנון"},
        # no name_map key — old row
    }

    delta = _compute_delta(current, prev)

    # Should not raise; name falls back to identifier
    assert delta["stage_changes"][0]["name"] == "P001"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_weekly_report.py::test_compute_delta_stage_changes_include_project_name tests/test_weekly_report.py::test_compute_delta_overdue_entered_and_resolved tests/test_weekly_report.py::test_compute_delta_backward_compat_missing_name_map -v
```

Expected: FAIL.

- [ ] **Step 3: Replace `_compute_delta`**

Replace the entire function:

```python
def _compute_delta(current: dict, prev: dict) -> dict:
    """Compute structured diff between current and previous raw_data snapshots."""
    c_dec = current.get("decisions") or {}
    p_dec = prev.get("decisions") or {}

    curr_total = c_dec.get("total", 0)
    prev_total = p_dec.get("total", 0)

    curr_stages  = current.get("stage_map", {})
    prev_stages  = prev.get("stage_map", {})
    curr_names   = current.get("name_map", {})

    stage_changes = [
        {
            "id":   k,
            "name": curr_names.get(k, k),
            "from": prev_stages[k],
            "to":   curr_stages[k],
        }
        for k in curr_stages
        if k in prev_stages and curr_stages[k] != prev_stages[k]
    ]

    curr_risk_ids = {p.get("project") or p.get("identifier", "") for p in current.get("projects_at_risk", [])}
    prev_risk_ids = {p.get("project") or p.get("identifier", "") for p in prev.get("projects_at_risk", [])}

    curr_behind = current.get("projects_behind", [])
    prev_behind = prev.get("projects_behind", [])
    curr_behind_names = {p["project"] for p in curr_behind}
    prev_behind_names = {p["project"] for p in prev_behind}

    overdue_entered = [
        {"name": p["project"], "days_behind": p["days_behind"]}
        for p in curr_behind
        if p["project"] not in prev_behind_names
    ]
    overdue_resolved = list(prev_behind_names - curr_behind_names)

    return {
        "decisions_change":         curr_total - prev_total,
        "prev_decisions_total":     prev_total,
        "curr_decisions_total":     curr_total,
        "prev_approval_rate_pct":   p_dec.get("approval_rate_pct", 0),
        "curr_approval_rate_pct":   c_dec.get("approval_rate_pct", 0),
        "pending_approvals_change": (
            len(current.get("pending_approvals", [])) -
            len(prev.get("pending_approvals", []))
        ),
        "stage_changes":            stage_changes,
        "new_risks":                list(curr_risk_ids - prev_risk_ids),
        "resolved_risks":           list(prev_risk_ids - curr_risk_ids),
        "behind_schedule_change":   (
            len(curr_behind) - len(prev_behind)
        ),
        "overdue_entered":          overdue_entered,
        "overdue_resolved":         overdue_resolved,
    }
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/weekly_report_service.py tests/test_weekly_report.py
git commit -m "feat(reports): enrich delta with project names, overdue_entered, overdue_resolved"
```

---

### Task 7: Wire new data into `_gather_raw_data` + `generate_report_for_user`

**Files:**
- Modify: `app/services/weekly_report_service.py`

- [ ] **Step 1: Update `_gather_raw_data`**

Replace the entire function:

```python
async def _gather_raw_data(user: User, session: AsyncSession) -> dict:
    since = datetime.utcnow() - timedelta(days=7)
    today = datetime.utcnow().date()

    decisions    = await _decisions_summary(user, session, since)
    pending      = await _pending_approvals(user, session)
    behind       = await _projects_behind_schedule(user, session, today)
    at_risk      = await _risky_projects(user, session)
    handle       = await _handle_projects(user, session)
    stage_map, name_map = await _project_stage_map(user, session)
    type_summary = await _project_type_summary(user, session)

    return {
        "decisions":            decisions,
        "pending_approvals":    pending,
        "projects_behind":      behind,
        "projects_at_risk":     at_risk,
        "handle_items":         handle,
        "stage_map":            stage_map,
        "name_map":             name_map,
        "project_type_summary": type_summary,
    }
```

- [ ] **Step 2: Update `generate_report_for_user` — delta computation**

In `generate_report_for_user`, the delta block currently reads:

```python
if prev_row and prev_row.raw_data:
    delta_input = _compute_delta(raw, prev_row.raw_data)
    ...
```

After this block, add variables for the new prompt slots (used in Step 3):

```python
overdue_entered_json  = json.dumps(delta_input.get("overdue_entered", []),  ensure_ascii=False) if has_delta == "true" else "[]"
overdue_resolved_json = json.dumps(delta_input.get("overdue_resolved", []), ensure_ascii=False) if has_delta == "true" else "[]"
```

If `has_delta == "false"` (no previous row), also set:

```python
else:
    overdue_entered_json  = "[]"
    overdue_resolved_json = "[]"
```

So the full delta block becomes:

```python
delta_section_text    = ""
has_delta             = "false"
overdue_entered_json  = "[]"
overdue_resolved_json = "[]"

if prev_row and prev_row.raw_data:
    delta_input = _compute_delta(raw, prev_row.raw_data)
    prev_date   = prev_row.generated_at.strftime("%d/%m/%Y")
    delta_section_text = (
        f"שינויים מהדוח הקודם ({prev_date}):\n"
        f"{json.dumps(delta_input, ensure_ascii=False)}\n"
    )
    has_delta             = "true"
    overdue_entered_json  = json.dumps(delta_input.get("overdue_entered",  []), ensure_ascii=False)
    overdue_resolved_json = json.dumps(delta_input.get("overdue_resolved", []), ensure_ascii=False)
```

- [ ] **Step 3: Update the `prompt.format(...)` call**

Add 3 new keyword arguments to the existing `_REPORT_PROMPT.format(...)` call:

```python
prompt = _REPORT_PROMPT.format(
    role_label=role_label,
    username=user.username or role_label,
    date_range=f"{since_str}–{today_str}",
    decisions_json=json.dumps(raw["decisions"], ensure_ascii=False),          # full dict (no trim)
    critical_urgent_json=json.dumps(
        raw["decisions"].get("critical_urgent", []), ensure_ascii=False
    ),
    pending_json=json.dumps(raw["pending_approvals"][:5], ensure_ascii=False),
    behind_json=json.dumps(raw["projects_behind"][:8], ensure_ascii=False),
    risks_json=json.dumps(raw["projects_at_risk"][:8], ensure_ascii=False),
    handle_json=json.dumps(raw["handle_items"][:3], ensure_ascii=False),
    type_summary_json=json.dumps(raw.get("project_type_summary", {}), ensure_ascii=False),
    delta_section=delta_section_text,
    has_delta=has_delta,
    overdue_entered_json=overdue_entered_json,
    overdue_resolved_json=overdue_resolved_json,
)
```

Note: `decisions_json` now passes the full dict (including `critical_urgent` and `sample` keys) instead of the old trimmed version. The prompt template will be updated in Task 8 to use `{critical_urgent_json}` directly.

- [ ] **Step 4: Run all existing tests**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass. The `generate_report_for_user` tests mock `llm_chat` so prompt content doesn't matter here.

- [ ] **Step 5: Commit**

```bash
git add app/services/weekly_report_service.py
git commit -m "feat(reports): wire type_summary, name_map, overdue into gather_raw_data and prompt call"
```

---

### Task 8: Rewrite `_REPORT_PROMPT`

**Files:**
- Modify: `app/services/weekly_report_service.py`

- [ ] **Step 1: Replace `_REPORT_PROMPT` constant**

Replace the entire `_REPORT_PROMPT` string with:

```python
_REPORT_PROMPT = """\
כתוב בעברית שוטפת וידידותית — כאילו מנהל בכיר מדווח בעל-פה לעמית. משפטים קצרים. אין מונחים טכניים מיותרים.

אתה עוזר ניהול פרויקטים לתשתיות חשמל. צור דוח שבועי בעברית עבור {username} (תפקיד: {role_label}).
תאריך: שבוע {date_range}

--- נתוני קלט ---
החלטות (7 ימים אחרונים): {decisions_json}
החלטות קריטיות/לא-ודאות: {critical_urgent_json}
אישורים ממתינים שלך: {pending_json}
פרויקטים באיחור: {behind_json}
פרויקטים בסיכון: {risks_json}
פרויקטים לטיפול (to_handle): {handle_json}
סיכום פרויקטים לפי סוג: {type_summary_json}
פרויקטים שנכנסו לאיחור השבוע: {overdue_entered_json}
פרויקטים שיצאו מאיחור השבוע: {overdue_resolved_json}
{delta_section}
--- הנחיות לפלט ---

prologue (50-70 מילה):
שלום {username}, 1-2 פריטים קריטיים לטיפול היום, ספירות (החלטות/פרויקטים/אישורים).

decisions (100-130 מילה):
אם יש החלטות קריטיות/לא-ודאות — פתח בהן עם ⚠️, כל אחת בשורה: "#ID — תיאור — פעולה מומלצת".
אחר כך: ספירה לפי סוג, אחוז אישורים, רשימה קצרה של אישורים ממתינים.
דגל ⚠️ אם נפח חריג.

projects (150-200 מילה) — ניתוח מלא:
פתח בטבלת סיכום: | סוג | פעיל | מאחר | בסיכון | — שורה לכל סוג (הקמה/הרחבה/שוש/ניידות).
לכל פרויקט באיחור: שם + 🔴/🟡 + כמה ימים + שלב נוכחי + סיבה קצרה. מיין לפי חשיבות סוג (הקמה ראשון).
"חייב לפעול השבוע" — 3 פריטים: מי (שם מנהל אם קיים, אחרת "דרוש טיפול") / מה / מתי.

summary (80-100 מילה):
3 משימות לשבוע הבא בפורמט: "• [שם מנהל / "דרוש טיפול"] — [פעולה ספציפית] — [מתי]".
הישג בולט אחד. סיכון מרכזי אחד. משפט עידוד קצר.

delta: {has_delta} — אם "true":
פתח ב"מאז הדוח הקודם:". ציין פרויקטים שנכנסו לאיחור (overdue_entered) ופרויקטים שיצאו מאיחור (overdue_resolved).
שינויי שלב: "פרויקט X עבר מ-Y ל-Z". מגמת החלטות (↑↓%). פסקה רציפה, ללא bullet points.
אם "false": null.

--- פורמט תשובה (JSON בלבד, ללא טקסט לפני ואחרי) ---
{{"prologue":"...","decisions":"...","projects":"...","summary":"...","delta":"..."}}"""
```

- [ ] **Step 2: Run full test suite**

```
pytest tests/test_weekly_report.py -v
```

Expected: all pass. (Prompt text doesn't affect unit tests since `llm_chat` is mocked.)

- [ ] **Step 3: Commit**

```bash
git add app/services/weekly_report_service.py
git commit -m "feat(reports): rewrite prompt — conversational Hebrew, CRITICAL decisions, type table, action items, narrative delta"
```

---

### Task 9: Docker restart + smoke test

- [ ] **Step 1: Restart the FastAPI service**

```bash
docker-compose restart fastapi
```

- [ ] **Step 2: Trigger a test report via dashboard**

Navigate to `/dashboard/reports`, pick any user, click "🔄 צור דוח חדש". Open the generated report and verify:

1. If that user has CRITICAL decisions → they appear at the top of the decisions section with `#ID — תיאור — פעולה מומלצת`
2. Projects section opens with a table: `| סוג | פעיל | מאחר | בסיכון |`
3. Summary section has 3 bullet-point action items with manager names
4. Language reads conversationally (no PMO boilerplate)

- [ ] **Step 3: If a second report exists for that user, verify delta**

Generate a second report immediately after the first. Delta section should say "מאז הדוח הקודם:" and list any stage/overdue changes (likely empty since generated seconds apart — that's correct).

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "chore: smoke-tested report improvements on local Docker"
```
