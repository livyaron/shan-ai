"""Every project number on every screen — ONE definition each (user, 2026-10-16:
"the numbers must always be identical everywhere").

The dashboard's projects tile and view, the patterns page and the list behind
any card all read the SAME rows: the pattern engine's live projects
(`pattern_service.compute_patterns(...)["projects"]` — active, not finished),
filtered by `insight_access.view_for` for the viewer. Two kinds of card:

  file cards    the master file's "תקציר הנהלה" KPIs. Their formulas were
                re-run at upload (`exec_summary.resolve`) into the projects they
                count; here they are counted over the ACTIVE projects in view
                (user decision 2026-10-16: finished projects are shown apart).
  engine cards  rules over the engine rows (`ENGINE_KPIS` below).

A card's number is always `len(rows_for(...))` — so the list a click opens has
exactly as many rows as the card said. Pure: no DB access.
"""
from __future__ import annotations

from typing import Callable

from app.services import stage_sectors

Test = Callable[[dict, "str | None"], bool]


def _past_due(x: dict, as_of: str | None) -> bool:
    return bool(x.get("fc") and as_of and x["fc"] < as_of)


# key → (label, help, test(row, as_of)) — the only place these are defined
ENGINE_KPIS: dict[str, tuple[str, str, Test]] = {
    "past_due": ("יעד חשמול עבר", "היעד המסתמן לפני תאריך הדוח והפרויקט פעיל", _past_due),
    "late": ("מאחרים מתכנית הפיתוח", "יעד מסתמן מאוחר ביותר מחודש מתכנית הפיתוח",
             lambda x, _: bool(x.get("late"))),
    "over_approved": ("חורגים מהיעד המאושר", "יעד מסתמן מאוחר ביותר מ-15 יום מהיעד שאישר מנהל האגף",
                      lambda x, _: bool(x.get("over_approved"))),
    "approved_lost": ("יעד מאושר ללא צפי", "יש יעד מאושר, אבל היעד המסתמן לא נקבע / הוחמץ",
                      lambda x, _: bool(x.get("approved_lost"))),
    "undated": ("אין יעד חשמול", "המלל ביעד נקרא — ואין בו יעד", lambda x, _: bool(x.get("undated"))),
}
FILE_PREFIX = "f"          # a file card's key: f0, f1, … (its position in the summary sheet)


def _file_kpis(exec_payload: dict | None) -> list[dict]:
    return list((exec_payload or {}).get("kpis") or [])


def rows_for(key: str, rows: list[dict], as_of: str | None, exec_payload: dict | None = None
             ) -> tuple[str, str, list[dict]] | None:
    """(label, help, rows) behind one card, out of `rows` (what the viewer sees).
    None = unknown key, or a file card whose formula could not be re-run."""
    if key == "active":
        return "פרויקטים פעילים", "כל הפרויקטים בקובץ שלא הסתיימו", list(rows)
    if key in ENGINE_KPIS:
        label, help_, test = ENGINE_KPIS[key]
        return label, help_, [x for x in rows if test(x, as_of)]
    if key.startswith(SECTOR_PREFIX):   # a sector slice: same rule as the donut's count
        sector = key[len(SECTOR_PREFIX):]
        if sector in stage_sectors.SECTORS:
            return (stage_sectors.SECTORS[sector], "הפרויקטים הפעילים שהשלב הנוכחי שלהם שייך למגזר",
                    [x for x in rows if sector in (x.get("sectors") or [])])
        return None
    if key.startswith(STAGE_PREFIX):   # a stage bar: stage:<group>:<stage>
        group, _, stage = key[len(STAGE_PREFIX):].partition(":")
        if group in STAGE_GROUPS:
            return (f"{STAGE_GROUPS[group]} · {stage}", "הפרויקטים הפעילים שזה השלב הנוכחי שלהם בקובץ",
                    [x for x in rows if stage_group(x) == group and x["stage"] == stage])
        return None
    if key.startswith(FILE_PREFIX) and key[len(FILE_PREFIX):].isdigit():
        kpis = _file_kpis(exec_payload)
        i = int(key[len(FILE_PREFIX):])
        if i < len(kpis) and kpis[i].get("ids") is not None:
            ids = set(kpis[i]["ids"])
            return (kpis[i]["label"], f"מתוך ״{(exec_payload or {}).get('sheet', 'תקציר הנהלה')}״ — "
                    "נספר מחדש על הפרויקטים הפעילים בלבד",
                    [x for x in rows if x["identifier"] in ids])
    return None


STAGE_PREFIX = "stage:"
SECTOR_PREFIX = "sector:"
# the three stage breakdowns (user, 2026-10-24): the substation road in its
# order, and apart from it the energy-storage agreements and the ניידות
STAGE_GROUPS = {"road": "לפי שלב", "storage": "הסכם אגירת אנרגיה", "mobile": "ניידות"}


def stage_group(x: dict) -> str:
    """Which stage breakdown a project row is counted in — the bar and its list both ask."""
    if (x.get("project_type") or "").strip() == stage_sectors.MOBILE_TYPE:
        return "mobile"
    if stage_sectors.normalize_stage(x["stage"]) == stage_sectors.ENERGY_STORAGE:
        return "storage"
    return "road"


def stage_key(group: str, stage: str) -> str:
    return f"{STAGE_PREFIX}{group}:{stage}"


def _ordered(counts: dict[str, int], group: str) -> list[list]:
    return [[st, n, stage_key(group, st)]
            for st, n in sorted(counts.items(), key=lambda kv: stage_sectors.stage_sort_key(kv[0]))]


def board(rows: list[dict], as_of: str | None, exec_payload: dict | None = None,
          finished: int | None = None) -> dict:
    """Every card a projects screen shows, each with its number and key."""
    file_cards = []
    for i, k in enumerate(_file_kpis(exec_payload)):
        r = rows_for(f"{FILE_PREFIX}{i}", rows, as_of, exec_payload)
        file_cards.append({
            "key": f"{FILE_PREFIX}{i}", "label": k["label"],
            # a formula we could not re-run keeps the file's number, marked, with no list
            "count": len(r[2]) if r else k.get("value"), "listable": r is not None,
            "file_value": k.get("value"),
            # the file's own "total projects" card: it counts every active row — the
            # screens already show that number as "פרויקטים פעילים", so they skip it
            "is_total": bool(r and rows and len(r[2]) == len(rows)),
        })
    cards = [{"key": key, "label": label, "help": help_,
              "count": sum(1 for x in rows if test(x, as_of))}
             for key, (label, help_, test) in ENGINE_KPIS.items()]
    sectors = {k: 0 for k in stage_sectors.SECTORS}
    stages: dict[str, dict[str, int]] = {g: {} for g in STAGE_GROUPS}
    for x in rows:
        g = stage_group(x)
        stages[g][x["stage"]] = stages[g].get(x["stage"], 0) + 1
        for s in x.get("sectors") or []:
            if s in sectors:
                sectors[s] += 1
    return {
        "active": len(rows),
        "finished": finished,
        "in_file": len(rows) + finished if finished is not None else None,
        "with_approved": sum(1 for x in rows if x.get("appr")),
        "file_cards": file_cards,
        "cards": cards,
        # per-sector, never summed: one stage belongs to two sectors
        "sectors": [[k, label, sectors[k]] for k, label in stage_sectors.SECTORS.items() if sectors[k]],
        # [stage, count, key of its list] — a bar is a link to /kpi/<key>
        "stages": _ordered(stages["road"], "road"),
        "stages_storage": _ordered(stages["storage"], "storage"),
        "stages_mobile": _ordered(stages["mobile"], "mobile"),
    }
