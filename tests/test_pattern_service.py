"""PLAN.md P2 — pattern engine and the stage → sector map. Synthetic frames, no DB."""
from datetime import date, timedelta

import pandas as pd
import pytest

from app.services import pattern_service as pt
from app.services import stage_sectors as ss

# Every stage value the weekly master file used between Mar and Sep 2026.
FILE_STAGES = [
    "תכנון", "הקפאת תכולה", "הקפאת תצורה", "קבלת היתר", "עבודה אזרחית", "לקראת ביצוע",
    "הרכבה חשמלית", "הרכבה חשמלית ובדיקות", "בדיקות", "עבודה אזרחית והרכבות",
    "בחירת קבלן", "הסכם- אגירת אנרגיה", "טופס 4",
]


@pytest.mark.parametrize("stage", FILE_STAGES)
def test_every_stage_in_the_file_has_an_owner(stage):
    owners = ss.sectors_for(stage)
    assert owners and ss.UNKNOWN not in owners


def test_shared_stage_belongs_to_both_sectors():
    assert ss.sectors_for("עבודה אזרחית והרכבות") == (ss.SUPERVISION, ss.EXECUTION)


@pytest.mark.parametrize("stage,expected", [
    ("הסתיים", ()), (" הסתיים ", ()), ("שלב חדש שלא ראינו", (ss.UNKNOWN,)),
    (None, (ss.UNKNOWN,)), (float("nan"), (ss.UNKNOWN,)), ("", (ss.UNKNOWN,)),
    ("קבלת  היתר", (ss.PLANNING,)),
])
def test_sectors_for_edges(stage, expected):
    assert ss.sectors_for(stage) == expected


@pytest.mark.parametrize("table,expected", [
    ((18, 6, 48, 54), 0.02183),   # values checked against scipy.stats.fisher_exact
    ((17, 6, 53, 54), 0.03931),
    ((3, 1, 1, 3), 0.48571),
    ((0, 5, 5, 0), 0.00794),
])
def test_fisher_exact_matches_scipy(table, expected):
    assert pt.fisher_exact_p(*table) == pytest.approx(expected, abs=1e-4)


def test_report_dates_collapse_legacy_upload_day_and_skip_partial_syncs():
    rows = []
    for pid in range(10):
        rows.append({"project_id": pid, "snapshot_date": date(2026, 9, 15)})   # legacy upload day
        rows.append({"project_id": pid, "snapshot_date": date(2026, 9, 16)})   # report-dated
        rows.append({"project_id": pid, "snapshot_date": date(2026, 9, 9)})
    rows.append({"project_id": 0, "snapshot_date": date(2026, 9, 20)})         # one-row partial sync
    m = pt.report_dates(pd.DataFrame(rows))
    assert m[date(2026, 9, 15)] == date(2026, 9, 16)
    assert m[date(2026, 9, 16)] == date(2026, 9, 16)
    assert m[date(2026, 9, 9)] == date(2026, 9, 9)       # a week apart: separate reports
    assert date(2026, 9, 20) not in m


# ── A small synthetic history ─────────────────────────────────────────────

D1, D2, D3 = date(2026, 3, 25), date(2026, 7, 8), date(2026, 9, 16)


def _snap(pid, d, stage, fc, dev, **kw):
    return {"project_id": pid, "snapshot_date": d, "stage": stage, "fc": fc, "dev": dev,
            "risks": kw.get("risks"), "to_handle": None, "finish_date_text": kw.get("text")}


def _frames(extra_projects: int = 0) -> pt.Frames:
    snaps = [
        # P1: planning → execution; forecast slips twice, baseline follows once.
        _snap(1, D1, "תכנון", date(2026, 12, 31), date(2026, 12, 31)),
        _snap(1, D2, "תכנון", date(2027, 6, 30), date(2027, 6, 30)),
        _snap(1, D3, "בדיקות", date(2027, 12, 31), date(2027, 6, 30)),
        # P2: stuck in קבלת היתר the whole time, dates never move.
        *[_snap(2, d, "קבלת היתר", date(2027, 3, 1), date(2027, 3, 1)) for d in (D1, D2, D3)],
        # P3: closed — must not count anywhere.
        *[_snap(3, d, "הסתיים", date(2026, 1, 1), date(2026, 1, 1)) for d in (D1, D2, D3)],
        # P4: shared stage, target is prose.
        *[_snap(4, d, "עבודה אזרחית והרכבות", None, date(2027, 1, 1), text="טרם נקבע") for d in (D1, D2, D3)],
    ]
    projects = [
        {"project_id": 1, "identifier": "P-1", "name": "a", "manager": "מנהל א", "project_type": "הרחבה", "is_active": True},
        {"project_id": 2, "identifier": "P-2", "name": "b", "manager": "מנהל א", "project_type": "הקמה", "is_active": True},
        {"project_id": 3, "identifier": "P-3", "name": "c", "manager": "מנהל ב", "project_type": "הרחבה", "is_active": True},
        {"project_id": 4, "identifier": "P-4", "name": "d", "manager": "מנהל ב", "project_type": "שוש", "is_active": True},
    ]
    for i in range(extra_projects):   # quiet filler so a manager clears the small-sample bar
        pid = 100 + i
        snaps += [_snap(pid, d, "תכנון", date(2027, 1, 1), date(2027, 1, 1)) for d in (D1, D2, D3)]
        projects.append({"project_id": pid, "identifier": f"F-{i}", "name": "f", "manager": "מנהל א",
                         "project_type": "הרחבה", "is_active": True})
    weekly = []
    for w in range(5):   # P2 repeats itself for 5 weeks → stale; P1 does not
        wd = D3 - timedelta(weeks=4 - w)
        weekly.append({"project_id": 2, "week_date": wd, "text": "ממתינים להיתר", "text_hash": "h"})
        weekly.append({"project_id": 1, "week_date": wd, "text": f"שבוע {w}", "text_hash": f"x{w}"})
    return pt.Frames(pd.DataFrame(snaps), pd.DataFrame(projects), pd.DataFrame(weekly))


@pytest.fixture(scope="module")
def result():
    return pt.compute_patterns(_frames())


def test_report_dates_and_as_of(result):
    assert result["report_dates"] == [D1.isoformat(), D2.isoformat(), D3.isoformat()]
    assert result["as_of"] == D3.isoformat()


def test_closed_projects_count_nowhere(result):
    assert all(r["manager"] != "מנהל ב" or r["n"] == 1 for r in result["league"])
    assert result["metrics"]["past_due"]["value"]["projects"] == []


def test_forecast_drift_and_baseline_moves(result):
    fd = result["metrics"]["forecast_drift"]["value"]
    assert (fd["later"], fd["same"]) == (1, 1)                  # P1 later, P2 same, P4 undated
    bm = result["metrics"]["baseline_moves"]["value"]
    assert bm["later"] == 1 and bm["same"] == 2


def test_update_waves_count_each_interval(result):
    waves = result["metrics"]["update_waves"]["value"]
    assert [(w["forecast_later"], w["baseline_later"], w["both"]) for w in waves] == [(1, 1, 1), (1, 0, 0)]


def test_stuck_uses_weeks_in_current_stage(result):
    stuck = result["metrics"]["stuck"]["value"]
    ids = {p["identifier"]: p for p in stuck["projects"]}
    assert "P-2" in ids and ids["P-2"]["lower_bound"] is True    # history starts in that stage
    assert "P-1" not in ids                                       # just moved to בדיקות


def test_slip_attribution_charges_the_stage_it_moved_in(result):
    att = result["metrics"]["slip_attribution"]["value"]
    # Both of P1's moves happened while it was in תכנון (the stage at the START
    # of each interval) — execution is not blamed for a slip born in planning.
    assert att["planning"]["events"] == 2
    assert att["execution"]["events"] == 0


def test_stale_reporting_and_undated(result):
    assert result["metrics"]["stale_reporting"]["value"]["projects"] == ["P-2"]
    assert result["metrics"]["undated"]["value"]["projects"] == ["P-4"]


def test_shared_stage_counts_in_both_sectors_and_never_sums(result):
    by = {s["sector"]: s["n"] for s in result["sectors"]}
    assert by[ss.SUPERVISION] == 1 and by[ss.EXECUTION] == 2     # P4 in both, P1 in execution
    assert sum(by.values()) > 3                                   # which is why sectors never sum


def test_small_samples_are_shown_but_not_ranked(result):
    assert all(r["small_sample"] and "rank" not in r for r in result["league"])
    ranked = pt.compute_patterns(_frames(extra_projects=4))["league"]
    top = [r for r in ranked if r["manager"] == "מנהל א"][0]
    assert top["rank"] == 1 and top["n"] == 6 and top["baseline_moved"] == 1


def test_leading_indicators_skip_thin_categories(result):
    assert result["metrics"]["leading_indicators"]["value"] == []


def test_every_metric_carries_n_confidence_and_caveat(result):
    for name, m in result["metrics"].items():
        assert set(m) == {"value", "n", "confidence", "caveat"}, name
        assert m["confidence"] in {"high", "medium", "low", "small_sample"}, name


def test_empty_history_is_not_an_error():
    empty = pt.Frames(
        pd.DataFrame(columns=["project_id", "snapshot_date", "stage", "fc", "dev", "risks", "to_handle", "finish_date_text"]),
        pd.DataFrame(columns=["project_id", "identifier", "name", "manager", "project_type", "is_active"]),
        pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))
    assert pt.compute_patterns(empty)["as_of"] is None
