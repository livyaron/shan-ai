# Project Report Improvements — Design Spec
**Date:** 2026-06-10  
**Scope:** Accuracy fixes, prologue/epilogue, trends over time  
**Approach:** B — Fix accuracy + add sections

---

## Context

The current 10-page project portfolio report has three problems:
1. **Risk scores too low** — missing data treated as "clean" (empty fields score 0)
2. **Too many projects appear on-track** — consequence of #1
3. **AI narrative too optimistic** — prompt instructs "professional" tone which AI interprets as reassuring

Additionally, the report is missing:
- A prologue (dynamic AI executive brief at the top)
- An epilogue (forward-looking "watch this" paragraph at the bottom)
- A trends section (week-over-week delta + 52-week history of risk score and delayed count)

---

## Section 1: Accuracy Fixes

### 1a. Missing-data penalties in `compute_risk_score()`
**File:** `app/services/project_learning_service.py`

Add penalties for fields that are absent or suspiciously sparse. Absence of data is not the same as clean data — unknown = suspicious.

| Condition | Penalty | Rationale |
|---|---|---|
| `risks` is None or blank (<10 chars) | +12 pts | No risk list = data not maintained |
| `to_handle` is None or blank (<10 chars) | +8 pts | No action items = likely stale |
| `weekly_report` is None or blank (<20 chars) | +5 pts | No status update reported |

These penalties apply on top of existing scoring. Max total score remains capped at 100.

### 1b. Increase weight of days overdue
**File:** `app/services/project_learning_service.py`

Current: `days_overdue` contributes 0–30 pts (log-scaled).  
Change: raise max contribution to **40 pts**, steepen the curve so that:
- 1 week late → ~15 pts (was ~10)
- 1 month late → ~28 pts (was ~20)
- 3+ months late → ~40 pts (was ~30)

Implementation: adjust the log base or multiplier in the overdue scoring formula.

### 1c. Harden the LLM narrative prompt
**File:** `app/services/project_report_service.py` — `_REPORT_PROMPT`

Replace the current "כתוב מקצועית" instruction with an explicitly critical voice:

```
המשימה שלך: לזהות מה לא בסדר, לא לתאר מה עובד.
אל תרכך בעיות. אם מגמה שלילית — אמור זאת ישירות.
אם הנתונים נראים טובים מדי — ציין זאת כסיכון בפני עצמו.
```

Also feed the LLM the **week-over-week delta** values (risk score change, delayed count change) so it has factual basis for critical commentary.

---

## Section 2: Trends Page (new Page 3)

Inserted between Portfolio Health (page 2) and Risk Register (page 4).

### 2a. Weekly Delta block

Side-by-side table comparing two most recent distinct `snapshot_date` aggregations:

| Metric | Last Week | This Week | Δ |
|---|---|---|---|
| Avg portfolio risk score | int | int | ↑/↓ colored |
| Delayed project count | int | int | ↑/↓ colored |
| At-risk count (score ≥70) | int | int | ↑/↓ colored |
| Newly entered risk zone | — | list of names | — |

Color coding: red if worsened, green if improved, gray if unchanged.

Data source: aggregate last 2 distinct `snapshot_date` values from `project_snapshots`.

### 2b. Full History Charts

Two CSS-based line charts (no external libraries — pure SVG or CSS bars):
- **Chart 1:** Average portfolio risk score per snapshot date (last 52 snapshots)
- **Chart 2:** Count of delayed projects per snapshot date (last 52 snapshots)

New aggregation query (runs at report generation time in `gather_report_data()`):

```sql
SELECT snapshot_date,
       AVG(risk_score)::int AS avg_risk,
       COUNT(*) FILTER (WHERE days_overdue > 0) AS delayed_count
FROM project_snapshots
WHERE is_active = TRUE AND risk_score IS NOT NULL
GROUP BY snapshot_date
ORDER BY snapshot_date DESC
LIMIT 52
```

Results stored in `report_data["trends"]` as a list of `{date, avg_risk, delayed_count}` dicts (ascending chronological order in the rendered chart).

---

## Section 3: Prologue & Epilogue

### 3a. Prologue (Page 0)

Dynamic AI-written executive brief — the first thing the reader sees.

**LLM input data:**
- `avg_risk_score` + week-over-week delta
- `total_delayed` + delta vs last week
- `total_at_risk` + count newly entering risk zone this week
- Top 2 most critical projects: name, risk score, main reason

**LLM prompt focus:**
```
מה הדבר הכי חשוב שמנהל תיק הפרויקטים צריך לדעת היום?
דווח על הממצאים הקריטיים ביותר בלבד. 2-3 משפטים. ישיר, ללא ריכוך.
```

**Visual:** Dark red/amber card spanning full width, placed before page 1. Bold Hebrew text.

**New LLM key:** `"prologue_narrative"` added to the existing 6-key JSON response.

### 3b. Epilogue (Page 12)

AI-written forward-looking paragraph — last page of the report.

**LLM input data:**
- Projects with rising risk trend (last 3 snapshots all increasing)
- Projects whose predicted score crosses 70 next week (`entering_risk_zone = True`)
- At-risk projects finishing within 14 days

**LLM prompt focus:**
```
מה כדאי לעקוב אחריו בשבוע הבא? אילו פרויקטים עשויים להתדרדר?
אל תכלול פעולות. תאר מה צפוי להשתנות ולמה.
```

**Visual:** Muted dark card, distinct from prologue. 3-4 sentences.

**New LLM key:** `"epilogue_narrative"` added to the JSON response.

---

## Updated Report Structure (12 pages)

| # | Page | Status |
|---|---|---|
| 0 | 🔴 Prologue — AI executive brief | **NEW** |
| 1 | Executive Summary (KPIs) | existing |
| 2 | Portfolio Health | existing |
| 3 | 📈 Trends — weekly delta + 52-week history | **NEW** |
| 4 | Risk Register (top 10) | existing (renumbered) |
| 5 | Action Items | existing |
| 6 | Finishing Soon | existing |
| 7 | Delayed Deep-Dive | existing |
| 8 | By-Type Analysis | existing |
| 9 | To-Handle Items | existing |
| 10 | Risk Forecast | existing |
| 11 | Data Quality | existing |
| 12 | 🔮 Epilogue — what to watch next week | **NEW** |

---

## Files to Modify

| File | Change |
|---|---|
| `app/services/project_learning_service.py` | Missing-data penalties + overdue weight increase |
| `app/services/project_report_service.py` | `gather_report_data()` adds trends query + delta calc + prologue/epilogue inputs; `_REPORT_PROMPT` rewritten; `_render_html()` adds pages 0, 3, 12 |

No new files. No schema changes (trends data is computed at report-generation time from existing `project_snapshots` table).

---

## Verification

1. Upload a project file → report generated → check PDF in Telegram
2. Verify prologue appears first and contains specific project names + numbers
3. Verify trends page shows correct weekly delta (compare against DB snapshots manually)
4. Verify 52-week chart has data points (confirm `project_snapshots` has enough rows)
5. Verify epilogue is forward-looking only (no decision items)
6. Check a project with empty `risks` field — confirm its risk score is now higher than before
7. Check a project with 60+ days overdue — confirm score is near-critical
8. Check AI narrative text — confirm it names problems directly, not optimistically
