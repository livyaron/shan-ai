import datetime
from datetime import date, timedelta

from sqlalchemy.orm import configure_mappers, class_mapper
from app.models import Mission, MissionStatusEnum, MissionUpdate, User

configure_mappers()
_mission_mgr = class_mapper(Mission).class_manager
_user_mgr = class_mapper(User).class_manager
_upd_mgr = class_mapper(MissionUpdate).class_manager

TODAY = date(2026, 7, 15)  # Wednesday


def _make_user(**kwargs):
    defaults = dict(id=1, username="דני לוי", telegram_id=111)
    defaults.update(kwargs)
    u = _user_mgr.new_instance()
    for k, v in defaults.items():
        setattr(u, k, v)
    return u


def _make_mission(**kwargs):
    defaults = dict(
        id=1,
        title="בדיקת שנאי בתחנת שדרות",
        description=None,
        is_urgent=True,
        is_important=True,
        status="open",
        owner_id=1,
        created_by_id=1,
        due_date=None,
        created_at=datetime.datetime(2026, 7, 1, 8, 0),
        completed_at=None,
    )
    owner = kwargs.pop("owner", _make_user())
    created_by = kwargs.pop("created_by", _make_user())
    updates = kwargs.pop("updates", [])
    defaults.update(kwargs)
    m = _mission_mgr.new_instance()
    for k, v in defaults.items():
        setattr(m, k, v)
    m.owner = owner
    m.created_by = created_by
    m.updates = updates
    return m


def _make_update(**kwargs):
    """Transient MissionUpdate. created_at is naive UTC, as stored."""
    defaults = dict(
        id=1,
        mission_id=1,
        text="הוחלף מבודד בפיידר 3",
        kind=None,
        author_id=1,
        author_name="דני לוי",
        created_at=datetime.datetime(2026, 7, 14, 15, 6),
    )
    author = kwargs.pop("author", None)
    defaults.update(kwargs)
    u = _upd_mgr.new_instance()
    for k, v in defaults.items():
        setattr(u, k, v)
    u.author = author
    return u


# ── Quadrant derivation ─────────────────────────────────────────────────────

def test_quadrant_key_all_four_combos():
    from app.services.missions_menu_service import quadrant_key
    assert quadrant_key(_make_mission(is_urgent=True, is_important=True)) == "do"
    assert quadrant_key(_make_mission(is_urgent=False, is_important=True)) == "plan"
    assert quadrant_key(_make_mission(is_urgent=True, is_important=False)) == "delegate"
    assert quadrant_key(_make_mission(is_urgent=False, is_important=False)) == "backlog"


def test_quadrant_flags_roundtrip():
    from app.services.missions_menu_service import quadrant_flags, quadrant_key
    for key in ("do", "plan", "delegate", "backlog"):
        urg, imp = quadrant_flags(key)
        assert quadrant_key(_make_mission(is_urgent=urg, is_important=imp)) == key


# ── Overdue rule ─────────────────────────────────────────────────────────────

def test_is_overdue_due_yesterday():
    from app.services.missions_menu_service import is_overdue
    m = _make_mission(due_date=TODAY - timedelta(days=1))
    assert is_overdue(m, TODAY) is True


def test_is_overdue_due_today_is_not_overdue():
    from app.services.missions_menu_service import is_overdue
    m = _make_mission(due_date=TODAY)
    assert is_overdue(m, TODAY) is False


def test_is_overdue_done_mission_never_overdue():
    from app.services.missions_menu_service import is_overdue
    m = _make_mission(due_date=TODAY - timedelta(days=5), status="done")
    assert is_overdue(m, TODAY) is False


def test_is_overdue_no_due_date():
    from app.services.missions_menu_service import is_overdue
    assert is_overdue(_make_mission(due_date=None), TODAY) is False


# ── Due-date parsing ─────────────────────────────────────────────────────────

def test_resolve_due_quick_picks():
    from app.services.missions_menu_service import resolve_due_quick_pick
    assert resolve_due_quick_pick("today", TODAY) == (True, TODAY)
    assert resolve_due_quick_pick("tomorrow", TODAY) == (True, TODAY + timedelta(days=1))
    assert resolve_due_quick_pick("week", TODAY) == (True, TODAY + timedelta(days=7))
    assert resolve_due_quick_pick("none", TODAY) == (True, None)
    assert resolve_due_quick_pick("custom", TODAY) == (False, None)


def test_parse_due_date_full():
    from app.services.missions_menu_service import parse_due_date_text
    assert parse_due_date_text("20/08/2026", TODAY) == date(2026, 8, 20)
    assert parse_due_date_text("20.08.2026", TODAY) == date(2026, 8, 20)
    assert parse_due_date_text("20/08/26", TODAY) == date(2026, 8, 20)


def test_parse_due_date_short_rolls_to_next_year():
    from app.services.missions_menu_service import parse_due_date_text
    # 01/03 already passed in 2026 → next year
    assert parse_due_date_text("01/03", TODAY) == date(2027, 3, 1)
    # 20/08 still ahead → this year
    assert parse_due_date_text("20/08", TODAY) == date(2026, 8, 20)


def test_parse_due_date_invalid():
    from app.services.missions_menu_service import parse_due_date_text
    assert parse_due_date_text("לא תאריך", TODAY) is None
    assert parse_due_date_text("45/13", TODAY) is None
    assert parse_due_date_text("", TODAY) is None


# ── Formatters ───────────────────────────────────────────────────────────────

def test_format_mission_line_overdue_marker():
    from app.services.missions_menu_service import format_mission_line
    m = _make_mission(due_date=TODAY - timedelta(days=2))
    line = format_mission_line(m, TODAY)
    assert "⚠️" in line
    assert "13/07/2026" in line  # DD/MM/YYYY, never ISO


def test_format_mission_line_truncates_title():
    from app.services.missions_menu_service import format_mission_line
    m = _make_mission(title="א" * 60)
    assert "…" in format_mission_line(m, TODAY)


def test_build_mission_card_shows_creator_and_axis():
    from app.services.missions_menu_service import build_mission_card
    m = _make_mission(created_by=_make_user(id=2, username="רות כהן"))
    card = build_mission_card(m)
    assert "רות כהן" in card
    assert "דחוף · חשוב" in card
    assert "01/07/2026" in card


def test_build_mission_card_shows_latest_status_update():
    """The card leads with where the mission stands now — one line, not the whole log."""
    from app.services.missions_menu_service import build_mission_card
    m = _make_mission(updates=[_make_update()])
    card = build_mission_card(m)
    assert "עדכון אחרון" in card
    assert "הוחלף מבודד בפיידר 3" in card
    assert "דני לוי" in card
    assert "14/07 18:06" in card  # 15:06 UTC → Israel local


def test_build_mission_card_shows_only_the_newest_update_by_default():
    from app.services.missions_menu_service import build_mission_card
    m = _make_mission(updates=[
        _make_update(id=1, text="ישן"),
        _make_update(id=2, text="חדש", created_at=datetime.datetime(2026, 7, 14, 16, 0)),
    ])
    card = build_mission_card(m)
    assert "חדש" in card
    assert "ישן" not in card
    assert "מתוך 2 עדכונים" in card


def test_build_mission_card_show_all_updates_lists_everything():
    from app.services.missions_menu_service import build_mission_card
    m = _make_mission(updates=[
        _make_update(id=1, text="ישן"),
        _make_update(id=2, text="חדש", created_at=datetime.datetime(2026, 7, 14, 16, 0)),
    ])
    card = build_mission_card(m, show_all_updates=True)
    assert "עדכוני סטטוס" in card
    assert "ישן" in card and "חדש" in card


def test_all_updates_view_caps_at_history_limit():
    from app.services.missions_menu_service import format_updates_block, HISTORY_LIMIT
    total = HISTORY_LIMIT + 5
    m = _make_mission(updates=[
        _make_update(id=i, text=f"עדכון מספר {i:03d}") for i in range(1, total + 1)
    ])
    block = format_updates_block(m, limit=HISTORY_LIMIT)
    assert "עדכון מספר 001" not in block          # trimmed off the front
    assert f"עדכון מספר {total:03d}" in block     # newest survives
    assert block.count("• ") == HISTORY_LIMIT
    assert f"מוצגים {HISTORY_LIMIT}" in block and f"מתוך {total}" in block


def test_closing_note_is_marked_with_a_lock():
    from app.services.missions_menu_service import format_updates_block
    m = _make_mission(updates=[_make_update(text="הוחלף והופעל", kind="close")])
    assert "🔒" in format_updates_block(m)


def test_card_keyboard_offers_full_log_only_when_there_is_more_than_one():
    from app.services.missions_menu_service import build_mission_card_keyboard
    one = _all_callback_data(build_mission_card_keyboard(
        _make_mission(updates=[_make_update()]), "my", 0))
    assert not any(cd.startswith("om:u:") for cd in one)

    many = _all_callback_data(build_mission_card_keyboard(
        _make_mission(updates=[_make_update(id=1), _make_update(id=2)]), "my", 0))
    assert "om:u:1:my:0" in many

    expanded = _all_callback_data(build_mission_card_keyboard(
        _make_mission(updates=[_make_update(id=1), _make_update(id=2)]), "my", 0,
        show_all_updates=True))
    assert not any(cd.startswith("om:u:") for cd in expanded)
    assert "om:d:1:my:0" in expanded


def test_build_mission_card_without_updates_has_no_block():
    from app.services.missions_menu_service import build_mission_card
    card = build_mission_card(_make_mission())
    assert "עדכוני סטטוס" not in card
    assert "עדכון אחרון" not in card


def test_format_updates_block_escapes_user_text():
    from app.services.missions_menu_service import format_updates_block
    m = _make_mission(updates=[_make_update(text="<b>דחוף</b>", author_name="<i>דני</i>")])
    block = format_updates_block(m)
    assert "&lt;b&gt;דחוף&lt;/b&gt;" in block
    assert "&lt;i&gt;דני&lt;/i&gt;" in block


def test_format_updates_block_plain_text_mode():
    from app.services.missions_menu_service import format_updates_block
    m = _make_mission(updates=[_make_update()])
    block = format_updates_block(m, html=False)
    assert "<b>" not in block
    assert "הוחלף מבודד בפיידר 3" in block


def test_format_updates_block_falls_back_to_live_author():
    """author_name is a snapshot; a row saved without one still names the author."""
    from app.services.missions_menu_service import format_updates_block
    m = _make_mission(updates=[_make_update(author_name=None, author=_make_user(username="רות כהן"))])
    assert "רות כהן" in format_updates_block(m)


def test_format_stamp_il_converts_from_utc():
    from app.services.missions_menu_service import format_stamp_il
    assert format_stamp_il(datetime.datetime(2026, 7, 14, 15, 6)) == "14/07 18:06"
    assert format_stamp_il(None) == "—"


def test_format_results_message_empty():
    from app.services.missions_menu_service import format_results_message
    msg = format_results_message("🔥 בצע עכשיו", [], 0, 0)
    assert "אין משימות" in msg


def test_format_digest_groups_and_overdue_first():
    from app.services.missions_menu_service import format_digest
    late = _make_mission(id=1, title="משימה באיחור", due_date=TODAY - timedelta(days=1))
    plan = _make_mission(id=2, title="משימת תכנון", is_urgent=False, is_important=True)
    text = format_digest([plan, late])
    assert text.index("באיחור") < text.index("תכנן")
    assert "משימה באיחור" in text
    assert "משימת תכנון" in text


def test_format_digest_manager_totals_line():
    from app.services.missions_menu_service import format_digest
    m = _make_mission()
    text = format_digest([m], board_totals=(12, 3))
    assert "12" in text and "3" in text and "סה\"כ" in text


# ── Keyboards ────────────────────────────────────────────────────────────────

def _all_callback_data(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_menu_keyboard_callbacks_within_limit():
    from app.services.missions_menu_service import get_menu_keyboard
    kb = get_menu_keyboard({"do": 3, "plan": 1, "delegate": 0, "backlog": 9})
    for cd in _all_callback_data(kb):
        assert len(cd.encode()) <= 64
    assert "om:qdo:0" in _all_callback_data(kb)


def test_card_keyboard_open_vs_done():
    from app.services.missions_menu_service import build_mission_card_keyboard
    open_kb = _all_callback_data(build_mission_card_keyboard(_make_mission(status="open"), "my", 0))
    done_kb = _all_callback_data(build_mission_card_keyboard(_make_mission(status="done"), "my", 0))
    assert any(cd.startswith("om:a:note:") for cd in open_kb)
    assert any(cd.startswith("om:a:reopen:") for cd in done_kb)
    assert not any(cd.startswith("om:a:cancel:") for cd in done_kb)
    for cd in open_kb + done_kb:
        assert len(cd.encode()) <= 64


def test_card_keyboard_has_no_start_action():
    """'התחל ביצוע' is gone — the board is open/closed only."""
    from app.services.missions_menu_service import build_mission_card_keyboard
    for status in ("open", "done", "cancelled"):
        cds = _all_callback_data(build_mission_card_keyboard(_make_mission(status=status), "my", 0))
        assert not any(cd.startswith("om:a:start:") for cd in cds)


def test_active_statuses_is_open_only():
    from app.services.missions_menu_service import ACTIVE_STATUSES, STATUS_LABELS
    assert ACTIVE_STATUSES == ["open"]
    assert "in_progress" not in STATUS_LABELS


def test_card_keyboard_legacy_in_progress_does_not_crash():
    """A row left on the removed status renders as closed, not as an exception.

    Startup normalizes these to 'open' (app/main.py), so this only guards the
    window before that migration runs.
    """
    from app.services.missions_menu_service import build_mission_card_keyboard
    cds = _all_callback_data(build_mission_card_keyboard(_make_mission(status="in_progress"), "my", 0))
    assert any(cd.startswith("om:a:reopen:") for cd in cds)


def test_digest_keyboard_one_done_button_per_mission():
    from app.services.missions_menu_service import build_digest_keyboard
    missions = [_make_mission(id=i, title=f"משימה {i}") for i in (1, 2, 3)]
    kb = build_digest_keyboard(missions)
    cds = _all_callback_data(kb)
    assert cds == ["om:dg:done:1", "om:dg:done:2", "om:dg:done:3"]


def test_digest_keyboard_empty_returns_none():
    from app.services.missions_menu_service import build_digest_keyboard
    assert build_digest_keyboard([]) is None


def test_results_keyboard_done_shortcut_only_for_my():
    from app.services.missions_menu_service import build_results_keyboard
    missions = [_make_mission(id=7)]
    with_shortcut = _all_callback_data(build_results_keyboard("my", 0, 1, missions, with_done_shortcut=True))
    without = _all_callback_data(build_results_keyboard("late", 0, 1, missions, with_done_shortcut=False))
    assert any(cd.startswith("om:ld:7:") for cd in with_shortcut)
    assert not any(cd.startswith("om:ld:") for cd in without)


def test_status_enum_values():
    assert MissionStatusEnum.OPEN.value == "open"
    assert MissionStatusEnum.DONE.value == "done"


# ── List buttons carry the mission, not a "פרטים" placeholder ──────────────

def test_results_keyboard_buttons_are_labelled_with_the_mission_title():
    from app.services.missions_menu_service import build_results_keyboard
    missions = [
        _make_mission(id=11, title="החלפת מבודד בשדרות"),
        _make_mission(id=12, title="בדיקת ממסר באשקלון"),
    ]
    kb = build_results_keyboard("qdo", 0, 2, missions)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("החלפת מבודד בשדרות" in t for t in labels)
    assert any("בדיקת ממסר באשקלון" in t for t in labels)
    assert not any("פרטים" in t for t in labels)
    # Pressing the mission still opens its card, and the way back is still there.
    cds = _all_callback_data(kb)
    assert "om:d:11:qdo:0" in cds and "om:d:12:qdo:0" in cds
    assert "om:menu" in cds


def test_results_keyboard_button_title_is_stripped_and_truncated():
    from app.services.missions_menu_service import button_title
    label = button_title(_make_mission(title="<b>דחוף</b> " + "א" * 80), 3)
    assert "<b>" not in label
    assert label.startswith("3. ")
    assert "…" in label


def test_results_keyboard_marks_overdue_missions():
    from app.services.missions_menu_service import button_title
    m = _make_mission(due_date=date.today() - timedelta(days=2))
    assert "⚠️" in button_title(m, 1)


# ── Closing a mission ──────────────────────────────────────────────────────

def test_close_prompt_keyboard_callbacks_are_short_and_correct():
    from app.services.missions_menu_service import build_close_prompt_keyboard
    kb = build_close_prompt_keyboard("done", 12345, "qbacklog", 9, back_cd="om:d:12345:qbacklog:9")
    cds = _all_callback_data(kb)
    assert "om:cl:d:12345:qbacklog:9" in cds
    assert "om:d:12345:qbacklog:9" in cds
    for cd in cds:
        assert len(cd.encode()) <= 64

    cancel_cds = _all_callback_data(
        build_close_prompt_keyboard("cancelled", 1, "my", 0, back_cd="om:d:1:my:0")
    )
    assert "om:cl:x:1:my:0" in cancel_cds


def test_close_tokens_round_trip():
    from app.services.missions_menu_service import CLOSE_TOKENS, CLOSE_TOKEN_BY_STATUS
    assert CLOSE_TOKENS["d"] == "done" and CLOSE_TOKENS["x"] == "cancelled"
    assert CLOSE_TOKEN_BY_STATUS["done"] == "d"


def test_close_prompt_text_differs_for_done_and_cancel():
    from app.services.missions_menu_service import close_prompt_text
    assert "מה בוצע" in close_prompt_text("done")
    assert "סיבת הביטול" in close_prompt_text("cancelled")
