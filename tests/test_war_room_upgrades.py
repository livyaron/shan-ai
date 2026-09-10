"""חדר מבצעים — the three board upgrades: KPI filters, משימת המשך, דחיית יעד.

DB-free by design, like test_war_room_styles.py: the query shaping is asserted
on the compiled SQL of a real select, the chain and the history rules on pure
functions over transient ORM objects, and the screens on a Jinja render with
StrictUndefined. It runs in CI without Postgres.
"""
import datetime
import re
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.models import Mission, MissionDueChange, MissionUpdate, User
from app.services import missions_menu_service as oms
from app.services import war_room_chain as chain
from app.services import war_room_kpis as kpis
from app.services import war_room_styles as wrs

TODAY = datetime.date(2026, 9, 10)

# The two layouts that carry a filterable board. Focus is "my missions today"
# and the wall is a screen nobody clicks — neither gets the KPI filter.
BOARD_STYLES = ["cyber", "table"]


def _sql(key: str) -> str:
    from sqlalchemy import select
    stmt = kpis.apply_filter(select(Mission), key, TODAY)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# --------------------------------------------------------------------------
# 1. KPI cards as filters
# --------------------------------------------------------------------------

def test_every_kpi_card_maps_to_a_stat_the_router_actually_computes():
    """A card that reads a stat key nobody fills would show a permanent 0."""
    router_stats = {"open", "do_now", "overdue", "done_week"}
    assert {stat for _k, stat, *_ in kpis.KPIS} == router_stats


def test_unknown_or_missing_kpi_is_no_filter_never_an_error():
    """A stale bookmark still has to answer with the board."""
    assert kpis.resolve(None) == ""
    assert kpis.resolve("") == ""
    assert kpis.resolve("nope") == ""
    for key in kpis.KPI_KEYS:
        assert kpis.resolve(key) == key


def test_each_kpi_selects_exactly_what_its_number_counts():
    open_sql = _sql("open")
    assert "missions.status IN ('open')" in open_sql

    do_sql = _sql("do")
    assert "is_urgent IS true" in do_sql and "is_important IS true" in do_sql

    overdue_sql = _sql("overdue")
    assert "due_date IS NOT NULL" in overdue_sql
    assert "due_date < '2026-09-10'" in overdue_sql

    done_sql = _sql("done_week")
    assert "status = 'done'" in done_sql
    assert "completed_at >=" in done_sql


def test_done_week_window_is_the_one_the_stat_row_uses():
    """The count and the list it opens read the same seven days."""
    now = datetime.datetime(2026, 9, 10, 12, 0)
    assert kpis.week_ago(now) == now - datetime.timedelta(days=kpis.DONE_WEEK_DAYS)
    assert kpis.DONE_WEEK_DAYS == 7


def test_a_kpi_owns_the_status_so_the_select_stands_down():
    """Otherwise 'הושלמו השבוע' filters itself against status=active and shows nothing."""
    for key in kpis.KPI_KEYS:
        assert kpis.owns_status(key)
    assert not kpis.owns_status("")


def test_clicking_a_card_filters_and_clicking_it_again_clears():
    off = {c["key"]: c for c in kpis.build_cards({}, "")}
    assert "kpi=overdue" in off["overdue"]["href"]
    assert not off["overdue"]["active"]

    on = {c["key"]: c for c in kpis.build_cards({}, "overdue")}
    assert on["overdue"]["active"]
    # The active card's own link is the way out — no second button anywhere.
    assert "kpi=" not in on["overdue"]["href"]
    assert "kpi=do" in on["do"]["href"]


def test_a_kpi_click_keeps_the_rest_of_the_filter_bar():
    card = next(c for c in kpis.build_cards(
        {}, "", {"owner": 3, "status": "active", "q": "ממסר", "style": "table"},
    ) if c["key"] == "do")
    assert "owner=3" in card["href"]
    assert "style=table" in card["href"]
    assert "kpi=do" in card["href"]


def test_card_values_come_from_the_board_wide_stats():
    cards = {c["key"]: c for c in kpis.build_cards(
        {"open": 12, "do_now": 3, "overdue": 5, "done_week": 8}, "do")}
    assert cards["do"]["value"] == 3
    assert cards["done_week"]["value"] == 8


# --------------------------------------------------------------------------
# 2. משימת בן — the chain
# --------------------------------------------------------------------------

def _mission(mid, title, status="open", parent_id=None, created=None, completed=None):
    m = Mission(id=mid, title=title, owner_id=1, status=status, is_urgent=True,
                is_important=True, parent_id=parent_id,
                created_at=created or datetime.datetime(2026, 8, 20, 6, 0))
    m.completed_at = completed
    m.updates = []
    m.due_changes = []
    return m


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Answers the chain loader's id→Mission lookups from a dict, no DB needed."""
    def __init__(self, by_id):
        self.by_id = by_id
        self.queries = 0

    async def scalars(self, stmt):
        self.queries += 1
        wanted = {
            int(v) for v in re.findall(r"\b\d+\b", str(
                stmt.compile(compile_kwargs={"literal_binds": True})))
        }
        return _FakeResult([m for mid, m in self.by_id.items() if mid in wanted])


@pytest.mark.asyncio
async def test_a_mission_with_no_parent_has_no_chain_and_costs_no_query():
    session = _FakeSession({})
    assert await chain.load_chains(session, [_mission(1, "לבד")]) == {}
    assert session.queries == 0


@pytest.mark.asyncio
async def test_the_chain_reads_oldest_first_and_ends_on_the_live_mission():
    first = _mission(1, "קבלת אישור מהוועדה", status="done",
                     created=datetime.datetime(2026, 8, 20, 6, 0),
                     completed=datetime.datetime(2026, 9, 1, 6, 0))
    second = _mission(2, "השלמת התנאים", status="done", parent_id=1,
                      created=datetime.datetime(2026, 9, 1, 6, 0),
                      completed=datetime.datetime(2026, 9, 8, 6, 0))
    third = _mission(3, "ביצוע העבודה", parent_id=2,
                     created=datetime.datetime(2026, 9, 8, 6, 0))

    chains = await chain.load_chains(_FakeSession({1: first, 2: second}), [third])
    line = chains[3]
    assert [s["title"] for s in line] == [
        "קבלת אישור מהוועדה", "השלמת התנאים", "ביצוע העבודה"]
    assert [s["current"] for s in line] == [False, False, True]
    assert line[0]["range"] == "20/08/2026–01/09/2026"
    assert line[1]["range"] == "01/09/2026–08/09/2026"
    # The live link has no closing date, and says so in words rather than blank.
    assert line[-1]["range"].endswith(chain.TODAY_LABEL)


@pytest.mark.asyncio
async def test_a_parent_that_no_longer_exists_is_not_a_chain():
    """A dangling link must not draw a one-line 'chain' of the mission itself."""
    child = _mission(5, "המשך", parent_id=99)
    assert await chain.load_chains(_FakeSession({}), [child]) == {}


@pytest.mark.asyncio
async def test_a_parent_loop_stops_instead_of_hanging_the_board():
    a = _mission(1, "א", parent_id=2)
    b = _mission(2, "ב", parent_id=1)
    chains = await chain.load_chains(_FakeSession({1: a, 2: b}), [a])
    assert len(chains[1]) == 2          # each mission appears once, then it stops


def test_a_closed_parent_is_not_a_card_on_the_open_board():
    """The board's own status rule is what hides it — nothing extra to maintain."""
    parent = _mission(1, "קבלת אישור מהוועדה", status="done")
    child = _mission(2, "השלמת התנאים", parent_id=1)
    live = [m for m in (parent, child) if m.status in oms.ACTIVE_STATUSES]
    assert live == [child]


# --------------------------------------------------------------------------
# 3. היסטוריית דחיית תאריך יעד
# --------------------------------------------------------------------------

def test_setting_a_first_target_is_not_a_postponement():
    m = _mission(1, "משימה")
    m.due_date = None
    assert not oms.needs_due_reason(m, TODAY)


def test_resaving_the_same_target_is_not_a_postponement():
    """Pressing 📅 עדכן twice must not add a phantom 'נדחה' to the card."""
    m = _mission(1, "משימה")
    m.due_date = TODAY
    assert not oms.needs_due_reason(m, TODAY)


def test_moving_an_existing_target_is_a_postponement_in_both_directions():
    m = _mission(1, "משימה")
    m.due_date = TODAY
    assert oms.needs_due_reason(m, TODAY + datetime.timedelta(days=5))
    assert oms.needs_due_reason(m, TODAY - datetime.timedelta(days=5))
    # Clearing a target that exists is also a change of an agreed date.
    assert oms.needs_due_reason(m, None)


def test_postpone_label_counts_in_hebrew():
    assert oms.postpone_label(0) == ""
    assert oms.postpone_label(1) == "נדחה פעם אחת"
    assert oms.postpone_label(2) == "נדחה פעמיים"
    assert oms.postpone_label(4) == "נדחה 4 פעמים"


def test_a_postponement_line_says_from_where_to_where_why_and_who():
    c = MissionDueChange(
        old_date=datetime.date(2026, 9, 10), new_date=datetime.date(2026, 9, 15),
        reason="ממתינים לקבלת התייחסות מהוועדה", requested_by="ישראל ישראלי",
    )
    line = oms.format_due_change(c)
    assert "10/09/2026" in line and "15/09/2026" in line
    assert "ממתינים לקבלת התייחסות מהוועדה" in line
    assert "ישראל ישראלי" in line


def test_the_telegram_card_states_that_a_target_moved():
    m = _mission(1, "החלפת מפסק")
    m.due_date = datetime.date(2026, 9, 15)
    m.owner = User(username="אבי")
    m.created_by = User(username="אבי")
    m.due_changes = [MissionDueChange(
        old_date=datetime.date(2026, 9, 10), new_date=datetime.date(2026, 9, 15),
        reason="הוועדה טרם התכנסה", requested_by="ישראל ישראלי",
    )]
    card = oms.build_mission_card(m)
    assert "נדחה פעם אחת" in card
    assert "הוועדה טרם התכנסה" in card


def test_an_unloaded_history_reads_as_empty_and_never_lazy_loads():
    """Under asyncio a lazy load raises MissingGreenlet — the card must not try."""
    m = Mission(id=1, title="משימה", owner_id=1, status="open")
    assert oms.get_due_changes(m) == []


# --------------------------------------------------------------------------
# the screens
# --------------------------------------------------------------------------

def _ctx(style, is_viewer=False, active_kpi="", chains=None, moves=None):
    parent = _mission(7, "קבלת אישור מהוועדה", status="done",
                      completed=datetime.datetime(2026, 9, 1, 6, 0))
    child = _mission(8, "השלמת התנאים שנקבעו באישור", parent_id=7)
    child.due_date = datetime.date(2026, 9, 15)
    child.due_changes = moves if moves is not None else [MissionDueChange(
        old_date=datetime.date(2026, 9, 10), new_date=datetime.date(2026, 9, 15),
        reason="ממתינים לקבלת התייחסות מהוועדה", requested_by="ישראל ישראלי",
        created_at=datetime.datetime(2026, 9, 1, 7, 0),
    )]
    for m in (parent, child):
        m.owner = User(username="אבי")
        m.created_by = User(username="אבי")
        m.owner_id = 1
        m.updates = [MissionUpdate(text="בטיפול", author_name="אבי",
                                   created_at=datetime.datetime(2026, 9, 2, 9, 0))]
    missions = [child, parent]

    quadrants = {key: [] for key, *_ in oms.QUADRANTS}
    for m in missions:
        quadrants[oms.quadrant_key(m)].append(m)

    return {
        "request": SimpleNamespace(url=SimpleNamespace(path="/dashboard/war-room")),
        "current_user": SimpleNamespace(id=1, username="אבי", is_admin=True,
                                        avatar_path=None, photo_path=None, role=None),
        "user": SimpleNamespace(id=1, username="אבי", is_admin=True,
                                avatar_path=None, photo_path=None, role=None),
        "missions": missions,
        "quadrants": quadrants,
        "quadrant_defs": oms.QUADRANTS,
        "status_labels": oms.STATUS_LABELS,
        "active_statuses": oms.ACTIVE_STATUSES,
        "stats": {"open": 4, "do_now": 2, "overdue": 1, "done_week": 6},
        "users": [SimpleNamespace(id=1, username="אבי"), SimpleNamespace(id=2, username="דנה")],
        "today": TODAY,
        "filters": {"owner": None, "status": "active", "q": "", "kpi": active_kpi},
        "kpi_cards": kpis.build_cards(
            {"open": 4, "do_now": 2, "overdue": 1, "done_week": 6}, active_kpi,
            {"owner": None, "status": "active", "q": "", "style": None}),
        "active_kpi": active_kpi,
        "chains": chains if chains is not None else {8: [
            {"id": 7, "title": "קבלת אישור מהוועדה", "status": "done", "current": False,
             "done": True, "cancelled": False, "range": "20/08/2026–01/09/2026"},
            {"id": 8, "title": "השלמת התנאים שנקבעו באישור", "status": "open",
             "current": True, "done": False, "cancelled": False,
             "range": "01/09/2026–היום"},
        ]},
        "child_counts": {7: 1},
        "postpone_label": oms.postpone_label,
        "fmt_due": oms.format_due,
        "is_viewer": is_viewer,
        "fmt_stamp": oms.format_stamp_il,
        "fmt_created": oms.format_created_il,
        "is_new": lambda m: False,
        "new_hours": oms.NEW_MISSION_HOURS,
        "quadrant_of": lambda m: oms.quadrant_label(oms.quadrant_key(m)),
        "msg": "",
        "styles": wrs.STYLES,
        "current_style": style,
        "style_labels": wrs.STYLE_LABELS,
        "tv": False,
    }


def _render(style, **kwargs):
    env = Environment(loader=FileSystemLoader("app/templates"), undefined=StrictUndefined)
    return env.get_template(wrs.template_for(style)).render(**_ctx(style, **kwargs))


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_every_kpi_card_is_a_link_on_the_board_layouts(style):
    html = _render(style)
    for key in kpis.KPI_KEYS:
        assert f"kpi={key}" in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_active_kpi_is_marked_and_its_own_link_clears_it(style):
    html = _render(style, active_kpi="overdue")
    assert "kpi=overdue" not in html          # the card that is on links back out
    assert "kpi=do" in html
    assert "on" in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_filter_bar_carries_the_kpi_so_the_two_compose(style):
    html = _render(style, active_kpi="do")
    assert 'name="kpi" value="do"' in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_board_draws_the_chain_inside_the_live_mission(style):
    html = _render(style)
    assert "השתלשלות" in html
    assert "קבלת אישור מהוועדה" in html
    assert "20/08/2026–01/09/2026" in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_board_offers_a_follow_up_mission_and_carries_the_parent_link(style):
    html = _render(style)
    assert "משימת המשך" in html
    assert 'data-child="8"' in html
    assert 'name="parent_id"' in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_a_viewer_is_offered_no_follow_up_button(style):
    html = _render(style, is_viewer=True)
    # The attribute on a real button, not the selector string inside the shared
    # script — that one is inert without a button to find.
    assert 'data-child="8"' not in html
    assert 'data-child="7"' not in html
    assert 'name="parent_id"' not in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_card_says_the_target_moved_and_hides_the_detail_until_asked(style):
    html = _render(style)
    assert "נדחה פעם אחת" in html or "↻ 1" in html
    assert "ממתינים לקבלת התייחסות מהוועדה" in html
    assert "ישראל ישראלי" in html
    # Collapsed by default: a <details> or a display:none expander, never an
    # always-open block that buries the card.
    assert "<details" in html or "tb-exp" in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_a_mission_whose_target_never_moved_says_nothing_about_postponement(style):
    html = _render(style, moves=[], chains={})
    assert oms.postpone_label(1) not in html
    assert "ממתינים לקבלת התייחסות מהוועדה" not in html
    # The chain block itself, not the word: the layouts document it in a CSS
    # comment, which is not something the reader of a card ever sees.
    assert "20/08/2026–01/09/2026" not in html


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_the_date_input_remembers_the_saved_target(style):
    """data-old is what turns a change into a postponement in the browser."""
    html = _render(style)
    assert 'data-old="2026-09-15"' in html


def test_only_the_board_layouts_gained_the_kpi_filter():
    """Focus is personal and the wall is a screen nobody clicks — neither grew one.

    Asserted on the template sources rather than a render: the point is which
    layouts read the KPI row at all.
    """
    import pathlib
    for key in wrs.STYLE_KEYS:
        text = (pathlib.Path("app/templates") / wrs.template_for(key)).read_text()
        assert ("kpi_cards" in text) == (key in BOARD_STYLES), key


def test_the_postponement_dialog_asks_for_a_reason_and_for_who_asked():
    html = _render("cyber")
    assert 'id="dueDlg"' in html
    assert "סיבת הדחייה" in html
    assert "מי דחה / ביקש את הדחייה" in html
    assert "אחר / גורם חיצוני" in html      # the requester is often not a user
