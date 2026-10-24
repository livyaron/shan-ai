"""The project's visual risk report: payload is a re-shape of the project page's
data (no new numbers), dates stay ISO in the data and read DD/MM/YYYY on screen,
and the page inserts text with textContent only."""
import json
import re
from itertools import pairwise
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


def test_twin_ladder_is_the_files_stage_ladder():
    from app.services import stage_sectors as ss
    # every rung of STAGE_RANK is a step, named as the file names it, in order
    labels = [s["label"] for s in pv.twin_steps("ais")]
    for rank, name in ss.STAGE_BY_RANK.items():
        assert labels[rank] == name
    assert labels[0] == "תכנון" and labels[-1] == "הסתיים"
    t = pv.twin_state("עבודה אזרחית והרכבות", 14)
    assert t["current"] == ss.STAGE_RANK["עבודה אזרחית"] and t["weeks"] == 14
    assert t["steps"][t["current"]]["label"] == "עבודה אזרחית"
    assert pv.twin_state("הסתיים")["current"] == len(labels) - 1
    off = pv.twin_state("הסכם- אגירת אנרגיה", 9)
    assert off["current"] is None and off["weeks"] is None          # off the ladder: no step marked


def test_twin_steps_only_ever_build_forward():
    # a layer once done stays done on every later step; the last two are fully built
    order = {"todo": 0, "active": 1, "done": 2}
    steps = pv.twin_state("תכנון")["steps"]
    for a, b in pairwise(steps):
        assert all(order[b["layers"][k]] >= order[a["layers"][k]] for k in pv.TWIN_LAYERS)
    assert all(st == "done" for st in steps[-1]["layers"].values()) and steps[-1]["energized"]
    assert not any(s["energized"] for s in steps[:-1])
    assert all(s["caption"] for s in steps)


def test_twin_module_and_model_ship_with_the_page():
    from pathlib import Path
    glb = Path("static/models/substation.glb")
    assert glb.exists() and glb.read_bytes()[:4] == b"glTF" and glb.stat().st_size < 5_000_000
    js = Path("static/js/substation_twin.js").read_text()
    assert "innerHTML" not in js and "fetch(" not in js
    # every layer the server names is a mesh in the model
    raw = glb.read_bytes()
    assert all(f'"name":"{k}"'.encode() in raw for k in pv.TWIN_LAYERS)
    h, d = _data()
    html = template_env().get_template("project_visual.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/x")), current_user=SimpleNamespace(is_admin=True), h=h, data=d)
    assert html.index('type="importmap"') < html.index('src="/static/js/substation_twin.js"') and "three@0.169.0" in html
    assert d["twin"] is not None


def test_station_type_picks_the_model_and_says_why():
    t = pv.twin_state("בדיקות", 3, False, "closed")
    assert t["model"] == "gis" and t["model_from"] == "lessons" and t["station_label"] == "תחנה סגורה"
    assert pv.twin_state("בדיקות", 3, False, "open")["model"] == "ais"
    other = pv.twin_state("בדיקות", 3, False, "switching")       # no model of its own yet
    assert other["model"] == pv.DEFAULT_MODEL and other["model_from"] == "default" and other["station_label"] == "תחנת מיתוג"
    none = pv.twin_state("בדיקות")
    assert none["model"] == pv.DEFAULT_MODEL and none["station_label"] is None
    # both models walk the same ladder, each with its own sentence per step
    a, g = (t["models"][k]["steps"] for k in ("ais", "gis"))
    assert [s["label"] for s in a] == [s["label"] for s in g] and [s["layers"] for s in a] == [s["layers"] for s in g]
    assert all(s["caption"] for s in g) and a[4]["caption"] != g[4]["caption"]
    assert t["steps"] == g


def test_gis_station_draws_every_layer_and_only_draws():
    from pathlib import Path
    kit = Path("static/js/station_kit.js").read_text()
    for name in ("gis_station.js", "mobile_station.js"):
        js = Path("static/js", name).read_text() + kit      # the kit draws the bushings for both
        for k in pv.TWIN_LAYERS:
            assert f"'{k}'" in js, (name, k)
        assert "innerHTML" not in js and "fetch(" not in js and "textContent" not in js   # geometry only, no text, no network
    twin = Path("static/js/substation_twin.js").read_text()
    assert "/static/js/gis_station.js" in twin and all(f"{k}:" in twin for k in pv.TWIN_MODELS)


def test_station_type_never_breaks_the_page():
    import asyncio

    from app.services import project_lessons

    class Broken:
        def begin_nested(self):
            raise RuntimeError("no lessons schema")
    assert asyncio.run(project_lessons.station_type(Broken(), "P-1")) is None
