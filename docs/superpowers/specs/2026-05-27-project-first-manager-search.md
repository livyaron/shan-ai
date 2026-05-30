# Spec: Project-First Priority in Manager Intent Routing

**Date:** 2026-05-27  
**Status:** Approved

## Problem

When user types a station/project name like "ניר יצחק", the AI intent detector routes it to `by_manager`. `search_by_manager` uses OR across name tokens, so "ניר" matches "ניר יעקבי" → 16 unrelated projects returned instead of the one project card.

## Root Cause

Two issues in `app/services/project_tools.py`:

1. `search_by_manager` (`line ~108`): multi-token search uses `or_(*conditions)` — any single token match is enough. "ניר" alone matches the manager "ניר יעקבי".
2. `answer_project_query` `by_manager` branch (`line ~575`): manager search runs before checking whether the input is actually a project name.

## Design

### Change A — `search_by_manager`: OR → AND for multi-token queries

```python
op = and_(*conditions) if len(conditions) > 1 else conditions[0]
stmt = select(Project).where(op, Project.is_active)
```

All tokens must appear in manager name. Single-token queries unaffected. "ניר יעקבי" still matches (both tokens present). "ניר יצחק" returns empty.

### Change B — `by_manager` branch: project lookup first

```python
elif intent == "by_manager":
    project_check = await find_projects_by_identifier(param, session)
    if project_check:
        # treat as by_identifier: return project card
        ...
    else:
        data = await search_by_manager(param, session)
        ...
```

Priority: project → manager. If 1+ project matches the param, return the project card (single match → card, 2–4 → disambig menu, 5+ → list). Only if no project found → proceed with manager search.

## Files Changed

- `app/services/project_tools.py` — two targeted edits, ~10 lines total

## Success Criteria

- "ניר יצחק" → returns project card for station ניר יצחק
- "ניר יעקבי" → still returns all projects managed by ניר יעקבי (no regression)
- "ניר" (single token) → manager search unchanged
