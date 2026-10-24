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

import re
from datetime import date

from app.services.il_format import il_dates

LEVEL_ORDER = ("high", "medium", "info")
QUOTE_CHARS = 180

# The 3D substation beside the file's own stage ladder (user decision
# 2026-10-24: the file's stages, never technical layer names). Two models:
#   ais — static/models/substation.glb, an open (air-insulated) yard (Sketchfab
#         sample, CC BY 4.0);
#   gis — a closed 161kV gas-insulated station, 8 bays (3 lines, 3
#         transformers, coupler, measurement), drawn in code by
#         static/js/gis_station.js (user decision 2026-10-24).
# Both are made of the same five layers. Each step says which layers stand,
# which are being built and which are still a blueprint, in one plain sentence
# per model. The project's step is read from its stage on
# stage_sectors.STAGE_RANK only; a stage off the ladder marks no step.
TWIN_LAYERS = ("site", "structure", "equipment", "insulators", "conductors")
_ALL = TWIN_LAYERS
_STEP_LAYERS = (   # (label, done layers, active layers) — shared by both models
    ("תכנון", (), ()),
    ("קבלת היתר", (), ()),
    ("בחירת קבלן", (), ()),
    ("לקראת ביצוע", (), ("site",)),
    ("עבודה אזרחית", (), ("site", "structure")),
    ("הרכבה חשמלית", ("site", "structure"), ("equipment", "insulators", "conductors")),
    ("בדיקות", ("site", "structure", "equipment", "insulators"), ("conductors",)),
    ("טופס 4", _ALL, ()),
    ("הסתיים", _ALL, ()),
)
TWIN_CAPTIONS = {
    "ais": (
        "התחנה כולה עדיין שרטוט כחול.",
        "התחנה כולה שרטוט — ממתינים להיתר.",
        "התחנה כולה שרטוט — עוד אין קבלן בשטח.",
        "השטח מתחיל להיבנות. כל השאר שרטוט.",
        "יסודות, קירות ומבנים נבנים. הציוד החשמלי עוד שרטוט.",
        "השטח והפלדה עומדים. הציוד, המבדדים והמוליכים מורכבים.",
        "הכול מורכב. המוליכים בבדיקות.",
        "התחנה בנויה ומוכנה לחשמול.",
        "התחנה מחושמלת — המוליכים זוהרים.",
    ),
    "mobile": (
        "התחנה כולה עדיין שרטוט כחול.",
        "התחנה כולה שרטוט — ממתינים להיתר.",
        "התחנה כולה שרטוט — עוד אין קבלן בשטח.",
        "משטח ההצבה מתחיל להיבנות. כל השאר שרטוט.",
        "משטח ההצבה, הגדר ושער הקו נבנים. הנגררים עוד לא הגיעו.",
        "האתר מוכן. הנגררים מוצבים, והשנאי, המפסק, מכולת המתח הגבוה ושנאי הבית מתחברים.",
        "הכול מחובר. בדיקות לפני חשמול.",
        "התחנה הניידת מוצבת ומוכנה לחשמול.",
        "התחנה הניידת מחושמלת — הקווים זוהרים.",
    ),
    "gis": (
        "התחנה כולה עדיין שרטוט כחול.",
        "התחנה כולה שרטוט — ממתינים להיתר.",
        "התחנה כולה שרטוט — עוד אין קבלן בשטח.",
        "השטח מתחיל להיבנות. כל השאר שרטוט.",
        "היסודות, אולם ה-GIS ומבנה הפיקוד נבנים. התאים והשנאים עוד שרטוט.",
        "המבנים עומדים. תאי ה-GIS, השנאים, המגשרים והקווים מורכבים.",
        "הכול מורכב. בדיקות לפני חשמול.",
        "התחנה בנויה ומוכנה לחשמול.",
        "התחנה מחושמלת — הקווים זוהרים.",
    ),
}
TWIN_MODELS = {"ais": "תחנה פתוחה", "gis": "תחנה סגורה (GIS)", "mobile": "תחנה ניידת"}
# lessons.projects.station_type → model (user decisions 2026-10-24). Switching
# and combined stations are read from the project's NAME first ("סגורה"/GIS,
# "פתוחה"/AIS); a name that says neither falls back per type — switching =
# closed, combined = open. A project with no type chosen shows the default.
# The page always says which model it drew and why (`model_why`).
STATION_MODEL = {"closed": "gis", "open": "ais", "mobile_substation": "mobile"}
NAME_DECIDES = {"switching": "gis", "combined": "ais"}        # type → fallback when the name is silent
NAME_CLOSED = re.compile(r"סגור|\bGIS\b", re.IGNORECASE)
NAME_OPEN = re.compile(r"פתוח|\bAIS\b", re.IGNORECASE)
DEFAULT_MODEL = "ais"
STATION_LABELS = {"closed": "תחנה סגורה", "open": "תחנה פתוחה", "switching": "תחנת מיתוג",
                  "combined": "תחנה משולבת", "mobile_substation": "תחנת משנה ניידת"}   # lessons_ui stationTypeLabels
_CLOSED_STEP = len(_STEP_LAYERS) - 1


def twin_steps(model: str) -> list[dict]:
    return [{"label": lbl, "caption": cap, "energized": i == _CLOSED_STEP,
             "layers": {k: "done" if k in done else "active" if k in active else "todo" for k in TWIN_LAYERS}}
            for i, ((lbl, done, active), cap) in enumerate(zip(_STEP_LAYERS, TWIN_CAPTIONS[model], strict=True))]


def station_model(station_type: str | None, name: str | None = None) -> tuple[str, str]:
    """(model key, one sentence saying why) for a project. Pure."""
    label = STATION_LABELS.get(station_type or "")
    if station_type in STATION_MODEL:
        return STATION_MODEL[station_type], f"לפי מערכת הלקחים: {label}"
    if station_type in NAME_DECIDES:
        text = name or ""
        if NAME_CLOSED.search(text):
            return "gis", f"{label} — סגורה לפי שם הפרויקט"
        if NAME_OPEN.search(text):
            return "ais", f"{label} — פתוחה לפי שם הפרויקט"
        model = NAME_DECIDES[station_type]
        return model, f"{label} — השם לא מציין, ולכן {TWIN_MODELS[model]}"
    if label:
        return DEFAULT_MODEL, f"{label} — עוד אין לה מודל משלה; מוצגת {TWIN_MODELS[DEFAULT_MODEL]}"
    return DEFAULT_MODEL, f"סוג התחנה לא נבחר במערכת הלקחים — מוצגת {TWIN_MODELS[DEFAULT_MODEL]}"


def twin_state(stage: str | None, weeks_in_stage: int | None = None, lower_bound: bool = False,
               station_type: str | None = None, name: str | None = None) -> dict:
    from app.services import stage_sectors
    st = stage_sectors.normalize_stage(stage)
    if stage_sectors.is_closed(st):
        current = _CLOSED_STEP
    elif st in stage_sectors.STAGE_RANK:
        current = stage_sectors.STAGE_RANK[st]
    else:
        current = None
    model, why = station_model(station_type, name)
    return {"current": current, "stage": st or None, "weeks": weeks_in_stage if current is not None else None,
            "weeks_lower_bound": bool(lower_bound),
            "model": model, "station_type": station_type, "station_label": STATION_LABELS.get(station_type or ""),
            "model_from": "default" if station_type is None or (station_type not in STATION_MODEL
                                                                and station_type not in NAME_DECIDES) else "lessons",
            "model_why": why,
            "models": {k: {"label": v, "steps": twin_steps(k)} for k, v in TWIN_MODELS.items()},
            "steps": twin_steps(model)}


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
          triage: dict | None = None, levels: dict | None = None, station_type: str | None = None) -> dict:
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
        "twin": twin_state(r.get("stage"), r.get("weeks_in_stage"), bool(r.get("weeks_lower_bound")), station_type,
                           h.get("name")),
        "delay": delay, "matrix": matrix,
        "weekly_n": len(h.get("weekly") or []),
        "repeats": sum(bool(w.get("same_as_before")) for w in h.get("weekly") or []),
    }
