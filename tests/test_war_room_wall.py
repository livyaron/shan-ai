"""חדר מבצעים — the wall display's data shaping (style=wall).

DB-free: every rule here is a pure function over hand-built mission objects, so
it runs in CI without Postgres — same contract as test_war_room_styles.py.
"""
import datetime

from app.models import Mission, MissionUpdate, User
from app.services import missions_menu_service as oms
from app.services import war_room_wall as wall
from app.services.missions_report_service import AT_RISK_DAYS, SILENT_DAYS

TODAY = datetime.date(2026, 8, 22)


def _update(text, days_ago=1, author="אבי", kind=None):
    stamp = datetime.datetime.combine(TODAY - datetime.timedelta(days=days_ago),
                                      datetime.time(9, 0))
    return MissionUpdate(text=text, author_name=author, kind=kind, created_at=stamp)


def _mission(mid, title="משימה", owner="אבי", due=None, quadrant="do",
             status="open", updates=None):
    """Real (transient) ORM objects, never fakes: the shaping code reads the log
    through SQLAlchemy's loaded/unloaded inspection, which a stand-in would not
    exercise at all."""
    urgent, important = oms.quadrant_flags(quadrant)
    m = Mission(id=mid, title=title, description=None, owner_id=1, due_date=due,
                status=status, is_urgent=urgent, is_important=important)
    m.owner = User(username=owner)
    if updates is not None:
        m.updates = list(updates)
    return m


def _days(n):
    return TODAY + datetime.timedelta(days=n)


# --------------------------------------------------------------------------
# tone — colour is time-to-target, never the quadrant
# --------------------------------------------------------------------------

def test_tone_bands_cover_the_whole_timeline():
    assert wall.tone_for(_mission(1, due=_days(-30)), TODAY) == "late-deep"
    assert wall.tone_for(_mission(2, due=_days(-1)), TODAY) == "late"
    assert wall.tone_for(_mission(3, due=TODAY), TODAY) == "today"
    assert wall.tone_for(_mission(4, due=_days(AT_RISK_DAYS)), TODAY) == "soon"
    assert wall.tone_for(_mission(5, due=_days(7)), TODAY) == "week"
    assert wall.tone_for(_mission(6, due=_days(40)), TODAY) == "later"
    assert wall.tone_for(_mission(7, due=None), TODAY) == "nodate"


def test_deep_late_boundary_is_exact():
    """Exactly DEEP_LATE_DAYS late is still 'late'; one more day is a management case."""
    assert wall.tone_for(_mission(1, due=_days(-wall.DEEP_LATE_DAYS)), TODAY) == "late"
    assert wall.tone_for(_mission(2, due=_days(-wall.DEEP_LATE_DAYS - 1)), TODAY) == "late-deep"


def test_every_tone_has_a_legend_entry():
    assert {k for k, _ in wall.LEGEND} == set(wall.TONES)
    assert [k for k, _ in wall.LEGEND][0] == "late-deep"


# --------------------------------------------------------------------------
# ordering and pagination — the room must eventually see everything
# --------------------------------------------------------------------------

def test_worst_mission_lands_on_the_first_page():
    missions = [
        _mission(1, "רגילה", due=_days(30)),
        _mission(2, "היום", due=TODAY),
        _mission(3, "איחור עמוק", due=_days(-20)),
        _mission(4, "ללא יעד", due=None),
    ]
    pages = wall.build_pages(missions, [], TODAY)
    assert [c["title"] for c in pages[0]["cards"]][0] == "איחור עמוק"


def test_undated_missions_are_shown_instead_of_dropped():
    """The old wall filtered them out entirely — an undated fire was invisible."""
    pages = wall.build_pages([_mission(1, "מיפוי מלאי", due=None)], [], TODAY)
    titles = [c["title"] for p in pages for c in p["cards"]]
    assert titles == ["מיפוי מלאי"]


def test_pagination_covers_every_mission_exactly_once():
    missions = [_mission(i, f"משימה {i}", due=_days(i)) for i in range(1, 20)]
    pages = wall.build_pages(missions, [], TODAY)
    titles = [c["title"] for p in pages for c in p["cards"]]
    assert len(pages) == 4                       # 19 missions / 6 per page
    assert sorted(titles) == sorted(m.title for m in missions)
    assert all(len(p["cards"]) <= wall.WALL_PAGE_SIZE for p in pages)


def test_closed_missions_get_their_own_final_page():
    closed = _mission(9, "נסגרה", status="done")
    closed.completed_at = datetime.datetime(2026, 8, 21, 9, 0)
    pages = wall.build_pages([_mission(1, due=TODAY)], [closed], TODAY)
    assert pages[-1]["kind"] == "closed"
    assert pages[-1]["cards"][0]["title"] == "נסגרה"


def test_an_empty_board_still_renders_one_page():
    assert wall.build_pages([], [], TODAY) == [{"kind": "missions", "cards": []}]


def test_refresh_waits_for_a_full_cycle():
    """A fixed 60s refresh would cut a long rotation in half and hide the tail."""
    assert wall.refresh_seconds(1) == 60
    assert wall.refresh_seconds(10) == 10 * wall.ROTATE_SECONDS + 3


# --------------------------------------------------------------------------
# status updates on the card
# --------------------------------------------------------------------------

def test_card_carries_the_newest_status_update():
    m = _mission(1, due=TODAY, updates=[
        _update("הוזמן ציוד", days_ago=9),
        _update("הותקן המבנה", days_ago=2, author="דנה"),
    ])
    card = wall.card_for(m, TODAY)
    assert card["update_text"] == "הותקן המבנה"
    assert card["update_who"] == "דנה"
    assert card["update_when"] == "לפני 2 ימים"
    assert card["is_silent"] is False


def test_a_mission_nobody_reported_on_is_marked_silent():
    card = wall.card_for(_mission(1, due=_days(20)), TODAY)
    assert card["update_text"] == ""
    assert card["silent_days"] is None
    assert card["is_silent"] is True


def test_an_old_report_counts_as_silent():
    m = _mission(1, due=_days(20), updates=[_update("בטיפול", days_ago=SILENT_DAYS)])
    assert wall.card_for(m, TODAY)["is_silent"] is True
    m2 = _mission(2, due=_days(20), updates=[_update("בטיפול", days_ago=SILENT_DAYS - 1)])
    assert wall.card_for(m2, TODAY)["is_silent"] is False


def test_card_big_number_says_how_late_in_days():
    card = wall.card_for(_mission(1, due=_days(-4)), TODAY)
    assert card["big"] == "4"
    assert card["unit"] == "ימים באיחור"


def test_updates_are_never_lazy_loaded():
    """A mission whose log was not eager-loaded must degrade, not raise.

    Under asyncio a lazy load raises MissingGreenlet mid-render, which is a 500
    on the wall — the mission below never touches `updates`, so the guard in
    get_mission_updates is what is under test here.
    """
    m = _mission(1, due=TODAY)          # updates left untouched = unloaded
    card = wall.card_for(m, TODAY)
    assert card["update_text"] == ""
    assert card["is_silent"] is True


# --------------------------------------------------------------------------
# text capping — the wall cuts its own strings, the browser never does
# --------------------------------------------------------------------------

def test_shorten_leaves_a_short_line_alone():
    assert wall.shorten("ממתין לאישור בטיחות", 40) == "ממתין לאישור בטיחות"
    assert wall.shorten(None, 40) == ""
    assert wall.shorten("  שתי   שורות\nלשורה אחת ", 40) == "שתי שורות לשורה אחת"


def test_shorten_cuts_on_a_word_boundary():
    """A cut mid-word is what put a meaningless three-letter fragment on the wall."""
    text = "הוזמן קבלן חיצוני להחלפת המבודדים בקו המתח הגבוה והעבודה מתחילה ביום ראשון"
    out = wall.shorten(text, 40)
    assert len(out) <= 41            # the ellipsis is the one extra character
    assert out.endswith("…")
    assert out[:-1].strip() in text  # nothing invented, nothing half-written
    assert not out[:-1].endswith(" ")


def test_shorten_never_leaves_a_stub_of_a_word():
    """A limit that lands two letters into a long word must not keep those two."""
    out = wall.shorten("קבלן " + "א" * 60, 20)
    assert out.startswith("קבלן")
    assert "…" in out


def test_card_text_is_capped_before_it_reaches_the_template():
    long_title = "החלפת מפסק ראשי " * 8
    long_update = "ממתין לאישור בטיחות מהמחוז " * 8
    m = _mission(1, title=long_title, due=TODAY, updates=[_update(long_update, days_ago=1)])
    card = wall.card_for(m, TODAY)
    assert len(card["title"]) <= wall.TITLE_CHARS + 1
    assert len(card["update_text"]) <= wall.UPDATE_CHARS + 1
