"""חסמים — every blocker the weekly file has reported, past and present.

A blocker EPISODE is a continuous run of weekly reports in which a project's
"פירוט סיכונים וחסמים עיקריים" cell is filled. It opens at the first report of
the run and closes at the first report where the cell is empty again (or the
project is finished / left the file). The handler is the file's own "לטיפול"
column — a role ("חסם לטיפול מנהל מגזר תכנון"), never a person.

Built on the pattern engine (`canonical_snaps`, `RISK_CATEGORIES`, the engine's
project rows) and on `insight_triage` for the handling level: the ORDER of the
blockers is a rule, never the model. `episodes` and `analyse` are pure; the AI
part at the end only writes prose around these numbers and is validated like
every other AI text in the patterns pages (`insight_ai.validate`).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from statistics import median
from typing import Any, Callable

import pandas as pd

from app.services import insight_triage as it
from app.services import pattern_service as ps
from app.services import stage_sectors as ss

logger = logging.getLogger(__name__)

NO_OWNER = "ללא גורם מטפל"
_PREFIX = re.compile(r"^\s*חסם לטיפול\s*")
AGES: tuple[tuple[str, float], ...] = (                 # weeks open (upper bound)
    ("עד חודש", 4), ("חודש עד 3 חודשים", 13), ("3 עד 6 חודשים", 26), ("מעל חצי שנה", float("inf")),
)
CHRONIC_WEEKS = 26          # open this long = a standing condition, not an event
TEXT_CHARS = 420            # a blocker's text on the page
AI_BLOCKERS = 12            # how many of the viewer's top blockers the model reads
AI_TEXT_CHARS = 240
CLOSED_SHOWN = 80


def owner_of(to_handle: object) -> str:
    """The handler as the file names it, without the "חסם לטיפול" prefix."""
    if not isinstance(to_handle, str) or not to_handle.strip():
        return NO_OWNER
    return _PREFIX.sub("", to_handle).strip() or NO_OWNER


def _text(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _weeks(a: date, b: date) -> int:
    return max(0, round((b - a).days / 7))


def episodes(frames: ps.Frames) -> tuple[list[dict[str, Any]], list[date]]:
    """(every episode, the report dates). One dict per run of filled risk cells."""
    snaps = ps.canonical_snaps(frames.snaps)
    if snaps.empty:
        return [], []
    rdates = sorted(snaps["report_date"].unique())
    first_report, as_of = rdates[0], rdates[-1]
    info = frames.projects.set_index("project_id").to_dict("index")
    out: list[dict[str, Any]] = []
    for pid, g in snaps.sort_values("report_date").groupby("project_id"):
        meta = info.get(pid) or {}
        rows = list(g.itertuples())
        run: dict[str, Any] | None = None

        def close(run: dict[str, Any], closed_at: date | None) -> None:
            last = run["rows"][-1]
            first = run["rows"][0]
            slip = (None if pd.isna(last.fc) or pd.isna(first.fc) else (last.fc - first.fc).days)
            text = _text(last.risks) or ""
            owners = []
            for r in run["rows"]:
                o = owner_of(r.to_handle)
                if not owners or owners[-1] != o:
                    owners.append(o)
            both = " ".join(x for x in (text, last.to_handle if isinstance(last.to_handle, str) else "") if x)
            end = closed_at or as_of
            out.append({
                "pid": pid, "id": meta.get("identifier") or str(pid), "name": meta.get("name") or "",
                "manager": meta.get("manager") or "", "type": meta.get("project_type") or "",
                "stage": last.stage or "ללא סטטוס", "sectors": list(ss.sectors_for(last.stage)),
                "opened": first.report_date, "last": last.report_date, "closed": closed_at,
                "open": closed_at is None,
                "weeks": _weeks(first.report_date, end), "reports": len(run["rows"]),
                # the history starts with this blocker already there: its real age is unknown
                "lower_bound": first.report_date == first_report,
                "text": text[:TEXT_CHARS] + ("…" if len(text) > TEXT_CHARS else ""),
                "changes": run["changes"], "owner": owners[-1], "owners": owners,
                "top": bool(isinstance(last.to_handle, str) and ps.ESCALATED_TOP.search(last.to_handle)),
                "topics": [c for c, pat in ps.RISK_CATEGORIES.items() if re.search(pat, both)],
                "slip_months": None if slip is None else round(slip / ps.MONTH_DAYS, 1),
            })

        for i, r in enumerate(rows):
            filled = _text(r.risks) is not None and not ss.is_closed(r.stage)
            if filled:
                if run is None:
                    run = {"rows": [r], "changes": 0}
                else:
                    if not ps.near_same(_text(r.risks), _text(run["rows"][-1].risks)):
                        run["changes"] += 1
                    run["rows"].append(r)
            elif run is not None:
                close(run, r.report_date)
                run = None
        if run is not None:
            # still filled at the project's last report: open only if that IS the latest report
            last_date = rows[-1].report_date
            later = [d for d in rdates if d > last_date]
            close(run, later[0] if later else None)
    return out, rdates


def _spread_weeks(values: list[int]) -> float | None:
    return round(float(median(values)), 1) if values else None


def analyse(frames: ps.Frames, p: dict, sees: Callable[[list, str | None, str | None], bool] | None = None,
            owner: str | None = None) -> dict[str, Any]:
    """Everything the page shows. `sees(sectors, manager, owner)` is the viewer's
    scope (insight_access.sees_blocker); `owner` narrows to one handler."""
    eps, rdates = episodes(frames)
    if not rdates:
        return {"reports": 0}
    live = {x["identifier"]: x for x in p.get("projects", [])}
    tri = it.triage_all(p)
    for e in eps:                                  # a live project: its current sectors / manager from the engine row
        row = live.get(e["id"])
        if row:
            e["sectors"], e["manager"], e["stage"] = row["sectors"], row["manager"] or "", row["stage"]
    if sees is not None:
        eps = [e for e in eps if sees(e["sectors"], e["manager"] or None, e["owner"])]

    owners_all: dict[str, int] = {}
    for e in eps:
        if e["open"]:
            owners_all[e["owner"]] = owners_all.get(e["owner"], 0) + 1
    if owner:
        eps = [e for e in eps if e["owner"] == owner]

    open_eps = [e for e in eps if e["open"]]
    closed_eps = [e for e in eps if not e["open"]]
    order = {lv: i for i, lv in enumerate(it.ORDER)}
    for e in open_eps:
        t = tri.get(e["id"]) or {"level": it.FYI, "reasons": []}
        e["level"] = t["level"]
        e["reason"] = (t["reasons"][0]["text"] if t.get("reasons") else None)
    # the order is a rule: handling level, then the top of the escalation ladder,
    # then how far the target moved while the blocker stood, then how long it stands
    open_eps.sort(key=lambda e: (order.get(e["level"], 9), not e["top"], -(e["slip_months"] or 0), -e["weeks"], e["id"]))
    for n, e in enumerate(open_eps, 1):
        e["rank"] = n

    def topic_rows() -> list[dict[str, Any]]:
        names = list(ps.RISK_CATEGORIES) + ["ללא נושא מזוהה"]
        rows = []
        for name in names:
            hit = [e for e in open_eps if (name in e["topics"]) or (name == "ללא נושא מזוהה" and not e["topics"])]
            if hit:
                rows.append({"topic": name, "open": len(hit), "median_weeks": _spread_weeks([e["weeks"] for e in hit]),
                             "slip_months": round(sum(max(0, e["slip_months"] or 0) for e in hit), 1),
                             "chronic": sum(1 for e in hit if e["weeks"] >= CHRONIC_WEEKS)})
        return sorted(rows, key=lambda r: -r["open"])

    def owner_rows() -> list[dict[str, Any]]:
        rows = []
        for name in sorted({e["owner"] for e in open_eps}):
            hit = [e for e in open_eps if e["owner"] == name]
            rows.append({"owner": name, "open": len(hit), "median_weeks": _spread_weeks([e["weeks"] for e in hit]),
                         "oldest_weeks": max(e["weeks"] for e in hit),
                         "slip_months": round(sum(max(0, e["slip_months"] or 0) for e in hit), 1)})
        return sorted(rows, key=lambda r: -r["open"])

    ages = dict.fromkeys((label for label, _ in AGES), 0)
    for e in open_eps:
        e["age"] = next(label for label, top in AGES if e["weeks"] <= top)
        ages[e["age"]] += 1

    flow = []
    for d in rdates:
        flow.append({"report": d.isoformat(),
                     "opened": sum(1 for e in eps if e["opened"] == d and not (e["lower_bound"] and d == rdates[0])),
                     "closed": sum(1 for e in eps if e["closed"] == d),
                     "open": sum(1 for e in eps if e["opened"] <= d and (e["closed"] is None or e["closed"] > d))})

    def public(e: dict[str, Any]) -> dict[str, Any]:
        keep = ("id", "name", "manager", "type", "stage", "weeks", "reports", "lower_bound", "text", "changes",
                "owner", "owners", "top", "topics", "slip_months", "level", "reason", "rank", "age")
        row = {k: e[k] for k in keep if k in e}
        row["opened"] = e["opened"].isoformat()
        row["closed"] = e["closed"].isoformat() if e["closed"] else None
        return row

    return {
        "reports": len(rdates), "first_report": rdates[0].isoformat(), "as_of": rdates[-1].isoformat(),
        "owner": owner, "owners": sorted(({"owner": k, "open": v} for k, v in owners_all.items()), key=lambda r: -r["open"]),
        "now": {
            "open": len(open_eps), "projects": len({e["id"] for e in open_eps}),
            "unowned": sum(1 for e in open_eps if e["owner"] == NO_OWNER),
            "top": sum(1 for e in open_eps if e["top"]),
            "chronic": sum(1 for e in open_eps if e["weeks"] >= CHRONIC_WEEKS),
            "median_weeks": _spread_weeks([e["weeks"] for e in open_eps]),
            "slip_months": round(sum(max(0, e["slip_months"] or 0) for e in open_eps), 1),
            "levels": {lv: sum(1 for e in open_eps if e["level"] == lv) for lv in it.ORDER},
        },
        "history": {
            "closed": len(closed_eps), "median_weeks_closed": _spread_weeks([e["weeks"] for e in closed_eps]),
            "ever": len(eps),
        },
        "by_topic": topic_rows(), "by_owner": owner_rows(), "ages": ages,
        "order": {"ages": [label for label, _ in AGES], "levels": list(it.ORDER)},
        "levels": {lv: {"icon": it.LEVELS[lv]["icon"], "label": it.LEVELS[lv]["label"]} for lv in it.ORDER},
        "flow": flow,
        "open": [public(e) for e in open_eps],
        "closed": [public(e) for e in sorted(closed_eps, key=lambda e: e["closed"], reverse=True)[:CLOSED_SHOWN]],
        "chronic_weeks": CHRONIC_WEEKS,
    }


# ── AI: recommendations for the viewer's top blockers (prose only) ─────────

SYSTEM = """אתה יועץ בכיר למנהל באגף פרויקטים של תחנות משנה בחברת חשמל. אתה מקבל את רשימת החסמים הפתוחים שבתחומו, כפי שחושבה מהדוחות השבועיים (בלי מודל שפה), לפי סדר עדיפות שנקבע בכללים. תפקידך: לומר לו מה לעשות.

""" + """כללים מחייבים:
1. השתמש רק בעובדות ובמספרים שמופיעים בנתונים. אסור לחשב, להמציא או לעגל מספרים.
2. סדר העדיפות ודרגת הטיפול כבר נקבעו בכללים. אל תשנה אותם.
3. זהה פרויקט רק לפי המזהה בסוגריים המרובעים, בדיוק כפי שהוא כתוב.
4. לכל ממצא: מה החסם באמת (סיבת השורש, לא חזרה על המלל), למה עכשיו, ופעולה אחת שמנהל יכול לבצע מחר בבוקר — מי, מה, ועד מתי במילים (השבוע, עד הדוח הבא, תוך חודש), לא תאריך.
5. חפש חסמים שחוזרים באותו גורם מטפל או באותו נושא ואפשר לפתור בפעולה אחת, וחסמים בלי גורם מטפל.
6. עברית בלבד. בלי markdown. בלי מרכאות כפולות רגילות בתוך טקסט — השתמש בגרשיים ״ או בגרש ׳.
7. החזר אובייקט JSON תקין בלבד, בצורה:
{"headline": "משפט אחד", "situation": "שניים-שלושה משפטים", "findings": [{"title": "", "analysis": "", "projects": ["מזהה"], "action": "", "owner": ""}], "patterns": [""], "questions": [""]}
עד 6 ממצאים, לפי סדר העדיפות שקיבלת."""


def ai_kind(scope_key: str, owner: str | None) -> str:
    return f"blockers:{scope_key}:{hashlib.md5((owner or 'all').encode()).hexdigest()[:8]}"


def ai_context(data: dict[str, Any]) -> str:
    now = data["now"]
    lines = [
        f"נכון לדוח {data['as_of']}. חסמים פתוחים בתחום הצופה: {now['open']} ב-{now['projects']} פרויקטים."
        + (f" מסונן לגורם המטפל: {data['owner']}." if data.get("owner") else ""),
        f"בלי גורם מטפל: {now['unowned']}. פתוחים {data['chronic_weeks']} שבועות ומעלה: {now['chronic']}. "
        f"חודשי הזזת יעד בזמן שהחסמים פתוחים: {now['slip_months']}.",
        "לפי גורם מטפל: " + " · ".join(f"{r['owner']} {r['open']}" for r in data["by_owner"][:9]),
        "לפי נושא: " + " · ".join(f"{r['topic']} {r['open']}" for r in data["by_topic"][:9]),
        "", "החסמים לפי סדר העדיפות:",
    ]
    for e in data["open"][:AI_BLOCKERS]:
        lv = data["levels"][e["level"]]
        text = e["text"][:AI_TEXT_CHARS].replace('"', "״").replace("'", "׳").replace("\n", " ")
        lines.append(
            f"{e['rank']}. [{e['id']}] {e['name'][:60]} | {lv['icon']} {lv['label']} | שלב: {e['stage']} | גורם מטפל: {e['owner']} | "
            f"פתוח {'לפחות ' if e['lower_bound'] else ''}{e['weeks']} שבועות | "
            + (f"היעד זז {e['slip_months']} חודשים מאז | " if e.get("slip_months") else "")
            + (f"נושאים: {', '.join(e['topics'])} | " if e["topics"] else "") + f"החסם: {text}")
    return "\n".join(lines)


async def recommend(session, data: dict[str, Any], kind: str) -> dict | None:
    """One model call → validated → stored for this report date. None on failure."""
    from app.services import insight_ai
    if not data.get("open"):
        return None
    ctx = ai_context(data)
    try:
        reply, provider = await insight_ai._ask(SYSTEM, ctx, "area")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"blockers: AI recommendations failed: {type(e).__name__}: {str(e)[:160]}")
        return None
    allowed = {e["id"] for e in data["open"][:AI_BLOCKERS]}
    payload = insight_ai.validate(reply, ctx, allowed, [])
    try:
        await insight_ai._store(session, kind, date.fromisoformat(data["as_of"]), payload, provider)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"blockers: storing AI recommendations failed: {e}")
        await session.rollback()
    return {"payload": payload, "provider": provider, "created_at": None}
