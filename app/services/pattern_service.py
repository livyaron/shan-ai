"""Pattern & risk engine over the weekly master file (PLAN.md P2).

Reads the report-dated history P0/P1 capture (project_snapshots +
project_weekly_entries) and computes the specialist-grade metrics: slips,
baseline moves, update waves, stuck stages, stale reporting, risk categories,
leading indicators and per-sector slip attribution.

**No LLM computes a number here.** Every metric is plain pandas over the DB and
comes back as {value, n, confidence, caveat} — a number without its sample size
is how a dashboard starts lying. `load_frames` is the only function that
touches the DB; everything else is a pure function of the frames, so the tests
run without a database.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectSnapshot, ProjectWeeklyEntry
from app.services import stage_sectors as ss

MONTH_DAYS = 30.44
LATE_MONTHS = 1.0         # forecast more than a month past the plan = late
MOVE_DAYS = 15            # a date that moved less than this is re-typing, not a move
STUCK_WEEKS = 12          # same stage this long = stuck
STALE_WEEKS = 4           # identical weekly text this many weeks running = stale
STALE_MAX_GAP_DAYS = 10   # weekly entries further apart than this are not "running"
SMALL_SAMPLE = 5          # below this, shown but never ranked (PLAN.md §6)
REPORT_CLUSTER_DAYS = 3   # snapshots this close are the same weekly report
FULL_SYNC_REFERENCE_DATES = 5   # the "full file" size = median of the biggest dates
FULL_SYNC_SHARE = 0.5     # a date with fewer snapshots than this share of the
                          # reference size is a partial sync, not a report

# Taxonomy v2. v1 matched raw substrings and the real reports showed the cost:
# "הרכבת" (assembly) read as a train → ~230 false "outside party" hits;
# "מוקדם" (early) as an operations centre; "הגורמים"/"כרמיאל" as רמ"י;
# "לעלות" (to rise) and "בעלות" (ownership) as cost. v2 anchors the ambiguous
# stems on Hebrew word edges: _B = no Hebrew letter before (optionally after
# one prefix letter), _E = no Hebrew letter after. Still unvalidated by hand —
# PLAN.md §8.3 — and every hit is shown with its quote so a reader can judge.
_B = r"(?<![א-ת])"
_E = r"(?![א-ת])"
RISK_CATEGORIES: dict[str, str] = {
    "רישוי/היתרים/סטטוטוריקה": r'היתר(?!ו)|תב"?ע|ועד[הת] (?:מקומית|מחוזית)|רישוי|טופס 4|תמ"?א|ות"?ל',
    # "קרקע" alone was mostly NOT land: "עלייה לקרקע" (contractor on site, ~200
    # hits), "תת קרקעי" (cable), "זיהום/דיגום/ביסוס/יועץ קרקע" (soil). Only
    # land-rights phrases count.
    "קרקע/גישה/הסכמים": (r'(?:רכישת|זכויות|בעלות|בעלת|רישום|הקצאת) (?:על )?[בה]?קרקע|רמ"י|' + _B + r'[וב]?רמי' + _E +
                         r'|מקרקעין|הפקע|חכיר|דרך גישה|כביש גישה|זיקת|הסכם'),
    "ציוד/אספקה": (r'שנאי|אספק|' + _B + r'(?:[והבל]|מה)?ספק(?:ים|י|ית)?' + _E +
                   r'|מפסק|GIS|ציוד|ייצור|יבוא|משלוח'),
    "קבלן/מכרז": r'קבלן|מכרז|ועדת מכרזים|הזמנת עבודה|התקשרות',
    "הפסקות/תפעול רשת": r'הפסק[הות]|חלון|ניתוק|העברת עומס|מוקד(?![םמ])',
    "כוח אדם/פיקוח/בדיקות": r'משגיח|בודק|כ[ו]?ח אדם|חוסר ב(?:כ[ו]?ח|פועלים|עובדים|משגיח|בודק|צוות|אנשי)',
    "גורם חיצוני/רשויות": (r'עיריי|' + _B + r'(?:[והלב]|מ)?רשות' + _E + r'|(?:מהנדס|אדריכל(?:ית)?|ראש) (?:ה)?עיר' + _E + r'|מועצ|נת"י|' + _B + r'[לבו]?נתי' + _E + r'|נתיבי ישראל|'
                           + _B + r'[לבו]?רכבת' + _E + r'|רכבת ישראל|רכבת קלה|רט"?ג|תושב|התנגד|יישוב|קיבוץ|משרד ה'),
    "תקציב/עלות": r'תקציב|אומדן|' + _B + r'[והלמ]?עלויות|עלות ה|מימון|חריגה',
    "ביטחוני/מלחמה": r'מלחמ|ביטחונ|צבא|צה"?ל|מיגון',
}


ESCALATED = re.compile(r'חסם לטיפול')          # the file's "לטיפול" column: who must act
ESCALATED_TOP = re.compile(r'סמנכ"?ל')           # the top of that ladder

NEAR_SAME_RATIO = 95      # weekly texts this similar (0–100, punctuation ignored) count as a repeat


def _norm_text(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", t or "").split())


def near_same(a: str | None, b: str | None) -> bool:
    """Two weekly texts that differ only by a typo fix, punctuation or spacing
    are the same report — an exact hash called a one-letter edit "new"."""
    from rapidfuzz import fuzz
    if not a or not b:
        return False
    x, y = _norm_text(a), _norm_text(b)
    return x == y or fuzz.ratio(x, y) >= NEAR_SAME_RATIO


def metric(value: Any, n: int, confidence: str, caveat: str = "") -> dict:
    """confidence: high | medium | low | small_sample."""
    return {"value": value, "n": int(n), "confidence": confidence, "caveat": caveat}


def _confidence(n: int) -> str:
    if n < SMALL_SAMPLE:
        return "small_sample"
    return "high" if n >= 30 else "medium" if n >= 10 else "low"


def _months(days: float) -> float:
    return round(days / MONTH_DAYS, 1)


def fisher_exact_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test on [[a, b], [c, d]] (scipy is not a dependency)."""
    n1, n2, k = a + b, c + d, a + c
    total = math.comb(n1 + n2, k)
    if total == 0:
        return 1.0

    def prob(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, k - x) / total

    observed = prob(a)
    lo, hi = max(0, k - n2), min(k, n1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


# ── Loading ──────────────────────────────────────────────────────────────

@dataclass
class Frames:
    snaps: pd.DataFrame      # project_id, snapshot_date, stage, fc, dev, risks, to_handle, finish_date_text
    projects: pd.DataFrame   # project_id, identifier, name, manager, project_type, is_active
    weekly: pd.DataFrame     # project_id, week_date, text, text_hash


async def load_frames(session: AsyncSession) -> Frames:
    snaps = (await session.execute(select(
        ProjectSnapshot.project_id, ProjectSnapshot.snapshot_date, ProjectSnapshot.stage,
        ProjectSnapshot.estimated_finish_date.label("fc"), ProjectSnapshot.dev_plan_date.label("dev"),
        ProjectSnapshot.risks, ProjectSnapshot.to_handle, ProjectSnapshot.finish_date_text,
        ProjectSnapshot.controller,
    ))).all()
    projects = (await session.execute(select(
        Project.id.label("project_id"), Project.project_identifier.label("identifier"), Project.name,
        Project.manager, Project.project_type, Project.is_active,
    ))).all()
    weekly = (await session.execute(select(
        ProjectWeeklyEntry.project_id, ProjectWeeklyEntry.week_date,
        ProjectWeeklyEntry.text, ProjectWeeklyEntry.text_hash,
    ))).all()
    return Frames(
        snaps=pd.DataFrame(snaps, columns=["project_id", "snapshot_date", "stage", "fc", "dev",
                                           "risks", "to_handle", "finish_date_text", "controller"]),
        projects=pd.DataFrame(projects, columns=["project_id", "identifier", "name", "manager",
                                                 "project_type", "is_active"]),
        weekly=pd.DataFrame(weekly, columns=["project_id", "week_date", "text", "text_hash"]),
    )


# ── Report dates ─────────────────────────────────────────────────────────

def report_dates(snaps: pd.DataFrame) -> dict[date, date]:
    """Map every snapshot date to the report date it belongs to.

    Only full-file syncs count as reports (a date with a handful of snapshots
    is a partial sync; "full" is judged against the median of the biggest
    dates, so one inflated date cannot disqualify the rest). Pre-P0 snapshots are stamped with the upload day —
    typically the day before the report date — so dates within
    REPORT_CLUSTER_DAYS of a cluster's FIRST date collapse into one report,
    named by its report-dated snapshot (else the latest date).
    Measured from the first date, never chained: an upload day sits 6 days
    after the previous week's report, and chaining merged the two weeks.
    """
    if snaps.empty:
        return {}
    counts = snaps.groupby("snapshot_date").size()
    # Measured against the median of the biggest dates, not the single biggest:
    # one inflated pre-P0 date (every sheet of a workbook synced as projects)
    # would otherwise disqualify every genuine weekly report.
    reference = float(counts.sort_values(ascending=False).head(FULL_SYNC_REFERENCE_DATES).median())
    full = sorted(d for d, c in counts.items() if c >= FULL_SYNC_SHARE * reference)
    clusters: list[list[date]] = []
    for d in full:
        if clusters and (d - clusters[-1][0]).days <= REPORT_CLUSTER_DAYS:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    # Which date names the cluster: the report-dated one. Snapshots written
    # since P0 carry תו"ב (`controller`); pre-P0 upload-day snapshots never do.
    # Without that signal (tests, old rows only), the latest date wins.
    if "controller" in snaps.columns:
        p0_share = snaps.groupby("snapshot_date")["controller"].apply(lambda c: c.notna().mean())
    else:
        p0_share = pd.Series(0.0, index=counts.index)
    return {d: max(cl, key=lambda x: (p0_share.get(x, 0.0) > 0.5, x)) for cl in clusters for d in cl}


def canonical_snaps(snaps: pd.DataFrame) -> pd.DataFrame:
    """One snapshot per project per report date — the report-dated one when a
    cluster holds both it and an upload-day copy of the same file."""
    mapping = report_dates(snaps)
    s = snaps[snaps["snapshot_date"].isin(mapping)].copy()
    s["report_date"] = s["snapshot_date"].map(mapping)
    s["_named"] = s["snapshot_date"] == s["report_date"]
    s = (s.sort_values(["_named", "snapshot_date"])
          .drop_duplicates(["project_id", "report_date"], keep="last").drop(columns="_named"))
    for c in ("fc", "dev"):
        s[c] = pd.to_datetime(s[c])
    s["stage"] = s["stage"].map(ss.normalize_stage)
    return s


# ── The engine ───────────────────────────────────────────────────────────

def _drift(first: pd.DataFrame, last: pd.DataFrame, col: str) -> pd.Series:
    """Days a date column moved between two snapshots, per project."""
    j = last[["project_id", col]].merge(first[["project_id", col]], on="project_id", suffixes=("", "_0"))
    return (j[col] - j[f"{col}_0"]).dt.days.set_axis(j["project_id"]).dropna()


def _move_summary(days: pd.Series) -> dict:
    later = days[days > MOVE_DAYS]
    return {
        "later": int(len(later)),
        "same": int((days.abs() <= MOVE_DAYS).sum()),
        "earlier": int((days < -MOVE_DAYS).sum()),
        "total_months_later": _months(later.sum()),
        "median_months_later": _months(later.median()) if len(later) else 0.0,
    }


def _pct(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def compute_patterns(frames: Frames) -> dict:
    snaps = canonical_snaps(frames.snaps)
    if snaps.empty:
        return {"report_dates": [], "as_of": None, "metrics": {}, "league": [], "sectors": [],
                "data_quality": {}}
    rdates = sorted(snaps["report_date"].unique())
    r_first, r_last = rdates[0], rdates[-1]
    by_date = {d: g for d, g in snaps.groupby("report_date")}
    first, last = by_date[r_first], by_date[r_last]

    projects = frames.projects.set_index("project_id")
    cur = last.merge(frames.projects, on="project_id")
    cur = cur[cur["is_active"].astype(bool) & ~cur["stage"].map(ss.is_closed)].copy()
    cur["sectors"] = cur["stage"].map(ss.sectors_for)
    cur["slip_days"] = (cur["fc"] - cur["dev"]).dt.days
    live_ids = set(cur["project_id"])

    fc_drift = _drift(first, last, "fc")
    dev_drift = _drift(first, last, "dev")
    cur["fc_drift"] = cur["project_id"].map(fc_drift)
    cur["dev_drift"] = cur["project_id"].map(dev_drift)

    m: dict[str, dict] = {}
    span = f"{r_first.isoformat()} → {r_last.isoformat()}"

    # 1. Lateness against the development plan — as the report shows it today.
    slip = cur["slip_days"].dropna()
    m["slip_vs_plan"] = metric(
        {"late": int((slip > LATE_MONTHS * MONTH_DAYS).sum()),
         "on_time": int((slip <= LATE_MONTHS * MONTH_DAYS).sum()),
         "median_months": _months(slip.median()) if len(slip) else None,
         "p90_months": _months(slip.quantile(0.9)) if len(slip) else None},
        len(slip), _confidence(len(slip)),
        "נמדד מול תכנית הפיתוח העדכנית. כשהבסיס זז יחד עם התחזית (baseline_moves) המדד הזה מראה פחות איחור מהמציאות.")

    # 2–3. How the forecast and the baseline moved over the whole history.
    live_fc, live_dev = fc_drift[fc_drift.index.isin(live_ids)], dev_drift[dev_drift.index.isin(live_ids)]
    m["forecast_drift"] = metric(_move_summary(live_fc), len(live_fc), _confidence(len(live_fc)),
                                 f"יעד חשמול מסתמן, {span}.")
    m["baseline_moves"] = metric(_move_summary(live_dev), len(live_dev), _confidence(len(live_dev)),
                                 f"יעד תכנית פיתוח, {span}. בסיס שנדחה יחד עם התחזית מסתיר איחור.")

    # 4. Update waves — dates move in bursts, not weekly.
    waves = []
    for a, b in zip(rdates, rdates[1:]):
        A, B = by_date[a], by_date[b]
        f = _drift(A, B, "fc")
        d = _drift(A, B, "dev")
        f, d = f[f.index.isin(live_ids)], d[d.index.isin(live_ids)]
        both = set(f[f > MOVE_DAYS].index) & set(d[d > MOVE_DAYS].index)
        waves.append({"from": a.isoformat(), "to": b.isoformat(), "weeks": round((b - a).days / 7),
                      "n": int(len(f)), "forecast_later": int((f > MOVE_DAYS).sum()),
                      "baseline_later": int((d > MOVE_DAYS).sum()), "both": len(both)})
    m["update_waves"] = metric(waves, len(waves), "high" if len(waves) >= 4 else "low",
                               "מקטע ארוך מסתיר מתי בתוכו זזו התאריכים — גל אמיתי מזוהה רק ברצף שבועי.")

    # 5. Stuck: weeks in the current stage (a lower bound when the run starts
    #    at the project's first snapshot — the history may simply begin there).
    stuck_rows, weeks_in_stage, stage_since_start = [], {}, {}
    for pid, g in snaps[snaps["project_id"].isin(live_ids)].sort_values("report_date").groupby("project_id"):
        stages = list(g["stage"])
        dates = list(g["report_date"])
        i = len(stages) - 1
        while i > 0 and stages[i - 1] == stages[-1]:
            i -= 1
        weeks = (r_last - dates[i]).days // 7
        weeks_in_stage[pid] = weeks
        stage_since_start[pid] = i == 0
        if weeks >= STUCK_WEEKS:
            stuck_rows.append({"identifier": projects.at[pid, "identifier"], "stage": stages[-1] or "ללא סטטוס",
                               "weeks": int(weeks), "lower_bound": i == 0})
    stuck_rows.sort(key=lambda r: -r["weeks"])
    by_stage = pd.Series([r["stage"] for r in stuck_rows]).value_counts().to_dict() if stuck_rows else {}
    cur["weeks_in_stage"] = cur["project_id"].map(weeks_in_stage)
    cur["weeks_lower_bound"] = cur["project_id"].map(stage_since_start).fillna(False)
    m["stuck"] = metric({"count": len(stuck_rows), "by_stage": by_stage, "projects": stuck_rows[:50]},
                        len(live_ids), _confidence(len(live_ids)),
                        f"באותו שלב {STUCK_WEEKS} שבועות ומעלה. 'lower_bound' = ההיסטוריה מתחילה כבר בשלב הזה.")

    # 6. Stale reporting: the same weekly text, week after week.
    stale = []
    wk = frames.weekly[frames.weekly["project_id"].isin(live_ids)].sort_values("week_date")
    for pid, g in wk.groupby("project_id"):
        tail = g.tail(STALE_WEEKS)
        if len(tail) < STALE_WEEKS:
            continue
        gaps = pd.Series(tail["week_date"]).diff().dropna().map(lambda x: x.days)
        texts_ = tail["text"].tolist()
        if all(near_same(a, b) for a, b in zip(texts_, texts_[1:])) and (gaps <= STALE_MAX_GAP_DAYS).all():
            stale.append(projects.at[pid, "identifier"])
    m["stale_reporting"] = metric({"count": len(stale), "projects": sorted(stale)},
                                  len(live_ids), _confidence(len(live_ids)),
                                  f"מלל שבועי זהה (או כמעט זהה — תיקון אות או פיסוק) {STALE_WEEKS} שבועות רצופים.")

    # 7–8. Undated targets and targets already behind us.
    undated = cur[cur["finish_date_text"].notna()]
    past = cur[cur["fc"] < pd.Timestamp(r_last)]
    m["undated"] = metric({"count": len(undated), "projects": sorted(undated["identifier"])},
                          len(cur), _confidence(len(cur)), "יעד חשמול מסתמן כתוב כמלל ולא כתאריך — לא נמדד.")
    m["past_due"] = metric({"count": len(past), "projects": sorted(past["identifier"])},
                           len(cur), _confidence(len(cur)), f"יעד מסתמן לפני {r_last.isoformat()} ועדיין לא הסתיים.")

    # 9. Risk categories — what people write about.
    texts = wk.groupby("project_id")["text"].apply(" ".join)
    cur["all_text"] = (cur["risks"].fillna("") + " " + cur["to_handle"].fillna("") + " "
                       + cur["project_id"].map(texts).fillna(""))
    cats = []
    cur["risk_cats"] = [[] for _ in range(len(cur))]
    for cat, pat in RISK_CATEGORIES.items():
        hit = cur["all_text"].str.contains(pat, regex=True)
        for lst, h in zip(cur["risk_cats"], hit):
            if h:
                lst.append(cat)
        in_col = (cur["risks"].fillna("") + " " + cur["to_handle"].fillna("")).str.contains(pat, regex=True)
        cats.append({"category": cat, "projects": int(hit.sum()), "in_risk_column": int(in_col.sum())})
    m["risk_categories"] = metric(sorted(cats, key=lambda r: -r["projects"]), len(cur), _confidence(len(cur)),
                                  "מה כתוב — לא מה גורם לדחייה. טקסונומיה v2 (ביטויים רגולריים עם גבולות מילה), טרם אומתה ידנית.")

    # 10. Leading indicators: a category written about by the first report →
    #     did the forecast move later by the last one? Fisher, Bonferroni.
    early_text = frames.weekly[frames.weekly["week_date"] <= r_first].groupby("project_id")["text"].apply(" ".join)
    measured = live_fc
    early = measured.index.to_series().map(early_text).fillna("")
    cur["early_cats"] = [
        [c for c, pat in RISK_CATEGORIES.items() if re.search(pat, early_text.get(pid, "") or "")]
        for pid in cur["project_id"]]
    drifted = measured > MOVE_DAYS
    rows = []
    for cat, pat in RISK_CATEGORIES.items():
        with_ = early.str.contains(pat, regex=True)
        a, b = int((with_ & drifted).sum()), int((with_ & ~drifted).sum())
        c, d = int((~with_ & drifted).sum()), int((~with_ & ~drifted).sum())
        if a + b >= SMALL_SAMPLE and c + d >= SMALL_SAMPLE:
            rows.append({"category": cat, "with_pct": _pct(a, a + b), "with_n": a + b,
                         "without_pct": _pct(c, c + d), "without_n": c + d, "p": fisher_exact_p(a, b, c, d)})
    for r in rows:
        r["p_adjusted"] = min(1.0, r["p"] * len(rows))
        r["signal"] = "signal" if r["p_adjusted"] < 0.05 else "weak" if r["p"] < 0.05 else "none"
        r["p"], r["p_adjusted"] = round(r["p"], 4), round(r["p_adjusted"], 4)
    rows.sort(key=lambda r: r["p"])
    m["leading_indicators"] = metric(
        rows, len(measured), "low" if len(measured) < 200 else "medium",
        f"{len(rows)} השערות נבדקו; p_adjusted מתוקן Bonferroni. 'weak' = מובהק רק לפני התיקון — "
        "לא להציג למנה\"פים עד שישתחזר על נתונים חדשים (PLAN.md §8.4).")

    # 11. Slip attribution: every forecast move charged to the sector that
    #     owned the project (by stage) when it moved.
    #     Every move is also kept as an event, so each number drills down.
    #     A move is measured against the last DATED report, not the previous
    #     report: a target written as text for a few weeks ("-", "יתקבל יעד
    #     חדש") and then dated again must not swallow the move across the gap.
    move_events: list[dict] = []
    for pid, g in snaps.sort_values("report_date").groupby("project_id"):
        for col, kind in (("fc", "forecast"), ("dev", "baseline")):
            prev = None   # (value, report_date, stage) of the last dated report
            undated_between = 0
            for r in g.itertuples():
                v = getattr(r, col)
                if pd.isna(v):
                    undated_between += prev is not None
                    continue
                if prev is not None and (days := (v - prev[0]).days) > MOVE_DAYS:
                    stage = prev[2] or ""
                    move_events.append({
                        "identifier": projects.at[pid, "identifier"], "name": projects.at[pid, "name"],
                        "manager": projects.at[pid, "manager"], "kind": kind,
                        "from": prev[1].isoformat(), "to": r.report_date.isoformat(),
                        "days": int(days), "months": _months(days), "undated_between": undated_between,
                        "stage": stage or "ללא סטטוס", "sectors": list(ss.sectors_for(stage)),
                        "live": pid in live_ids})
                prev, undated_between = (v, r.report_date, r.stage), 0
    move_events.sort(key=lambda e: (e["to"], e["from"], e["kind"], e["identifier"]))
    attribution = {k: {"events": 0, "months": 0.0} for k in ss.SECTORS}
    for e in move_events:
        if e["kind"] == "forecast":
            for sec in e["sectors"]:
                attribution[sec]["events"] += 1
                attribution[sec]["months"] = round(attribution[sec]["months"] + e["days"] / MONTH_DAYS, 1)
    events = sum(v["events"] for v in attribution.values())
    m["slip_attribution"] = metric(attribution, events, _confidence(events),
                                   "כל דחייה נרשמת על המגזר שהחזיק את הפרויקט (לפי שלב) ברגע שזז. "
                                   "שלב משותף נרשם לשני המגזרים — אין לסכם לסך אגף.")

    return {
        "report_dates": [d.isoformat() for d in rdates],
        "as_of": r_last.isoformat(),
        "metrics": m,
        "league": _league(cur),
        "sectors": _sectors(cur),
        "projects": _project_rows(cur, set(stale)),
        "events": move_events,
        "data_quality": {
            "unknown_stages": sorted({s for s in cur["stage"] if s and ss.sectors_for(s) == (ss.UNKNOWN,)}),
            "no_status": int((cur["stage"] == "").sum()),
            "undated": len(undated),
            "stale_reporting": len(stale),
        },
    }


def _project_rows(cur: pd.DataFrame, stale: set) -> list[dict]:
    """One row per live project — what the per-role views filter (P3)."""
    def months(days: Any) -> float | None:
        return None if pd.isna(days) else _months(days)

    rows = []
    for r in cur.itertuples():
        rows.append({
            "identifier": r.identifier, "name": r.name, "manager": r.manager,
            "stage": r.stage or "ללא סטטוס", "sectors": list(r.sectors),
            "slip_months": months(r.slip_days),
            "forecast_moved_months": months(r.fc_drift),
            "baseline_moved_months": months(r.dev_drift),
            "forecast_moved": None if pd.isna(r.fc_drift) else bool(r.fc_drift > MOVE_DAYS),
            "baseline_moved": None if pd.isna(r.dev_drift) else bool(r.dev_drift > MOVE_DAYS),
            "stuck": not pd.isna(r.weeks_in_stage) and r.weeks_in_stage >= STUCK_WEEKS,
            "late": not pd.isna(r.slip_days) and r.slip_days > LATE_MONTHS * MONTH_DAYS,
            "risk_cats": list(r.risk_cats), "early_cats": list(r.early_cats),
            "weeks_in_stage": None if pd.isna(r.weeks_in_stage) else int(r.weeks_in_stage),
            "weeks_lower_bound": bool(r.weeks_lower_bound),
            "stale": r.identifier in stale,
            "undated": isinstance(r.finish_date_text, str),
            "fc": None if pd.isna(r.fc) else r.fc.date().isoformat(),
            "dev": None if pd.isna(r.dev) else r.dev.date().isoformat(),
        })
    # Worst first: most forecast slippage, then longest in stage.
    return sorted(rows, key=lambda x: (-(x["forecast_moved_months"] or 0), -(x["weeks_in_stage"] or 0)))


def _row_stats(g: pd.DataFrame) -> dict:
    slip, fcd, devd = g["slip_days"].dropna(), g["fc_drift"].dropna(), g["dev_drift"].dropna()
    late = int((slip > LATE_MONTHS * MONTH_DAYS).sum())
    drifted = int((fcd > MOVE_DAYS).sum())
    return {
        "n": int(len(g)),
        "late_pct": _pct(late, len(slip)),
        "drift_pct": _pct(drifted, len(fcd)),
        "baseline_moved": int((devd > MOVE_DAYS).sum()),
        "stuck": int((g["weeks_in_stage"] >= STUCK_WEEKS).sum()),
        "small_sample": len(g) < SMALL_SAMPLE,
    }


def _league(cur: pd.DataFrame) -> list[dict]:
    """The named PM table (PLAN.md D3) with its three guardrails: n and stage
    mix on every row, baseline moves beside lateness, small samples unranked."""
    rows = []
    for manager, g in cur.groupby(cur["manager"].fillna("טרם הוקצה")):
        r = {"manager": manager, **_row_stats(g),
             "stage_mix": g["stage"].replace("", "ללא סטטוס").value_counts().to_dict()}
        rows.append(r)
    ranked = sorted((r for r in rows if not r["small_sample"]), key=lambda r: -(r["late_pct"] or 0))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked + sorted((r for r in rows if r["small_sample"]), key=lambda r: r["manager"])


def _sectors(cur: pd.DataFrame) -> list[dict]:
    exploded = cur.explode("sectors")
    return [{"sector": key, "label": label, **_row_stats(exploded[exploded["sectors"] == key])}
            for key, label in ss.SECTORS.items() if (exploded["sectors"] == key).any()]


def _plain(x: Any) -> Any:
    """numpy/pandas scalars → Python, so the result is JSON as-is."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        return x.item()
    if isinstance(x, float) and math.isnan(x):
        return None
    return x


async def compute(session: AsyncSession) -> dict:
    frames = await load_frames(session)
    result = compute_patterns(frames)
    result["health"] = await data_health(session, frames)
    return _plain(result)


async def data_health(session: AsyncSession, frames: Frames) -> dict:
    """What the history tables actually hold — shown on the page so a gap in
    the data is visible to the reader, not only to someone with DB access."""
    from sqlalchemy import text
    counts = frames.snaps.groupby("snapshot_date").size().sort_index() if not frames.snaps.empty else pd.Series(dtype=int)
    mapping = report_dates(frames.snaps)
    weekly = (frames.weekly.groupby("week_date").size() if not frames.weekly.empty else pd.Series(dtype=int))
    try:
        idx = (await session.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'project_snapshots'"))).scalars().all()
    except Exception as exc:   # not Postgres (tests) or no catalog access
        idx = [f"n/a: {type(exc).__name__}"]
    return {
        "snapshot_dates": [{"date": d.isoformat(), "rows": int(c),
                            "report_date": mapping[d].isoformat() if d in mapping else None}
                           for d, c in counts.items()],
        "snapshot_rows": int(len(frames.snaps)),
        "weekly_rows": int(len(frames.weekly)),
        "weekly_range": [weekly.index.min().isoformat(), weekly.index.max().isoformat()] if len(weekly) else None,
        "snapshot_indexes": list(idx),
    }


# ── One project's full story (the project page) ──────────────────────────

SNIPPET_CHARS = 70        # context either side of a matched topic word
EVIDENCE_PER_TOPIC = 3    # newest quotes shown per topic
PEER_SLOWER_FACTOR = 1.5  # "slower than peers" = this × the stage's median


def _times(n: int) -> str:
    return "פעם אחת" if n == 1 else "פעמיים" if n == 2 else f"{n} פעמים"


def _snippet(text: str, match: re.Match) -> str:
    a, b = max(0, match.start() - SNIPPET_CHARS), min(len(text), match.end() + SNIPPET_CHARS)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def project_history(frames: Frames, p: dict, identifier: str) -> dict | None:
    """Everything the history holds about one project, plus a computed risk
    read-out. Pure: `p` is compute_patterns' output. None = unknown project.
    Every flag is a rule over the data below it — no LLM wrote any of it."""
    match = frames.projects[frames.projects["identifier"] == identifier]
    if match.empty:
        return None
    pid = match.iloc[0]["project_id"]

    snaps = canonical_snaps(frames.snaps)
    mine = snaps[snaps["project_id"] == pid].sort_values("report_date")
    timeline, prev_stage = [], None
    for r in mine.itertuples():
        timeline.append({
            "date": r.report_date.isoformat(), "stage": r.stage or "ללא סטטוס",
            "stage_changed": prev_stage is not None and r.stage != prev_stage,
            "fc": None if pd.isna(r.fc) else r.fc.date().isoformat(),
            "dev": None if pd.isna(r.dev) else r.dev.date().isoformat(),
            "fc_text": r.finish_date_text if isinstance(r.finish_date_text, str) else None,
            "risks": r.risks if isinstance(r.risks, str) else None,
            "to_handle": r.to_handle if isinstance(r.to_handle, str) else None,
        })
        prev_stage = r.stage

    wk = frames.weekly[frames.weekly["project_id"] == pid].sort_values("week_date")
    weekly, prev_text = [], None
    for r in wk.itertuples():
        weekly.append({"week": r.week_date.isoformat(), "text": r.text, "same_as_before": near_same(r.text, prev_text)})
        prev_text = r.text
    weekly.reverse()   # newest first

    row = next((x for x in p.get("projects", []) if x["identifier"] == identifier), None)
    events = [e for e in p.get("events", []) if e["identifier"] == identifier]
    fc_events = [e for e in events if e["kind"] == "forecast"]
    dev_events = [e for e in events if e["kind"] == "baseline"]

    # Topics with the words that triggered them, newest first.
    latest = mine.iloc[-1] if len(mine) else None
    current_text = " ".join(str(x) for x in ((latest.risks, latest.to_handle) if latest is not None else ())
                            if isinstance(x, str))
    sources = [("עמודת סיכונים/לטיפול", current_text)] + [(w["week"], w["text"]) for w in weekly]
    leading = {r["category"]: r for r in p.get("metrics", {}).get("leading_indicators", {}).get("value", [])}
    topics = []
    for cat, pat in RISK_CATEGORIES.items():
        evidence, weeks = [], 0
        for label, text in sources:
            m = re.search(pat, text or "")
            if m:
                weeks += label != "עמודת סיכונים/לטיפול"
                if len(evidence) < EVIDENCE_PER_TOPIC:
                    evidence.append({"source": label, "quote": _snippet(text, m)})
        if evidence:
            topics.append({"category": cat, "weeks": weeks, "evidence": evidence,
                           "leading": leading.get(cat) if row and cat in row.get("early_cats", []) else None})
    topics.sort(key=lambda t: -t["weeks"])

    # Peers: live projects in the same current stage.
    peers = None
    if row:
        same = [x for x in p.get("projects", []) if x["stage"] == row["stage"] and x["identifier"] != identifier]
        wks = sorted(x["weeks_in_stage"] for x in same if x["weeks_in_stage"] is not None)
        mv = sorted(x["forecast_moved_months"] for x in same if x["forecast_moved_months"] is not None)
        med = lambda v: v[len(v) // 2] if v else None   # noqa: E731
        peers = {"n": len(same), "median_weeks": med(wks), "median_moved_months": med(mv)}

    flags = []
    def flag(level: str, text: str) -> None:
        flags.append({"level": level, "text": text})

    if fc_events:
        total = round(sum(e["days"] for e in fc_events) / MONTH_DAYS, 1)
        flag("high" if len(fc_events) >= 2 else "medium",
             f"יעד החשמול נדחה {_times(len(fc_events))}, {total} חודשים בסך הכל."
             + (" חלק מהדחייה נמדד על פני דוחות שבהם היעד נכתב כמלל — ייתכן שהיו בהם כמה דחיות."
                if any(e.get("undated_between") for e in fc_events) else ""))
    if fc_events and dev_events:
        flag("medium", f"תכנית הפיתוח זזה {_times(len(dev_events))} יחד עם היעד — האיחור מול התכנית נראה קטן מהאמיתי.")
    if row:
        if row["late"]:
            flag("medium", f"מאחר {row['slip_months']} חודשים מול תכנית הפיתוח העדכנית.")
        if row["fc"] and p.get("as_of") and row["fc"] < p["as_of"]:
            flag("high", f"יעד החשמול המסתמן ({row['fc']}) כבר עבר והפרויקט לא הסתיים.")
        if row["undated"]:
            flag("medium", "יעד החשמול כתוב כמלל ולא כתאריך — אי אפשר למדוד אותו.")
        if row["stuck"]:
            flag("medium", (f"לפחות {row['weeks_in_stage']} שבועות בשלב \"{row['stage']}\" — ההיסטוריה מתחילה כשהפרויקט כבר בשלב הזה."
                            if row["weeks_lower_bound"] else f"{row['weeks_in_stage']} שבועות בשלב \"{row['stage']}\"."))
        if (peers and peers["n"] >= SMALL_SAMPLE and peers["median_weeks"] and row["weeks_in_stage"] is not None
                and row["weeks_in_stage"] > PEER_SLOWER_FACTOR * peers["median_weeks"]):
            flag("info", f"איטי מהרגיל לשלב: {row['weeks_in_stage']} שבועות מול חציון {peers['median_weeks']} "
                         f"ב-{peers['n']} פרויקטים באותו שלב.")
        if row["stale"]:
            flag("medium", f"הדיווח השבועי זהה {STALE_WEEKS} שבועות ברצף — ייתכן שאינו מתעדכן.")
    # The file's own escalation column ("חסם לטיפול <who>"): the PM already
    # said who must act. The VP level is the top of the ladder.
    esc = timeline[-1]["to_handle"] if timeline else None
    if esc and ESCALATED.search(esc):
        run = 0
        while run < len(timeline) and timeline[-1 - run]["to_handle"] == esc:
            run += 1
        since = timeline[-run]["date"]
        since = f"לפחות מאז {since} (הדוח הראשון בהיסטוריה)" if run == len(timeline) else f"מאז {since}"
        flag("high" if ESCALATED_TOP.search(esc) else "medium", f"בקובץ: \"{esc}\" — {since}.")
    for t in topics:
        li = t["leading"]
        if li and li["signal"] in ("signal", "weak"):
            flag("info", f"הוזכר \"{t['category']}\" כבר בדוח הראשון. בכלל האגף, {li['with_pct']}% מהפרויקטים שהזכירו אותו "
                         f"נדחו מול {li['without_pct']}% — אות {'מובהק' if li['signal'] == 'signal' else 'חלש (לפני תיקון בלבד)'}.")
    order = {"high": 0, "medium": 1, "info": 2}
    flags.sort(key=lambda f: order[f["level"]])

    info = match.iloc[0]
    return {
        "identifier": identifier, "name": info["name"], "manager": info["manager"],
        "project_type": info["project_type"], "is_active": bool(info["is_active"]),
        "row": row, "timeline": timeline, "weekly": weekly, "events": events,
        "topics": topics, "peers": peers, "flags": flags, "as_of": p.get("as_of"),
    }
