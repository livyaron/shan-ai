"""Handling level per project (PLAN.md P5) — a rule, never the model.

Four levels by who must act (user decision 2026-09-28). A project takes the
highest level any of its rules reaches; every rule that fired is kept as a
reason, so the screen shows WHY and the AI is told the level, not asked for it.
Pure: reads the engine's project rows and move events (pattern_service).
"""
from __future__ import annotations

from app.services.pattern_service import BIG_SLIP_MONTHS

ESCALATE, WEEK, FOLLOW, FYI = "escalate", "week", "follow", "fyi"
ORDER = (ESCALATE, WEEK, FOLLOW, FYI)            # worst first
LEVELS = {
    ESCALATE: {"icon": "🔴", "label": "הסלמה", "owner": 'סמנכ"ל', "when": "עכשיו"},
    WEEK:     {"icon": "🟠", "label": "טיפול מנהל אגף", "owner": "מנהל האגף", "when": "השבוע"},
    FOLLOW:   {"icon": "🟡", "label": "מעקב", "owner": 'מנהל המגזר / מנה"פ', "when": "מעקב שוטף"},
    FYI:      {"icon": "🟢", "label": "לידיעה", "owner": "—", "when": "—"},
}

MANY_MOVES = 3              # this many forecast moves = the plan is not holding
LONG_STUCK_WEEKS = 24       # twice the engine's "stuck" = the division manager's problem


def fc_move_counts(events: list[dict]) -> dict[str, int]:
    """Forecast moves per project, live projects only."""
    counts: dict[str, int] = {}
    for e in events:
        if e["kind"] == "forecast" and e.get("live", True):
            counts[e["identifier"]] = counts.get(e["identifier"], 0) + 1
    return counts


def triage_project(x: dict, moves: int, as_of: str | None) -> dict:
    """{level, reasons: [(level, text)]} for one engine project row."""
    reasons: list[tuple[str, str]] = []

    def hit(level: str, text: str) -> None:
        reasons.append((level, text))

    past_due = bool(x.get("fc") and as_of and x["fc"] < as_of)
    moved = x.get("forecast_moved_months")
    big = moved is not None and moved >= BIG_SLIP_MONTHS

    if x.get("frozen"):
        hit(ESCALATE, "מוקפא או נבחן לביטול לפי הדיווח")
    if x.get("escalation_top"):
        hit(ESCALATE, f"בקובץ: {x['escalation']}")
    if past_due and big:
        hit(ESCALATE, f"היעד ({x['fc']}) עבר והוא נדחה {moved} חודשים")
    if moves >= MANY_MOVES:
        hit(ESCALATE, f"יעד החשמול נדחה {moves} פעמים")

    if past_due and not big:
        hit(WEEK, f"יעד מסתמן ({x['fc']}) כבר עבר")
    if big and not past_due:
        hit(WEEK, f"יעד החשמול נדחה {moved} חודשים בסך הכל")
    if x.get("forecast_moved") and x.get("baseline_moved"):
        hit(WEEK, "תכנית הפיתוח זזה יחד עם היעד — האיחור מול התכנית מוסתר")
    if x.get("escalation") and not x.get("escalation_top"):
        hit(WEEK, f"בקובץ: {x['escalation']}")
    weeks = x.get("weeks_in_stage")
    if weeks is not None and weeks >= LONG_STUCK_WEEKS:
        hit(WEEK, f"{'לפחות ' if x.get('weeks_lower_bound') else ''}{weeks} שבועות בשלב {x['stage']}")

    if x.get("stuck") and not (weeks is not None and weeks >= LONG_STUCK_WEEKS):
        hit(FOLLOW, f"{'לפחות ' if x.get('weeks_lower_bound') else ''}{weeks} שבועות בשלב {x['stage']}")
    if x.get("late"):
        hit(FOLLOW, f"מאחר {x.get('slip_months')} חודשים מול תכנית הפיתוח")
    if x.get("undated"):
        hit(FOLLOW, "יעד החשמול כתוב כמלל")
    if x.get("stale"):
        hit(FOLLOW, "דיווח שבועי זהה 4 שבועות")
    if x.get("stage_gap"):
        hit(FOLLOW, "השלב בקובץ לא תואם לדיווח האחרון")
    if x.get("forecast_moved") and moves < MANY_MOVES and not big:
        hit(FOLLOW, "יעד החשמול נדחה")

    level = min((ORDER.index(lv) for lv, _ in reasons), default=ORDER.index(FYI))
    reasons.sort(key=lambda r: ORDER.index(r[0]))
    return {"level": ORDER[level], "reasons": [{"level": lv, "text": t} for lv, t in reasons]}


def triage_all(p: dict) -> dict[str, dict]:
    """identifier → triage, for every live project in the engine output."""
    counts = fc_move_counts(p.get("events", []))
    return {x["identifier"]: triage_project(x, counts.get(x["identifier"], 0), p.get("as_of"))
            for x in p.get("projects", [])}


def board(projects: list[dict], tri: dict[str, dict]) -> dict[str, list[dict]]:
    """The viewer's projects grouped by level (worst first), each row carrying
    its reasons. Only rows the viewer already sees go in."""
    out: dict[str, list[dict]] = {lv: [] for lv in ORDER}
    for x in projects:
        t = tri.get(x["identifier"])
        if t:
            out[t["level"]].append({**x, "triage": t})
    return out
