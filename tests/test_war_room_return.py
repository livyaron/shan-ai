"""חדר מבצעים — owner search in the create dialog + back to the same spot after a save (user 2026-10-21)."""
import re
from pathlib import Path

import pytest

from app.services import war_room_styles as wrs
from tests.test_war_room_styles import _render

BOARD_STYLES = [s for s in wrs.STYLE_KEYS if s != "wall"]   # the wall is a screen nobody edits on
ACTIONS = Path("app/templates/_war_room_actions.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_create_dialog_has_free_text_owner_search_next_to_the_list(style):
    html = _render(style)
    assert html.count('class="wr-owner-q"') == 1
    assert 'oninput="wrOwnerFilter(this)"' in html
    # the list is still what is posted — the search only narrows it
    form = html[html.index('class="wr-owner-q"'):]
    assert re.search(r'<select name="owner_id">\s*<option value="1"', form)


@pytest.mark.parametrize("style", BOARD_STYLES)
def test_every_mission_card_can_be_found_after_a_reload(style):
    html = _render(style)
    assert re.search(r'data-mission="\d+"', html), style


def test_every_save_returns_to_the_same_spot():
    # every write that reloads goes through wrReload (the one reload left is inside it)
    assert ACTIONS.count("location.reload()") == 1
    body = ACTIONS[ACTIONS.index("function wrReload"):]
    assert body.index("location.reload()") < body.index("(function wrRestore")
    for fn in ("act", "addNote", "move", "assign", "setDue", "saveDue", "closeMission"):
        start = ACTIONS.index(f"function {fn}(")
        end = ACTIONS.index("\n}", start)
        assert "wrReload(" in ACTIONS[start:end], fn
    table = Path("app/templates/war_room_table.html").read_text(encoding="utf-8")
    assert "location.reload()" not in table


def test_position_is_kept_only_for_the_same_page_and_one_reload():
    restore = ACTIONS[ACTIONS.index("(function wrRestore"):]
    assert "sessionStorage.removeItem(WR_POS_KEY)" in restore          # used once
    assert "s.path !== location.pathname + location.search" in restore  # same page only
    assert "Date.now() - s.at > 60000" in restore                        # never a stale jump
