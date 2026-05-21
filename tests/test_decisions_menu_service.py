import pytest
from datetime import datetime
from sqlalchemy.orm import configure_mappers, class_mapper
from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum

# Ensure SQLAlchemy mapper instrumentation is initialised so that bare
# instances created via new_instance() work without a DB session.
configure_mappers()
_decision_mgr = class_mapper(Decision).class_manager
from app.services.decisions_menu_service import (
    format_result_line,
    format_results_message,
    build_custom_filter_keyboard,
    get_menu_keyboard,
    SHORTCUT_PRESETS,
)


def _make_decision(**kwargs):
    defaults = dict(
        id=1,
        type=DecisionTypeEnum.NORMAL,
        status=DecisionStatusEnum.PENDING,
        summary="בדיקה",
        created_at=datetime(2026, 5, 20),
    )
    defaults.update(kwargs)
    d = _decision_mgr.new_instance()
    for k, v in defaults.items():
        setattr(d, k, v)
    return d


def test_format_result_line_critical_pending():
    d = _make_decision(id=42, type=DecisionTypeEnum.CRITICAL, status=DecisionStatusEnum.PENDING)
    line = format_result_line(d)
    assert "🚨" in line
    assert "#42" in line
    assert "⏳" in line
    assert "20/05" in line


def test_format_result_line_truncates_long_summary():
    d = _make_decision(summary="א" * 50)
    line = format_result_line(d)
    assert "…" in line


def test_format_result_line_does_not_truncate_short_summary():
    d = _make_decision(summary="קצר")
    line = format_result_line(d)
    assert "…" not in line


def test_format_results_message_empty():
    msg = format_results_message("📋 כל ההחלטות", [], 0, 0)
    assert "לא נמצאו" in msg


def test_format_results_message_header_counts():
    decisions = [_make_decision(id=i) for i in range(3)]
    msg = format_results_message("📋 תוצאות", decisions, 3, 0)
    assert "3" in msg
    assert "1–3" in msg


def test_build_custom_filter_keyboard_marks_active_owner():
    state = {"owner": "my", "type": None, "status": None, "date_days": 30, "page": 0}
    kb = build_custom_filter_keyboard(state)
    flat = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("שלי" in b and "✓" in b for b in flat)
    assert not any("שקיבלתי" in b and "✓" in b for b in flat)


def test_build_custom_filter_keyboard_marks_active_status():
    state = {"owner": "all", "type": None, "status": "pending", "date_days": 7, "page": 0}
    kb = build_custom_filter_keyboard(state)
    flat = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("ממתין" in b and "✓" in b for b in flat)
    assert any("7" in b and "✓" in b for b in flat)


def test_get_menu_keyboard_has_six_buttons():
    kb = get_menu_keyboard()
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(all_buttons) == 6


def test_shortcut_presets_all_keys_present():
    for key in ("recent", "critical", "pending", "recv", "my"):
        assert key in SHORTCUT_PRESETS
        p = SHORTCUT_PRESETS[key]
        assert "owner" in p and "type" in p and "status" in p
        assert "date_days" in p and "title" in p
