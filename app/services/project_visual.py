"""Data for the project's visual risk report (`/dashboard/projects/p/<id>/visual`).

Pure: it re-shapes what the project page already computed — `project_history`,
`project_chart.delay_story` / `risk_matrix` and the triage rule — into one
payload the page's script animates. Nothing here invents a number: every
figure on the visual page is a value from that data or a count of its rows.

Dates stay ISO (the script formats them DD/MM/YYYY for the eye); free text
that quotes dates (flag lines) goes through `il_dates` here, because the
script inserts it as-is with textContent.
"""
from __future__ import annotations

from datetime import date

from app.services.il_format import il_dates

LEVEL_ORDER = ("high", "medium", "info")
QUOTE_CHARS = 180

# The 3D substation (static/models/substation.glb, one mesh per key below), in
# construction order. Which layers stand, which are being built and which are
# still a blueprint is read from the project's stage on the stage ladder —
# never from free text. A stage off the ladder draws no claim (`twin` = None).
TWIN_LAYERS = (
    ("site", "אתר ועבודות אזרחיות"),
    ("structure", "קונסטרוקציית פלדה"),
    ("equipment", "ציוד חשמלי"),
    ("insulators", "מבדדים"),
    ("conductors", "מוליכים ופסי צבירה"),
)
_TWIN_BY_RANK = {   # rank → (done layers, active layers, phase label)
    0: ((), (), "התחנה עדיין על הנייר — שלב התכנון"),
    1: ((), (), "התחנה עדיין על הנייר — ממתינה להיתר"),
    2: ((), (), "התחנה עדיין על הנייר — בחירת קבלן"),
    3: ((), ("site",), "לקראת ביצוע — האתר מתכונן"),
    4: ((), ("site",), "עבודות אזרחיות באתר"),
    5: (("site", "structure"), ("equipment", "insulators", "conductors"), "הרכבה חשמלית"),
    6: (("site", "structure", "equipment", "insulators"), ("conductors",), "בדיקות לפני חשמול"),
    7: (("site", "structure", "equipment", "insulators", "conductors"), (), "טופס 4 — התחנה בנויה"),
}


def twin_state(stage: str | None) -> dict | None:
    from app.services import stage_sectors
    st = stage_sectors.normalize_stage(stage)
    if stage_sectors.is_closed(st):
        done, active, phase, energized = tuple(k for k, _ in TWIN_LAYERS), (), "הפרויקט הסתיים — התחנה מחושמלת", True
    elif st in stage_sectors.STAGE_RANK:
        done, active, phase = _TWIN_BY_RANK[stage_sectors.STAGE_RANK[st]]
        energized = False
        if st == "עבודה אזרחית והרכבות":
            active = ("site", "structure")
    else:
        return None
    return {"phase": phase, "energized": energized,
            "layers": [{"key": k, "label": lbl, "state": "done" if k in done else "active" if k in active else "todo"}
                       for k, lbl in TWIN_LAYERS]}


def _weeks_between(a: str, b: str) -> int:
    return max(0, round((date.fromisoformat(b) - date.fromisoformat(a)).days / 7))


def stage_journey(timeline: list[dict], as_of: str | None) -> list[dict]:
    """Consecutive reports in one stage → one segment, with its length in weeks.
    The last segment runs to the report date (it is still open)."""
    segs: list[dict] = []
    for t in timeline:
        if segs and segs[-1]["stage"] == t["stage"]:
            segs[-1]["to"] = t["date"]
            continue
        if segs:
            segs[-1]["to"] = t["date"]
        segs.append({"stage": t["stage"], "from": t["date"], "to": t["date"]})
    if segs and as_of and as_of > segs[-1]["to"]:
        segs[-1]["to"] = as_of
    for s in segs:
        s["weeks"] = _weeks_between(s["from"], s["to"])
    if segs:
        segs[-1]["current"] = True
    return segs


def build(h: dict, delay: dict | None, matrix: dict | None,
          triage: dict | None = None, levels: dict | None = None) -> dict:
    r = h.get("row") or {}
    flags = [{"level": f["level"], "text": il_dates(f["text"])} for f in h.get("flags") or []]
    counts = {lv: sum(f["level"] == lv for f in flags) for lv in LEVEL_ORDER}
    lv = None
    if triage and levels and triage.get("level") in levels:
        meta = levels[triage["level"]]
        lv = {"key": triage["level"], "icon": meta.get("icon"), "label": meta.get("label"),
              "owner": meta.get("owner"), "when": meta.get("when"),
              "reasons": [il_dates(x["text"]) for x in triage.get("reasons") or []]}
    topics = []
    for t in h.get("topics") or []:
        ev = (t.get("evidence") or [{}])[0]
        q = ev.get("quote") or ""
        topics.append({"category": t["category"], "weeks": t["weeks"], "source": ev.get("source"),
                       "quote": il_dates(q[:QUOTE_CHARS] + ("…" if len(q) > QUOTE_CHARS else ""))})
    peers = h.get("peers") or None
    return {
        "identifier": h["identifier"], "name": h.get("name") or h["identifier"],
        "manager": h.get("manager"), "as_of": h.get("as_of"), "is_active": h.get("is_active", True),
        "stage": r.get("stage"), "weeks_in_stage": r.get("weeks_in_stage"),
        "weeks_lower_bound": bool(r.get("weeks_lower_bound")),
        "targets": {"dev": r.get("dev"), "appr": r.get("appr"), "fc": r.get("fc"),
                    "fc_approx": bool(r.get("fc_approx")), "fc_label": r.get("fc_label"),
                    "fc_kind": r.get("fc_kind"), "over_approved": bool(r.get("over_approved")),
                    "appr_gap_days": r.get("appr_gap_days")},
        "kpis": {"slip_months": r.get("slip_months"), "forecast_moved_months": r.get("forecast_moved_months"),
                 "baseline_moved_months": r.get("baseline_moved_months")},
        "level": lv, "flags": flags, "counts": counts, "topics": topics, "peers": peers,
        "journey": stage_journey(h.get("timeline") or [], h.get("as_of")),
        "events": [{"from": e["from"], "to": e["to"], "kind": e["kind"], "months": e["months"],
                    "stage": e.get("stage")} for e in h.get("events") or []],
        "twin": twin_state(r.get("stage")),
        "delay": delay, "matrix": matrix,
        "weekly_n": len(h.get("weekly") or []),
        "repeats": sum(bool(w.get("same_as_before")) for w in h.get("weekly") or []),
    }
