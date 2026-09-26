"""Data for the project page's two interactive charts. Pure — the page's
script draws them and owns the hover layer.

Colours are the reference palette's first three categorical slots, dark steps
(#3987e5 / #d95926 / #199e70), validated against the page surface (#0f1826):
all six checks pass. The risk matrix is one hue (presence), gray for repeats.
"""
from __future__ import annotations

import re
from datetime import date

from app.services.pattern_service import RISK_CATEGORIES, _snippet

def _d(s: str) -> date:
    return date.fromisoformat(s)


# ── The delay story and the risk matrix (project page, interactive) ───────
#
# Both are plain data; the page's script draws them and owns the hover layer.
# One y axis only (months) — "how late" is never plotted against a date axis.

DELAY_SERIES = (
    {"key": "fc_slip", "label": "דחייה מצטברת של יעד החשמול", "short": "יעד", "color": "#3987e5",
     "help": "כמה חודשים זז יעד החשמול המסתמן מאז הדוח הראשון"},
    {"key": "dev_slip", "label": "תזוזת תכנית הפיתוח", "short": "תכנית", "color": "#d95926",
     "help": "כמה חודשים זז יעד תכנית הפיתוח (הבסיס) מאז הדוח הראשון"},
    {"key": "gap", "label": "איחור מול התכנית", "short": "פער", "color": "#199e70",
     "help": "יעד החשמול פחות יעד תכנית הפיתוח, באותו דוח"},
)
WEEK_TEXT_CHARS = 220


def _months_between(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return round((_d(a) - _d(b)).days / 30.44, 1)


def _norm(t: str | None) -> str:
    return " ".join((t or "").split())


def _risk_text(t: dict) -> str:
    """The file's two columns, kept apart: "<risks> · לטיפול: <who>". Run
    together, "…סיום הפרויקט" + "אחר" read as one sentence."""
    risks, who = _norm(t.get("risks")), _norm(t.get("to_handle"))
    return " · ".join(x for x in (risks, f"לטיפול: {who}" if who else "") if x)


def delay_story(h: dict) -> dict | None:
    """Per report: the three delay measures plus everything a hover should
    explain — stage, what moved in this interval, the targets, the week's
    report text, the topics in it and whether the risk column changed."""
    tl = h.get("timeline") or []
    if len(tl) < 2:
        return None
    fc0 = next((t["fc"] for t in tl if t["fc"]), None)
    dev0 = next((t["dev"] for t in tl if t["dev"]), None)
    weekly = sorted(h.get("weekly") or [], key=lambda w: w["week"])
    by_to: dict[str, list[dict]] = {}
    for e in h.get("events") or []:
        by_to.setdefault(e["to"], []).append({"kind": e["kind"], "months": e["months"]})

    points, prev_risk = [], None
    for t in tl:
        week = next((w for w in reversed(weekly) if w["week"] <= t["date"]), None)
        text = week["text"] if week else ""
        risk = _risk_text(t)
        points.append({
            "date": t["date"], "stage": t["stage"], "stage_changed": t["stage_changed"],
            "fc": t["fc"], "dev": t["dev"], "fc_text": t.get("fc_text"),
            "fc_slip": _months_between(t["fc"], fc0), "dev_slip": _months_between(t["dev"], dev0),
            "gap": _months_between(t["fc"], t["dev"]),
            "moves": by_to.get(t["date"], []),
            "week": week["week"] if week else None,
            "week_text": (text[:WEEK_TEXT_CHARS] + "…") if len(text) > WEEK_TEXT_CHARS else text,
            "week_repeat": bool(week and week["same_as_before"]),
            "topics": [c for c, pat in RISK_CATEGORIES.items() if re.search(pat, text)],
            "risk_changed": prev_risk is not None and risk != prev_risk,
            "risk_text": risk[:WEEK_TEXT_CHARS] + ("…" if len(risk) > WEEK_TEXT_CHARS else ""),
        })
        prev_risk = risk

    vals = [p[s["key"]] for p in points for s in DELAY_SERIES if p[s["key"]] is not None]
    if not vals:
        return None
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    step = next(s for s in (1, 2, 3, 6, 12, 24) if (hi - lo) / s <= 6)
    y_min = step * (lo // step)
    y_max = step * -(-hi // step) if hi > 0 else step
    ticks = [round(y_min + i * step, 1) for i in range(int((y_max - y_min) / step) + 1)]
    return {"series": list(DELAY_SERIES), "points": points, "y_min": y_min, "y_max": y_max, "ticks": ticks,
            "stage_changes": [p["date"] for p in points if p["stage_changed"]]}


def risk_matrix(h: dict) -> dict | None:
    """Topic × week: was the topic written about that week, with the words.
    Plus a row for weeks whose text repeated the week before."""
    weekly = sorted(h.get("weekly") or [], key=lambda w: w["week"])
    if not weekly:
        return None
    rows = []
    for cat, pat in RISK_CATEGORIES.items():
        cells = []
        for w in weekly:
            m = re.search(pat, w["text"] or "")
            cells.append({"hit": bool(m), "quote": _snippet(w["text"], m) if m else None})
        if any(c["hit"] for c in cells):
            rows.append({"label": cat, "kind": "topic", "cells": cells, "weeks": sum(c["hit"] for c in cells)})
    rows.sort(key=lambda r: -r["weeks"])
    rows.append({"label": "דיווח זהה לשבוע הקודם", "kind": "repeat",
                 "cells": [{"hit": w["same_as_before"], "quote": None} for w in weekly],
                 "weeks": sum(w["same_as_before"] for w in weekly)})
    # Weeks the file never had a column for. The grid is one cell per report,
    # so without this a five-week hole reads as one week.
    dates = [_d(w["week"]) for w in weekly]
    missing = [0] + [max(0, round((b - a).days / 7) - 1) for a, b in zip(dates, dates[1:])]
    return {"weeks": [w["week"] for w in weekly], "rows": rows, "missing_before": missing}


def risk_column_changes(h: dict) -> list[dict]:
    """Every report where the file's risk / to-handle text changed, newest first."""
    out, prev = [], None
    for t in h.get("timeline") or []:
        cur = _risk_text(t)
        if prev is None or cur != prev:
            out.append({"date": t["date"], "text": cur or "— (ריק)", "first": prev is None})
        prev = cur
    return out[::-1]
