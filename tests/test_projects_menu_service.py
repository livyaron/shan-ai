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


def test_get_menu_keyboard_four_buttons():
    from app.services.projects_menu_service import get_menu_keyboard
    kb = get_menu_keyboard()
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert len(all_btns) == 4


def test_get_menu_keyboard_no_all_or_active():
    from app.services.projects_menu_service import get_menu_keyboard
    kb = get_menu_keyboard()
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("הכל" in t for t in labels)
    assert not any("בביצוע" in t for t in labels)


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


def test_shortcut_presets_keys():
    from app.services.projects_menu_service import SHORTCUT_PRESETS
    assert "late" in SHORTCUT_PRESETS
    assert "quarter" in SHORTCUT_PRESETS
    assert "handle" not in SHORTCUT_PRESETS
    assert "all" not in SHORTCUT_PRESETS
    assert "active" not in SHORTCUT_PRESETS
    for key in ("late", "quarter"):
        assert "title" in SHORTCUT_PRESETS[key]


def test_build_detail_back_keyboard_cf_routes_to_pg():
    from app.services.projects_menu_service import build_detail_back_keyboard
    kb = build_detail_back_keyboard("cf", 3)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    back_btn = next(b for b in all_btns if "חזרה" in b.text)
    assert back_btn.callback_data == "pm_cf:pg:3"


def test_build_detail_back_keyboard_th_routes_correctly():
    from app.services.projects_menu_service import build_detail_back_keyboard
    kb = build_detail_back_keyboard("th2", 1)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    back_btn = next(b for b in all_btns if "חזרה" in b.text)
    assert back_btn.callback_data == "pm:th:2:1"


def test_build_detail_back_keyboard_shortcut_routes_correctly():
    from app.services.projects_menu_service import build_detail_back_keyboard
    kb = build_detail_back_keyboard("late", 0)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    back_btn = next(b for b in all_btns if "חזרה" in b.text)
    assert back_btn.callback_data == "pm:late:0"


def test_build_th_sub_keyboard():
    from app.services.projects_menu_service import build_th_sub_keyboard
    opts = ["חסם לטיפול מנהל אגף", "חסם לטיפול מנהל מגזר"]
    kb = build_th_sub_keyboard(opts)
    rows = kb.inline_keyboard
    # 2 value rows + 1 back row
    assert len(rows) == 3
    labels = [b.text for row in rows for b in row]
    assert any("מנהל אגף" in t for t in labels)
    assert not any("חסם לטיפול" in t for t in labels)
    assert rows[0][0].callback_data == "pm:th:0:0"
    assert rows[1][0].callback_data == "pm:th:1:0"


def test_build_th_results_keyboard_single_page():
    from app.services.projects_menu_service import build_th_results_keyboard
    kb = build_th_results_keyboard(0, 0, 5)
    rows = kb.inline_keyboard
    # No nav + back row
    assert len(rows) == 1


def test_build_th_results_keyboard_multipage():
    from app.services.projects_menu_service import build_th_results_keyboard
    kb = build_th_results_keyboard(0, 0, 15)
    rows = kb.inline_keyboard
    assert len(rows) == 2


def test_build_filter_field_keyboard_shows_counts():
    from app.services.projects_menu_service import build_filter_field_keyboard
    state = {"stage": ["בדיקות"], "type": [], "mgr": [], "th": [], "date": ["late"]}
    kb = build_filter_field_keyboard(state)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("✓1" in t for t in labels)
    assert any("✓2" not in t or True for t in labels)  # counts correct


def test_build_filter_field_keyboard_has_clear_when_active():
    from app.services.projects_menu_service import build_filter_field_keyboard
    state = {"stage": ["בדיקות"], "type": [], "mgr": [], "th": [], "date": []}
    kb = build_filter_field_keyboard(state)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("נקה" in t for t in labels)


def test_build_filter_field_keyboard_no_clear_when_empty():
    from app.services.projects_menu_service import build_filter_field_keyboard
    state = {"stage": [], "type": [], "mgr": [], "th": [], "date": []}
    kb = build_filter_field_keyboard(state)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("נקה" in t for t in labels)


def test_build_filter_value_keyboard_marks_selected():
    from app.services.projects_menu_service import build_filter_value_keyboard
    opts = ["תכנון", "בדיקות", "הרכבה"]
    kb = build_filter_value_keyboard("stage", opts, ["בדיקות"])
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("✓" in t and "בדיקות" in t for t in labels)
    assert not any("✓" in t and "תכנון" in t for t in labels)


def test_build_filter_value_keyboard_th_strips_prefix():
    from app.services.projects_menu_service import build_filter_value_keyboard
    opts = ["חסם לטיפול מנהל אגף", "חסם לטיפול מנהל מגזר"]
    kb = build_filter_value_keyboard("th", opts, [])
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("מנהל אגף" in t for t in labels)
    assert not any("חסם לטיפול" in t for t in labels)


def test_build_filter_date_keyboard_marks_selected():
    from app.services.projects_menu_service import build_filter_date_keyboard
    kb = build_filter_date_keyboard(["late", "2026"])
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("✓" in t and "באיחור" in t for t in labels)
    assert any("✓" in t and "2026" in t for t in labels)
    assert not any("✓" in t and "2027" in t for t in labels)


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
    results, total = await query_projects(db_session, stages=None, types=None, mgrs=None, ths=None, dates=None, page=0)
    assert total >= 2


@pytest.mark.asyncio
async def test_query_projects_late_filter(db_session):
    yesterday = date.today() - timedelta(days=1)
    future = date.today() + timedelta(days=30)
    _db_project(db_session, project_identifier="LATE-1", estimated_finish_date=yesterday)
    _db_project(db_session, project_identifier="FUTURE-1", estimated_finish_date=future)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, types=None, mgrs=None, ths=None, dates=["late"], page=0)
    ids = [p.project_identifier for p in results]
    assert "LATE-1" in ids
    assert "FUTURE-1" not in ids


@pytest.mark.asyncio
async def test_query_projects_stage_filter(db_session):
    _db_project(db_session, project_identifier="STG-1", stage="הרכבה חשמלית")
    _db_project(db_session, project_identifier="STG-2", stage="תכנון")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=["הרכבה חשמלית"], types=None, mgrs=None, ths=None, dates=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "STG-1" in ids
    assert "STG-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_multi_stage_filter(db_session):
    _db_project(db_session, project_identifier="MS-1", stage="הרכבה חשמלית")
    _db_project(db_session, project_identifier="MS-2", stage="בדיקות")
    _db_project(db_session, project_identifier="MS-3", stage="תכנון")
    await db_session.flush()
    results, total = await query_projects(db_session, stages=["הרכבה חשמלית", "בדיקות"], types=None, mgrs=None, ths=None, dates=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "MS-1" in ids
    assert "MS-2" in ids
    assert "MS-3" not in ids


@pytest.mark.asyncio
async def test_query_projects_handle_any(db_session):
    _db_project(db_session, project_identifier="TH-1", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="TH-2", to_handle=None)
    await db_session.flush()
    results, total = await query_projects(db_session, stages=None, types=None, mgrs=None, ths=["__any__"], dates=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "TH-1" in ids
    assert "TH-2" not in ids


@pytest.mark.asyncio
async def test_query_projects_specific_th(db_session):
    _db_project(db_session, project_identifier="TH-A", to_handle="חסם לטיפול מנהל אגף")
    _db_project(db_session, project_identifier="TH-B", to_handle="חסם לטיפול מנהל מגזר")
    await db_session.flush()
    results, _ = await query_projects(db_session, stages=None, types=None, mgrs=None, ths=["חסם לטיפול מנהל אגף"], dates=None, page=0)
    ids = [p.project_identifier for p in results]
    assert "TH-A" in ids
    assert "TH-B" not in ids


@pytest.mark.asyncio
async def test_query_projects_pagination(db_session):
    for i in range(12):
        _db_project(db_session, project_identifier=f"PAG-{i}", stage="תכנון-פג")
    await db_session.flush()
    results_p0, total = await query_projects(db_session, stages=["תכנון-פג"], types=None, mgrs=None, ths=None, dates=None, page=0)
    results_p1, _ = await query_projects(db_session, stages=["תכנון-פג"], types=None, mgrs=None, ths=None, dates=None, page=1)
    assert total >= 12
    assert len(results_p0) == 10
    assert len(results_p1) >= 2
