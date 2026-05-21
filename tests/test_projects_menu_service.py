import pytest
from datetime import date, timedelta
from sqlalchemy.orm import configure_mappers, class_mapper
from app.models import Project

configure_mappers()
_project_mgr = class_mapper(Project).class_manager


def _make_project(**kwargs):
    defaults = dict(
        id=1,
        project_identifier="TEST-001",
        name="פרוייקט בדיקה",
        project_type="הקמה",
        stage="הרכבה חשמלית",
        manager="כוכבה כהן",
        weekly_report_brief="הרכבת הלוח הושלמה.",
        to_handle=None,
        dev_plan_date=date(2025, 1, 1),
        estimated_finish_date=date(2025, 3, 1),
        is_active=True,
    )
    defaults.update(kwargs)
    p = _project_mgr.new_instance()
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


def test_format_project_line_truncates_name():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(name="א" * 50, estimated_finish_date=date(2025, 6, 1))
    line = format_project_line(p)
    assert "…" in line


def test_format_project_line_short_name_no_ellipsis():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(name="קצר", estimated_finish_date=date(2025, 6, 1))
    line = format_project_line(p)
    assert "…" not in line


def test_format_project_line_includes_stage_and_date():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(stage="בדיקות", estimated_finish_date=date(2026, 3, 15))
    line = format_project_line(p)
    assert "בדיקות" in line
    assert "03/26" in line


def test_format_project_line_no_date():
    from app.services.projects_menu_service import format_project_line
    p = _make_project(estimated_finish_date=None)
    line = format_project_line(p)
    assert "📁" in line


def test_format_results_message_empty():
    from app.services.projects_menu_service import format_results_message
    msg = format_results_message("📋 כל הפרוייקטים", [], 0, 0)
    assert "לא נמצאו" in msg


def test_format_results_message_header():
    from app.services.projects_menu_service import format_results_message
    projects = [_make_project(id=i + 1) for i in range(3)]
    msg = format_results_message("📋 תוצאות", projects, 3, 0)
    assert "3" in msg
    assert "1–3" in msg


def test_build_project_card_overdue():
    from app.services.projects_menu_service import build_project_card
    import datetime
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    p = _make_project(estimated_finish_date=yesterday)
    card = build_project_card(p)
    assert "🔴" in card


def test_build_project_card_not_overdue():
    from app.services.projects_menu_service import build_project_card
    import datetime
    future = datetime.date.today() + datetime.timedelta(days=30)
    p = _make_project(estimated_finish_date=future)
    card = build_project_card(p)
    assert "🔴 באיחור" not in card


def test_build_project_card_no_to_handle():
    from app.services.projects_menu_service import build_project_card
    p = _make_project(to_handle=None)
    card = build_project_card(p)
    assert "—" in card


def test_get_menu_keyboard_six_buttons():
    from app.services.projects_menu_service import get_menu_keyboard
    kb = get_menu_keyboard()
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert len(all_btns) == 6


def test_build_results_keyboard_no_nav_single_page():
    from app.services.projects_menu_service import build_results_keyboard
    kb = build_results_keyboard("late", 0, 5)
    rows = kb.inline_keyboard
    assert len(rows) == 1


def test_build_results_keyboard_has_nav_multipage():
    from app.services.projects_menu_service import build_results_keyboard
    kb = build_results_keyboard("late", 0, 15)
    rows = kb.inline_keyboard
    assert len(rows) == 2


def test_build_custom_filter_keyboard_marks_active_stage():
    from app.services.projects_menu_service import build_custom_filter_keyboard
    state = {"stage": "בדיקות", "type": None, "mgr": None, "th": None, "date": None}
    filter_options = {
        "stage": ["תכנון", "בדיקות"],
        "type": ["הקמה"],
        "mgr": ["כוכבה כהן"],
        "th": ["חסם לטיפול מנהל אגף"],
    }
    kb = build_custom_filter_keyboard(state, filter_options)
    flat = [b.text for row in kb.inline_keyboard for b in row]
    assert any("בדיקות" in t and "✓" in t for t in flat)
    assert not any("תכנון" in t and "✓" in t for t in flat)


def test_build_custom_filter_keyboard_th_strips_prefix():
    from app.services.projects_menu_service import build_custom_filter_keyboard
    state = {"stage": None, "type": None, "mgr": None, "th": None, "date": None}
    filter_options = {
        "stage": [],
        "type": [],
        "mgr": [],
        "th": ["חסם לטיפול מנהל אגף", "חסם לטיפול מנהל מגזר ביצוע"],
    }
    kb = build_custom_filter_keyboard(state, filter_options)
    flat = [b.text for row in kb.inline_keyboard for b in row]
    assert any("מנהל אגף" in t for t in flat)
    assert not any("חסם לטיפול מנהל אגף" in t for t in flat)


def test_shortcut_presets_keys():
    from app.services.projects_menu_service import SHORTCUT_PRESETS
    for key in ("late", "handle", "quarter", "all", "active"):
        assert key in SHORTCUT_PRESETS
        assert "title" in SHORTCUT_PRESETS[key]


from app.services.projects_menu_service import (
    get_filter_options, get_total_active, query_projects,
)


def _db_project(db_session, **kwargs):
    defaults = dict(
        project_identifier=f"TEST-{abs(hash(str(kwargs))) % 100000}",
        name="פרוייקט",
        project_type="הקמה",
        stage="תכנון",
        manager="מנהל",
        is_active=True,
        estimated_finish_date=None,
        to_handle=None,
    )
    defaults.update(kwargs)
    p = Project(**defaults)
    db_session.add(p)
    return p


@pytest.mark.asyncio
async def test_get_total_active(db_session):
    _db_project(db_session, project_identifier="ACT-1", is_active=True)
    _db_project(db_session, project_identifier="ACT-2", is_active=True)
    _db_project(db_session, project_identifier="INACT-1", is_active=False)
    await db_session.flush()
    total = await get_total_active(db_session)
    assert total >= 2


@pytest.mark.asyncio
async def test_get_filter_options_returns_distinct(db_session):
    _db_project(db_session, project_identifier="FO-1", stage="תכנון", project_type="הקמה", manager="א", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="FO-2", stage="תכנון", project_type="הרחבה", manager="ב", to_handle=None)
    _db_project(db_session, project_identifier="FO-3", stage="בדיקות", project_type="הקמה", manager="א", to_handle="חסם לטיפול מנהל אגף")
    await db_session.flush()
    opts = await get_filter_options(db_session)
    assert "תכנון" in opts["stage"]
    assert "בדיקות" in opts["stage"]
    assert len([s for s in opts["stage"] if s == "תכנון"]) == 1
    assert "הקמה" in opts["type"]
    assert "הרחבה" in opts["type"]
    assert "א" in opts["mgr"]
    assert "ב" in opts["mgr"]
    assert "חסם לטיפול מנהל אגף" in opts["th"]
    assert len([t for t in opts["th"] if t == "חסם לטיפול מנהל אגף"]) == 1


@pytest.mark.asyncio
async def test_query_projects_all(db_session):
    _db_project(db_session, project_identifier="QP-1")
    _db_project(db_session, project_identifier="QP-2")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th=None, date_filter=None, page=0)
    assert total >= 2


@pytest.mark.asyncio
async def test_query_projects_late_filter(db_session):
    yesterday = date.today() - timedelta(days=1)
    future = date.today() + timedelta(days=30)
    _db_project(db_session, project_identifier="LATE-1", estimated_finish_date=yesterday)
    _db_project(db_session, project_identifier="FUTURE-1", estimated_finish_date=future)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th=None, date_filter="late", page=0)
    ids = [p.project_identifier for p in results]
    assert "LATE-1" in ids
    assert "FUTURE-1" not in ids


@pytest.mark.asyncio
async def test_query_projects_stage_filter(db_session):
    _db_project(db_session, project_identifier="STG-1", stage="הרכבה חשמלית")
    _db_project(db_session, project_identifier="STG-2", stage="תכנון")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=["הרכבה חשמלית"], type_=None, mgr=None, th=None, date_filter=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "STG-1" in ids
    assert "STG-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_handle_any(db_session):
    _db_project(db_session, project_identifier="TH-1", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="TH-2", to_handle=None)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, type_=None, mgr=None, th="__any__", date_filter=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "TH-1" in ids
    assert "TH-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_pagination(db_session):
    for i in range(12):
        _db_project(db_session, project_identifier=f"PAG-{i}", stage="תכנון-פג")
    await db_session.flush()
    results_p0, total = await query_projects(db_session, stages=["תכנון-פג"], type_=None, mgr=None, th=None, date_filter=None, page=0)
    results_p1, _ = await query_projects(db_session, stages=["תכנון-פג"], type_=None, mgr=None, th=None, date_filter=None, page=1)
    assert total >= 12
    assert len(results_p0) == 10
    assert len(results_p1) >= 2
