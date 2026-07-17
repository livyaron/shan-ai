import datetime
from datetime import date, timedelta

from sqlalchemy.orm import configure_mappers, class_mapper
from app.models import Mission, MissionStatusEnum, User

configure_mappers()
_mission_mgr = class_mapper(Mission).class_manager
_user_mgr = class_mapper(User).class_manager

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
    defaults.update(kwargs)
    m = _mission_mgr.new_instance()
    for k, v in defaults.items():
        setattr(m, k, v)
    m.owner = owner
    m.created_by = created_by
    return m


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
    assert any(cd.startswith("om:a:start:") for cd in open_kb)
    assert any(cd.startswith("om:a:reopen:") for cd in done_kb)
    assert not any(cd.startswith("om:a:cancel:") for cd in done_kb)
    for cd in open_kb + done_kb:
        assert len(cd.encode()) <= 64


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
