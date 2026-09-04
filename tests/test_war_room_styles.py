"""חדר מבצעים — the per-user layout switcher.

DB-free by design: everything here is either pure resolution logic or a Jinja
render against a hand-built context, so it runs in CI without Postgres. The
render tests use StrictUndefined — a layout that reads a context key the router
does not pass fails here instead of at 500 in production.
"""
import datetime
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.models import Mission, MissionUpdate, User
from app.services import missions_menu_service as oms
from app.services import war_room_styles as wrs
from app.services import war_room_motto as wrm
from app.services import war_room_wall as wall

TODAY = datetime.date(2026, 8, 22)


# --------------------------------------------------------------------------
# style resolution
# --------------------------------------------------------------------------

def test_default_style_is_the_existing_board():
    """Nobody gets a new screen unasked: NULL preference renders what they know."""
    assert wrs.DEFAULT_STYLE == "cyber"
    assert wrs.resolve(None, None) == "cyber"
    assert wrs.template_for(wrs.DEFAULT_STYLE) == "war_room.html"


def test_saved_preference_is_used_when_no_query_param():
    assert wrs.resolve(None, "focus") == "focus"
    assert wrs.resolve(None, "table") == "table"


def test_query_param_overrides_saved_preference():
    """?style= pins one view so the wall display needs no user of its own."""
    assert wrs.resolve("wall", "focus") == "wall"


def test_unknown_style_falls_back_instead_of_failing():
    """A stale bookmark or a dropped column value must still show the board."""
    assert wrs.resolve("nope", None) == wrs.DEFAULT_STYLE
    assert wrs.resolve(None, "nope") == wrs.DEFAULT_STYLE
    assert wrs.resolve("nope", "wall") == "wall"


def test_is_known_matches_the_registry():
    for key in wrs.STYLE_KEYS:
        assert wrs.is_known(key)
    assert not wrs.is_known(None)
    assert not wrs.is_known("")
    assert not wrs.is_known("cyber ")


def test_every_style_has_a_label_and_a_template_file():
    import pathlib
    for key, name, desc in wrs.STYLES:
        assert name and desc
        assert wrs.STYLE_LABELS[key] == name
        path = pathlib.Path("app/templates") / wrs.template_for(key)
        assert path.exists(), f"missing template for style {key}: {path}"


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _mission(mid, title, owner, due, quadrant="do", status="open", updates=()):
    """Transient ORM objects rather than stand-ins: the wall reads the status log
    through SQLAlchemy's loaded/unloaded inspection, which a fake never triggers."""
    urgent, important = oms.quadrant_flags(quadrant)
    m = Mission(
        id=mid, title=title, description="תיאור בדיקה", owner_id=1,
        due_date=due, status=status, is_urgent=urgent, is_important=important,
    )
    m.owner = User(username=owner)
    m.created_by = User(username=owner)
    m.updates = list(updates)
    return m


def _update(text, author="אבי"):
    return MissionUpdate(text=text, author_name=author, kind=None,
                         created_at=datetime.datetime(2026, 8, 21, 14, 20))


def _context(style, is_viewer=False):
    late = _mission(1, "החלפת מפסק ראשי", "אבי", TODAY - datetime.timedelta(days=2),
                    updates=[_update("ממתין לאישור בטיחות")])
    today_due = _mission(2, "ליקוי בטיחות", "דנה", TODAY)
    planned = _mission(3, "בדיקת ממסרים", "שרון", TODAY + datetime.timedelta(days=14), "plan")
    undated = _mission(4, "מיפוי מלאי", "יעל", None, "backlog")
    missions = [late, today_due, planned, undated]

    quadrants = {key: [] for key, *_ in oms.QUADRANTS}
    for m in missions:
        quadrants[oms.quadrant_key(m)].append(m)

    closed = _mission(9, "נסגר", "דנה", TODAY, status="done")
    closed.completed_at = datetime.datetime(2026, 8, 21, 9, 0)
    wall_pages = wall.build_pages(missions, [closed], TODAY)

    return {
        "request": SimpleNamespace(url=SimpleNamespace(path="/dashboard/war-room")),
        "current_user": SimpleNamespace(id=1, username="אבי", is_admin=True, avatar_path=None,
                                        photo_path=None, role=None),
        "user": SimpleNamespace(id=1, username="אבי", is_admin=True, avatar_path=None,
                                photo_path=None, role=None),
        "missions": missions,
        "quadrants": quadrants,
        "quadrant_defs": oms.QUADRANTS,
        "status_labels": oms.STATUS_LABELS,
        "active_statuses": oms.ACTIVE_STATUSES,
        "stats": {"open": 4, "do_now": 2, "overdue": 1, "done_week": 6},
        "users": [SimpleNamespace(id=1, username="אבי"), SimpleNamespace(id=2, username="דנה")],
        "today": TODAY,
        "filters": {"owner": None, "status": "active", "q": ""},
        "is_viewer": is_viewer,
        "fmt_stamp": oms.format_stamp_il,
        "quadrant_of": lambda m: oms.quadrant_label(oms.quadrant_key(m)),
        "msg": "",
        "styles": wrs.STYLES,
        "current_style": style,
        "style_labels": wrs.STYLE_LABELS,
        "tv": False,
        # per-style extras, mirroring the router
        "my_due_now": [late, today_due], "my_upcoming": [planned],
        "my_undated": [undated], "my_total": 4,
        "owner_load": [("אבי", 3), ("דנה", 1)], "owner_load_max": 3,
        # Built through the real shaping code, so a change there fails the render
        # test instead of quietly drifting from what the router passes.
        "wall_pages": wall_pages,
        "wall_rotate_ms": wall.ROTATE_SECONDS * 1000,
        "wall_refresh": wall.refresh_seconds(len(wall_pages)),
        "wall_legend": wall.LEGEND,
        "wall_active_total": len(missions),
        "wall_silent": 3,
        "wall_silent_days": wall.SILENT_DAYS,
        "wall_feed": [{"mission": "החלפת מפסק ראשי", "text": "ממתין לאישור בטיחות",
                       "who": "אבי", "when": "21/08 14:20", "close": False}],
        "wall_motto": {"quote": wrm.STOIC[0][0], "author": wrm.STOIC[0][1],
                       "kind": "stoic", "line": "סוגרים היום את שתי המשימות באיחור."},
    }


def _render(style, **kwargs):
    env = Environment(loader=FileSystemLoader("app/templates"), undefined=StrictUndefined)
    return env.get_template(wrs.template_for(style)).render(**_context(style, **kwargs))


@pytest.mark.parametrize("style", wrs.STYLE_KEYS)
def test_every_layout_renders(style):
    html = _render(style)
    assert "חדר מבצעים" in html
    assert "החלפת מפסק ראשי" in html


@pytest.mark.parametrize("style", wrs.STYLE_KEYS)
def test_every_layout_renders_for_a_viewer(style):
    """A viewer-role user must not be offered write actions in ANY layout."""
    html = _render(style, is_viewer=True)
    assert "/dashboard/war-room/create" not in html


@pytest.mark.parametrize("style", wrs.STYLE_KEYS)
def test_every_layout_carries_the_style_switcher(style):
    """The switcher is the only way back out of a layout — it must never be missing."""
    html = _render(style)
    assert 'action="/dashboard/war-room/style"' in html
    for key, name, _ in wrs.STYLES:
        assert f'value="{key}"' in html
        assert name in html


@pytest.mark.parametrize("style", wrs.STYLE_KEYS)
def test_switcher_reaches_viewers_too(style):
    """Switching is a personal preference, not a board write: viewers get it as well."""
    html = _render(style, is_viewer=True)
    assert 'action="/dashboard/war-room/style"' in html


def test_wall_layout_offers_no_write_actions_at_all():
    """The wall display is a screen that talks, not one you work in.

    Asserted on the actual form targets rather than a keyword blocklist: the only
    POST the wall may carry is the style switcher, which changes nothing on the board.
    """
    import re
    html = _render("wall")
    posts = re.findall(r'action="([^"]+)"', html)
    assert posts == ["/dashboard/war-room/style"], posts
    assert "onclick=" not in html


def test_wall_layout_refreshes_itself():
    """Still self-refreshing — but only after a full rotation cycle, never mid-page."""
    html = _render("wall")
    assert 'http-equiv="refresh"' in html
    assert f'content="{wall.refresh_seconds(len(_context("wall")["wall_pages"]))}"' in html


def test_wall_layout_rotates_through_every_active_mission():
    """The board's whole active list must reach the screen, undated included —
    the old wall cut it at five dated missions and dropped the rest forever."""
    html = _render("wall")
    for title in ("החלפת מפסק ראשי", "ליקוי בטיחות", "בדיקת ממסרים", "מיפוי מלאי"):
        assert title in html
    assert 'class="wl-page' in html
    assert "wlPager" in html


def test_wall_layout_shows_the_latest_status_update():
    html = _render("wall")
    assert "ממתין לאישור בטיחות" in html      # on the card
    assert "wlFeed" in html                    # and in the board-wide ticker


def test_wall_layout_colours_by_time_not_by_quadrant():
    """Every tone in the registry has a CSS class and a legend entry."""
    html = _render("wall")
    for key, label in wall.LEGEND:
        assert f"--t-{key}" in html
        assert label in html


def test_wall_layout_carries_the_hourly_line():
    """A real quote plus the sentence that ties it to the board — both on screen."""
    html = _render("wall")
    assert wrm.STOIC[0][0] in html
    assert wrm.STOIC[0][1] in html
    assert "סוגרים היום את שתי המשימות באיחור." in html


def test_tv_mode_drops_the_navigation_chrome():
    """?tv=1 is the wall-mounted screen: board data only, no nav, no switcher."""
    env = Environment(loader=FileSystemLoader("app/templates"), undefined=StrictUndefined)
    ctx = _context("wall")
    ctx["tv"] = True
    html = env.get_template(wrs.template_for("wall")).render(**ctx)
    assert "/dashboard/war-room/style" not in html
    assert "החלפת מפסק ראשי" in html


def test_table_layout_wires_batch_actions():
    html = _render("table")
    assert "/dashboard/war-room/bulk" in html
    assert "bulkBar" in html


def test_focus_layout_shows_todays_missions_first():
    """The point of this layout: what today asks of me, before anything else."""
    html = _render("focus")
    assert "היום" in html
    assert html.index("החלפת מפסק ראשי") < html.index("בדיקת ממסרים")


def test_layouts_do_not_hardcode_the_public_url():
    """Same rule as the rest of the app — no baked-in host names in templates."""
    for key in wrs.STYLE_KEYS:
        assert "shan-ai.up.railway.app" not in _render(key)
