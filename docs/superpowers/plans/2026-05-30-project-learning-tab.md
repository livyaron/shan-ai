# Project Learning Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "פרויקטים" tab to the learning page with cross-project insights and a delay-risk prediction table powered by weekly master-file snapshots.

**Architecture:** New `ProjectSnapshot` table stores one row per project per sync. `project_learning_service.py` computes a 0–100 delay-risk score from 6 signals (schedule slip velocity, overdue urgency, dev_plan buffer burn, Hebrew risk keywords, to_handle items, staleness) with a stage multiplier. EWMA + Theil-Sen slope predicts next-week score. Three JSON endpoints feed a new projects tab in `learning.html` (CSS/SVG charts, no new JS libraries).

**Tech Stack:** SQLAlchemy async, PostgreSQL (pg INSERT ON CONFLICT), FastAPI, Jinja2 + vanilla JS fetch, existing dark theme tokens.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `app/models.py` | Modify | Add `ProjectSnapshot` class |
| `app/services/project_learning_service.py` | **Create** | All score/prediction/query logic |
| `app/services/project_sync.py` | Modify | Call `save_snapshot()` after each upsert |
| `app/routers/dashboard.py` | Modify | 3 new GET JSON endpoints |
| `app/templates/learning.html` | Modify | Tab bar + projects tab UI |
| `tests/test_project_learning.py` | **Create** | Unit tests for all service functions |

---

## Task 1: DB Migration + ProjectSnapshot Model

**Files:**
- Modify: `app/models.py`
- Create: `tests/test_project_learning.py`

- [ ] **Step 1: Run migration on local DB**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "
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
"
```

Expected: `CREATE TABLE` / `CREATE INDEX`

- [ ] **Step 2: Write failing test**

Create `tests/test_project_learning.py`:

```python
"""Tests for project_learning_service."""
import pytest
from app.models import ProjectSnapshot


def test_project_snapshot_has_required_columns():
    cols = {c.key for c in ProjectSnapshot.__table__.columns}
    assert "project_id" in cols
    assert "snapshot_date" in cols
    assert "risk_score" in cols
    assert "days_overdue" in cols
    assert "stage" in cols
    assert "estimated_finish_date" in cols
    assert "dev_plan_date" in cols
    assert "risks" in cols
    assert "to_handle" in cols
    assert "weekly_report_brief" in cols
    assert "is_active" in cols


def test_project_snapshot_unique_constraint():
    """unique constraint must be on (project_id, snapshot_date)."""
    ucs = [str(uc) for uc in ProjectSnapshot.__table_args__]
    assert any("project_id" in u and "snapshot_date" in u for u in ucs)
```

- [ ] **Step 3: Run test to confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -v
```

Expected: `ImportError: cannot import name 'ProjectSnapshot' from 'app.models'`

- [ ] **Step 4: Add ProjectSnapshot to `app/models.py`**

After the `Project` class (around line 323), add:

```python
class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"

    id                    = Column(Integer, primary_key=True)
    project_id            = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date         = Column(Date, nullable=False, index=True)
    stage                 = Column(String(100), nullable=True)
    estimated_finish_date = Column(Date, nullable=True)
    dev_plan_date         = Column(Date, nullable=True)
    risks                 = Column(Text, nullable=True)
    to_handle             = Column(Text, nullable=True)
    weekly_report_brief   = Column(String(500), nullable=True)
    is_active             = Column(Boolean, nullable=True)
    risk_score            = Column(Integer, nullable=True)
    days_overdue          = Column(Integer, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    project               = relationship("Project", back_populates="snapshots")

    __table_args__ = (UniqueConstraint("project_id", "snapshot_date"),)
```

Also add `snapshots` back-reference to the `Project` class, after its existing relationships:

```python
    snapshots = relationship("ProjectSnapshot", back_populates="project", order_by="ProjectSnapshot.snapshot_date")
```

- [ ] **Step 5: Run test to confirm PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -v
```

Expected: `2 passed`

- [ ] **Step 6: Restart and verify no startup errors**

```bash
docker-compose restart fastapi && docker logs shan-ai-fastapi --tail 15
```

Expected: `Application startup complete.`

- [ ] **Step 7: Commit**

```bash
git add app/models.py tests/test_project_learning.py
git commit -m "feat(projects): add ProjectSnapshot model and DB migration"
```

---

## Task 2: compute_risk_score (pure Python, no DB)

**Files:**
- Create: `app/services/project_learning_service.py`
- Modify: `tests/test_project_learning.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_project_learning.py`:

```python
from datetime import date, datetime, timedelta
from app.services.project_learning_service import compute_risk_score


def test_no_dates_gives_zero_schedule_signals():
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=None,
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=datetime.utcnow(),
        prior_finish_dates=[],
        today=date(2026, 5, 30),
    )
    assert result["score"] == 0
    assert result["reliable"] is True


def test_overdue_project_scores_high():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=60),
        dev_plan_date=None,
        risks=None,
        to_handle=None,
        last_updated=datetime.utcnow(),
        today=today,
    )
    assert result["score"] >= 30
    assert result["breakdown"]["overdue"] > 0


def test_severe_keywords_add_points():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="תכנון",
        estimated_finish_date=today + timedelta(days=90),
        dev_plan_date=None,
        risks="הפרויקט תקוע מול חח״י לא אישרה המשך",
        to_handle=None,
        last_updated=datetime.utcnow(),
        today=today,
    )
    assert result["breakdown"]["keywords"] >= 6  # תקוע=3 + חח״י לא אישרה=3


def test_stage_multiplier_biutz_raises_score():
    today = date(2026, 5, 30)
    base = compute_risk_score(
        stage="תכנון",
        estimated_finish_date=today - timedelta(days=14),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow(), today=today,
    )
    high = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=14),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow(), today=today,
    )
    assert high["score"] > base["score"]


def test_stale_project_sets_unreliable():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today + timedelta(days=60),
        dev_plan_date=None, risks=None, to_handle=None,
        last_updated=datetime.utcnow() - timedelta(days=25),
        today=today,
    )
    assert result["reliable"] is False


def test_score_capped_at_100():
    today = date(2026, 5, 30)
    result = compute_risk_score(
        stage="ביצוע",
        estimated_finish_date=today - timedelta(days=200),
        dev_plan_date=today - timedelta(days=300),
        risks="תקוע מעוכב חסם הקפאה ביטול אין תקציב חריגה ללא היתר חח״י לא אישרה",
        to_handle="\n".join(f"פריט {i}" for i in range(20)),
        last_updated=datetime.utcnow() - timedelta(days=30),
        today=today,
    )
    assert result["score"] <= 100
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -k "risk_score or keywords or multiplier or stale or capped" -v
```

Expected: `ImportError: cannot import name 'compute_risk_score'`

- [ ] **Step 3: Create `app/services/project_learning_service.py`**

```python
"""Project learning service — risk scoring, snapshots, insight queries."""
import math
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, desc, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectSnapshot
from app.services.projects_menu_service import TYPE_ORDER

logger = logging.getLogger(__name__)

# ── Risk scoring constants ───────────────────────────────────────────────────

STAGE_MULTIPLIER = {
    "תכנון":   0.8,
    "ביצוע":   1.3,
    "השלמות":  1.15,
    "סיום":    0.7,
}

STAGE_TO_HANDLE_DIVISOR = {
    "תכנון":   1.5,
    "ביצוע":   1.0,
    "השלמות":  1.2,
    "סיום":    2.0,
}

SEVERE_KEYWORDS = [
    "תקוע", "מעוכב", "חסם", "הקפאה", "ביטול",
    "אין תקציב", "חריגה", "ללא היתר", "חח״י לא אישרה",
]
MODERATE_KEYWORDS = [
    "עיכוב", "בעיה", "מאחר", "חסרים", "ממתין",
    "תלוי", "אישור", "קבלן", "רגולציה", "הפקעה",
]

_MAIN_REASON_MAP = {
    "velocity":  "מגמת החמרה בתאריך סיום",
    "overdue":   "ימי איחור",
    "buffer":    "צריכת מרווח תכנון",
    "keywords":  "מילות מפתח בסיכונים",
    "to_handle": "פריטי לטיפול",
    "staleness": "עדכון ישן",
}


# ── Signal helpers ────────────────────────────────────────────────────────────

def _velocity_pts(prior_finish_dates: list, current_finish: Optional[date]) -> int:
    """0-25: how many weeks did estimated_finish_date slip vs most recent snapshot."""
    if not prior_finish_dates or not current_finish:
        return 0
    last_prior = prior_finish_dates[-1]
    if not last_prior:
        return 0
    slippage_days = (current_finish - last_prior).days
    return min(int(max(slippage_days, 0) / 7 * 5), 25)


def _overdue_pts(estimated_finish_date: Optional[date], today: date) -> int:
    """0-30: log-scaled overdue or urgency for imminent deadlines."""
    if not estimated_finish_date:
        return 0
    days_diff = (today - estimated_finish_date).days  # positive = overdue
    if days_diff > 0:
        return min(int(20 * math.log(1 + days_diff) / math.log(60)), 30)
    days_until = -days_diff
    if days_until < 14:
        return max(0, int(15 - (days_until / 14 * 5)))
    return 0


def _buffer_pts(dev_plan_date: Optional[date], estimated_finish_date: Optional[date], today: date) -> int:
    """0-15: percentage of schedule buffer consumed."""
    if not dev_plan_date or not estimated_finish_date:
        return 0
    buffer_days = (estimated_finish_date - dev_plan_date).days
    if buffer_days <= 0:
        return 15  # inverted schedule (finish before plan) or no buffer
    consumed_pct = (today - dev_plan_date).days / buffer_days * 100
    if consumed_pct > 80:
        return 15
    if consumed_pct > 60:
        return 8
    return 0


def _keyword_pts(risks: Optional[str]) -> int:
    """0-15: severity-tiered Hebrew risk keyword scoring."""
    if not risks:
        return 0
    severe = sum(3 for kw in SEVERE_KEYWORDS if kw in risks)
    moderate = sum(1 for kw in MODERATE_KEYWORDS if kw in risks)
    return min(severe, 12) + min(moderate, 3)


def _to_handle_pts(to_handle: Optional[str], stage: Optional[str]) -> int:
    """0-10: item count normalized by stage urgency."""
    if not to_handle:
        return 0
    items = [l.strip() for l in to_handle.splitlines() if l.strip()]
    divisor = STAGE_TO_HANDLE_DIVISOR.get(stage or "", 1.0)
    return min(int(len(items) / divisor * 3), 10)


def _staleness_pts(last_updated: Optional[datetime]) -> tuple[int, bool]:
    """0-5 pts, plus unreliable flag if >21 days stale."""
    if not last_updated:
        return 0, False
    days_stale = (datetime.utcnow() - last_updated).days
    return min(int(days_stale / 5), 5), days_stale > 21


# ── Public scoring function ──────────────────────────────────────────────────

def compute_risk_score(
    stage: Optional[str],
    estimated_finish_date: Optional[date],
    dev_plan_date: Optional[date],
    risks: Optional[str],
    to_handle: Optional[str],
    last_updated: Optional[datetime],
    prior_finish_dates: Optional[list] = None,
    today: Optional[date] = None,
) -> dict:
    """
    Compute delay risk score (0-100) + breakdown.
    Returns: {score, reliable, breakdown, main_reason, days_overdue}
    """
    if today is None:
        today = date.today()
    if prior_finish_dates is None:
        prior_finish_dates = []

    vel  = _velocity_pts(prior_finish_dates, estimated_finish_date)
    over = _overdue_pts(estimated_finish_date, today)
    buf  = _buffer_pts(dev_plan_date, estimated_finish_date, today)

    mult = STAGE_MULTIPLIER.get(stage or "", 1.0)
    schedule_pts = int((vel + over + buf) * mult)

    kw     = _keyword_pts(risks)
    handle = _to_handle_pts(to_handle, stage)
    stale, unreliable = _staleness_pts(last_updated)

    score = min(schedule_pts + kw + handle + stale, 100)

    breakdown = {
        "velocity":  vel,
        "overdue":   over,
        "buffer":    buf,
        "keywords":  kw,
        "to_handle": handle,
        "staleness": stale,
    }
    main_signal = max(breakdown, key=breakdown.get)

    days_overdue = None
    if estimated_finish_date:
        d = (today - estimated_finish_date).days
        days_overdue = d if d > 0 else None

    main_reason = _MAIN_REASON_MAP.get(main_signal, "")
    if main_signal == "overdue" and days_overdue:
        main_reason = f"{days_overdue} ימי איחור"

    return {
        "score":       score,
        "reliable":    not unreliable,
        "breakdown":   breakdown,
        "main_reason": main_reason,
        "days_overdue": days_overdue,
    }
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/services/project_learning_service.py tests/test_project_learning.py
git commit -m "feat(projects): add compute_risk_score with 6 signals and stage multiplier"
```

---

## Task 3: predict_next_score (EWMA + Theil-Sen)

**Files:**
- Modify: `app/services/project_learning_service.py`
- Modify: `tests/test_project_learning.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_project_learning.py`:

```python
from app.services.project_learning_service import predict_next_score


def test_predict_returns_none_with_fewer_than_3_scores():
    assert predict_next_score([]) is None
    assert predict_next_score([50]) is None
    assert predict_next_score([40, 50]) is None


def test_predict_rising_trend():
    scores = [20, 30, 40, 50, 60, 70, 75, 80]
    pred = predict_next_score(scores)
    assert pred is not None
    assert pred > 80  # rising trend → predict higher


def test_predict_falling_trend():
    scores = [80, 70, 60, 50, 40, 30, 20, 15]
    pred = predict_next_score(scores)
    assert pred is not None
    assert pred < 15  # falling → lower (clamped at 0)


def test_predict_clamped_0_100():
    assert predict_next_score([95, 98, 99, 100, 100, 100, 100, 100]) <= 100
    assert predict_next_score([5, 3, 2, 1, 1, 1, 1, 1]) >= 0


def test_predict_needs_only_3_scores():
    pred = predict_next_score([30, 50, 70])
    assert pred is not None
    assert pred > 70
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -k "predict" -v
```

Expected: `ImportError: cannot import name 'predict_next_score'`

- [ ] **Step 3: Add `predict_next_score` to `project_learning_service.py`**

Add after `compute_risk_score`:

```python
def predict_next_score(scores: list[int]) -> Optional[int]:
    """
    EWMA (α=0.4) level + Theil-Sen 3-point slope → next-week prediction.
    Returns None if fewer than 3 data points.
    """
    if len(scores) < 3:
        return None

    # EWMA over all scores
    ewma = float(scores[0])
    for s in scores[1:]:
        ewma = 0.4 * s + 0.6 * ewma

    # Theil-Sen slope on last 3 (robust to outliers)
    last3 = scores[-3:]
    pairs = [
        (last3[j] - last3[i]) / (j - i)
        for i in range(3)
        for j in range(i + 1, 3)
    ]
    slope = sorted(pairs)[len(pairs) // 2]

    return max(0, min(100, int(ewma + 2 * slope)))
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/services/project_learning_service.py tests/test_project_learning.py
git commit -m "feat(projects): add predict_next_score via EWMA + Theil-Sen slope"
```

---

## Task 4: save_snapshot (DB write + prune)

**Files:**
- Modify: `app/services/project_learning_service.py`
- Modify: `tests/test_project_learning.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_project_learning.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime
from app.services.project_learning_service import save_snapshot
from app.models import Project


@pytest.mark.asyncio
async def test_save_snapshot_executes_upsert():
    proj = MagicMock(spec=Project)
    proj.id = 1
    proj.stage = "ביצוע"
    proj.estimated_finish_date = date(2026, 4, 1)
    proj.dev_plan_date = date(2026, 3, 1)
    proj.risks = "תקוע"
    proj.to_handle = "פריט אחד\nפריט שניים"
    proj.weekly_report_brief = "עדכון"
    proj.is_active = True
    proj.last_updated = datetime.utcnow()

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    session.scalar = AsyncMock(return_value=None)

    await save_snapshot(proj, session)

    assert session.execute.called
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py::test_save_snapshot_executes_upsert -v
```

Expected: `ImportError: cannot import name 'save_snapshot'`

- [ ] **Step 3: Add `save_snapshot` to `project_learning_service.py`**

Add after `predict_next_score`:

```python
async def save_snapshot(project: Project, session: AsyncSession) -> None:
    """
    Upsert one ProjectSnapshot row for today.
    ON CONFLICT (project_id, snapshot_date) → update all fields.
    Prunes snapshots older than the 52nd most-recent per project.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    today = date.today()

    # Fetch last 3 finish dates for velocity calculation
    prior_rows = (await session.execute(
        select(ProjectSnapshot.estimated_finish_date)
        .where(ProjectSnapshot.project_id == project.id)
        .order_by(desc(ProjectSnapshot.snapshot_date))
        .limit(3)
    )).scalars().all()
    prior_finish_dates = [d for d in reversed(prior_rows) if d is not None]

    result = compute_risk_score(
        stage=project.stage,
        estimated_finish_date=project.estimated_finish_date,
        dev_plan_date=project.dev_plan_date,
        risks=project.risks,
        to_handle=project.to_handle,
        last_updated=project.last_updated,
        prior_finish_dates=prior_finish_dates,
        today=today,
    )

    values = dict(
        project_id            = project.id,
        snapshot_date         = today,
        stage                 = project.stage,
        estimated_finish_date = project.estimated_finish_date,
        dev_plan_date         = project.dev_plan_date,
        risks                 = project.risks,
        to_handle             = project.to_handle,
        weekly_report_brief   = project.weekly_report_brief,
        is_active             = project.is_active,
        risk_score            = result["score"],
        days_overdue          = result["days_overdue"],
    )

    stmt = pg_insert(ProjectSnapshot).values(**values).on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_={k: v for k, v in values.items() if k not in ("project_id", "snapshot_date")},
    )
    await session.execute(stmt)

    # Prune: keep only the 52 most-recent snapshots per project
    cutoff_date = await session.scalar(
        select(ProjectSnapshot.snapshot_date)
        .where(ProjectSnapshot.project_id == project.id)
        .order_by(desc(ProjectSnapshot.snapshot_date))
        .offset(51)
        .limit(1)
    )
    if cutoff_date:
        await session.execute(
            delete(ProjectSnapshot).where(
                ProjectSnapshot.project_id == project.id,
                ProjectSnapshot.snapshot_date < cutoff_date,
            )
        )
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
docker exec shan-ai-fastapi pytest tests/test_project_learning.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/services/project_learning_service.py tests/test_project_learning.py
git commit -m "feat(projects): add save_snapshot with upsert and 52-row prune"
```

---

## Task 5: get_overview_stats + get_risk_table + get_project_detail

**Files:**
- Modify: `app/services/project_learning_service.py`

- [ ] **Step 1: Add the three query functions**

Append to `app/services/project_learning_service.py`:

```python
# ── Query functions ──────────────────────────────────────────────────────────

async def get_overview_stats(session: AsyncSession) -> dict:
    """Cross-project insights: type breakdown, delay trend, stage distribution."""
    today = date.today()

    # Latest snapshot per active project
    latest_subq = (
        select(
            ProjectSnapshot.project_id,
            func.max(ProjectSnapshot.snapshot_date).label("latest_date"),
        )
        .where(ProjectSnapshot.is_active == True)
        .group_by(ProjectSnapshot.project_id)
        .subquery()
    )

    rows = (await session.execute(
        select(Project.project_type, ProjectSnapshot.days_overdue, ProjectSnapshot.risk_score)
        .join(latest_subq, and_(
            ProjectSnapshot.project_id == latest_subq.c.project_id,
            ProjectSnapshot.snapshot_date == latest_subq.c.latest_date,
        ))
        .join(Project, Project.id == ProjectSnapshot.project_id)
        .where(Project.is_active == True)
    )).all()

    type_counts: dict = {t: {"active": 0, "delayed": 0, "at_risk": 0} for t in TYPE_ORDER}
    total_active = total_delayed = total_at_risk = 0
    for ptype, days_over, risk in rows:
        bucket = type_counts.setdefault(ptype or "אחר", {"active": 0, "delayed": 0, "at_risk": 0})
        bucket["active"] += 1
        total_active += 1
        if days_over and days_over > 0:
            bucket["delayed"] += 1
            total_delayed += 1
        if risk and risk >= 70:
            bucket["at_risk"] += 1
            total_at_risk += 1

    # Delay trend: last 8 distinct snapshot dates
    trend_rows = (await session.execute(
        select(ProjectSnapshot.snapshot_date, func.count().label("cnt"))
        .where(ProjectSnapshot.days_overdue > 0, ProjectSnapshot.is_active == True)
        .group_by(ProjectSnapshot.snapshot_date)
        .order_by(desc(ProjectSnapshot.snapshot_date))
        .limit(8)
    )).all()
    delay_trend = [{"week": str(r[0]), "count": r[1]} for r in reversed(trend_rows)]

    # Stage distribution
    stage_rows = (await session.execute(
        select(Project.stage, func.count().label("cnt"))
        .where(Project.is_active == True)
        .group_by(Project.stage)
    )).all()
    stage_dist = {(r[0] or "לא ידוע"): r[1] for r in stage_rows}

    # Next-week prediction count (projects entering risk zone)
    entering_count = 0
    risk_rows = await _raw_risk_rows(session)
    for _, _, _, sparkline, _ in risk_rows:
        pred = predict_next_score(sparkline)
        current = sparkline[-1] if sparkline else 0
        if pred is not None and current < 70 and pred >= 70:
            entering_count += 1

    return {
        "totals":             {"active": total_active, "delayed": total_delayed, "at_risk": total_at_risk, "entering_next_week": entering_count},
        "type_counts":        type_counts,
        "delay_trend":        delay_trend,
        "stage_distribution": stage_dist,
    }


async def _raw_risk_rows(session: AsyncSession) -> list:
    """Internal helper: returns (snap, proj, score_result, sparkline, predicted) tuples."""
    latest_subq = (
        select(
            ProjectSnapshot.project_id,
            func.max(ProjectSnapshot.snapshot_date).label("latest_date"),
        )
        .group_by(ProjectSnapshot.project_id)
        .subquery()
    )

    rows = (await session.execute(
        select(ProjectSnapshot, Project)
        .join(Project, Project.id == ProjectSnapshot.project_id)
        .join(latest_subq, and_(
            ProjectSnapshot.project_id == latest_subq.c.project_id,
            ProjectSnapshot.snapshot_date == latest_subq.c.latest_date,
        ))
        .where(Project.is_active == True)
        .order_by(desc(ProjectSnapshot.risk_score))
        .limit(50)
    )).all()

    result = []
    for snap, proj in rows:
        sparkline_scores = (await session.execute(
            select(ProjectSnapshot.risk_score)
            .where(
                ProjectSnapshot.project_id == proj.id,
                ProjectSnapshot.risk_score.isnot(None),
            )
            .order_by(desc(ProjectSnapshot.snapshot_date))
            .limit(8)
        )).scalars().all()
        sparkline = list(reversed(sparkline_scores))

        score_result = compute_risk_score(
            stage=proj.stage,
            estimated_finish_date=proj.estimated_finish_date,
            dev_plan_date=proj.dev_plan_date,
            risks=proj.risks,
            to_handle=proj.to_handle,
            last_updated=proj.last_updated,
        )
        predicted = predict_next_score(sparkline)
        result.append((snap, proj, score_result, sparkline, predicted))
    return result


async def get_risk_table(session: AsyncSession) -> list[dict]:
    """Projects ranked by risk score with sparklines and predictions."""
    raw = await _raw_risk_rows(session)
    out = []
    for snap, proj, score_result, sparkline, predicted in raw:
        current = snap.risk_score or 0
        entering = (predicted is not None and current < 70 and predicted >= 70)
        out.append({
            "project_id":        proj.id,
            "name":              proj.name or proj.project_identifier,
            "identifier":        proj.project_identifier,
            "type":              proj.project_type or "",
            "stage":             proj.stage or "",
            "risk_score":        current,
            "score_reliable":    score_result["reliable"],
            "sparkline":         sparkline,
            "predicted_score":   predicted,
            "entering_risk_zone": entering,
            "main_reason":       score_result["main_reason"],
        })
    return out


async def get_project_detail(project_id: int, session: AsyncSession) -> Optional[dict]:
    """Full project + snapshot history + score breakdown + finish-date drift."""
    proj = await session.scalar(select(Project).where(Project.id == project_id))
    if not proj:
        return None

    snaps = (await session.execute(
        select(ProjectSnapshot)
        .where(ProjectSnapshot.project_id == project_id)
        .order_by(ProjectSnapshot.snapshot_date.asc())
        .limit(12)
    )).scalars().all()

    prior_finish = [s.estimated_finish_date for s in snaps[-3:] if s.estimated_finish_date]
    current = compute_risk_score(
        stage=proj.stage,
        estimated_finish_date=proj.estimated_finish_date,
        dev_plan_date=proj.dev_plan_date,
        risks=proj.risks,
        to_handle=proj.to_handle,
        last_updated=proj.last_updated,
        prior_finish_dates=prior_finish,
    )

    return {
        "project": {
            "id":                   proj.id,
            "name":                 proj.name,
            "identifier":           proj.project_identifier,
            "type":                 proj.project_type,
            "stage":                proj.stage,
            "manager":              proj.manager,
            "estimated_finish_date": str(proj.estimated_finish_date) if proj.estimated_finish_date else None,
            "dev_plan_date":        str(proj.dev_plan_date) if proj.dev_plan_date else None,
            "risks":                proj.risks,
            "to_handle":            proj.to_handle,
            "weekly_report_brief":  proj.weekly_report_brief,
        },
        "snapshots": [
            {
                "snapshot_date":         str(s.snapshot_date),
                "risk_score":            s.risk_score,
                "days_overdue":          s.days_overdue,
                "stage":                 s.stage,
                "estimated_finish_date": str(s.estimated_finish_date) if s.estimated_finish_date else None,
            }
            for s in snaps
        ],
        "current_score":    current["score"],
        "score_breakdown":  current["breakdown"],
        "finish_date_drift": [
            {"date": str(s.snapshot_date), "estimated_finish_date": str(s.estimated_finish_date)}
            for s in snaps if s.estimated_finish_date
        ],
    }
```

- [ ] **Step 2: Restart and verify import**

```bash
docker-compose restart fastapi && docker logs shan-ai-fastapi --tail 10
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git add app/services/project_learning_service.py
git commit -m "feat(projects): add get_overview_stats, get_risk_table, get_project_detail"
```

---

## Task 6: Hook save_snapshot into project_sync.py

**Files:**
- Modify: `app/services/project_sync.py`

- [ ] **Step 1: Add import at top of `project_sync.py`**

Find the imports section and add:

```python
from app.services.project_learning_service import save_snapshot
```

- [ ] **Step 2: Call save_snapshot after every project commit**

In `sync_projects_file`, find the existing upsert block (around line 311–349):

```python
                if existing:
                    ...
                    if changed:
                        existing.last_updated = datetime.utcnow()
                        result["updated"] += 1
                else:
                    project = Project(project_identifier=ident, **fields)
                    session.add(project)
                    result["created"] += 1

                # Commit per row — progress is saved immediately
                await session.commit()
```

Change the commit block to:

```python
                if existing:
                    ...
                    if changed:
                        existing.last_updated = datetime.utcnow()
                        result["updated"] += 1
                else:
                    project = Project(project_identifier=ident, **fields)
                    session.add(project)
                    result["created"] += 1

                # Commit per row — progress is saved immediately
                await session.commit()

                # Save daily snapshot for learning / risk tracking
                _snap_target = existing if existing is not None else project
                try:
                    await save_snapshot(_snap_target, session)
                    await session.commit()
                except Exception as snap_exc:
                    logger.warning(f"project_sync: snapshot failed for {ident}: {snap_exc}")
                    try:
                        await session.rollback()
                    except Exception:
                        pass
```

- [ ] **Step 3: Restart and verify no errors**

```bash
docker-compose restart fastapi && docker logs shan-ai-fastapi --tail 10
```

Expected: `Application startup complete.`

- [ ] **Step 4: Trigger a sync and verify snapshot row created**

Upload any project master file through the dashboard. Then:

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT project_id, snapshot_date, risk_score, days_overdue FROM project_snapshots LIMIT 5;"
```

Expected: rows with today's date and non-null `risk_score`.

- [ ] **Step 5: Commit**

```bash
git add app/services/project_sync.py
git commit -m "feat(projects): call save_snapshot after each project upsert in sync loop"
```

---

## Task 7: Dashboard Endpoints

**Files:**
- Modify: `app/routers/dashboard.py`

- [ ] **Step 1: Add 3 endpoints**

Find `@router.get("/learning"` in `dashboard.py`. Add these 3 endpoints **before** it (so they don't get shadowed by the wildcard):

```python
@router.get("/learning/projects/overview")
async def learning_projects_overview(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.project_learning_service import get_overview_stats
    return await get_overview_stats(session)


@router.get("/learning/projects/risk-table")
async def learning_projects_risk_table(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.project_learning_service import get_risk_table
    return await get_risk_table(session)


@router.get("/learning/projects/{project_id}/detail")
async def learning_project_detail(
    project_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.project_learning_service import get_project_detail
    result = await get_project_detail(project_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result
```

- [ ] **Step 2: Restart and verify endpoints reachable**

```bash
docker-compose restart fastapi
```

Open in browser (logged in): `http://localhost:8000/dashboard/learning/projects/overview`

Expected: JSON response `{"totals": {...}, "type_counts": {...}, ...}` (may be empty if no snapshots yet)

- [ ] **Step 3: Commit**

```bash
git add app/routers/dashboard.py
git commit -m "feat(projects): add /learning/projects/* endpoints for overview, risk-table, detail"
```

---

## Task 8: learning.html — Projects Tab UI

**Files:**
- Modify: `app/templates/learning.html`

- [ ] **Step 1: Add tab bar CSS and tab container**

In `learning.html`, find `<div class="container-fluid py-4 px-4">` (line 175). Replace it with:

```html
<div class="container-fluid py-4 px-4">

<!-- ── Page-level tab bar ── -->
<div style="display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:20px;" id="learn-tab-bar">
  <button class="learn-tab learn-tab-active" onclick="switchLearnTab('decisions')" id="ltab-decisions">
    🧠 למידה וכיול
  </button>
  <button class="learn-tab" onclick="switchLearnTab('projects')" id="ltab-projects">
    🏗️ פרויקטים
  </button>
</div>

<style>
.learn-tab {
  padding: 7px 18px; font-size: .88rem; font-family: var(--ui);
  border: 1px solid transparent; border-bottom: none;
  border-radius: 6px 6px 0 0; cursor: pointer;
  background: transparent; color: var(--text-2);
  transition: all .14s;
}
.learn-tab:hover { color: var(--text-1); }
.learn-tab-active {
  background: var(--bg-card); color: var(--cyan) !important;
  border-color: var(--border); font-weight: 600;
}
/* sub-tabs inside projects panel */
.proj-stab {
  padding: 4px 14px; font-size: .8rem; border-radius: 20px;
  border: 1px solid var(--border); background: transparent;
  color: var(--text-2); cursor: pointer; font-family: var(--ui);
}
.proj-stab-active { background: var(--cyan-dim); color: var(--cyan); border-color: rgba(0,212,255,.4); }
/* KPI cards */
.proj-kpi { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; text-align: center; }
.proj-kpi-val { font-size: 1.7rem; font-weight: 700; color: var(--cyan); }
.proj-kpi-val.warn { color: #f59e0b; }
.proj-kpi-val.danger { color: var(--red); }
.proj-kpi-val.purple { color: #a78bfa; }
.proj-kpi-label { font-size: .72rem; color: var(--text-2); margin-top: 2px; }
/* Risk table */
.proj-tbl { width: 100%; border-collapse: collapse; font-size: .82rem; }
.proj-tbl th { color: var(--text-2); border-bottom: 1px solid var(--border); padding: 6px 8px; text-align: right; font-size: .72rem; text-transform: uppercase; }
.proj-tbl td { border-bottom: 1px solid #0c1422; padding: 7px 8px; vertical-align: middle; }
.proj-tbl tr:hover td { background: var(--cyan-dim); cursor: pointer; }
.score-bar-wrap { display: inline-flex; align-items: center; gap: 6px; }
.score-bar-track { width: 48px; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 3px; }
.proj-badge { font-size: .68rem; padding: 2px 7px; border-radius: 4px; }
.proj-badge-type { background: var(--cyan-dim); color: var(--cyan); }
/* Sparkline SVG */
.sparkline-svg { overflow: visible; }
</style>

<div id="learn-panel-decisions">
```

- [ ] **Step 2: Close the decisions panel div and add projects panel**

Find the very last `</div>` before `</body>` in `learning.html` and replace it with:

```html
</div><!-- end learn-panel-decisions -->

<!-- ══ PROJECTS TAB PANEL ══════════════════════════════════════════════════ -->
<div id="learn-panel-projects" style="display:none">

  <!-- Sub-tab bar -->
  <div style="display:flex;gap:8px;margin-bottom:16px;">
    <button class="proj-stab proj-stab-active" onclick="switchProjTab('overview')" id="pstab-overview">🌐 תובנות כלליות</button>
    <button class="proj-stab" onclick="switchProjTab('risk')" id="pstab-risk">⚠️ סיכון וחיזוי</button>
  </div>

  <!-- ── Overview sub-panel ── -->
  <div id="proj-panel-overview">
    <div id="proj-kpi-row" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;">
      <div class="proj-kpi"><div class="proj-kpi-val" id="pkpi-active">—</div><div class="proj-kpi-label">פרויקטים פעילים</div></div>
      <div class="proj-kpi"><div class="proj-kpi-val warn" id="pkpi-delayed">—</div><div class="proj-kpi-label">באיחור</div></div>
      <div class="proj-kpi"><div class="proj-kpi-val danger" id="pkpi-risk">—</div><div class="proj-kpi-label">סיכון גבוה (≥70)</div></div>
      <div class="proj-kpi"><div class="proj-kpi-val purple" id="pkpi-entering">—</div><div class="proj-kpi-label">חיזוי — נכנסים לסיכון</div></div>
    </div>

    <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;margin-bottom:16px;">
      <!-- Type breakdown -->
      <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:14px;">
        <div style="font-size:.75rem;color:var(--text-2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;">ניתוח לפי סוג פרויקט</div>
        <div id="proj-type-bars">טוען...</div>
      </div>
      <!-- Delay trend -->
      <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:14px;">
        <div style="font-size:.75rem;color:var(--text-2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;">מגמת איחורים — 8 שבועות</div>
        <div id="proj-trend-chart" style="display:flex;align-items:flex-end;gap:5px;height:70px;"></div>
      </div>
    </div>

    <!-- Stage distribution -->
    <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:14px;">
      <div style="font-size:.75rem;color:var(--text-2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;">התפלגות שלבים</div>
      <div id="proj-stage-dist" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
    </div>
  </div>

  <!-- ── Risk & Prediction sub-panel ── -->
  <div id="proj-panel-risk" style="display:none">
    <div style="background:rgba(167,139,250,.06);border:1px solid rgba(167,139,250,.2);border-radius:8px;padding:9px 14px;margin-bottom:12px;font-size:.8rem;color:#a78bfa;">
      ⚡ ציון סיכון = שילוב: ימי איחור · מגמת תאריך סיום · מרווח תכנון · מילות מפתח · פריטי טיפול · טריות עדכון
    </div>
    <table class="proj-tbl">
      <thead>
        <tr>
          <th>פרויקט</th><th>סוג</th><th>שלב</th><th>ציון</th>
          <th>8 שבועות</th><th>חיזוי הבא</th><th>סיבה עיקרית</th>
        </tr>
      </thead>
      <tbody id="proj-risk-tbody">
        <tr><td colspan="7" style="color:var(--text-2);text-align:center;padding:20px;">טוען...</td></tr>
      </tbody>
    </table>
    <!-- Detail card (appears inline below clicked row) -->
    <div id="proj-detail-card" style="display:none;background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:16px;margin-top:10px;"></div>
  </div>

</div><!-- end learn-panel-projects -->
```

- [ ] **Step 3: Add JavaScript at the bottom of learning.html (before `</body>`)**

Find the closing `</script>` tag of the last script block in `learning.html` and add a new script block after it:

```html
<script>
// ── Tab switching ─────────────────────────────────────────────────────────
function switchLearnTab(tab) {
  document.getElementById('learn-panel-decisions').style.display = tab === 'decisions' ? '' : 'none';
  document.getElementById('learn-panel-projects').style.display  = tab === 'projects'  ? '' : 'none';
  document.getElementById('ltab-decisions').classList.toggle('learn-tab-active', tab === 'decisions');
  document.getElementById('ltab-projects').classList.toggle('learn-tab-active',  tab === 'projects');
  if (tab === 'projects') initProjectsTab();
}

function switchProjTab(sub) {
  document.getElementById('proj-panel-overview').style.display = sub === 'overview' ? '' : 'none';
  document.getElementById('proj-panel-risk').style.display     = sub === 'risk'     ? '' : 'none';
  document.getElementById('pstab-overview').classList.toggle('proj-stab-active', sub === 'overview');
  document.getElementById('pstab-risk').classList.toggle('proj-stab-active',     sub === 'risk');
  if (sub === 'risk' && !window._riskLoaded) loadRiskTable();
}

// ── Load-once flags ───────────────────────────────────────────────────────
let _overviewLoaded = false;
window._riskLoaded = false;

function initProjectsTab() {
  if (!_overviewLoaded) { loadOverview(); _overviewLoaded = true; }
}

// ── Overview ──────────────────────────────────────────────────────────────
async function loadOverview() {
  const data = await fetch('/dashboard/learning/projects/overview').then(r => r.json());
  const t = data.totals || {};
  document.getElementById('pkpi-active').textContent   = t.active   ?? '—';
  document.getElementById('pkpi-delayed').textContent  = t.delayed  ?? '—';
  document.getElementById('pkpi-risk').textContent     = t.at_risk  ?? '—';
  document.getElementById('pkpi-entering').textContent = t.entering_next_week ?? '—';

  // Type bars
  const typeBarsEl = document.getElementById('proj-type-bars');
  typeBarsEl.innerHTML = '';
  const types = data.type_counts || {};
  const maxActive = Math.max(1, ...Object.values(types).map(v => v.active));
  for (const [name, vals] of Object.entries(types)) {
    const pct = Math.round((vals.active / maxActive) * 100);
    typeBarsEl.innerHTML += `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:7px;font-size:.8rem;">
        <span style="width:52px;color:var(--text-2);text-align:right;">${name}</span>
        <div style="flex:1;background:var(--border);border-radius:3px;height:9px;">
          <div style="width:${pct}%;height:100%;background:var(--cyan);border-radius:3px;"></div>
        </div>
        <span style="color:#f59e0b;width:18px;text-align:center;">${vals.delayed}</span>
        <span style="color:var(--red);width:18px;text-align:center;">${vals.at_risk}</span>
      </div>`;
  }

  // Trend bars
  const trendEl = document.getElementById('proj-trend-chart');
  trendEl.innerHTML = '';
  const trend = data.delay_trend || [];
  const maxCnt = Math.max(1, ...trend.map(r => r.count));
  trend.forEach(r => {
    const h = Math.max(6, Math.round((r.count / maxCnt) * 64));
    const color = r.count > maxCnt * 0.7 ? 'var(--red)' : r.count > maxCnt * 0.4 ? '#f59e0b' : 'var(--border)';
    trendEl.innerHTML += `
      <div style="display:flex;flex-direction:column;align-items:center;gap:2px;flex:1;">
        <div style="width:100%;height:${h}px;background:${color};border-radius:3px 3px 0 0;"></div>
        <span style="font-size:.6rem;color:var(--text-2);">${(r.week||'').slice(5)}</span>
      </div>`;
  });

  // Stage distribution
  const stageEl = document.getElementById('proj-stage-dist');
  stageEl.innerHTML = '';
  const stages = data.stage_distribution || {};
  const colors = ['var(--cyan)','#f59e0b','var(--red)','#a78bfa','var(--green)'];
  Object.entries(stages).forEach(([stage, cnt], i) => {
    const c = colors[i % colors.length];
    stageEl.innerHTML += `<div style="background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:6px;padding:5px 12px;font-size:.8rem;">
      <span style="color:${c};font-weight:700;">${cnt}</span> ${stage}</div>`;
  });
}

// ── Risk table ────────────────────────────────────────────────────────────
async function loadRiskTable() {
  window._riskLoaded = true;
  const tbody = document.getElementById('proj-risk-tbody');
  tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-2);text-align:center;">טוען...</td></tr>';
  const rows = await fetch('/dashboard/learning/projects/risk-table').then(r => r.json());
  tbody.innerHTML = '';
  rows.forEach(p => {
    const scoreColor = p.risk_score >= 70 ? 'var(--red)' : p.risk_score >= 40 ? '#f59e0b' : 'var(--green)';
    const spark = renderSparkline(p.sparkline, p.predicted_score);
    const predTxt = p.predicted_score === null ? '—'
      : p.entering_risk_zone ? `<span style="color:var(--red);">⚡ ${p.predicted_score}</span>`
      : `<span style="color:var(--text-2);">${p.predicted_score}</span>`;
    const reliableBadge = p.score_reliable ? '' : ' <span title="עדכון ישן" style="color:#f59e0b;font-size:.75rem;">⚠️</span>';
    tbody.innerHTML += `
      <tr onclick="toggleDetail(${p.project_id}, this)">
        <td><strong>${p.name}</strong> <span style="color:var(--text-2);font-size:.72rem;">${p.identifier}</span></td>
        <td><span class="proj-badge proj-badge-type">${p.type}</span></td>
        <td style="color:var(--text-2);">${p.stage}</td>
        <td>
          <div class="score-bar-wrap">
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${p.risk_score}%;background:${scoreColor};"></div></div>
            <span style="color:${scoreColor};font-weight:700;">${p.risk_score}</span>${reliableBadge}
          </div>
        </td>
        <td>${spark}</td>
        <td>${predTxt}</td>
        <td style="color:var(--text-2);font-size:.75rem;">${p.main_reason}</td>
      </tr>`;
  });
}

function renderSparkline(scores, predicted) {
  if (!scores || scores.length === 0) return '—';
  const all = predicted !== null ? [...scores, predicted] : scores;
  const max = Math.max(100, ...all);
  const w = 8, gap = 2, h = 24;
  const totalW = all.length * (w + gap) - gap;
  let bars = '';
  all.forEach((s, i) => {
    const barH = Math.max(2, Math.round((s / max) * h));
    const y = h - barH;
    const isDashed = (predicted !== null && i === all.length - 1);
    const color = s >= 70 ? '#ef4444' : s >= 40 ? '#f59e0b' : '#1a2d47';
    bars += isDashed
      ? `<rect x="${i*(w+gap)}" y="${y}" width="${w}" height="${barH}" fill="none" stroke="${color}" stroke-width="1" stroke-dasharray="2,2" rx="1"/>`
      : `<rect x="${i*(w+gap)}" y="${y}" width="${w}" height="${barH}" fill="${color}" rx="1"/>`;
  });
  return `<svg class="sparkline-svg" width="${totalW}" height="${h}" viewBox="0 0 ${totalW} ${h}">${bars}</svg>`;
}

let _openDetailId = null;
async function toggleDetail(projectId, row) {
  const card = document.getElementById('proj-detail-card');
  if (_openDetailId === projectId) {
    card.style.display = 'none';
    _openDetailId = null;
    return;
  }
  _openDetailId = projectId;
  card.style.display = '';
  card.innerHTML = '<div style="color:var(--text-2);">טוען פרטים...</div>';
  // Move card after clicked row
  row.parentNode.insertBefore(card, row.nextSibling);

  const d = await fetch(`/dashboard/learning/projects/${projectId}/detail`).then(r => r.json());
  const p = d.project;
  const bd = d.score_breakdown || {};

  const driftRows = (d.finish_date_drift || []).map(fd =>
    `<span style="font-size:.75rem;color:var(--text-2);">${fd.date}: <span style="color:var(--text-1);">${fd.estimated_finish_date}</span></span>`
  ).join(' → ');

  const bdRows = Object.entries(bd).map(([k, v]) =>
    `<div style="display:flex;justify-content:space-between;font-size:.78rem;margin-bottom:3px;">
      <span style="color:var(--text-2);">${k}</span>
      <span style="color:var(--cyan);">${v}</span>
    </div>`
  ).join('');

  card.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;">
      <div>
        <div style="color:var(--text-2);font-size:.75rem;margin-bottom:6px;">פרטי פרויקט</div>
        <div style="font-size:.82rem;line-height:1.7;">
          <strong>${p.name || p.identifier}</strong><br>
          מנהל: ${p.manager || '—'}<br>
          שלב: ${p.stage || '—'}<br>
          סיום משוער: ${p.estimated_finish_date || '—'}<br>
          ${p.risks ? `<span style="color:#f59e0b;">⚠️ ${p.risks.slice(0,120)}${p.risks.length>120?'...':''}</span>` : ''}
        </div>
      </div>
      <div>
        <div style="color:var(--text-2);font-size:.75rem;margin-bottom:6px;">פירוק ציון</div>
        ${bdRows}
        <div style="font-size:.72rem;color:var(--text-2);margin-top:6px;">סה"כ: <strong style="color:var(--cyan);">${d.current_score}</strong></div>
      </div>
      <div>
        <div style="color:var(--text-2);font-size:.75rem;margin-bottom:6px;">היסטוריית תאריך סיום</div>
        <div style="line-height:2;">${driftRows || 'אין מספיק היסטוריה'}</div>
      </div>
    </div>`;
}
</script>
```

- [ ] **Step 4: Restart and test in browser**

```bash
docker-compose restart fastapi
```

Navigate to `http://localhost:8000/dashboard/learning`:
1. Verify "🏗️ פרויקטים" tab appears in the tab bar
2. Click it → "תובנות כלליות" sub-tab loads with KPI cards and type bars
3. Click "⚠️ סיכון וחיזוי" → risk table loads (empty sparklines until sync runs)
4. Upload a master file → then verify sparklines and risk scores appear

- [ ] **Step 5: Commit**

```bash
git add app/templates/learning.html
git commit -m "feat(projects): add projects tab to learning page with overview and risk sub-views"
```

---

## Task 9: Railway DB Migration + Deploy

- [ ] **Step 1: Run migration on Railway DB**

```bash
docker exec shan-ai-postgres psql "$RAILWAY_DATABASE_URL" -c "
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
"
```

- [ ] **Step 2: Push and redeploy Railway**

```bash
git push origin master

TOKEN="$RAILWAY_API_TOKEN"
SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"
ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { serviceInstanceDeploy(serviceId: \\\"$SVC_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}"
```

Expected: `{"data":{"serviceInstanceDeploy":true}}`

- [ ] **Step 3: Smoke test on Railway**

1. Open `https://easygoing-endurance-production-df54.up.railway.app/dashboard/learning`
2. Click "🏗️ פרויקטים" tab
3. Upload a master file → verify risk scores appear in the table
4. Click a project row → verify detail card expands with score breakdown and drift history

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "chore: smoke-tested project learning tab on Railway"
```

---

## Verification Checklist

- [ ] `project_snapshots` table exists in both local and Railway DB
- [ ] Master file sync creates snapshot rows (check with psql query in Task 6 Step 4)
- [ ] `/dashboard/learning/projects/overview` returns valid JSON
- [ ] `/dashboard/learning/projects/risk-table` returns ranked projects with sparklines
- [ ] "🏗️ פרויקטים" tab visible and clickable in learning page
- [ ] KPI cards show correct counts
- [ ] Type bar chart renders for הקמה/הרחבה/שוש/ניידות
- [ ] Risk table rows clickable → detail card expands inline
- [ ] Sparkline last bar is dashed (prediction) for projects with ≥3 snapshots
- [ ] ⚡ indicator appears on rows entering risk zone (current<70, predicted≥70)
- [ ] ⚠️ badge on unreliable scores (last_updated >21 days)
