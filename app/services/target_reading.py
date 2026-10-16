"""What a target cell written as TEXT actually says — and how the three targets relate.

The master file has three electrification targets per project:
    יעד תכנית פיתוח            the development plan (the baseline)
    יעד חשמול מסתמן            the forecast — often prose, not a date
    יעד חשמול מאושר ע"י מנהל האגף   the target the division manager approved

User decision 2026-10-16: prose in the forecast is READ, not shown raw. It
replaces the old "never extract a date from prose" rule, but keeps its reason:
a date the text itself calls impossible ("01/07/2026 לא אפשרי…") is a MISSED
target, never the forecast. A staged project is compared by its LAST date
(full completion). Pure — no DB, no model; every reading is a rule.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

MIN_YEAR, MAX_YEAR = 2000, 2045
OVER_APPROVED_DAYS = 15   # = pattern_service.MOVE_DAYS: less than this is re-typing, not a slip

# kind → label on screen
DATE, STAGED, UNAPPROVED, MISSED, ENERGIZED, PARTIAL, UNKNOWN = (
    "date", "staged", "unapproved", "missed", "energized", "partial", "unknown")
LABELS = {
    DATE: "תאריך (נכתב כמלל)",
    STAGED: "בשלבים — נמדד לפי השלב האחרון",
    UNAPPROVED: "צפי שאינו מאושר",
    MISSED: "היעד הוחמץ",
    ENERGIZED: "חושמל",
    PARTIAL: "חושמל חלקית",
    UNKNOWN: "אין יעד",
}
# kinds that carry a forecast date (the date is the forecast)
DATED = (DATE, STAGED, UNAPPROVED, MISSED, PARTIAL)

_SEP = r"[./\-]"
_FULL = re.compile(rf"(?<!\d)(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{4}}|\d{{2}})(?!\d)")
_MONTH = re.compile(rf"(?<![\d./])(\d{{1,2}}){_SEP}(\d{{4}})(?!\d)")   # "ב-09.2026" counts
_YEAR = re.compile(r"(?<![\d./\-])(20[2-4]\d)(?![\d./\-])")

_ENERGIZED_WORD = re.compile(r"חושמל|חשמול בוצע|הסתיים|בוצע")
_PENDING = re.compile(r"צפי|נשאר|יתרה|טרם חושמל")
_UNAPPROVED = re.compile(r"לא\s*מאושר")
_MISSED = re.compile(r"לא\s*אפשרי|לא\s*ניתן\s*לעמוד|לא\s*יעמוד|הוחמץ")
_UNKNOWN = re.compile(r"טרם\s*(נקבע|ידוע)|לא\s*ידוע|לא\s*ניתן\s*(לקבוע|להתייחס|לתת)|לא\s*נקבע|אין\s*יעד")
_REASONS = (                       # why there is no target — shown next to "אין יעד"
    (re.compile(r"נגה|תוכנית העמדה|תכנית העמדה"), "תלוי בנגה"),
    (re.compile(r"תכנון"), "ממתין לתכנון"),
    (re.compile(r"היתר"), "ממתין להיתר"),
    (re.compile(r"קבלן"), "ממתין לקבלן"),
)


def _date(d: int, m: int, y: int) -> tuple[date | None, bool]:
    """A calendar date; an impossible day (31/06, 30/02) is the month's end — approx."""
    if y < 100:
        y += 2000
    if not (1 <= m <= 12 and MIN_YEAR <= y <= MAX_YEAR and d >= 1):
        return None, False
    last = calendar.monthrange(y, m)[1]
    return (date(y, m, d), False) if d <= last else (date(y, m, last), True)


def _month_end(m: int, y: int) -> date | None:
    if not (1 <= m <= 12 and MIN_YEAR <= y <= MAX_YEAR):
        return None
    return date(y, m, calendar.monthrange(y, m)[1])


def find_dates(text: str) -> list[tuple[int, date, bool]]:
    """Every date in the text: (position, date, approx). Month-only and year-only
    targets read as the END of that month/year — a target is met by its last day."""
    out: list[tuple[int, date, bool]] = []
    taken: list[tuple[int, int]] = []
    for m in _FULL.finditer(text):
        d, approx = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append((m.start(), d, approx))
            taken.append(m.span())
    free = lambda s, e: all(e <= a or s >= b for a, b in taken)   # noqa: E731
    for m in _MONTH.finditer(text):
        if free(*m.span()):
            d = _month_end(int(m.group(1)), int(m.group(2)))
            if d:
                out.append((m.start(), d, True))
                taken.append(m.span())
    for m in _YEAR.finditer(text):                # "…ניידת 2 \n 2029": a stage of its own
        if free(*m.span()):
            out.append((m.start(), date(int(m.group(1)), 12, 31), True))
    return sorted(out)


def _energized_positions(text: str) -> list[int]:
    return [m.start() for m in re.finditer(r"חושמל", text)]


def read_text(text: str | None) -> dict | None:
    """One target cell written as prose → {kind, label, date, dates, approx, reason, text}."""
    if text is None:
        return None
    s = " ".join(str(text).split())
    if not s:
        return None
    found = find_dates(s)
    dates = [d for _, d, _ in found]
    approx = any(a for _, _, a in found)
    res = {"kind": UNKNOWN, "date": None, "dates": [d.isoformat() for d in dates],
           "approx": approx, "reason": None, "text": s}

    if _ENERGIZED_WORD.search(s):
        # a date right after (or just before) "חושמל" is when it was energized; any
        # other date — or a "צפי"/"נשאר" — is work still ahead
        epos = _energized_positions(s)
        near = lambda p: any(-12 <= p - e <= 16 for e in epos)   # noqa: E731
        pending = [d for p, d, _ in found if not near(p)]
        if pending:
            res.update(kind=PARTIAL, date=max(pending).isoformat())
        elif _PENDING.search(s):
            res.update(kind=PARTIAL)
        else:
            res.update(kind=ENERGIZED, date=max(dates).isoformat() if dates else None)
    elif _MISSED.search(s):
        # the first date is the one that will not be met; a LATER one is the new forecast
        first = dates[0] if dates else None
        later = [d for d in dates[1:] if first and d > first]
        res.update(kind=MISSED, date=max(later).isoformat() if later else None,
                   missed=first.isoformat() if first else None)
    elif _UNKNOWN.search(s) or not dates:
        res.update(kind=UNKNOWN)
    elif _UNAPPROVED.search(s):
        res.update(kind=UNAPPROVED, date=max(dates).isoformat())
    elif len(set(dates)) > 1:
        res.update(kind=STAGED, date=max(dates).isoformat())
    else:
        res.update(kind=DATE, date=dates[0].isoformat())

    if res["kind"] == UNKNOWN:
        res["reason"] = next((label for rx, label in _REASONS if rx.search(s)), None)
    res["label"] = LABELS[res["kind"]]
    return res


def effective_date(value: date | None, text: str | None) -> date | None:
    """The date a target cell stands for: the cell's own date, else what its text says."""
    if value:
        return value
    r = read_text(text)
    return date.fromisoformat(r["date"]) if r and r["kind"] in DATED and r["date"] else None


def compare(plan: date | None, forecast: date | None, forecast_text: str | None,
            approved: date | None) -> dict:
    """How the three targets relate, in days (positive = later).

    over_approved: the forecast is more than OVER_APPROVED_DAYS past the approved
    target (an EARLIER forecast is not flagged — user decision 2026-10-16).
    approved_lost: an approved target exists, but the forecast has no date
    (unknown, or a missed target with no new one).
    """
    reading = read_text(forecast_text) if forecast_text else None
    # an energized cell's date is when it WAS energized — never a forecast
    fc = forecast or (date.fromisoformat(reading["date"])
                      if reading and reading["kind"] in DATED and reading["date"] else None)
    done = bool(reading and reading["kind"] == ENERGIZED)
    days = lambda a, b: (a - b).days if a and b else None   # noqa: E731
    gap = days(fc, approved)
    return {
        "forecast": fc.isoformat() if fc else None,
        "reading": reading,
        "approved_vs_plan": days(approved, plan),     # delay the division manager accepted
        "forecast_vs_approved": None if done else gap,
        "forecast_vs_plan": None if done else days(fc, plan),
        "over_approved": (not done) and gap is not None and gap > OVER_APPROVED_DAYS,
        "approved_lost": bool(approved and not done and fc is None),
        "unapproved": bool(reading and reading["kind"] == UNAPPROVED),
    }
