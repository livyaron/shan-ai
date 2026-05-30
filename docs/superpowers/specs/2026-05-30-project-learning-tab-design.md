# Project Learning Tab — Design Spec

**Date:** 2026-05-30  
**Status:** Approved for implementation

---

## Context

The existing למידה (learning) page covers AI decision calibration. Projects are managed separately. This spec adds a **"פרויקטים" tab** to the learning page with two sub-views: cross-project insights and a risk/prediction table. The goal is to surface delay risk before it becomes overdue, using snapshot history derived from periodic master-file syncs.

Decisions are NOT always linked to projects — all project data comes from the master file sync, not from the decision engine.

---

## Data Architecture

### ProjectSnapshot table

One row per project per sync. Unique constraint on `(project_id, snapshot_date)` prevents duplicates on same-day syncs.

```python
class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"

    id                    = Column(Integer, primary_key=True)
    project_id            = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date         = Column(Date, nullable=False, index=True)

    # State copy
    stage                 = Column(String(100), nullable=True)
    estimated_finish_date = Column(Date, nullable=True)
    dev_plan_date         = Column(Date, nullable=True)
    risks                 = Column(Text, nullable=True)
    to_handle             = Column(Text, nullable=True)
    weekly_report_brief   = Column(String(500), nullable=True)
    is_active             = Column(Boolean, nullable=True)

    # Computed at snapshot time
    risk_score            = Column(Integer, nullable=True)   # 0-100
    days_overdue          = Column(Integer, nullable=True)   # negative = days until due

    created_at            = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("project_id", "snapshot_date"),)
```

**Retention:** Last 52 snapshots per project (1 year). `save_snapshot()` prunes rows beyond 52 after inserting.

**Trigger:** `project_sync.py` calls `save_snapshot(project, session)` after each project upsert — no separate cron needed.

**Migration:** `ALTER TABLE` — add the table. Existing projects get their first snapshot on next sync.

---

## Risk Score Formula

Score range: 0–100. Computed in pure Python (no LLM). Stage multiplier applied to schedule-based signals only.

### Signals

| # | Signal | Max pts | Formula |
|---|--------|---------|---------|
| 1 | **Schedule slip velocity** | 25 | Compare `estimated_finish_date` across last 3 snapshots. Each week of slippage = +5, capped at 25. No prior snapshots → 0. |
| 2 | **Overdue / urgency** | 30 | Overdue: `min(20·ln(1+days)/ln(60), 30)`. Due in <14 days: `15 - (days_until/14·5)`. No date → 0. |
| 3 | **dev_plan buffer burn** | 15 | `buffer = (estimated_finish_date - dev_plan_date).days`. `consumed_pct = (today - dev_plan_date).days / buffer × 100`. >80% → 15, >60% → 8, <0% (inverted) → 15. Both dates required. |
| 4 | **Risk text keywords** | 15 | Severe keywords ×3 (cap +12): `תקוע, מעוכב, חסם, הקפאה, ביטול, אין תקציב, חריגה, ללא היתר, חח״י לא אישרה`. Moderate ×1 (cap +3): `עיכוב, בעיה, מאחר, חסרים, ממתין, תלוי, אישור, קבלן, רגולציה, הפקעה`. No length bonus. |
| 5 | **to_handle items** | 10 | Count non-empty lines. Divide by stage weight (ביצוע÷1, תכנון÷1.5, השלמות÷1.2, סיום÷2). `min(normalized_count × 3, 10)`. |
| 6 | **Staleness** | 5 | `min(days_since_last_updated / 5, 5)`. >21 days → also set `score_reliable=False`. |

### Stage multiplier (on signals 1+2+3 only)

| Stage | Multiplier |
|-------|-----------|
| תכנון | 0.8 |
| ביצוע | 1.3 |
| השלמות | 1.15 |
| סיום | 0.7 |
| unknown | 1.0 |

Final score = `min(int((sig1+sig2+sig3) × stage_mult + sig4+sig5+sig6), 100)`.

### Score breakdown

`compute_risk_score()` returns `{"score": int, "reliable": bool, "breakdown": {signal: pts}, "main_reason": str}`. `main_reason` = the signal that contributed the most points (human-readable Hebrew string).

---

## Prediction Algorithm

**Method:** EWMA (α=0.4) for smoothed level + Theil-Sen 3-point slope (median of pairwise slopes) for trend. Requires ≥3 snapshots; returns `None` if fewer.

```python
def predict_next_score(scores: list[int]) -> int | None:
    if len(scores) < 3:
        return None
    # EWMA
    ewma = scores[0]
    for s in scores[1:]:
        ewma = 0.4 * s + 0.6 * ewma
    # Theil-Sen slope on last 3
    last3 = scores[-3:]
    slopes = [(last3[j]-last3[i])/(j-i) for i in range(3) for j in range(i+1,3)]
    slope = sorted(slopes)[len(slopes)//2]
    predicted = max(0, min(100, int(ewma + 2 * slope)))
    return predicted
```

**Flag:** `entering_risk_zone = True` when `current_score < 70 AND predicted_score >= 70`.

---

## Backend Endpoints

All read-only. Added to `app/routers/dashboard.py`. Require `get_current_user` (same as all dashboard routes).

### `GET /dashboard/learning/projects/overview`
```json
{
  "type_counts": {
    "הקמה":  {"active": 18, "delayed": 4, "at_risk": 2},
    "הרחבה": {"active": 12, "delayed": 3, "at_risk": 2},
    "שוש":   {"active": 9,  "delayed": 2, "at_risk": 1},
    "ניידות": {"active": 8,  "delayed": 2, "at_risk": 1}
  },
  "delay_trend": [
    {"week": "2026-W14", "count": 7},
    ...
  ],
  "stage_distribution": {"תכנון": 12, "ביצוע": 18, "השלמות": 5, "סיום": 4}
}
```
Delay trend: last 8 distinct `snapshot_date` weeks from `project_snapshots` where `days_overdue > 0`.

### `GET /dashboard/learning/projects/risk-table`
```json
[
  {
    "project_id": 42,
    "name": "מתחם הדר",
    "identifier": "HD-12",
    "type": "הקמה",
    "stage": "ביצוע",
    "risk_score": 88,
    "score_reliable": true,
    "sparkline": [30, 38, 45, 52, 60, 71, 80, 88],
    "predicted_score": 94,
    "entering_risk_zone": false,
    "main_reason": "47 ימי איחור + מגמת החמרה"
  }
]
```
Sorted by `risk_score DESC`. Max 50 rows. Only `is_active=True` projects.

### `GET /dashboard/learning/projects/{project_id}/detail`
```json
{
  "project": { ...all Project fields... },
  "snapshots": [ ...last 12 ProjectSnapshot rows, oldest first... ],
  "current_score": 88,
  "score_breakdown": {"overdue": 30, "velocity": 20, "buffer": 15, "keywords": 15, "to_handle": 5, "staleness": 3},
  "finish_date_drift": [
    {"date": "2026-03-01", "estimated_finish_date": "2026-06-01"},
    {"date": "2026-03-15", "estimated_finish_date": "2026-07-01"}
  ]
}
```
`finish_date_drift` shows how the target date has moved across snapshots — the core "slip velocity" visualization.

---

## UI — learning.html changes

Add "🏗️ פרויקטים" tab to the existing tab bar. The tab content is a hidden `<div>` with two inner sub-tabs:

1. **תובנות כלליות** — loaded on first click via `fetch('/dashboard/learning/projects/overview')`:
   - 4 KPI cards: פעיל / באיחור / סיכון גבוה / חיזוי הבא
   - Type breakdown bar chart (HTML/CSS bars, no charting lib)
   - Delay trend chart (8-week bar chart, CSS)
   - Stage distribution badges

2. **סיכון וחיזוי** — loaded on click via `fetch('/dashboard/learning/projects/risk-table')`:
   - Risk score table with CSS sparklines (inline SVG, no lib)
   - Dashed last bar = next-week prediction
   - Row click → `fetch('/dashboard/learning/projects/{id}/detail')` → expand inline card showing finish_date drift + score breakdown

No new JS libraries. All charts are CSS/SVG inline. Matches existing dark theme (`--bg-deep: #070b12`, `--cyan: #00d4ff`).

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `app/models.py` | Modify | Add `ProjectSnapshot` class |
| `app/services/project_learning_service.py` | **Create** | `save_snapshot()`, `compute_risk_score()`, `predict_next_score()`, `get_overview_stats()`, `get_risk_table()`, `get_project_detail()` |
| `app/services/project_sync.py` | Modify | Call `save_snapshot()` after each upsert; prune old snapshots |
| `app/routers/dashboard.py` | Modify | Add 3 GET endpoints |
| `app/templates/learning.html` | Modify | Add פרויקטים tab + sub-tab UI + fetch logic |

---

## Edge Cases

| Case | Handling |
|------|---------|
| No `estimated_finish_date` | Overdue/urgency signal = 0; buffer burn = 0 |
| First sync (0 snapshots) | slip velocity = 0; prediction = None |
| <3 snapshots | prediction = None (show "—" in UI) |
| `last_updated` >21 days | `score_reliable=False` → show ⚠️ badge in table |
| `project_type` not in TYPE_ORDER | treated as unknown; multiplier = 1.0 |
| Sync same day twice | `ON CONFLICT (project_id, snapshot_date) DO UPDATE` — overwrites with latest state |

---

## DB Migration

```sql
-- Run after deploy
CREATE TABLE IF NOT EXISTS project_snapshots (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    stage VARCHAR(100),
    estimated_finish_date DATE,
    dev_plan_date DATE,
    risks TEXT,
    to_handle TEXT,
    weekly_report_brief VARCHAR(500),
    is_active BOOLEAN,
    risk_score INTEGER,
    days_overdue INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(project_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS ix_project_snapshots_project_id ON project_snapshots(project_id);
CREATE INDEX IF NOT EXISTS ix_project_snapshots_snapshot_date ON project_snapshots(snapshot_date);
```
