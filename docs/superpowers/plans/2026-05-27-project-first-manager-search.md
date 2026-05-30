# Project-First Manager Search Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When user types a term that could be a project name or a manager name, always check for a matching project first; only search by manager if no project is found.

**Architecture:** Two targeted edits in `app/services/project_tools.py`. Change A: `search_by_manager` internal helper uses AND instead of OR for multi-token queries, so "ניר יצחק" only matches a manager whose name contains BOTH tokens. Change B: the `by_manager` branch in `answer_project_query` checks `find_projects_by_identifier` first; if a project is found it short-circuits to return a project card.

**Tech Stack:** Python 3.11, SQLAlchemy async, pytest-asyncio

---

## Files

- Modify: `app/services/project_tools.py` — two targeted edits (~12 lines total)
- Modify: `tests/test_phase0_smoke.py` — add "ניר יצחק" to smoke set
- Create: `tests/test_project_first_manager_search.py` — unit tests for both changes

---

### Task 1: AND token matching in `search_by_manager`

**Files:**
- Modify: `app/services/project_tools.py:7` (import)
- Modify: `app/services/project_tools.py:108-112` (`_search_tokens` inner function)
- Test: `tests/test_project_first_manager_search.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_first_manager_search.py`:

```python
"""Tests for project-first manager search fix."""
import pytest
from unittest.mock import AsyncMock, patch
from app.services.project_tools import search_by_manager, find_projects_by_identifier


@pytest.mark.asyncio
async def test_search_by_manager_multi_token_requires_all_tokens(db_session):
    """Multi-token query must match ALL tokens (AND), not any token (OR)."""
    from app.models import Project

    # Manager "ניר יעקבי" — has "ניר" but NOT "יצחק"
    mgr = Project(
        name="פרויקט בדיקה",
        project_identifier="TEST-01",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    # "ניר יצחק" should NOT match "ניר יעקבי" with AND logic
    results = await search_by_manager("ניר יצחק", db_session)
    names = [r["manager"] for r in results]
    assert "ניר יעקבי" not in names, (
        "AND logic should not match 'ניר יעקבי' when searching 'ניר יצחק'"
    )


@pytest.mark.asyncio
async def test_search_by_manager_multi_token_matches_correct_manager(db_session):
    """Multi-token query must still match the correct manager with AND logic."""
    from app.models import Project

    mgr = Project(
        name="פרויקט בדיקה 2",
        project_identifier="TEST-02",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    # "ניר יעקבי" should match "ניר יעקבי"
    results = await search_by_manager("ניר יעקבי", db_session)
    assert len(results) >= 1
    assert any(r["manager"] == "ניר יעקבי" for r in results)


@pytest.mark.asyncio
async def test_search_by_manager_single_token_unchanged(db_session):
    """Single-token queries must still work (no regression)."""
    from app.models import Project

    mgr = Project(
        name="פרויקט בדיקה 3",
        project_identifier="TEST-03",
        manager="ניר יעקבי",
        is_active=True,
    )
    db_session.add(mgr)
    await db_session.commit()

    results = await search_by_manager("ניר", db_session)
    assert any(r["manager"] == "ניר יעקבי" for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec shan-ai-fastapi python -m pytest tests/test_project_first_manager_search.py::test_search_by_manager_multi_token_requires_all_tokens -v
```

Expected: FAIL — "ניר יעקבי" IS currently returned (OR logic matches on "ניר").

- [ ] **Step 3: Add `and_` to the SQLAlchemy import**

In `app/services/project_tools.py` line 7, change:

```python
from sqlalchemy import select, or_
```

to:

```python
from sqlalchemy import select, or_, and_
```

- [ ] **Step 4: Change OR → AND in `_search_tokens`**

In `app/services/project_tools.py`, inside `search_by_manager`, the inner `_search_tokens` function (lines ~104–113). Change:

```python
    async def _search_tokens(name: str) -> list:
        tokens = [t for t in name.split() if t]
        if not tokens:
            return []
        conditions = [Project.manager.ilike(f"%{t}%") for t in tokens]
        stmt = select(Project).where(
            or_(*conditions),
            Project.is_active,
        ).order_by(Project.name)
        return (await session.execute(stmt)).scalars().all()
```

to:

```python
    async def _search_tokens(name: str) -> list:
        tokens = [t for t in name.split() if t]
        if not tokens:
            return []
        conditions = [Project.manager.ilike(f"%{t}%") for t in tokens]
        op = and_(*conditions) if len(conditions) > 1 else conditions[0]
        stmt = select(Project).where(op, Project.is_active).order_by(Project.name)
        return (await session.execute(stmt)).scalars().all()
```

- [ ] **Step 5: Run Task 1 tests to verify they pass**

```bash
docker exec shan-ai-fastapi python -m pytest tests/test_project_first_manager_search.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/project_tools.py tests/test_project_first_manager_search.py
git commit -m "fix(project_tools): use AND for multi-token manager search to prevent false matches"
```

---

### Task 2: Project-first check in `by_manager` branch

**Files:**
- Modify: `app/services/project_tools.py:575–607` (`by_manager` branch in `answer_project_query`)
- Test: `tests/test_project_first_manager_search.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_project_first_manager_search.py`:

```python
@pytest.mark.asyncio
async def test_by_manager_intent_prefers_project_when_name_matches(db_session):
    """When intent is by_manager but the param matches a project name, return project card."""
    import json
    from unittest.mock import patch, AsyncMock
    from app.models import Project
    from app.services.project_tools import answer_project_query

    # Seed a project named "ניר יצחק"
    proj = Project(
        name="ניר יצחק",
        project_identifier="NIR-01",
        stage="תכנון",
        manager="כלשהו מנהל",
        is_active=True,
    )
    db_session.add(proj)
    await db_session.commit()

    # Simulate AI-detected intent = by_manager, param = "ניר יצחק"
    async def fake_llm(*args, **kwargs):
        return "פרויקט ניר יצחק נמצא בשלב תכנון."

    with patch("app.services.project_tools.llm_chat", side_effect=fake_llm):
        answer, _ = await answer_project_query(
            text="ניר יצחק",
            session=db_session,
            user_data={},
            user_id=None,
            precomputed_intent="by_manager",
            precomputed_param="ניר יצחק",
        )

    # Must NOT return a "projects list" format (manager branch format)
    # Must contain the project identifier or project name
    assert "NIR-01" in answer or "ניר יצחק" in answer, (
        f"Expected project card, got: {answer!r}"
    )
    # Must NOT be a list of manager's projects
    assert "פרויקטים של" not in answer, (
        f"Should not return manager project list, got: {answer!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec shan-ai-fastapi python -m pytest tests/test_project_first_manager_search.py::test_by_manager_intent_prefers_project_when_name_matches -v
```

Expected: FAIL — currently returns manager's project list.

- [ ] **Step 3: Add project-first check to the `by_manager` branch**

In `app/services/project_tools.py`, find the `by_manager` branch (~line 575). It currently starts with:

```python
        elif intent == "by_manager":
            if not param:
                _, param = _detect_intent(text, user_data)
            if not param:
                log_id = await _log_query(text, "לא הצלחתי לזהות את שם המנהל מהשאלה.", intent, None, session, user_id)
                return "לא הצלחתי לזהות את שם המנהל מהשאלה.", log_id
            data = await search_by_manager(param, session)
```

Replace the entire `by_manager` block (from `elif intent == "by_manager":` through the closing `else:` of the `if data:` check at ~line 607) with:

```python
        elif intent == "by_manager":
            if not param:
                _, param = _detect_intent(text, user_data)
            if not param:
                log_id = await _log_query(text, "לא הצלחתי לזהות את שם המנהל מהשאלה.", intent, None, session, user_id)
                return "לא הצלחתי לזהות את שם המנהל מהשאלה.", log_id

            # Project-first: if the param matches a project, prefer the project card.
            project_first = await find_projects_by_identifier(param, session)
            if len(project_first) == 1:
                data_p = project_first[0]
                user_data["last_project"] = data_p["project_identifier"]
                current_project_id = data_p["project_identifier"]
                context_str = json.dumps(data_p, ensure_ascii=False, indent=2)
                intent = "by_identifier"
            elif 2 <= len(project_first) <= 4:
                candidates = [
                    {"id": p["project_identifier"], "name": p["name"] or p["project_identifier"]}
                    for p in project_first
                ]
                return f"__DISAMBIG__:{json.dumps(candidates, ensure_ascii=False)}", None
            else:
                # 0 or ≥5 project matches — proceed with manager search
                data = await search_by_manager(param, session)
                if data:
                    compact = [
                        {
                            "שם": p["name"],
                            "זיהוי": p["project_identifier"],
                            "שלב": p["stage"],
                            "מנהל": p["manager"],
                            "עיכוב בחודשים": p["delay_months"],
                        }
                        for p in data
                    ]
                    context_str = (
                        f"פרויקטים של {param} ({len(data)}):\n\n"
                        + json.dumps(compact, ensure_ascii=False, indent=2)
                    )
                else:
                    # No manager match — try as project identifier fallback
                    project = await get_project_details(param, session)
                    if project:
                        user_data["last_project"] = project["project_identifier"]
                        current_project_id = project["project_identifier"]
                        context_str = json.dumps(project, ensure_ascii=False, indent=2)
                    else:
                        answer = f"לא נמצאו תוצאות עבור '{param}'."
                        log_id = await _log_query(text, answer, intent, None, session, user_id)
                        return answer, log_id
```

- [ ] **Step 4: Run all project-first tests**

```bash
docker exec shan-ai-fastapi python -m pytest tests/test_project_first_manager_search.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Add "ניר יצחק" to smoke set**

In `tests/test_phase0_smoke.py`, add to `SMOKE_QUESTIONS` list:

```python
    "ניר יצחק",  # project name that looks like a manager name
```

Place it in the `# project_tools path` section, after `"פרויקט יזרעאל"`.

- [ ] **Step 6: Run full test suite**

```bash
docker exec shan-ai-fastapi python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add app/services/project_tools.py tests/test_project_first_manager_search.py tests/test_phase0_smoke.py
git commit -m "fix(project_tools): project-first priority in by_manager intent branch"
```

---

## Self-Review

**Spec coverage:**
- ✅ Change A (OR→AND): Task 1
- ✅ Change B (project-first): Task 2
- ✅ Regression test for "ניר יעקבי" still works: Task 1 Step 1 test 2
- ✅ Single-token regression: Task 1 Step 1 test 3
- ✅ Smoke set updated: Task 2 Step 5

**Placeholder scan:** None found. All steps have actual code.

**Type consistency:** `find_projects_by_identifier` returns `list[dict]`, used consistently across both tasks. `search_by_manager` returns `list[dict]`, unchanged. `json.dumps` calls match existing pattern in file.
