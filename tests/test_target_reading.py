"""Prose in a target cell is READ, and the three targets are set against each
other (user decision 2026-10-16). Synthetic phrases only — the repo is public.
"""
import datetime
from datetime import date

import pandas as pd
import pytest

from app.services import insight_triage as it
from app.services import pattern_service as pt
from app.services import target_reading as tr
from app.services.project_sync import _split_finish_date
from tests.test_pattern_service import D1, D2, D3, _frames, _snap


@pytest.mark.parametrize("text, kind, when", [
    ("31.12.2027", tr.DATE, "2027-12-31"),                         # a date the parser missed
    ("30.06.28", tr.DATE, "2028-06-30"),
    ("30/10/2026*", tr.DATE, "2026-10-30"),
    ("30.02.2028", tr.DATE, "2028-02-29"),                         # no such day → month end, approx
    ("עד ל- 03/2027", tr.DATE, "2027-03-31"),                      # month only → its last day
    ("10/11/2027- לא מאושר", tr.UNAPPROVED, "2027-11-10"),
    ("שלב א - 31/06/27 שלב ב - 31/10/27", tr.STAGED, "2027-10-31"),  # the LAST stage counts
    ("04/2027 שלב 1 11/2027 שלב 2 2029", tr.STAGED, "2029-12-31"),  # a bare year is a stage too
    ("01/07/2026 לא אפשרי, יתקבל יעד חדש", tr.MISSED, None),        # the date is NOT the target
    ("30/06/2026 לא אפשרי, החישמול יהיה ב-09.2026", tr.MISSED, "2026-09-30"),  # …a later one is
    ("חושמל 30/09/2025", tr.ENERGIZED, "2025-09-30"),
    ("10/06/2026 חושמל", tr.ENERGIZED, "2026-06-10"),
    ("הסתיים", tr.ENERGIZED, None),
    ("שלב 1 חושמל 13/05/25 שלב 2 צפי 30/11/2026", tr.PARTIAL, "2026-11-30"),
    ('שנאי חושמל 11/2/26, נשאר מ"ג', tr.PARTIAL, None),
    ("טרם נקבע", tr.UNKNOWN, None),
    ("לא ניתן לקבוע עד להגשת תכנון מאושר", tr.UNKNOWN, None),
])
def test_read_text(text, kind, when):
    r = tr.read_text(text)
    assert r["kind"] == kind and r["date"] == when and r["label"] == tr.LABELS[kind]


def test_reasons_and_missed_date():
    assert tr.read_text("אין תוכנית העמדה מנגה")["reason"] == "תלוי בנגה"
    assert tr.read_text("כל עוד אין תכנון, לא ידוע")["reason"] == "ממתין לתכנון"
    assert tr.read_text("01/07/2026 לא אפשרי")["missed"] == "2026-07-01"
    assert tr.read_text("30.02.2028")["approx"] and not tr.read_text("31.12.2027")["approx"]


def test_sync_stores_the_read_date_but_never_a_missed_one():
    assert _split_finish_date("31.12.2027") == (date(2027, 12, 31), None)
    assert _split_finish_date("10/11/2027- לא מאושר") == (date(2027, 11, 10), "10/11/2027- לא מאושר")
    assert _split_finish_date("01/07/2026 לא אפשרי") == (None, "01/07/2026 לא אפשרי")
    assert _split_finish_date("חושמל 30/09/2025") == (None, "חושמל 30/09/2025")
    assert _split_finish_date(datetime.datetime(2027, 1, 1)) == (date(2027, 1, 1), None)


def test_compare_flags_only_a_later_forecast_beyond_15_days():
    plan, appr = date(2026, 6, 30), date(2026, 12, 31)
    c = tr.compare(plan, date(2027, 1, 15), None, appr)
    assert c["forecast_vs_approved"] == 15 and not c["over_approved"]      # 15 days = re-typing
    c = tr.compare(plan, date(2027, 1, 16), None, appr)
    assert c["over_approved"] and c["approved_vs_plan"] == 184
    assert not tr.compare(plan, date(2026, 1, 1), None, appr)["over_approved"]   # earlier: not flagged
    # prose with a later date counts; prose with none loses the approved target
    assert tr.compare(plan, None, "30/12/2027- לא מאושר", appr)["over_approved"]
    lost = tr.compare(plan, None, "טרם נקבע", appr)
    assert lost["approved_lost"] and not lost["over_approved"]
    # energized: nothing to compare — and its date is never a forecast
    done = tr.compare(plan, None, "חושמל 30/09/2027", appr)
    assert done["forecast"] is None and not done["over_approved"] and not done["approved_lost"]


def _approved_frames() -> pt.Frames:
    f = _frames()
    snaps = [
        # P-20: forecast as prose ("לא מאושר"), 1 year past the approved target
        *[_snap(20, d, "תכנון", None, date(2026, 6, 30), text="31/12/2027- לא מאושר") for d in (D1, D2, D3)],
        # P-21: approved target, forecast lost
        *[_snap(21, d, "תכנון", None, date(2026, 6, 30), text="טרם נקבע") for d in (D1, D2, D3)],
        # P-22: on the approved target
        *[_snap(22, d, "תכנון", date(2027, 1, 1), date(2026, 6, 30)) for d in (D1, D2, D3)],
    ]
    for s in snaps:
        s["appr"] = date(2026, 12, 31)
    projects = [{"project_id": i, "identifier": f"P-{i}", "name": "x", "manager": "מנהל א",
                 "project_type": "הקמה", "is_active": True} for i in (20, 21, 22)]
    return pt.Frames(pd.concat([f.snaps, pd.DataFrame(snaps)], ignore_index=True),
                     pd.concat([f.projects, pd.DataFrame(projects)], ignore_index=True), f.weekly)


def test_engine_reads_prose_and_compares_to_the_approved_target():
    p = pt.compute_patterns(_approved_frames())
    rows = {x["identifier"]: x for x in p["projects"]}
    assert rows["P-20"]["fc"] == "2027-12-31" and rows["P-20"]["fc_kind"] == tr.UNAPPROVED
    assert rows["P-20"]["over_approved"] and rows["P-20"]["appr_gap_days"] == 365
    assert not rows["P-20"]["undated"]                    # read → dated
    assert rows["P-21"]["approved_lost"] and rows["P-21"]["undated"]
    assert not rows["P-22"]["over_approved"]
    assert p["metrics"]["over_approved"]["value"]["projects"] == ["P-20"]
    assert p["metrics"]["approved_lost"]["value"]["projects"] == ["P-21"]
    # old frames (no approved column) still compute
    assert pt.compute_patterns(_frames())["metrics"]["over_approved"]["value"]["count"] == 0


def test_triage_puts_the_approved_target_on_the_division_manager():
    p = pt.compute_patterns(_approved_frames())
    tri = it.triage_all(p)
    assert tri["P-20"]["level"] in (it.ESCALATE, it.WEEK)
    assert any("שאישר מנהל האגף" in r["text"] for r in tri["P-20"]["reasons"])
    assert any("יש יעד מאושר" in r["text"] for r in tri["P-21"]["reasons"])
    assert any("לא מאושר" in r["text"] for r in tri["P-20"]["reasons"])
