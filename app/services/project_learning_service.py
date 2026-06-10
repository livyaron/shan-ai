"""Project learning service — risk scoring, snapshots, insight queries."""
import math
import logging
from math import ceil
from datetime import date, datetime
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
    """0-40: log-scaled overdue (steeper curve) or urgency for imminent deadlines."""
    if not estimated_finish_date:
        return 0
    days_diff = (today - estimated_finish_date).days  # positive = overdue
    if days_diff > 0:
        return min(int(35 * math.log(1 + days_diff) / math.log(50)), 40)
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
        return 15  # inverted schedule or no buffer
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
    weekly_report: Optional[str] = None,
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

    # Missing-data penalties: absence of data is suspicious, not clean
    missing = 0
    if not risks or len(risks.strip()) < 10:
        missing += 12
    if not to_handle or len(to_handle.strip()) < 10:
        missing += 8
    if not weekly_report or len(weekly_report.strip()) < 20:
        missing += 5

    score = min(schedule_pts + kw + handle + stale + missing, 100)

    breakdown = {
        "velocity":  vel,
        "overdue":   over,
        "buffer":    buf,
        "keywords":  kw,
        "to_handle": handle,
        "staleness": stale,
    }
    days_overdue = None
    if estimated_finish_date:
        d = (today - estimated_finish_date).days
        days_overdue = d if d > 0 else None

    if score == 0:
        main_reason = ""
    else:
        main_signal = max(breakdown, key=breakdown.get)
        main_reason = _MAIN_REASON_MAP.get(main_signal, "")
        if main_signal == "overdue" and days_overdue:
            main_reason = f"{days_overdue} ימי איחור"

    return {
        "score":        score,
        "reliable":     not unreliable,
        "breakdown":    breakdown,
        "main_reason":  main_reason,
        "days_overdue": days_overdue,
    }


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

    return max(0, min(100, ceil(ewma + 2 * slope)))


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
        weekly_report=project.weekly_report,
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


# ── Query functions ──────────────────────────────────────────────────────────

async def get_overview_stats(session: AsyncSession) -> dict:
    """Cross-project insights: type breakdown, delay trend, stage distribution.
    Computed live from Project rows so it works even with no snapshots.
    """
    today = date.today()
    projects = (await session.execute(
        select(Project).where(Project.is_active == True)
    )).scalars().all()

    type_counts: dict = {t: {"active": 0, "delayed": 0, "at_risk": 0} for t in TYPE_ORDER}
    total_active = total_delayed = total_at_risk = 0

    for proj in projects:
        result = compute_risk_score(
            stage=proj.stage,
            estimated_finish_date=proj.estimated_finish_date,
            dev_plan_date=proj.dev_plan_date,
            risks=proj.risks,
            to_handle=proj.to_handle,
            last_updated=proj.last_updated,
            today=today,
        )
        days_over = result["days_overdue"]
        risk = result["score"]

        bucket = type_counts.setdefault(proj.project_type or "אחר", {"active": 0, "delayed": 0, "at_risk": 0})
        bucket["active"] += 1
        total_active += 1
        if days_over and days_over > 0:
            bucket["delayed"] += 1
            total_delayed += 1
        if risk >= 70:
            bucket["at_risk"] += 1
            total_at_risk += 1

    # Trend from snapshots (historical — empty until snapshots accumulate)
    trend_rows = (await session.execute(
        select(ProjectSnapshot.snapshot_date, func.count().label("cnt"))
        .where(ProjectSnapshot.days_overdue > 0, ProjectSnapshot.is_active == True)
        .group_by(ProjectSnapshot.snapshot_date)
        .order_by(desc(ProjectSnapshot.snapshot_date))
        .limit(8)
    )).all()
    delay_trend = [{"week": str(r[0]), "count": r[1]} for r in reversed(trend_rows)]

    stage_rows = (await session.execute(
        select(Project.stage, func.count().label("cnt"))
        .where(Project.is_active == True)
        .group_by(Project.stage)
    )).all()
    stage_dist = {(r[0] or "לא ידוע"): r[1] for r in stage_rows}

    risk_rows = await _raw_risk_rows(session)
    entering_count = sum(
        1 for _, _, sparkline, predicted in risk_rows
        if predicted is not None and (sparkline[-1] if sparkline else 0) < 70 and predicted >= 70
    )

    return {
        "totals":             {"active": total_active, "delayed": total_delayed, "at_risk": total_at_risk, "entering_next_week": entering_count},
        "type_counts":        type_counts,
        "delay_trend":        delay_trend,
        "stage_distribution": stage_dist,
    }


async def _raw_risk_rows(session: AsyncSession) -> list:
    """Internal: returns (proj, score_result, sparkline, predicted) per active project.
    Computed live from Project — works even with no snapshots.
    Tuple changed: no longer includes snap as first element.
    """
    projects = (await session.execute(
        select(Project).where(Project.is_active == True)
    )).scalars().all()

    result = []
    for proj in projects:
        snap_rows = (await session.execute(
            select(ProjectSnapshot.risk_score, ProjectSnapshot.estimated_finish_date)
            .where(
                ProjectSnapshot.project_id == proj.id,
                ProjectSnapshot.risk_score.isnot(None),
            )
            .order_by(desc(ProjectSnapshot.snapshot_date))
            .limit(8)
        )).all()

        sparkline = list(reversed([r[0] for r in snap_rows]))
        # Prior finish dates for velocity (ascending chronological, most recent last)
        prior_finish_dates = [r[1] for r in reversed(snap_rows) if r[1] is not None]

        # Use stored snapshot score (calculated with full history) if available
        stored_score = sparkline[-1] if sparkline else None

        score_result = compute_risk_score(
            stage=proj.stage,
            estimated_finish_date=proj.estimated_finish_date,
            dev_plan_date=proj.dev_plan_date,
            risks=proj.risks,
            to_handle=proj.to_handle,
            last_updated=proj.last_updated,
            weekly_report=proj.weekly_report,
            prior_finish_dates=prior_finish_dates,
        )
        # Blend: if stored snapshot score is available, use the higher of the two
        # (prevents stale snapshots masking current deterioration)
        if stored_score is not None:
            score_result = {**score_result, "score": max(score_result["score"], stored_score)}

        predicted = predict_next_score(sparkline)
        result.append((proj, score_result, sparkline, predicted))

    result.sort(key=lambda x: x[1]["score"], reverse=True)
    return result[:50]


async def get_risk_table(session: AsyncSession) -> list[dict]:
    """Projects ranked by risk score with sparklines and predictions."""
    raw = await _raw_risk_rows(session)
    out = []
    for proj, score_result, sparkline, predicted in raw:
        current = score_result["score"]
        entering = (predicted is not None and current < 70 and predicted >= 70)
        out.append({
            "project_id":          proj.id,
            "name":                proj.name or proj.project_identifier,
            "identifier":          proj.project_identifier,
            "type":                proj.project_type or "",
            "stage":               proj.stage or "",
            "manager":             proj.manager or "—",
            "risk_score":          current,
            "score_reliable":      score_result["reliable"],
            "sparkline":           sparkline,
            "predicted_score":     predicted,
            "entering_risk_zone":  entering,
            "main_reason":         score_result["main_reason"],
            "weekly_report_brief": proj.weekly_report_brief or "",
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
            "id":                    proj.id,
            "name":                  proj.name,
            "identifier":            proj.project_identifier,
            "type":                  proj.project_type,
            "stage":                 proj.stage,
            "manager":               proj.manager,
            "estimated_finish_date": str(proj.estimated_finish_date) if proj.estimated_finish_date else None,
            "dev_plan_date":         str(proj.dev_plan_date) if proj.dev_plan_date else None,
            "risks":                 proj.risks,
            "to_handle":             proj.to_handle,
            "weekly_report_brief":   proj.weekly_report_brief,
        },
        "snapshots": [
            {
                "snapshot_date":          str(s.snapshot_date),
                "risk_score":             s.risk_score,
                "days_overdue":           s.days_overdue,
                "stage":                  s.stage,
                "estimated_finish_date":  str(s.estimated_finish_date) if s.estimated_finish_date else None,
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
