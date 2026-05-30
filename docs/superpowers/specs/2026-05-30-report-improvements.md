# Spec: Weekly Report Improvements
**Date:** 2026-05-30  
**Status:** Approved  
**Scope:** `app/services/weekly_report_service.py` only (no DB changes)

---

## Problem

Five deficiencies in the weekly report:

1. **CRITICAL decisions invisible** — `_trim_decisions` caps the sample at 3 items. If a user has >3 decisions, CRITICAL/UNCERTAIN ones may be cut before reaching the LLM, so the report never mentions them.
2. **Projects section misses important projects** — no ordering by type importance; capped at 4 per category regardless of priority.
3. **Delta shows raw numbers, not narrative** — stage changes lack project names; no tracking of which projects entered or exited overdue status.
4. **Conclusion has no actionable next steps** — summary prompt produces a general feel-good paragraph, not concrete tasks with owners.
5. **Language is PMO-jargon heavy** — too formal for the intended audience.

---

## Approach: Data Layer + Prompt Fix (Approach B)

Fix the data fed to the LLM first, then improve the prompt. All changes in `weekly_report_service.py`. No DB schema changes. Single LLM call preserved.

---

## Design

### 1. Data Layer Changes

#### 1a. `_decisions_summary` — split by severity

Replace the flat `sample` list (capped at 3) with two keys:

```python
{
    "total": N,
    "by_type": {"CRITICAL": N, ...},
    "approval_rate_pct": N,
    "critical_urgent": [   # CRITICAL + UNCERTAIN only, up to 8
        {"id": d.id, "type": "...", "summary": "...", "recommended_action": "..."}
    ],
    "sample": [            # INFO + NORMAL only, up to 5
        {"id": d.id, "type": "...", "summary": "..."}
    ]
}
```

- Sort `critical_urgent` by `created_at DESC` so newest CRITICAL is first.
- Include `recommended_action` field (from `Decision.recommended_action`) in `critical_urgent` — this is what the report was missing for דוד עמר's case.
- Remove `_trim_decisions` helper entirely.

#### 1b. `_projects_behind_schedule` — sort by importance + severity

Add SQLAlchemy `case()` ordering using `TYPE_ORDER = ["הקמה", "הרחבה", "שוש", "ניידות"]` imported from `projects_menu_service`:

```python
type_order = case(
    *[(Project.project_type == t, i) for i, t in enumerate(TYPE_ORDER)],
    else_=len(TYPE_ORDER),
)
stmt = stmt.order_by(type_order, desc(days_behind_expr))
```

Raise DB fetch limit from 8 → 15. Prompt still receives `[:8]` but now the 8 are the most important.

#### 1c. `_risky_projects` — same type ordering

Add same `case()` ordering. Raise limit 8 → 12.

#### 1d. New `_project_type_summary(user, session)` function

Returns a dict used for the projects summary table:

```python
{
    "הקמה":    {"active": N, "delayed": N, "at_risk": N},
    "הרחבה":   {"active": N, "delayed": N, "at_risk": N},
    "שוש":     {"active": N, "delayed": N, "at_risk": N},
    "ניידות":  {"active": N, "delayed": N, "at_risk": N},
}
```

- `active`: `is_active=True` count per type (role-scoped same as other queries)
- `delayed`: `is_active=True AND estimated_finish_date <= today` per type
- `at_risk`: `is_active=True AND risks IS NOT NULL AND risks != ''` per type
- Uses 3 scalar subqueries — no extra joins.

#### 1e. `_compute_delta` — enrich stage changes + track overdue transitions

**Stage changes:** add `name` field using a name lookup from the current `stage_map`'s companion data. Since `stage_map` only stores `{identifier: stage}`, extend `_project_stage_map` to return a tuple `(stage_map, name_map)` where `name_map = {identifier: name}`. Both are stored in `raw_data`. Use `name_map` in delta:

```python
{"id": k, "name": name_map.get(k, k), "from": prev_stages[k], "to": curr_stages[k]}
```

**Overdue transitions:** compare `projects_behind` lists between snapshots by project name key:

```python
curr_behind_names = {p["project"] for p in current.get("projects_behind", [])}
prev_behind_names = {p["project"] for p in prev.get("projects_behind", [])}

overdue_entered = [
    {"name": p["project"], "days_behind": p["days_behind"]}
    for p in current.get("projects_behind", [])
    if p["project"] not in prev_behind_names
]
overdue_resolved = list(prev_behind_names - curr_behind_names)
```

Add `overdue_entered` and `overdue_resolved` to the delta dict.

#### 1f. `_gather_raw_data` — add new keys

```python
return {
    ...existing...,
    "project_type_summary": type_summary,   # new
    "name_map": name_map,                   # new — {identifier: name} for delta enrichment
}
```

Old `ReportHistory.raw_data` rows without these keys are handled with `.get(key, {})` — no migration needed.

---

### 2. Prompt Changes

All changes to `_REPORT_PROMPT`.

#### 2a. Language preamble (add at top, before role/date)

```
כתוב בעברית שוטפת וידידותית — כאילו מנהל בכיר מדווח בעל-פה לעמית. משפטים קצרים. אין מונחים טכניים מיותרים.
```

#### 2b. New input slots

```
החלטות קריטיות/לא-ודאות: {critical_urgent_json}
סיכום פרויקטים לפי סוג: {type_summary_json}
פרויקטים שנכנסו לאיחור השבוע: {overdue_entered_json}
פרויקטים שיצאו מאיחור השבוע: {overdue_resolved_json}
```

#### 2c. `decisions` section instruction (replaces existing)

```
decisions (100-130 מילה):
אם יש החלטות קריטיות/לא-ודאות — פתח בהן עם ⚠️, כל אחת בשורה: "#ID — תיאור — פעולה מומלצת".
אחר כך: ספירה לפי סוג, אחוז אישורים, רשימה קצרה של אישורים ממתינים.
```

#### 2d. `projects` section instruction (replaces existing)

```
projects (150-200 מילה):
פתח בטבלת סיכום: | סוג | פעיל | מאחר | בסיכון | — שורה לכל סוג (הקמה/הרחבה/שוש/ניידות).
לכל פרויקט באיחור: שם + 🔴/🟡 + ימים + שלב + סיבה קצרה. מיין לפי חשיבות (הקמה ראשון).
"חייב לפעול השבוע" — 3 פריטים: מי (שם מנהל אם קיים, אחרת "דרוש טיפול") / מה / מתי.
```

#### 2e. `summary` section instruction (replaces existing)

```
summary (80-100 מילה):
3 משימות לשבוע הבא בפורמט: "• [שם מנהל / "דרוש טיפול"] — [פעולה ספציפית] — [מתי]".
הישג בולט אחד. סיכון מרכזי אחד. משפט עידוד קצר.
```

#### 2f. `delta` section instruction (replaces existing)

```
delta (אם has_delta=true):
פתח ב"מאז הדוח הקודם:". ציין פרויקטים שנכנסו לאיחור (overdue_entered) ופרויקטים שיצאו (overdue_resolved).
שינויי שלב: "פרויקט X עבר מ-Y ל-Z". מגמת החלטות (↑↓%). פסקה רציפה, ללא bullet points.
אם has_delta=false: null.
```

---

### 3. Wiring & Backward Compatibility

- `_trim_decisions` function deleted.
- `generate_report_for_user`: add `type_summary`, `name_map` calls; pass new prompt slots.
- `_compute_delta`: receives both `current` and `prev` which now include `name_map` and `projects_behind` — backward compat via `.get(key, {})`.
- `ReportHistory.raw_data` is untyped JSON — new keys appear on next report generation; old rows without them degrade gracefully.
- Import: `from app.services.projects_menu_service import TYPE_ORDER` added at top of file.
- No changes to `send_report_to_user`, `send_weekly_reports_cron`, templates, or DB models.

---

## Files Changed

| File | Change |
|------|--------|
| `app/services/weekly_report_service.py` | All changes — data layer + prompt |

---

## Success Criteria

1. A user with a CRITICAL decision always sees it in their report's decisions section, regardless of total decision count.
2. Projects section opens with a type-summary table (הקמה/הרחבה/שוש/ניידות × active/delayed/at-risk).
3. Behind-schedule projects sorted הקמה first, then by days overdue descending.
4. Delta section names specific projects that entered/exited overdue, and names specific projects in stage changes.
5. Summary section lists 3 action items with manager names where available.
6. Report language reads conversationally — no PMO boilerplate.
