"""Forecast reliability — pure `compute` over a small synthetic history. No DB."""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from app.services import forecast_reliability as fr
from app.services import pattern_service as ps

D1, D2, D3 = date(2026, 3, 25), date(2026, 7, 8), date(2026, 9, 16)


def _snap(pid, d, stage, fc, dev):
    return {"project_id": pid, "snapshot_date": d, "stage": stage, "fc": fc, "dev": dev,
            "risks": None, "to_handle": None, "finish_date_text": None}


def _frames() -> ps.Frames:
    snaps = [
        # P1 (planning): the forecast slips ~6 months twice; the baseline follows once.
        _snap(1, D1, "תכנון", date(2026, 12, 31), date(2026, 12, 31)),
        _snap(1, D2, "תכנון", date(2027, 6, 30), date(2027, 6, 30)),
        _snap(1, D3, "בדיקות", date(2027, 12, 31), date(2027, 6, 30)),
        # P2 (permit): dates never move.
        *[_snap(2, d, "קבלת היתר", date(2027, 3, 1), date(2027, 3, 1)) for d in (D1, D2, D3)],
        # P3: finished — its fixed date must not dilute anything.
        *[_snap(3, d, "הסתיים", date(2026, 1, 1), date(2026, 1, 1)) for d in (D1, D2, D3)],
        # P4: no dated forecast at all.
        *[_snap(4, d, "עבודה אזרחית והרכבות", None, date(2027, 1, 1)) for d in (D1, D2, D3)],
    ]
    projects = [{"project_id": i, "identifier": f"P-{i}", "name": "x", "manager": f"מנהל {i}",
                 "project_type": t, "is_active": True} for i, t in ((1, "הרחבה"), (2, "הקמה"), (3, "הרחבה"), (4, "שוש"))]
    return ps.Frames(pd.DataFrame(snaps), pd.DataFrame(projects),
                     pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))


R = fr.compute(_frames())


def test_span_and_population():
    assert (R["reports"], R["first_report"], R["last_report"]) == (3, "2026-03-25", "2026-09-16")
    assert R["projects_with_two_dated_reports"] == 2            # P1 and P2; P3 is finished, P4 undated


def test_a_move_belongs_to_the_stage_the_project_was_in():
    plan, permit = R["by_stage"]["תכנון"], R["by_stage"]["קבלת היתר"]
    assert (plan["intervals"], plan["moved_later"], plan["moved_later_pct"]) == (2, 2, 100)
    assert plan["median_move_months"] == 6.0                    # 181 and 184 days
    assert (permit["intervals"], permit["moved_later"], permit["slip_months_per_year"]) == (2, 0, 0.0)
    assert "בדיקות" not in R["by_stage"]                         # nothing was measured FROM that stage
    assert "הסתיים" not in R["by_stage"]


def test_slip_per_year_and_the_realism_factor():
    plan = R["by_stage"]["תכנון"]
    # 365 days of slip in 175 days of calendar → 25 months a year: no factor can fix that
    assert plan["slip_months_per_year"] == 25.0 and plan["realism_factor"] is None
    assert R["by_stage"]["קבלת היתר"]["realism_factor"] == 1.0
    whole = R["all"]
    assert whole["slip_months_per_year"] == 12.5 and whole["realism_factor"] is None
    assert fr._rate([(61, 365.25, 1)])["realism_factor"] == 1.2   # 2 months a year → ×1.2


def test_small_samples_are_marked_not_hidden():
    assert R["by_stage"]["תכנון"]["small_sample"] is True


def test_by_how_far_ahead_the_first_forecast_looked():
    h = R["by_first_forecast_horizon"]
    assert list(h) == ["6–12 חודשים"]                            # P1 looked 9 months ahead, P2 11
    b = h["6–12 חודשים"]
    assert (b["projects"], b["moved_later_pct"]) == (2, 50)
    assert (b["median_drift_months"], b["p75_drift_months"]) == (6.0, 9.0)    # 0 and 12 months
    assert b["median_months_observed"] == 5.7


def test_a_baseline_that_moved_with_the_forecast_is_counted():
    assert R["baseline_moved_with_forecast"] == {"forecast_later": 1, "baseline_also_later": 1, "pct": 100}


def test_empty_history():
    empty = ps.Frames(pd.DataFrame(columns=["project_id", "snapshot_date", "stage", "fc", "dev", "risks",
                                            "to_handle", "finish_date_text"]),
                      pd.DataFrame(columns=["project_id", "identifier", "name", "manager", "project_type", "is_active"]),
                      pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))
    assert fr.compute(empty) == {"reports": 0}


def test_no_project_or_person_in_the_result_and_no_constant_respelled():
    blob = json.dumps(ps._plain(R), ensure_ascii=False)
    assert "P-1" not in blob and "מנהל 1" not in blob
    src = Path(fr.__file__).read_text(encoding="utf-8")
    assert "ps.MOVE_DAYS" in src and "ps.MONTH_DAYS" in src and "30.44" not in src and "= 15" not in src


# ── the page's detail (drill-down) and viewer scoping ─────────────────────

DETAIL = fr.analyse(_frames(), with_projects=True)


def test_detail_has_one_row_per_measured_project_with_its_moves():
    rows = {p["id"]: p for p in DETAIL["projects"]}
    assert set(rows) == {"P-1", "P-2"}                           # finished and undated projects are not rows
    p1 = rows["P-1"]
    assert (p1["net_months"], p1["later"], p1["earlier"], p1["baseline_later"]) == (12.0, 2, 0, True)
    assert p1["stages"] == {"תכנון": 12.0} and len(p1["events"]) == 2
    assert p1["series"][0] == ["2026-03-25", "2026-12-31"] and p1["fc_last"] == "2027-12-31"
    assert rows["P-2"]["stages"] == {"קבלת היתר": 0.0} and rows["P-2"]["events"] == []


def test_every_group_number_is_the_number_of_rows_behind_it():
    for stage, g in DETAIL["by_stage"].items():
        assert g["projects"] == sum(1 for p in DETAIL["projects"] if stage in p["stages"]), stage
    for kind, g in DETAIL["by_project_type"].items():
        assert g["projects"] == sum(1 for p in DETAIL["projects"] if p["type"] == kind), kind
    for label, g in DETAIL["by_first_forecast_horizon"].items():
        assert g["projects"] == sum(1 for p in DETAIL["projects"] if p["horizon"] == label), label


def test_the_logged_result_never_carries_projects():
    assert "projects" not in fr.compute(_frames()) and "stage_rank" not in fr.compute(_frames())


def test_a_scoped_viewer_gets_figures_over_their_projects_only():
    frames = _frames()
    ids = fr.visible_ids(frames, lambda sectors, manager: manager == "מנהל 2")
    assert ids == {2}
    scoped = fr.analyse(frames, allowed=ids, with_projects=True)
    assert [p["id"] for p in scoped["projects"]] == ["P-2"]
    assert scoped["all"]["projects"] == 1 and scoped["all"]["moved_later"] == 0
    assert "תכנון" not in scoped["by_stage"]                      # another manager's stage is not even named
    assert fr.analyse(frames, allowed=set()) == {"reports": 0}


def test_the_page_is_gated_scoped_and_built_without_the_html_parser():
    root = Path(fr.__file__).resolve().parents[2]
    route = (root / "app/routers/projects.py").read_text(encoding="utf-8")
    body = route[route.index("async def project_delays_page"):route.index('@router.get("/insights"')]
    assert "scope.allowed" in body and "insight_access.sees_project" in body and "sees_all" in body
    page = (root / "app/templates/project_delays.html").read_text(encoding="utf-8")
    assert "innerHTML" not in page and "{{ data|tojson }}" in page
    assert "'/dashboard/projects/p/' + encodeURIComponent(p.id)" in page       # the drill ends at the project page
    assert "prefers-reduced-motion" in page


# ── the added analyses ────────────────────────────────────────────────────

def test_when_the_postponements_are_declared():
    assert R["timeline"] == [
        {"report": "2026-07-08", "later": 1, "earlier": 0, "later_months": 5.9, "earlier_months": 0.0, "measured": 2},
        {"report": "2026-09-16", "later": 1, "earlier": 0, "later_months": 6.0, "earlier_months": 0.0, "measured": 2},
    ]


def test_sizes_repeats_plan_gap_years_and_stage_change():
    assert R["move_sizes"]["4–6 חודשים"] == 2 and sum(R["move_sizes"].values()) == R["all"]["moved_later"]
    assert R["postponements_per_project"] == {"0": 1, "1": 0, "2": 1, "3+": 0}
    assert R["plan_gap"]["בזמן"] == 1 and R["plan_gap"]["עד חצי שנה"] == 1          # P2 on plan, P1 six months behind it
    assert R["target_years"] == [{"from": 2026, "to": 2027, "projects": 1}, {"from": 2027, "to": 2027, "projects": 1}]
    assert R["at_stage_change"] == {"moves": 2, "with_stage_change": 1, "pct": 50}   # P1's second move came with תכנון → בדיקות
    assert R["past_due"] == {"projects": 0, "pct": 0}


def test_every_new_figure_is_the_number_of_rows_behind_it():
    rows = DETAIL["projects"]
    for label, n in DETAIL["plan_gap"].items():
        assert n == sum(1 for p in rows if p["plan_gap"] == label), label
    for label, n in DETAIL["move_sizes"].items():
        assert n == sum(1 for p in rows for e in p["events"] if e["size"] == label), label
    for wave in DETAIL["timeline"]:
        assert wave["later"] == sum(1 for p in rows for e in p["events"] if e["to_report"] == wave["report"] and e["months"] > 0)
    for flow in DETAIL["target_years"]:
        assert flow["projects"] == sum(1 for p in rows if int(p["fc_first"][:4]) == flow["from"] and int(p["fc_last"][:4]) == flow["to"])
    assert DETAIL["past_due"]["projects"] == sum(1 for p in rows if p["past_due"])
    assert DETAIL["order"]["plan"] == list(DETAIL["plan_gap"]) and DETAIL["order"]["sizes"] == list(DETAIL["move_sizes"])


def test_manager_names_reach_the_page_but_never_the_log():
    assert set(DETAIL["by_manager"]) == {"מנהל 1", "מנהל 2"}
    assert "by_manager" not in R and "order" not in R


def test_the_page_has_no_all_projects_table():
    page = (Path(fr.__file__).resolve().parents[2] / "app/templates/project_delays.html").read_text(encoding="utf-8")
    assert "כל הפרויקטים" not in page.split("<script>")[0]            # no such section in the page body
    for chart in ("pipe", "years", "timeline", "sizes", "plan", "dots", "cuts"):
        assert f'data-build="{chart}"' in page and f"BUILD.{chart} = " in page
