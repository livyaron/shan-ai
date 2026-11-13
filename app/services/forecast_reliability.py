"""How far the energization forecast moves — by stage, sector, project type and
by how far ahead the forecast looked.

Built on the pattern engine's own reading of the weekly file (`canonical_snaps`,
`MOVE_DAYS`, `stage_sectors`) — nothing is re-spelled here. `compute` is pure;
the frames come from `pattern_service.load_frames`. Numbers only (stage, sector
and project-type names, never a project or a person), so the result can be
written to the log: one line starting with LOG_PREFIX.

A move is measured between two consecutive DATED reports of the same project
and belongs to the stage the project was in at the earlier one. A project with
no realized date yet is measured against its LATEST forecast, so every figure
here is a lower bound on the real slip.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

import pandas as pd

from app.services import pattern_service as ps
from app.services import stage_sectors as ss

logger = logging.getLogger(__name__)

LOG_PREFIX = "FORECAST_RELIABILITY "
YEAR_DAYS = 365.25
HORIZONS: tuple[tuple[str, float, float], ...] = (      # months ahead the first forecast looked
    ("כבר עבר", float("-inf"), 0), ("עד 6 חודשים", 0, 6), ("6–12 חודשים", 6, 12),
    ("12–24 חודשים", 12, 24), ("מעל 24 חודשים", 24, float("inf")),
)


def _rate(rows: list[tuple[float, float, Any]]) -> dict[str, Any]:
    """rows = (move_days, span_days, project_id) → the group's slip figures."""
    n = len(rows)
    years = sum(r[1] for r in rows) / YEAR_DAYS
    later = [r[0] for r in rows if r[0] > ps.MOVE_DAYS]
    earlier = [r[0] for r in rows if r[0] < -ps.MOVE_DAYS]
    net_months = (sum(later) + sum(earlier)) / ps.MONTH_DAYS
    per_year = round(net_months / years, 1) if years > 0 else None
    return {
        "intervals": n,
        "projects": len({r[2] for r in rows}),
        "project_years": round(years, 1),
        "moved_later": len(later),
        "moved_earlier": len(earlier),
        "moved_later_pct": round(100 * len(later) / n) if n else None,
        "median_move_months": round(float(pd.Series(later).median()) / ps.MONTH_DAYS, 1) if later else None,
        "slip_months_per_year": per_year,
        # what a forecast needs multiplying by to hold, if the stage keeps slipping at this rate
        "realism_factor": round(1 / (1 - per_year / 12), 2) if per_year is not None and 0 <= per_year < 12 else None,
        "small_sample": len({r[2] for r in rows}) < ps.SMALL_SAMPLE,
    }


def _horizon(months: float) -> str:
    for label, lo, hi in HORIZONS:
        if (lo == float("-inf") or months > lo) and months <= hi:
            return label
    return HORIZONS[-1][0]


def analyse(frames: ps.Frames, allowed: set | None = None, with_projects: bool = False) -> dict[str, Any]:
    """The figures, over every project or only the `allowed` project ids.

    `with_projects` adds one row per project (name, manager, its moves and the
    forecast at every dated report) — that is what the page drills into, and it
    is never logged. A viewer's page passes the ids that viewer may see, so no
    number on it can describe a project outside their scope.
    """
    snaps = ps.canonical_snaps(frames.snaps)
    if allowed is not None:
        snaps = snaps[snaps["project_id"].isin(allowed)]
    if snaps.empty:
        return {"reports": 0}
    rdates = sorted(snaps["report_date"].unique())
    info = frames.projects.set_index("project_id").to_dict("index")

    by_stage: dict[str, list] = defaultdict(list)
    by_sector: dict[str, list] = defaultdict(list)
    by_type: dict[str, list] = defaultdict(list)
    everything: list = []
    first_last: list[tuple[float, float, float, bool]] = []   # horizon_m, drift_days, observed_days, baseline_moved_later
    projects: list[dict[str, Any]] = []

    current_stage = snaps.sort_values("report_date").groupby("project_id")["stage"].last().to_dict()
    dated = snaps.dropna(subset=["fc"]).sort_values("report_date")
    for pid, g in dated.groupby("project_id"):
        rows = list(zip(g["report_date"], g["fc"], g["stage"], g["dev"]))
        rows = [r for r in rows if not ss.is_closed(r[2])]      # a finished project's date no longer "holds"
        ptype = (info.get(pid) or {}).get("project_type") or "ללא סוג"
        in_stage: dict[str, float] = defaultdict(float)
        events = []
        for (d0, fc0, stage0, _), (d1, fc1, _, _) in zip(rows, rows[1:]):
            stage = stage0 or "ללא סטטוס"
            move = (fc1 - fc0).days
            item = (move, (d1 - d0).days, pid)
            everything.append(item)
            by_stage[stage].append(item)
            for sector in ss.sectors_for(stage0):
                by_sector[ss.SECTORS.get(sector, sector)].append(item)
            by_type[ptype].append(item)
            in_stage[stage] += 0.0                                # the project was measured in this stage
            if abs(move) > ps.MOVE_DAYS:
                in_stage[stage] += move
                events.append({"from_report": d0.isoformat(), "to_report": d1.isoformat(), "stage": stage,
                               "fc_from": fc0.date().isoformat(), "fc_to": fc1.date().isoformat(),
                               "months": round(move / ps.MONTH_DAYS, 1)})
        if len(rows) < 2:
            continue
        (d0, fc0, _, dev0), (d1, fc1, _, dev1) = rows[0], rows[-1]
        base_later = bool(pd.notna(dev0) and pd.notna(dev1) and (dev1 - dev0).days > ps.MOVE_DAYS)
        horizon_m = (fc0 - pd.Timestamp(d0)).days / ps.MONTH_DAYS
        first_last.append((horizon_m, (fc1 - fc0).days, (d1 - d0).days, base_later))
        if with_projects:
            p = info.get(pid) or {}
            stage_now = current_stage.get(pid) or "ללא סטטוס"
            projects.append({
                "id": p.get("identifier") or str(pid), "name": p.get("name") or "", "manager": p.get("manager") or "",
                "type": ptype, "stage": stage_now,
                "sectors": [ss.SECTORS.get(x, x) for x in ss.sectors_for(stage_now)],
                "net_months": round((fc1 - fc0).days / ps.MONTH_DAYS, 1),
                "later": sum(1 for e in events if e["months"] > 0),
                "earlier": sum(1 for e in events if e["months"] < 0),
                "fc_first": fc0.date().isoformat(), "fc_last": fc1.date().isoformat(),
                "observed_months": round((d1 - d0).days / ps.MONTH_DAYS, 1),
                "horizon": _horizon(horizon_m), "baseline_later": base_later,
                "stages": {k: round(v / ps.MONTH_DAYS, 1) for k, v in in_stage.items()},
                "events": events,
                "series": [[d.isoformat(), fc.date().isoformat()] for d, fc, _, _ in rows],
            })

    horizons = {}
    for label, _lo, _hi in HORIZONS:
        rows = [r for r in first_last if _horizon(r[0]) == label]
        if not rows:
            continue
        drift = pd.Series([r[1] for r in rows]) / ps.MONTH_DAYS
        horizons[label] = {
            "projects": len(rows),
            "moved_later_pct": round(100 * sum(1 for r in rows if r[1] > ps.MOVE_DAYS) / len(rows)),
            "median_drift_months": round(float(drift.median()), 1),
            "p75_drift_months": round(float(drift.quantile(0.75)), 1),
            "median_months_observed": round(float(pd.Series([r[2] for r in rows]).median()) / ps.MONTH_DAYS, 1),
            "small_sample": len(rows) < ps.SMALL_SAMPLE,
        }

    slipped = [r for r in first_last if r[1] > ps.MOVE_DAYS]
    order = lambda d: dict(sorted(d.items(), key=lambda kv: -(kv[1]["project_years"])))   # noqa: E731
    out: dict[str, Any] = {
        "reports": len(rdates),
        "first_report": rdates[0].isoformat(),
        "last_report": rdates[-1].isoformat(),
        "projects_with_two_dated_reports": len(first_last),
        "all": _rate(everything),
        "by_stage": order({k: _rate(v) for k, v in by_stage.items()}),
        "by_sector": order({k: _rate(v) for k, v in by_sector.items()}),      # a stage can sit in two sectors — never summed
        "by_project_type": order({k: _rate(v) for k, v in by_type.items()}),
        "by_first_forecast_horizon": horizons,
        "baseline_moved_with_forecast": {
            "forecast_later": len(slipped),
            "baseline_also_later": sum(1 for r in slipped if r[3]),
            "pct": round(100 * sum(1 for r in slipped if r[3]) / len(slipped)) if slipped else None,
        },
    }
    if with_projects:
        out["projects"] = sorted(projects, key=lambda r: -r["net_months"])
        # where each stage sits on the way to energization (stage_sectors.STAGE_RANK); off-ladder = None
        out["stage_rank"] = {k: ss.STAGE_RANK.get(k) for k in out["by_stage"]}
        out["stage_sectors"] = {k: [ss.SECTORS.get(x, x) for x in ss.sectors_for(k)] for k in out["by_stage"]}
        out["move_days"] = ps.MOVE_DAYS
    return out


def compute(frames: ps.Frames) -> dict[str, Any]:
    """Numbers only, every project — what is written to the log."""
    return analyse(frames)


def visible_ids(frames: ps.Frames, sees) -> set:
    """Project ids a viewer may see: `sees(sectors_of_current_stage, manager)` is
    insight_access.sees_project with the viewer's scope bound."""
    snaps = ps.canonical_snaps(frames.snaps)
    if snaps.empty:
        return set()
    stage = snaps.sort_values("report_date").groupby("project_id")["stage"].last().to_dict()
    managers = frames.projects.set_index("project_id")["manager"].to_dict()
    return {pid for pid, st in stage.items() if sees(ss.sectors_for(st), managers.get(pid))}


async def snapshot(session, log: bool = True) -> dict[str, Any]:
    """Compute now; never raises."""
    try:
        result = compute(await ps.load_frames(session))
    except Exception as e:  # noqa: BLE001
        logger.warning("forecast reliability failed: %s", e)
        return {"ok": False}
    if log:
        print(LOG_PREFIX + json.dumps(ps._plain(result), ensure_ascii=False, separators=(",", ":")), flush=True)
    return {"ok": True, **result}
