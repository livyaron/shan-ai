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
            "risks": kw.get("risks"), "to_handle": kw.get("to_handle"), "finish_date_text": kw.get("text")}


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
    assert "נדחה פעמיים" in texts and "תכנית הפיתוח זזה" in texts
    assert h["flags"][0]["level"] == "high"
    assert h["weekly"][0]["week"] > h["weekly"][-1]["week"]                     # newest first


def test_project_history_marks_repeated_weekly_text_and_unknown_is_none():
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt.project_history(frames, p, "P-2")
    assert sum(w["same_as_before"] for w in h["weekly"]) == 4
    assert any("זהה" in f["text"] for f in h["flags"])
    assert pt.project_history(frames, p, "NOPE") is None


def test_project_page_renders():
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader
    from app.services import project_chart as pc
    frames = _frames()
    h = pt._plain(pt.project_history(frames, pt._plain(pt.compute_patterns(frames)), "P-1"))
    html = Environment(loader=FileSystemLoader("app/templates")).get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels=ss.SECTORS)
    for s in ("ניתוח סיכונים", "איך האיחור מתפתח", "סיכונים לאורך זמן", "אירועי דחייה",
              "דיווחים שבועיים", 'id="delay-data"', "drawDelay"):
        assert s in html
    assert "innerHTML" not in html          # data is inserted with textContent only


def test_delay_story_measures_each_report():
    from app.services import project_chart as pc
    frames = _frames()
    h = pt._plain(pt.project_history(frames, pt._plain(pt.compute_patterns(frames)), "P-1"))
    d = pc.delay_story(h)
    by = {x["date"]: x for x in d["points"]}
    # P-1: fc 31.12.26 → 30.6.27 → 31.12.27; dev 31.12.26 → 30.6.27 → 30.6.27
    assert by[D1.isoformat()]["fc_slip"] == 0.0 and by[D1.isoformat()]["gap"] == 0.0
    # 31.12→30.6 is 181 days = 5.9 months; the plan followed the forecast exactly.
    assert by[D2.isoformat()]["fc_slip"] == by[D2.isoformat()]["dev_slip"] == pytest.approx(5.9)
    assert by[D3.isoformat()]["fc_slip"] == pytest.approx(12.0) and by[D3.isoformat()]["gap"] == pytest.approx(6.0)
    assert {m["kind"] for m in by[D2.isoformat()]["moves"]} == {"forecast", "baseline"}
    assert by[D3.isoformat()]["stage_changed"] and d["stage_changes"] == [D3.isoformat()]
    assert d["y_min"] <= 0 <= 12 <= d["y_max"] and 0 in d["ticks"]


def test_risk_matrix_and_risk_column_changes():
    from app.services import project_chart as pc
    frames = _frames()
    h = pt._plain(pt.project_history(frames, pt._plain(pt.compute_patterns(frames)), "P-2"))
    m = pc.risk_matrix(h)
    assert len(m["weeks"]) == 5 and all(len(r["cells"]) == 5 for r in m["rows"])
    repeat = next(r for r in m["rows"] if r["kind"] == "repeat")
    assert repeat["weeks"] == 4
    changes = pc.risk_column_changes(h)
    assert changes and changes[-1]["first"]


# ── Taxonomy v2 and near-duplicate reports (caught on real data) ──────────

@pytest.mark.parametrize("text,cat,hit", [
    ("ממתינים להפסקת פ\"צ לצורך הרכבת מנתק פ\"צ", "גורם חיצוני/רשויות", False),   # assembly, not a train
    ("עבודות ליד פסי רכבת ישראל", "גורם חיצוני/רשויות", True),
    ("אישור נת\"י התקבל", "גורם חיצוני/רשויות", True),
    ("תכנית עבודה שנתית", "גורם חיצוני/רשויות", False),                        # שנתית ⊃ נתי
    ("סיום מוקדם מהצפוי", "הפסקות/תפעול רשת", False),                         # early, not a control centre
    ("תיאום מול המוקד", "הפסקות/תפעול רשת", True),
    ("בהמתנה לכל הגורמים", "קרקע/גישה/הסכמים", False),                        # הגורמים ⊃ רמי
    ("עבודות בכרמיאל", "קרקע/גישה/הסכמים", False),
    ("ממתינים לאישור רמ\"י", "קרקע/גישה/הסכמים", True),
    ("המחירים צפויים לעלות", "תקציב/עלות", False),                           # to rise
    ("הקרקע בבעלות פרטית", "תקציב/עלות", False),                             # ownership
    ("עלות החוזה חרגה", "תקציב/עלות", True),
    ("חוסר בסוללות", "כוח אדם/פיקוח/בדיקות", False),
    ("חוסר בפועלים באתר", "כוח אדם/פיקוח/בדיקות", True),
    ("הציוד מספק את הדרישה", "ציוד/אספקה", True),                             # ציוד still counts
    ("הכמות מספקת", "ציוד/אספקה", False),                                    # sufficient, not a supplier
    ("התקבל היתר בנייה", "רישוי/היתרים/סטטוטוריקה", True),
    ("היתרון של הפתרון", "רישוי/היתרים/סטטוטוריקה", False),
    # WBC-057 page: "קרקע" alone is mostly site work, cables and soil, not land.
    ("צפי עלייה לקרקע בנובמבר", "קרקע/גישה/הסכמים", False),                  # contractor on site
    ("חיבור מ\"ע תת קרקעי", "קרקע/גישה/הסכמים", False),                      # underground cable
    ("התקבל דוח זיהום קרקע", "קרקע/גישה/הסכמים", False),                     # soil
    ("הוסדר נושא רכישת הקרקע", "קרקע/גישה/הסכמים", True),
    ("התקבל מסמך זכויות בקרקע", "קרקע/גישה/הסכמים", True),
    ("לרישום העירייה כבעלת הקרקע", "קרקע/גישה/הסכמים", True),
    ("נדרשות חפירות נוספות", "גורם חיצוני/רשויות", False),                    # נדרשות ⊃ רשות
    ("ממתינים לאישור רשות העתיקות", "גורם חיצוני/רשויות", True),
    ("הועבר מסמך למהנדס העיר אשדוד", "גורם חיצוני/רשויות", True),
    ("פגישה עם אדריכלית העיר", "גורם חיצוני/רשויות", True),
])
def test_taxonomy_v2_word_edges(text, cat, hit):
    import re
    assert bool(re.search(pt.RISK_CATEGORIES[cat], text)) is hit


def test_near_same_treats_a_typo_fix_as_a_repeat():
    a = 'נאמר שבפסקת פ"צ לצורך החזרת מנתק פ"צ תהיה ב 11.2026,'
    b = 'נאמר שהפסקת פ"צ לצורך החזרת מנתק פ"צ תהיה ב 11.2026.'
    assert pt.near_same(a, b)
    assert not pt.near_same(a, "הקבלן התחיל עבודות אזרחיות באתר השבוע")
    assert not pt.near_same(None, a)


# ── Caught on the WBC-057 page ────────────────────────────────────────────

def _gap_frames() -> pt.Frames:
    """P-9: dated, then the target is written as text for one report, then
    dated again 22 months later. The move must survive the undated report."""
    f = _frames()
    snaps = [
        _snap(9, D1, "קבלת היתר", date(2028, 2, 28), date(2027, 6, 30)),
        _snap(9, D2, "קבלת היתר", None, date(2027, 6, 30), text="-", to_handle="חסם לטיפול סמנכ\"ל"),
        _snap(9, D3, "קבלת היתר", date(2029, 12, 31), date(2027, 6, 30), to_handle="חסם לטיפול סמנכ\"ל"),
    ]
    projects = [{"project_id": 9, "identifier": "P-9", "name": "g", "manager": "מנהל ב",
                 "project_type": "הקמה", "is_active": True}]
    return pt.Frames(pd.concat([f.snaps, pd.DataFrame(snaps)], ignore_index=True),
                     pd.concat([f.projects, pd.DataFrame(projects)], ignore_index=True), f.weekly)


def test_a_move_across_an_undated_report_is_still_a_move():
    frames = _gap_frames()
    p = pt._plain(pt.compute_patterns(frames))
    ev = [e for e in p["events"] if e["identifier"] == "P-9"]
    assert len(ev) == 1 and ev[0]["kind"] == "forecast"
    assert (ev[0]["from"], ev[0]["to"], ev[0]["undated_between"]) == (D1.isoformat(), D3.isoformat(), 1)
    row = next(r for r in p["projects"] if r["identifier"] == "P-9")
    assert ev[0]["months"] == row["forecast_moved_months"]         # events add up to the total move
    # A wave is one consecutive pair; the gap-crossing move is in no wave's drill.
    from app.services import insight_access as ia
    w = next(x for x in p["metrics"]["update_waves"]["value"] if x["from"] == D1.isoformat())
    d = ia.drill_for(ia.view_for(ia.Scope(admin=True), p), p, "wave", D1.isoformat(), "forecast")
    assert len(d["rows"]) == w["forecast_later"]
    assert all(r["identifier"] != "P-9" for r in d["rows"])


def test_escalation_flag_and_times_wording():
    frames = _gap_frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt.project_history(frames, p, "P-9")
    top = h["flags"][0]
    assert top["level"] == "high" and "סמנכ" in top["text"] and f"מאז {D2.isoformat()}" in top["text"]
    assert "לפחות" not in top["text"]                                # D1 had no escalation
    assert any("נדחה פעם אחת" in f["text"] and "כמלל" in f["text"] for f in h["flags"])
    assert pt._times(2) == "פעמיים" and pt._times(3) == "3 פעמים"


def test_risk_matrix_marks_missing_weeks():
    from app.services import project_chart as pc
    weekly = [{"week": w, "text": "x", "same_as_before": False}
              for w in ("2026-04-01", "2026-04-08", "2026-05-13", "2026-05-20")]
    m = pc.risk_matrix({"weekly": weekly[::-1]})
    assert m["missing_before"] == [0, 0, 4, 0]
