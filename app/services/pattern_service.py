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
FULL_SYNC_SHARE = 0.5     # a date with fewer snapshots than this share of the
                          # biggest one is a partial sync, not a report

# v1 taxonomy — validate on a hand-labelled sample before showing it to PMs
# (PLAN.md §8.3). Substring patterns tolerate Hebrew prefixes (ב/ה/ל/ש/ו).
RISK_CATEGORIES: dict[str, str] = {
    "רישוי/היתרים/סטטוטוריקה": r'היתר|תב"?ע|ועד[הת] (?:מקומית|מחוזית)|רישוי|טופס 4|תמ"?א|ות"?ל',
    "קרקע/גישה/הסכמים": r'קרקע|רמ"?י|מקרקעין|הפקע|חכיר|דרך גישה|כביש גישה|זיקת|הסכם',
    "ציוד/אספקה": r'שנאי|אספק|ספק|מפסק|GIS|ציוד|ייצור|יבוא|משלוח',
    "קבלן/מכרז": r'קבלן|מכרז|ועדת מכרזים|הזמנת עבודה|התקשרות',
    "הפסקות/תפעול רשת": r'הפסק[הות]|חלון|ניתוק|העברת עומס|מוקד',
    "כוח אדם/פיקוח/בדיקות": r'משגיח|בודק|כ[ו]?ח אדם|חוסר ב',
    "גורם חיצוני/רשויות": r'עיריי|רשות|מועצ|נת"?י|נתיבי|רכבת|רט"?ג|תושב|התנגד|יישוב|קיבוץ|משרד ה',
    "תקציב/עלות": r'תקציב|אומדן|עלות|מימון|חריגה',
    "ביטחוני/מלחמה": r'מלחמ|ביטחונ|צבא|צה"?ל|מיגון',
}


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
                                           "risks", "to_handle", "finish_date_text"]),
        projects=pd.DataFrame(projects, columns=["project_id", "identifier", "name", "manager",
                                                 "project_type", "is_active"]),
        weekly=pd.DataFrame(weekly, columns=["project_id", "week_date", "text", "text_hash"]),
    )


# ── Report dates ─────────────────────────────────────────────────────────

def report_dates(snaps: pd.DataFrame) -> dict[date, date]:
    """Map every snapshot date to the report date it belongs to.

    Only full-file syncs count as reports (a date with a handful of snapshots
    is a partial sync). Pre-P0 snapshots are stamped with the upload day —
    typically the day before the report date — so dates within
    REPORT_CLUSTER_DAYS of a cluster's FIRST date collapse onto its latest.
    Measured from the first date, never chained: an upload day sits 6 days
    after the previous week's report, and chaining merged the two weeks.
    """
    if snaps.empty:
        return {}
    counts = snaps.groupby("snapshot_date").size()
    full = sorted(d for d, c in counts.items() if c >= FULL_SYNC_SHARE * counts.max())
    clusters: list[list[date]] = []
    for d in full:
        if clusters and (d - clusters[-1][0]).days <= REPORT_CLUSTER_DAYS:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    return {d: cl[-1] for cl in clusters for d in cl}


def canonical_snaps(snaps: pd.DataFrame) -> pd.DataFrame:
    """One snapshot per project per report date (the latest within a cluster)."""
    mapping = report_dates(snaps)
    s = snaps[snaps["snapshot_date"].isin(mapping)].copy()
    s["report_date"] = s["snapshot_date"].map(mapping)
    s = s.sort_values("snapshot_date").drop_duplicates(["project_id", "report_date"], keep="last")
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
    stuck_rows, weeks_in_stage = [], {}
    for pid, g in snaps[snaps["project_id"].isin(live_ids)].sort_values("report_date").groupby("project_id"):
        stages = list(g["stage"])
        dates = list(g["report_date"])
        i = len(stages) - 1
        while i > 0 and stages[i - 1] == stages[-1]:
            i -= 1
        weeks = (r_last - dates[i]).days // 7
        weeks_in_stage[pid] = weeks
        if weeks >= STUCK_WEEKS:
            stuck_rows.append({"identifier": projects.at[pid, "identifier"], "stage": stages[-1] or "ללא סטטוס",
                               "weeks": int(weeks), "lower_bound": i == 0})
    stuck_rows.sort(key=lambda r: -r["weeks"])
    by_stage = pd.Series([r["stage"] for r in stuck_rows]).value_counts().to_dict() if stuck_rows else {}
    cur["weeks_in_stage"] = cur["project_id"].map(weeks_in_stage)
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
        if tail["text_hash"].nunique() == 1 and (gaps <= STALE_MAX_GAP_DAYS).all():
            stale.append(projects.at[pid, "identifier"])
    m["stale_reporting"] = metric({"count": len(stale), "projects": sorted(stale)},
                                  len(live_ids), _confidence(len(live_ids)),
                                  f"מלל שבועי זהה {STALE_WEEKS} שבועות רצופים.")

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
    for cat, pat in RISK_CATEGORIES.items():
        hit = cur["all_text"].str.contains(pat, regex=True)
        in_col = (cur["risks"].fillna("") + " " + cur["to_handle"].fillna("")).str.contains(pat, regex=True)
        cats.append({"category": cat, "projects": int(hit.sum()), "in_risk_column": int(in_col.sum())})
    m["risk_categories"] = metric(sorted(cats, key=lambda r: -r["projects"]), len(cur), _confidence(len(cur)),
                                  "מה כתוב — לא מה גורם לדחייה. טקסונומיה v1 (ביטויים רגולריים), טרם אומתה ידנית.")

    # 10. Leading indicators: a category written about by the first report →
    #     did the forecast move later by the last one? Fisher, Bonferroni.
    early_text = frames.weekly[frames.weekly["week_date"] <= r_first].groupby("project_id")["text"].apply(" ".join)
    measured = live_fc
    early = measured.index.to_series().map(early_text).fillna("")
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
    attribution = {k: {"events": 0, "months": 0.0} for k in ss.SECTORS}
    for a, b in zip(rdates, rdates[1:]):
        A, B = by_date[a], by_date[b]
        f = _drift(A, B, "fc")
        moved = f[f > MOVE_DAYS]
        stage_at = A.set_index("project_id")["stage"]
        for pid, days in moved.items():
            for sec in ss.sectors_for(stage_at.get(pid)):
                attribution[sec]["events"] += 1
                attribution[sec]["months"] = round(attribution[sec]["months"] + days / MONTH_DAYS, 1)
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
        "data_quality": {
            "unknown_stages": sorted({s for s in cur["stage"] if s and ss.sectors_for(s) == (ss.UNKNOWN,)}),
            "no_status": int((cur["stage"] == "").sum()),
            "undated": len(undated),
            "stale_reporting": len(stale),
        },
    }


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
