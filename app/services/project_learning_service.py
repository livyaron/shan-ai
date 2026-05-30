"""Project learning service — risk scoring, snapshots, insight queries."""
import math
import logging
from datetime import date, datetime, timedelta
from typing import Optional

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
        return min(int(25 * math.log(1 + days_diff) / math.log(60)), 30)
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
        "score":        score,
        "reliable":     not unreliable,
        "breakdown":    breakdown,
        "main_reason":  main_reason,
        "days_overdue": days_overdue,
    }
