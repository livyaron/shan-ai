"""The four-station dashboard: missions / projects / lessons beside decisions,
each tile zooming into a full view. No DB — fake sessions answer the queries.
"""
import datetime
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers.dashboard import DASHBOARD_DAYS, templates
from app.services import dashboard_domains as dd
from app.services import missions_menu_service as oms
from app.services import stage_sectors

TEMPLATE = Path("app/templates/dashboard.html").read_text(encoding="utf-8")
STATIONS = ("decisions", "missions", "projects", "lessons")


class _Rows:
    def __init__(self, rows=(), scalar=0):
        self._rows, self._scalar = list(rows), scalar

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)

    def scalar(self):
        return self._scalar


class _ProjectsSession:
    """Answers the single projects query with hand-made rows."""
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _Rows(self.rows)


def _proj(ident, stage, plan=None, forecast=None, text=None, sup=None, test=None):
    return (ident, f"name {ident}", stage, "PM", plan, forecast, text, sup, test)


@pytest.mark.asyncio
async def test_projects_station_counts_and_never_sums_sectors():
    today = oms.today_il()
    long_ago = today - datetime.timedelta(days=400)
    rows = [
        _proj("A", "עבודה אזרחית והרכבות", plan=long_ago, forecast=today - datetime.timedelta(days=5)),
        _proj("B", "תכנון", plan=today, forecast=today + datetime.timedelta(days=10)),
        _proj("C", "", text="לא אפשרי, יתקבל יעד חדש", sup=True),
        _proj("D", "הסתיים", forecast=long_ago),          # closed: not active, never past due
    ]
    s = await dd.projects_stats(_ProjectsSession(rows))
    assert s["ok"] and s["active"] == 3
    assert s["past_due"] == 1 and s["late"] == 1        # only A
    assert s["undated"] == 1 and s["short_staff"] == 1  # only C
    sectors = {k: n for k, _label, n in s["sectors"]}
    # A sits in two sectors at once, so the sector counts add up to more than the projects
    assert sectors[stage_sectors.SUPERVISION] == 1 and sectors[stage_sectors.EXECUTION] == 1
    assert sectors[stage_sectors.UNKNOWN] == 1
    assert sum(sectors.values()) > s["active"]
    assert [w["id"] for w in s["watch"]] == ["A"] and s["watch"][0]["past_due"]


@pytest.mark.asyncio
async def test_lessons_station_is_off_when_the_schema_is_missing():
    class _NoSchema:
        async def execute(self, *_a, **_k):
            return _Rows(scalar=False)

    assert await dd.lessons_stats(_NoSchema(), DASHBOARD_DAYS) == {"ok": False}


@pytest.mark.asyncio
async def test_a_failing_station_never_hides_the_others():
    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    out = await dd.all_domains(_Boom(), DASHBOARD_DAYS)
    assert out == {"missions": {"ok": False}, "projects": {"ok": False}, "lessons": {"ok": False}}


def test_series_is_zero_filled():
    today = datetime.date(2026, 10, 16)
    days = dd._days(DASHBOARD_DAYS, today)
    assert len(days) == DASHBOARD_DAYS and days[-1] == today
    rows = [(datetime.datetime(2026, 10, 16, 0, 0), 3)]
    assert dd._series(rows, days) == [0] * (DASHBOARD_DAYS - 1) + [3]


def test_every_station_has_a_tile_a_tab_and_a_view():
    for key in STATIONS:
        assert f'data-zoom="{key}"' in TEMPLATE, key
        assert f'data-tab="{key}"' in TEMPLATE, key
        assert f'data-view="{key}"' in TEMPLATE, key
    js_keys = re.search(r"const ZM_KEYS = \[(.*?)\]", TEMPLATE).group(1)
    assert re.findall(r"'(\w+)'", js_keys) == list(STATIONS)
    for key in STATIONS:
        assert re.search(rf"\n    {key}\(a\) \{{", TEMPLATE), f"STATION_DRAW.{key}"


def _render(stations, scope="all", decisions_all=True):
    ctx = dict(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/")),
        current_user=SimpleNamespace(id=1, username="u", is_admin=False, role=None, full_name="u"),
        total_decisions=0, total_users=0, avg_feedback=None, type_counts={}, status_counts={},
        daily_labels=["01/01"] * DASHBOARD_DAYS, daily_data=[0] * DASHBOARD_DAYS, week_total=0,
        recent_decisions=[], role_counts={}, pending_approvals=0, raci_counts={}, written_count=0,
        type_labels_he={}, status_labels_he={}, stations=stations,
        scope=scope, decisions_all=decisions_all, scope_labels=dd.SCOPES,
    )
    return templates.env.get_template("dashboard.html").render(ctx)


def test_stations_render_full_and_unavailable():
    full = _render({
        "missions": {"ok": True, "open": 4, "do_now": 1, "overdue": 2, "done_week": 3, "new_24h": 1,
                     "undated": 0, "quadrants": [["do", "🔥 בצע עכשיו", 1]], "daily_data": [0] * 7,
                     "daily_done": [0] * 7, "owners": [["dana", 4]],
                     "recent": [{"id": 9, "title": "<i>t</i>", "owner": "dana", "quadrant": "do",
                                 "quadrant_label": "🔥", "due": "01/01/2026", "overdue": True, "new": False}]},
        "projects": {"ok": True, "active": 2, "past_due": 0, "late": 0, "undated": 0, "short_staff": 0,
                     "sectors": [], "stages": [], "watch": [], "watch_total": 0},
        "lessons": {"ok": False},
    })
    assert 'data-live="m_overdue">2<' in full
    assert "<i>t</i>" not in full and "&lt;i&gt;t&lt;/i&gt;" in full        # titles stay escaped
    assert "מערכת הלקחים לא זמינה כרגע" in full
    assert re.search(r'class="st alarm" data-zoom="missions"', full)      # overdue trips the tile

    off = _render({"missions": {"ok": False}, "projects": {"ok": False}, "lessons": {"ok": False}})
    assert off.count("התחנה לא זמינה כרגע") == 4                          # tile + view, twice


# ── שלי / כל האגף ─────────────────────────────────────────────────────────

from app.services import insight_access


def test_scope_resolution():
    assert dd.resolve_scope(None, None, is_admin=True) == dd.ALL
    assert dd.resolve_scope(None, None, is_admin=False) == dd.MINE
    assert dd.resolve_scope("all", "mine", is_admin=False) == dd.ALL      # the URL beats the cookie
    assert dd.resolve_scope(None, "all", is_admin=False) == dd.ALL        # the cookie remembers
    assert dd.resolve_scope("bogus", "junk", is_admin=False) == dd.MINE   # garbage = the default


class _Recorder:
    def __init__(self, scalar=0):
        self.sql, self._scalar = [], scalar

    async def execute(self, stmt, params=None):
        self.sql.append((str(stmt), params or {}))
        return _Rows(scalar=self._scalar)


@pytest.mark.asyncio
async def test_my_missions_filter_every_query_by_owner():
    rec = _Recorder()
    s = await dd.missions_stats(rec, DASHBOARD_DAYS, owner_id=77)
    assert s["owners"] == [] and [b[0] for b in s["due_buckets"]]
    assert rec.sql and all("missions.owner_id = :owner_id" in q for q, _ in rec.sql), [q for q, _ in rec.sql if ":owner_id" not in q]

    rec_all = _Recorder()
    await dd.missions_stats(rec_all, DASHBOARD_DAYS)
    assert not any("missions.owner_id = :owner_id" in q for q, _ in rec_all.sql)


@pytest.mark.asyncio
async def test_my_projects_follow_insight_access():
    rows = [_proj("PLAN", "תכנון"), _proj("EXEC", "בדיקות"), _proj("BOTH", "עבודה אזרחית והרכבות")]
    planning = await dd.projects_stats(_ProjectsSession(rows), insight_access.Scope(sector=stage_sectors.PLANNING))
    assert planning["active"] == 1 and not planning["unlinked"]
    execution = await dd.projects_stats(_ProjectsSession(rows), insight_access.Scope(sector=stage_sectors.EXECUTION))
    assert execution["active"] == 2                     # בדיקות + the shared stage
    named = await dd.projects_stats(_ProjectsSession(rows), insight_access.Scope(managers=frozenset({"PM"})))
    assert named["active"] == 3                         # every row's manager is "PM"
    nobody = await dd.projects_stats(_ProjectsSession(rows), insight_access.Scope())
    assert nobody["active"] == 0 and nobody["unlinked"]  # never linked = nothing, never everything
    admin = await dd.projects_stats(_ProjectsSession(rows), insight_access.Scope(admin=True))
    assert admin["active"] == 3


@pytest.mark.asyncio
async def test_my_lessons_filter_every_query_by_my_profiles():
    rec = _Recorder(scalar=True)
    s = await dd.lessons_stats(rec, DASHBOARD_DAYS, user_id=5)
    assert s["unlinked"]                                # no profile rows came back
    data_queries = [(q, p) for q, p in rec.sql if "lessons.lessons" in q or "workflow_events" in q]
    data_queries = [(q, p) for q, p in data_queries if "to_regclass" not in q]
    assert data_queries and all("ANY(:ids)" in q and p.get("ids") == [] for q, p in data_queries)


@pytest.mark.asyncio
async def test_division_decisions_only_for_an_admin():
    from httpx import ASGITransport, AsyncClient
    from app.database import get_db_session
    from app.main import app
    from app.models import User
    from app.routers.login import get_current_user

    class _Empty:
        async def execute(self, *_a, **_k):
            return _Rows()

    async def fake_session():
        yield _Empty()

    async def get(user, scope):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db_session] = fake_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                return (await c.get(f"/dashboard/live?scope={scope}")).json()
        finally:
            app.dependency_overrides.clear()

    pm = User(id=9301, username="pm", password_hash="", is_admin=False)
    boss = User(id=9302, username="boss", password_hash="", is_admin=True)
    assert (await get(pm, "all"))["decisions_all"] is False      # the decisions page shows a PM only theirs
    assert (await get(boss, "all"))["decisions_all"] is True
    assert (await get(boss, "mine"))["decisions_all"] is False
    assert (await get(pm, "all"))["scope"] == "all"


def test_switch_and_honest_labels_render():
    off = {"missions": {"ok": False}, "projects": {"ok": False}, "lessons": {"ok": False}}
    pm_all = _render(off, scope="all", decisions_all=False)
    assert 'data-scope="mine"' in pm_all and 'data-scope="all"' in pm_all
    assert "החלטות מוצגות רק שלך" in pm_all
    boss_all = _render(off, scope="all", decisions_all=True)
    assert "כל ההחלטות באגף" in boss_all and "החלטות מוצגות רק שלך" not in boss_all
    assert "/dashboard/live?scope=" in TEMPLATE


# ── live station scenes ───────────────────────────────────────────────────

SCENES_JS = Path("static/js/station_scenes.js")
SCENE_OF = {"decisions": "scope", "missions": "arc", "projects": "line", "lessons": "globe"}


def test_scenes_are_decoration_only():
    js = SCENES_JS.read_text(encoding="utf-8")
    assert "/static/js/station_scenes.js" in TEMPLATE
    # driven by the page, never the other way round: no fetches, no text, no numbers on screen
    assert "fetch(" not in js and "fillText" not in js and "innerHTML" not in js
    assert "prefers-reduced-motion" in js
    for scene in SCENE_OF.values():
        assert re.search(rf"function {scene}\(", js), scene


def test_every_station_has_its_scene_on_the_tile_and_in_the_zoom():
    for key, scene in SCENE_OF.items():
        tile = re.search(rf'data-zoom="{key}"[^>]*>\s*<canvas class="st-scene" data-scene="{scene}"', TEMPLATE)
        view = re.search(rf'data-view="{key}"[^>]*>\s*<canvas class="zv-scene" data-scene="{scene}"', TEMPLATE)
        assert tile and view, key
    assert re.search(r"const SCENE_OF = \{ decisions: 'scope', missions: 'arc', projects: 'line', lessons: 'globe' \}", TEMPLATE)


def test_scope_switch_is_in_the_header_and_the_zoom_bar():
    off = {"missions": {"ok": False}, "projects": {"ok": False}, "lessons": {"ok": False}}
    html = _render(off, scope="mine", decisions_all=False)
    assert html.count('class="sc-switch"') == 2
    zoom_bar = html[html.index('class="zm-bar"'):html.index('id="zm-body"')]
    assert 'data-scope="mine"' in zoom_bar and 'data-scope="all"' in zoom_bar
