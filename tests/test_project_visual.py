"""The project's visual risk report: payload is a re-shape of the project page's
data (no new numbers), dates stay ISO in the data and read DD/MM/YYYY on screen,
and the page inserts text with textContent only."""
import json
import re
from types import SimpleNamespace

from app.services import insight_triage as it
from app.services import pattern_service as pt
from app.services import project_chart as pc
from app.services import project_visual as pv
from app.services.il_format import template_env
from tests.test_pattern_service import _frames

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _data(ident="P-1"):
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt._plain(pt.project_history(frames, p, ident))
    return h, pv.build(h, pc.delay_story(h), pc.risk_matrix(h), it.triage_all(p).get(ident), it.LEVELS)


def test_payload_counts_are_the_pages_own_rows():
    h, d = _data()
    assert len(d["flags"]) == len(h["flags"])
    assert sum(d["counts"].values()) == len(h["flags"])
    assert d["counts"]["high"] == sum(f["level"] == "high" for f in h["flags"])
    assert d["kpis"]["slip_months"] == h["row"]["slip_months"]
    assert len(d["events"]) == len(h["events"])
    assert d["repeats"] == sum(w["same_as_before"] for w in h["weekly"])
    assert d["level"] and d["level"]["key"] in it.LEVELS


def test_flag_text_reads_il_dates_but_data_stays_iso():
    _, d = _data()
    assert not any(ISO.search(f["text"]) for f in d["flags"])
    assert all(ISO.fullmatch(w) for w in d["matrix"]["weeks"])
    assert ISO.fullmatch(d["as_of"])


def test_stage_journey_segments_and_runs_to_the_report_date():
    tl = [{"date": "2026-01-07", "stage": "תכנון"}, {"date": "2026-02-04", "stage": "תכנון"},
          {"date": "2026-03-04", "stage": "בדיקות"}]
    segs = pv.stage_journey(tl, "2026-03-18")
    assert [(s["stage"], s["from"], s["to"], s["weeks"]) for s in segs] == [
        ("תכנון", "2026-01-07", "2026-03-04", 8), ("בדיקות", "2026-03-04", "2026-03-18", 2)]
    assert segs[-1].get("current") and not segs[0].get("current")
    assert pv.stage_journey([], None) == []


def test_visual_page_renders_and_uses_textcontent_only():
    h, d = _data()
    html = template_env().get_template("project_visual.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/x")), current_user=SimpleNamespace(is_admin=True), h=h, data=d)
    assert 'id="visual-data"' in html and "motion@13.4.6" in html
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html
    blob = html.split('id="visual-data">')[1].split("</script>")[0]
    assert json.loads(blob)["identifier"] == "P-1"


def test_project_page_has_the_visual_button():
    h, _ = _data()
    html = template_env().get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels={})
    assert "/dashboard/projects/p/P-1/visual" in html and "דוח ויזואלי" in html


def test_visual_route_is_declared_before_the_page_route():
    # `{identifier:path}` on the page route would otherwise swallow "/visual".
    from app.routers import projects
    paths = [r.path for r in projects.router.routes]
    assert paths.index("/dashboard/projects/p/{identifier:path}/visual") < paths.index("/dashboard/projects/p/{identifier:path}")
