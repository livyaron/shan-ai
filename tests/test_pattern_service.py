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


# ── The admin page renders whatever the engine returns ────────────────────

def _render(p):
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("app/templates"))
    request = SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights"))
    user = SimpleNamespace(is_admin=True, username="u", role=None, id=1)
    from app.services import insight_access
    view = insight_access.view_for(insight_access.Scope(admin=True), p)
    return env.get_template("project_insights.html").render(
        request=request, current_user=user, p=p, view=view, sector_labels=ss.SECTORS)


def test_insights_page_renders_every_section():
    html = _render(pt._plain(pt.compute_patterns(_frames(extra_projects=4))))
    for heading in ("איפה נוצרות הדחיות", "גלי עדכון", "לפי מגזר", "טבלת מנהלי פרויקטים",
                    "אותות מקדימים", "על מה כותבים", "רשימות לטיפול"):
        assert heading in html
    assert "מנהל א" in html and "P-2" in html


def test_insights_page_with_no_history():
    empty = pt.Frames(
        pd.DataFrame(columns=["project_id", "snapshot_date", "stage", "fc", "dev", "risks", "to_handle", "finish_date_text"]),
        pd.DataFrame(columns=["project_id", "identifier", "name", "manager", "project_type", "is_active"]),
        pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))
    assert "אין עדיין היסטוריה מתוארכת" in _render(pt._plain(pt.compute_patterns(empty)))


def test_one_inflated_date_does_not_disqualify_the_real_reports():
    # Production shape: a pre-P0 upload day that synced every sheet of the
    # workbook holds twice the projects of any real weekly report.
    rows = [{"project_id": i, "snapshot_date": date(2026, 7, 24)} for i in range(520)]
    for d in (date(2026, 3, 25), date(2026, 8, 19), date(2026, 9, 16)):
        rows += [{"project_id": i, "snapshot_date": d} for i in range(250)]
    rows += [{"project_id": i, "snapshot_date": date(2026, 9, 20)} for i in range(12)]   # partial
    m = pt.report_dates(pd.DataFrame(rows))
    assert sorted(set(m.values())) == [date(2026, 3, 25), date(2026, 7, 24), date(2026, 8, 19), date(2026, 9, 16)]


def test_a_cluster_is_named_by_its_report_dated_snapshot():
    # P0+ snapshots carry תו"ב; pre-P0 upload-day copies of the same file don't.
    rows = []
    for i in range(20):
        rows += [
            {"project_id": i, "snapshot_date": date(2026, 9, 2), "controller": "תו\"ב"},   # report
            {"project_id": i, "snapshot_date": date(2026, 9, 4), "controller": None},     # uploaded later
            {"project_id": i, "snapshot_date": date(2026, 9, 15), "controller": None},    # uploaded earlier
            {"project_id": i, "snapshot_date": date(2026, 9, 16), "controller": "תו\"ב"},  # report
        ]
    m = pt.report_dates(pd.DataFrame(rows))
    assert m[date(2026, 9, 4)] == date(2026, 9, 2)
    assert m[date(2026, 9, 15)] == date(2026, 9, 16)


# ── The project page ──────────────────────────────────────────────────────

def test_project_history_tells_p1s_story():
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt.project_history(frames, p, "P-1")
    assert [t["date"] for t in h["timeline"]] == [D1.isoformat(), D2.isoformat(), D3.isoformat()]
    assert [t["stage_changed"] for t in h["timeline"]] == [False, False, True]    # תכנון → בדיקות
    kinds = sorted(e["kind"] for e in h["events"])
    assert kinds == ["baseline", "forecast", "forecast"]
    texts = " ".join(f["text"] for f in h["flags"])
    assert "נדחה 2 פעמים" in texts and "תכנית הפיתוח זזה" in texts
    assert h["flags"][0]["level"] == "high"
    assert h["weekly"][0]["week"] > h["weekly"][-1]["week"]                     # newest first


def test_project_history_marks_repeated_weekly_text_and_unknown_is_none():
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt.project_history(frames, p, "P-2")
    assert sum(w["same_as_before"] for w in h["weekly"]) == 4
    assert any("זהה" in f["text"] for f in h["flags"])
    assert pt.project_history(frames, p, "NOPE") is None


def test_chart_geometry():
    from app.services.project_chart import timeline_chart
    frames = _frames()
    h = pt.project_history(frames, pt._plain(pt.compute_patterns(frames)), "P-1")
    c = timeline_chart(h["timeline"])
    assert [s["key"] for s in c["series"]] == ["fc", "dev"] and all(len(s["points"]) == 3 for s in c["series"])
    ys = [pt_["y"] for s in c["series"] for pt_ in s["points"]]
    assert all(0 <= y <= c["h"] for y in ys) and c["y_ticks"]
    assert timeline_chart(h["timeline"][:1]) is None


def test_project_page_renders():
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader
    from app.services.project_chart import timeline_chart
    frames = _frames()
    h = pt._plain(pt.project_history(frames, pt._plain(pt.compute_patterns(frames)), "P-1"))
    html = Environment(loader=FileSystemLoader("app/templates")).get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, chart=timeline_chart(h["timeline"]), sector_labels=ss.SECTORS)
    for s in ("ניתוח סיכונים", "היסטוריית יעדים ושלבים", "אירועי דחייה", "דיווחים שבועיים", "<polyline"):
        assert s in html


def test_chart_x_labels_never_collide():
    from app.services.project_chart import MIN_LABEL_GAP, timeline_chart
    weekly = [{"date": f"2026-09-{d:02d}", "fc": "2027-01-01", "dev": "2027-01-01"} for d in (2, 9, 16)]
    tl = [{"date": "2026-03-25", "fc": "2026-12-31", "dev": "2026-12-31"}] + weekly
    xs = [lab["x"] for lab in timeline_chart(tl)["x_labels"]]
    assert all(b - a >= MIN_LABEL_GAP for a, b in zip(xs, xs[1:]))
    assert timeline_chart(tl)["x_labels"][-1]["label"] == "16/09"     # the newest is always labelled
