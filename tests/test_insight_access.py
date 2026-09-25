"""PLAN.md P3 — who sees what on the patterns page. Pure, no DB."""
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import insight_access as ia
from app.services import pattern_service as pt
from app.services import stage_sectors as ss
from tests.test_pattern_service import _frames

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
    env = Environment(loader=FileSystemLoader("app/templates"))
    html = env.get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights")),
        current_user=SimpleNamespace(is_admin=scope.admin, username="u", role=None, id=1),
        p=P_WITH_HEALTH, view=ia.view_for(scope, P_WITH_HEALTH), sector_labels=ss.SECTORS)
    assert ("אותות מקדימים" in html) is scope.admin
    assert ("בריאות הנתונים" in html) is scope.admin
    assert ("טבלת מנהלי פרויקטים" in html) is ("league" in ia.view_for(scope, P)["sections"])
    assert "P-1" in html or "P-4" in html


def test_access_page_renders():
    env = Environment(loader=FileSystemLoader("app/templates"))
    users = [SimpleNamespace(id=1, username="ירון", is_admin=True, sector=None),
             SimpleNamespace(id=2, username="גלי", is_admin=False, sector=ss.PM_DEPT)]
    html = env.get_template("project_insights_access.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights/access")),
        current_user=users[0], rows=[{"name": "גולן, לירון", "count": 18, "user_id": None, "hint": "ירון"}],
        users=users, sectors=ss.ASSIGNABLE_SECTORS, saved="1")
    assert 'name="alias::גולן, לירון"' in html and 'name="sector::2"' in html
    assert "ללא סטטוס" not in html                    # the bucket is never assignable
