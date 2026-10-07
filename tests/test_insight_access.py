"""PLAN.md P3 — who sees what on the patterns page. Pure, no DB."""
from types import SimpleNamespace

import pytest

from app.services.il_format import template_env
from app.services import insight_access as ia
from app.services import pattern_service as pt
from app.services import stage_sectors as ss
from tests.test_pattern_service import _frames, page_ctx

P = pt._plain(pt.compute_patterns(_frames(extra_projects=4)))
P_WITH_HEALTH = {**P, "health": {"snapshot_dates": [], "snapshot_rows": 0, "weekly_rows": 0,
                                  "weekly_range": None, "snapshot_indexes": []}}
IDS = lambda view: {x["identifier"] for x in view["projects"]}   # noqa: E731


def test_admin_sees_everything_and_tiles_match_the_engine():
    v = ia.view_for(ia.Scope(admin=True), P)
    assert ia.ADMIN_SECTIONS <= v["sections"] and {"league", "sectors", "attribution"} <= v["sections"]
    assert len(v["projects"]) == len(P["projects"])
    m = P["metrics"]
    assert v["summary"]["forecast_later"] == m["forecast_drift"]["value"]["later"]
    assert v["summary"]["forecast_n"] == m["forecast_drift"]["n"]
    assert v["summary"]["baseline_later"] == m["baseline_moves"]["value"]["later"]
    assert v["summary"]["stuck"] == m["stuck"]["value"]["count"]
    assert v["summary"]["stale"] == m["stale_reporting"]["value"]["count"]
    assert v["summary"]["undated"] == m["undated"]["value"]["count"]
    assert v["summary"]["past_due"] == m["past_due"]["value"]["count"]


def test_pm_department_sees_all_projects_and_the_league_but_no_admin_sections():
    v = ia.view_for(ia.Scope(sector=ss.PM_DEPT), P)
    assert len(v["projects"]) == len(P["projects"]) and v["league"]
    assert not (ia.ADMIN_SECTIONS & v["sections"])


def test_sector_manager_sees_only_their_stages_and_their_attribution():
    v = ia.view_for(ia.Scope(sector=ss.EXECUTION), P)
    assert IDS(v) == {"P-1", "P-4"}                     # בדיקות + the shared stage
    assert [s["sector"] for s in v["sectors"]] == [ss.EXECUTION]
    assert set(v["attribution"]) == {ss.EXECUTION}
    assert "league" not in v["sections"] and not v["league"]
    assert not (ia.ADMIN_SECTIONS & v["sections"])


def test_pm_sees_own_projects_and_the_named_league():
    v = ia.view_for(ia.Scope(managers=frozenset({"מנהל ב"})), P)
    assert IDS(v) == {"P-4"}                            # P-3 is closed
    assert v["league"] and "sectors" not in v["sections"] and "attribution" not in v["sections"]
    assert v["title"] == "הפרויקטים שלי"


def test_sector_manager_who_is_also_a_pm_gets_both_sets():
    v = ia.view_for(ia.Scope(sector=ss.SUPERVISION, managers=frozenset({"מנהל א"})), P)
    assert {"P-4", "P-1", "P-2"} <= IDS(v)


@pytest.mark.parametrize("scope,allowed", [
    (ia.Scope(), False),
    (ia.Scope(sector=ss.UNKNOWN), False),                # the bucket is not a role
    (ia.Scope(sector="nonsense"), False),
    (ia.Scope(sector=ss.PLANNING), True),
    (ia.Scope(managers=frozenset({"x"})), True),
    (ia.Scope(admin=True), True),
])
def test_who_is_allowed_at_all(scope, allowed):
    assert scope.allowed is allowed


@pytest.mark.parametrize("scope", [
    ia.Scope(admin=True), ia.Scope(sector=ss.PM_DEPT), ia.Scope(sector=ss.EXECUTION),
    ia.Scope(managers=frozenset({"מנהל א"})),
])
def test_page_renders_for_every_role_and_hides_admin_sections(scope):
    env = template_env()
    html = env.get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights")),
        current_user=SimpleNamespace(is_admin=scope.admin, username="u", role=None, id=1),
        p=P_WITH_HEALTH, view=ia.view_for(scope, P_WITH_HEALTH), sector_labels=ss.SECTORS, **page_ctx())
    assert ("אותות מקדימים" in html) is scope.admin
    assert ("בריאות הנתונים" in html) is scope.admin
    assert ("טבלת מנהלי פרויקטים" in html) is ("league" in ia.view_for(scope, P)["sections"])
    assert "P-1" in html or "P-4" in html


def test_access_page_renders():
    env = template_env()
    users = [SimpleNamespace(id=1, username="ירון", is_admin=True, sector=None),
             SimpleNamespace(id=2, username="גלי", is_admin=False, sector=ss.PM_DEPT)]
    html = env.get_template("project_insights_access.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights/access")),
        current_user=users[0], rows=[{"name": "גולן, לירון", "count": 18, "user_id": None, "hint": "ירון"}],
        users=users, sectors=ss.ASSIGNABLE_SECTORS, saved="1")
    assert 'name="alias::גולן, לירון"' in html and 'name="sector::2"' in html
    assert "ללא סטטוס" not in html                    # the bucket is never assignable


def test_preview_banner_and_selector_render_for_the_admin():
    env = template_env()
    html = env.get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights"),
                                query_params={"as_sector": ss.EXECUTION}),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        p=P, view=ia.view_for(ia.Scope(sector=ss.EXECUTION), P), sector_labels=ss.SECTORS, **page_ctx(),
        preview_label=ss.SECTORS[ss.EXECUTION],
        preview_options={"users": [], "sectors": ss.ASSIGNABLE_SECTORS, "managers": ["מנהל א"]})
    assert "צפה כ" in html and "תצוגה מקדימה — מגזר ביצוע" in html
    assert "אותות מקדימים" not in html            # previewing drops admin power


# ── Drill-down ────────────────────────────────────────────────────────────

ADMIN_VIEW = ia.view_for(ia.Scope(admin=True), P)


@pytest.mark.parametrize("value,summary_key", [
    ("forecast", "forecast_later"), ("baseline", "baseline_later"), ("stuck", "stuck"),
    ("stale", "stale"), ("undated", "undated"), ("past_due", "past_due")])
def test_every_tile_drills_to_exactly_its_count(value, summary_key):
    d = ia.drill_for(ADMIN_VIEW, P, "tile", value)
    assert len(d["rows"]) == ADMIN_VIEW["summary"][summary_key]


def test_sector_and_league_rows_drill_to_their_numbers():
    for s in P["sectors"]:
        assert len(ia.drill_for(ADMIN_VIEW, P, "sector", s["sector"], "all")["rows"]) == s["n"]
        assert len(ia.drill_for(ADMIN_VIEW, P, "sector", s["sector"], "stuck")["rows"]) == s["stuck"]
        assert len(ia.drill_for(ADMIN_VIEW, P, "sector", s["sector"], "baseline")["rows"]) == s["baseline_moved"]
    for r in P["league"]:
        assert len(ia.drill_for(ADMIN_VIEW, P, "manager", r["manager"], "all")["rows"]) == r["n"]


def test_attribution_and_waves_drill_to_events():
    att = P["metrics"]["slip_attribution"]["value"]
    for key, v in att.items():
        assert len(ia.drill_for(ADMIN_VIEW, P, "attribution", key)["rows"]) == v["events"]
    for w in P["metrics"]["update_waves"]["value"]:
        assert len(ia.drill_for(ADMIN_VIEW, P, "wave", w["from"], "forecast")["rows"]) == w["forecast_later"]
        assert len(ia.drill_for(ADMIN_VIEW, P, "wave", w["from"], "baseline")["rows"]) == w["baseline_later"]


def test_a_drill_never_widens_access():
    exec_view = ia.view_for(ia.Scope(sector=ss.EXECUTION), P)
    assert ia.drill_for(exec_view, P, "sector", ss.PLANNING, "all") is None       # another sector
    assert ia.drill_for(exec_view, P, "attribution", ss.PLANNING) is None
    assert ia.drill_for(exec_view, P, "risk", "ציוד/אספקה") is None                # admin-only topic
    assert ia.drill_for(exec_view, P, "wave", "2026-03-25", "forecast") is None
    pm_view = ia.view_for(ia.Scope(managers=frozenset({"מנהל ב"})), P)
    assert ia.drill_for(pm_view, P, "manager", "מנהל א", "all")["rows"] == []     # a colleague's row
    assert ia.drill_for(ADMIN_VIEW, P, "tile", "nonsense") is None
    assert ia.drill_for(ADMIN_VIEW, P, "sector", ss.PLANNING, "nonsense") is None


def test_risk_topics_drill_both_counts():
    for r in P["metrics"]["risk_categories"]["value"]:
        assert len(ia.drill_for(ADMIN_VIEW, P, "risk", r["category"])["rows"]) == r["projects"]
        assert len(ia.drill_for(ADMIN_VIEW, P, "risk", r["category"], "column")["rows"]) == r["in_risk_column"]


def _admin_html(p=P_WITH_HEALTH):
    env = template_env()
    return env.get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        p=p, view=ia.view_for(ia.Scope(admin=True), p), sector_labels=ss.SECTORS, **page_ctx())


def test_lists_name_every_project_and_drill_to_their_tile():
    html = _admin_html()
    lists = html[html.index("רשימות לטיפול"):]
    for x in P["projects"]:
        if x["stuck"] or x["stale"] or x["undated"]:
            assert f'>{x["name"]}</a>' in lists, x["identifier"]      # the name, not only the id
    for key in ("stuck", "stale", "undated", "past_due"):
        assert f"drill=tile&amp;dv={key}" in lists or f"drill=tile&dv={key}" in lists


def test_rtl_signed_numbers_and_spans_stay_isolated():
    import copy
    p = copy.deepcopy(P_WITH_HEALTH)
    p["projects"][0]["forecast_moved_months"] = -2.5
    html = _admin_html(p)
    assert '<bdi class="ltr">-2.5</bdi>' in html                    # not "2.5-"
    assert '<bdi class="ltr">≥' in html                             # not "25≤"
    body = html[html.index("<body>"):]
    assert "→" not in body.replace("→ חזרה לפרויקטים", "")          # an LTR arrow points backwards in RTL


def test_league_stage_mix_drills_to_each_count():
    for r in P["league"]:
        for st, c in r["stage_mix"].items():
            d = ia.drill_for(ADMIN_VIEW, P, "manager", r["manager"], "all", stage=st)
            assert len(d["rows"]) == c and all(x["stage"] == st for x in d["rows"])


def test_stage_and_whole_wave_drills():
    stages = {x["stage"] for x in P["projects"]}
    for st in stages:
        assert len(ia.drill_for(ADMIN_VIEW, P, "stage", st)["rows"]) == sum(x["stage"] == st for x in P["projects"])
    for w in P["metrics"]["update_waves"]["value"]:
        rows = ia.drill_for(ADMIN_VIEW, P, "wave", w["from"], "any")["rows"]
        assert sum(e["kind"] == "forecast" for e in rows) == w["forecast_later"]
        assert sum(e["kind"] == "baseline" for e in rows) == w["baseline_later"]
    pm_view = ia.view_for(ia.Scope(managers=frozenset({"מנהל ב"})), P)
    assert {x["identifier"] for x in ia.drill_for(pm_view, P, "stage", "תכנון")["rows"]} <= IDS(pm_view)


def test_every_league_and_sector_label_links_to_its_drill():
    from urllib.parse import urlencode
    html = _admin_html().replace("&amp;", "&")
    for r in P["league"]:
        for st in r["stage_mix"]:
            link = urlencode({"drill": "manager", "dv": r["manager"], "dm": "all"}) + "&" + urlencode({"ds": st})
            assert link in html, (r["manager"], st)
    assert "drill=stage" in html and "dm=any" in html
