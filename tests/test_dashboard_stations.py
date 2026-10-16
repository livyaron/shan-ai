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


def _render(stations):
    ctx = dict(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/")),
        current_user=SimpleNamespace(id=1, username="u", is_admin=False, role=None, full_name="u"),
        total_decisions=0, total_users=0, avg_feedback=None, type_counts={}, status_counts={},
        daily_labels=["01/01"] * DASHBOARD_DAYS, daily_data=[0] * DASHBOARD_DAYS, week_total=0,
        recent_decisions=[], role_counts={}, pending_approvals=0, raci_counts={}, written_count=0,
        type_labels_he={}, status_labels_he={}, stations=stations,
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
