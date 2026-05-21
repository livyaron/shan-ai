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
    decisions = [_make_decision(id=i + 1) for i in range(3)]
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


def test_format_result_line_escapes_html():
    d = _make_decision(summary='<b>attack</b> & "test"')
    line = format_result_line(d)
    assert "<b>attack</b>" not in line
    assert "&lt;" in line or "&amp;" in line


from app.models import (
    User, Decision, DecisionDistribution,
    DecisionTypeEnum, DecisionStatusEnum, RoleEnum, DistributionTypeEnum,
)
from app.services.decisions_menu_service import query_decisions


@pytest.mark.asyncio
async def test_query_decisions_my_only(db_session):
    u1 = User(telegram_id=9001, username="qd_u1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u2 = User(telegram_id=9002, username="qd_u2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([u1, u2])
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.APPROVED, summary="mine")
    d2 = Decision(submitter_id=u2.id, type=DecisionTypeEnum.CRITICAL,
                  status=DecisionStatusEnum.PENDING, summary="theirs")
    db_session.add_all([d1, d2])
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "my", None, None, 0, 0)
    assert total == 1
    assert results[0].summary == "mine"


@pytest.mark.asyncio
async def test_query_decisions_recv(db_session):
    u1 = User(telegram_id=9003, username="qd_u3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u2 = User(telegram_id=9004, username="qd_u4", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([u1, u2])
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.PENDING, summary="recv_test")
    db_session.add(d1)
    await db_session.flush()

    dist = DecisionDistribution(
        decision_id=d1.id, user_id=u2.id,
        distribution_type=DistributionTypeEnum.INFO,
    )
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_decisions(db_session, u2.id, "recv", None, None, 0, 0)
    assert total == 1
    assert results[0].summary == "recv_test"


@pytest.mark.asyncio
async def test_query_decisions_all_no_duplicates(db_session):
    """Decision submitted by user AND distributed to same user must appear once."""
    u1 = User(telegram_id=9005, username="qd_u5", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    d1 = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                  status=DecisionStatusEnum.PENDING, summary="no_dup")
    db_session.add(d1)
    await db_session.flush()

    dist = DecisionDistribution(
        decision_id=d1.id, user_id=u1.id,
        distribution_type=DistributionTypeEnum.INFO,
    )
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "all", None, None, 0, 0)
    assert total == 1


@pytest.mark.asyncio
async def test_query_decisions_type_filter(db_session):
    u1 = User(telegram_id=9006, username="qd_u6", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    d_crit = Decision(submitter_id=u1.id, type=DecisionTypeEnum.CRITICAL,
                      status=DecisionStatusEnum.PENDING, summary="crit")
    d_norm = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                      status=DecisionStatusEnum.PENDING, summary="norm")
    db_session.add_all([d_crit, d_norm])
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "my", "critical", None, 0, 0)
    assert total == 1
    assert results[0].summary == "crit"


@pytest.mark.asyncio
async def test_query_decisions_pagination(db_session):
    u1 = User(telegram_id=9007, username="qd_u7", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    for i in range(12):
        db_session.add(Decision(
            submitter_id=u1.id,
            type=DecisionTypeEnum.NORMAL,
            status=DecisionStatusEnum.PENDING,
            summary=f"page_test_{i}",
        ))
    await db_session.flush()

    results_p0, total = await query_decisions(db_session, u1.id, "my", None, None, 0, 0)
    results_p1, _ = await query_decisions(db_session, u1.id, "my", None, None, 0, 1)

    assert total == 12
    assert len(results_p0) == 10
    assert len(results_p1) == 2


@pytest.mark.asyncio
async def test_query_decisions_status_filter(db_session):
    u1 = User(telegram_id=9008, username="qd_u8", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u1)
    await db_session.flush()

    d_pending = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                         status=DecisionStatusEnum.PENDING, summary="pending_one")
    d_approved = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                          status=DecisionStatusEnum.APPROVED, summary="approved_one")
    db_session.add_all([d_pending, d_approved])
    await db_session.flush()

    results, total = await query_decisions(db_session, u1.id, "my", None, "pending", 0, 0)
    assert total == 1
    assert results[0].summary == "pending_one"


def test_get_menu_shortcut_keyboard_has_projects_button():
    from app.services.decisions_menu_service import get_menu_shortcut_keyboard
    kb = get_menu_shortcut_keyboard()
    all_btns = [btn for row in kb.inline_keyboard for btn in row]
    assert any("פרוייקטים" in b.text for b in all_btns)
    assert any("pm:menu" == b.callback_data for b in all_btns)
