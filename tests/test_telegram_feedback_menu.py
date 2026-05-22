"""TDD tests for the ⭐ pending-feedback shortcut in the decisions menu.

Covers:
- get_menu_keyboard() badge when feedback_count > 0
- build_feedback_results_keyboard() clickable decisions
- query_pending_feedback() DB query
- save_telegram_feedback_score() upsert + avg recalc
- save_telegram_feedback_text() notes save
"""
import pytest
from datetime import datetime
from sqlalchemy.orm import configure_mappers, class_mapper

from app.models import (
    Decision, DecisionTypeEnum, DecisionStatusEnum,
    User, DecisionDistribution, DecisionFeedback,
    DecisionRaciRole, RaciRoleEnum, RoleEnum, DistributionTypeEnum,
)

configure_mappers()
_decision_mgr = class_mapper(Decision).class_manager


def _make_decision(**kwargs):
    defaults = dict(
        id=1, type=DecisionTypeEnum.NORMAL,
        status=DecisionStatusEnum.EXECUTED,
        summary="בדיקה", created_at=datetime(2026, 5, 20),
    )
    defaults.update(kwargs)
    d = _decision_mgr.new_instance()
    for k, v in defaults.items():
        setattr(d, k, v)
    return d


# ── get_menu_keyboard ────────────────────────────────────────────────────────

from app.services.decisions_menu_service import get_menu_keyboard, build_feedback_results_keyboard


def test_get_menu_keyboard_has_seven_buttons_with_feedback():
    kb = get_menu_keyboard(feedback_count=0)
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(all_buttons) == 7


def test_get_menu_keyboard_shows_badge_when_count_positive():
    kb = get_menu_keyboard(feedback_count=3)
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    feedback_btn = next(b for b in all_buttons if "משוב" in b.text)
    assert "(3)" in feedback_btn.text


def test_get_menu_keyboard_no_badge_when_count_zero():
    kb = get_menu_keyboard(feedback_count=0)
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    feedback_btn = next(b for b in all_buttons if "משוב" in b.text)
    assert "(" not in feedback_btn.text


def test_get_menu_keyboard_feedback_button_callback():
    kb = get_menu_keyboard()
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    feedback_btn = next(b for b in all_buttons if "משוב" in b.text)
    assert feedback_btn.callback_data == "dm:feedback:0"


# ── build_feedback_results_keyboard ─────────────────────────────────────────

def test_build_feedback_results_keyboard_each_decision_is_button():
    decisions = [_make_decision(id=i + 1, summary=f"summary {i}") for i in range(3)]
    kb = build_feedback_results_keyboard(decisions, page=0, total=3)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    sel_btns = [b for b in all_btns if b.callback_data.startswith("dm:fbsel:")]
    assert len(sel_btns) == 3


def test_build_feedback_results_keyboard_callback_format():
    decisions = [_make_decision(id=42, summary="test")]
    kb = build_feedback_results_keyboard(decisions, page=0, total=1)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    sel_btn = next(b for b in all_btns if b.callback_data.startswith("dm:fbsel:"))
    assert sel_btn.callback_data == "dm:fbsel:42:0"


def test_build_feedback_results_keyboard_has_back_button():
    decisions = [_make_decision(id=1)]
    kb = build_feedback_results_keyboard(decisions, page=0, total=1)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "dm:menu" for b in all_btns)


def test_build_feedback_results_keyboard_pagination():
    decisions = [_make_decision(id=i + 1) for i in range(10)]
    kb = build_feedback_results_keyboard(decisions, page=0, total=15)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    assert any("הבא" in b.text for b in all_btns)


# ── query_pending_feedback ───────────────────────────────────────────────────

from app.services.decisions_menu_service import query_pending_feedback


@pytest.mark.asyncio
async def test_query_pending_feedback_returns_received_without_rating(db_session):
    submitter = User(telegram_id=88001, username="pfb_sub1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    recipient = User(telegram_id=88001 + 100, username="pfb_rec1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, recipient])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="pending_fb")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=recipient.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, recipient.id, 0)
    assert total == 1
    assert results[0].summary == "pending_fb"


@pytest.mark.asyncio
async def test_query_pending_feedback_excludes_submitted_decisions(db_session):
    u = User(telegram_id=88002, username="pfb_u2x", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    d = Decision(submitter_id=u.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="own_decision")
    db_session.add(d)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, u.id, 0)
    assert total == 0


@pytest.mark.asyncio
async def test_query_pending_feedback_excludes_already_rated(db_session):
    submitter = User(telegram_id=88002 + 200, username="pfb_sub2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    recipient = User(telegram_id=88002 + 201, username="pfb_rec2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, recipient])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="rated_fb")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=recipient.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    fb = DecisionFeedback(decision_id=d.id, user_id=recipient.id, score=4)
    db_session.add(fb)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, recipient.id, 0)
    assert total == 0


@pytest.mark.asyncio
async def test_query_pending_feedback_excludes_pending_decisions(db_session):
    submitter = User(telegram_id=88003 + 300, username="pfb_sub3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    recipient = User(telegram_id=88003 + 301, username="pfb_rec3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, recipient])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.CRITICAL,
                 status=DecisionStatusEnum.PENDING, summary="still_pending")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=recipient.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, recipient.id, 0)
    assert total == 0


@pytest.mark.asyncio
async def test_query_pending_feedback_includes_received_decisions(db_session):
    submitter = User(telegram_id=88004, username="pfb_sub", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    recipient = User(telegram_id=88005, username="pfb_rec", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, recipient])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.APPROVED, summary="recv_pending_fb")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=recipient.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, recipient.id, 0)
    assert total == 1
    assert results[0].summary == "recv_pending_fb"


@pytest.mark.asyncio
async def test_query_pending_feedback_includes_raci_decisions(db_session):
    submitter = User(telegram_id=88006, username="pfb_raci_sub", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    raci_user = User(telegram_id=88007, username="pfb_raci_u", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, raci_user])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.CRITICAL,
                 status=DecisionStatusEnum.APPROVED, summary="raci_pending_fb")
    db_session.add(d)
    await db_session.flush()

    raci = DecisionRaciRole(decision_id=d.id, user_id=raci_user.id, role=RaciRoleEnum.ACCOUNTABLE)
    db_session.add(raci)
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, raci_user.id, 0)
    assert total == 1
    assert results[0].summary == "raci_pending_fb"


@pytest.mark.asyncio
async def test_query_pending_feedback_no_duplicates(db_session):
    """User in both distribution AND RACI for same decision — must appear once."""
    submitter = User(telegram_id=88008, username="pfb_nodup_sub", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u = User(telegram_id=88009, username="pfb_nodup", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([submitter, u])
    await db_session.flush()

    d = Decision(submitter_id=submitter.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="nodup_fb")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=u.id,
                                distribution_type=DistributionTypeEnum.INFO)
    raci = DecisionRaciRole(decision_id=d.id, user_id=u.id, role=RaciRoleEnum.ACCOUNTABLE)
    db_session.add_all([dist, raci])
    await db_session.flush()

    results, total = await query_pending_feedback(db_session, u.id, 0)
    assert total == 1


# ── save_telegram_feedback_score ─────────────────────────────────────────────

from app.services.feedback_service import save_telegram_feedback_score, save_telegram_feedback_text
from sqlalchemy import select


@pytest.mark.asyncio
async def test_save_telegram_feedback_score_creates_feedback_row(db_session):
    u = User(telegram_id=88010, username="sfb_u1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    d = Decision(submitter_id=u.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="score_test")
    db_session.add(d)
    await db_session.flush()

    ok = await save_telegram_feedback_score(db_session, u.id, d.id, 4)
    assert ok is True

    row = await db_session.scalar(
        select(DecisionFeedback).where(
            DecisionFeedback.decision_id == d.id,
            DecisionFeedback.user_id == u.id,
        )
    )
    assert row is not None
    assert row.score == 4


@pytest.mark.asyncio
async def test_save_telegram_feedback_score_updates_decision_avg(db_session):
    u1 = User(telegram_id=88011, username="sfb_avg1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u2 = User(telegram_id=88012, username="sfb_avg2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([u1, u2])
    await db_session.flush()

    d = Decision(submitter_id=u1.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="avg_test")
    db_session.add(d)
    await db_session.flush()

    await save_telegram_feedback_score(db_session, u1.id, d.id, 4)
    await save_telegram_feedback_score(db_session, u2.id, d.id, 2)

    await db_session.refresh(d)
    assert d.feedback_score == round((4 + 2) / 2)


@pytest.mark.asyncio
async def test_save_telegram_feedback_score_upserts_existing(db_session):
    u = User(telegram_id=88013, username="sfb_upsert", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    d = Decision(submitter_id=u.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="upsert_test")
    db_session.add(d)
    await db_session.flush()

    await save_telegram_feedback_score(db_session, u.id, d.id, 2)
    await save_telegram_feedback_score(db_session, u.id, d.id, 5)

    rows = (await db_session.execute(
        select(DecisionFeedback).where(
            DecisionFeedback.decision_id == d.id,
            DecisionFeedback.user_id == u.id,
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].score == 5


@pytest.mark.asyncio
async def test_save_telegram_feedback_score_returns_false_for_missing_decision(db_session):
    u = User(telegram_id=88014, username="sfb_miss", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    ok = await save_telegram_feedback_score(db_session, u.id, 999999, 3)
    assert ok is False


# ── save_telegram_feedback_text ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_telegram_feedback_text_saves_notes(db_session):
    u = User(telegram_id=88020, username="sft_u1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    d = Decision(submitter_id=u.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="text_test")
    db_session.add(d)
    await db_session.flush()

    await save_telegram_feedback_score(db_session, u.id, d.id, 4)
    ok = await save_telegram_feedback_text(db_session, u.id, d.id, "הביצוע היה מוצלח")
    assert ok is True

    row = await db_session.scalar(
        select(DecisionFeedback).where(
            DecisionFeedback.decision_id == d.id,
            DecisionFeedback.user_id == u.id,
        )
    )
    assert row.notes == "הביצוע היה מוצלח"


@pytest.mark.asyncio
async def test_save_telegram_feedback_text_empty_skip_leaves_notes_null(db_session):
    u = User(telegram_id=88021, username="sft_u2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    d = Decision(submitter_id=u.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="skip_test")
    db_session.add(d)
    await db_session.flush()

    await save_telegram_feedback_score(db_session, u.id, d.id, 3)
    ok = await save_telegram_feedback_text(db_session, u.id, d.id, "")
    assert ok is True

    row = await db_session.scalar(
        select(DecisionFeedback).where(
            DecisionFeedback.decision_id == d.id,
            DecisionFeedback.user_id == u.id,
        )
    )
    assert row.notes is None or row.notes == ""


# ── get_menu_counts feedback key ─────────────────────────────────────────────

from app.services.decisions_menu_service import get_menu_counts


@pytest.mark.asyncio
async def test_get_menu_counts_feedback_key_present(db_session):
    u = User(telegram_id=88030, username="gmc_u1", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add(u)
    await db_session.flush()

    counts = await get_menu_counts(db_session, u.id)
    assert "feedback" in counts


@pytest.mark.asyncio
async def test_get_menu_counts_feedback_reflects_pending(db_session):
    sub = User(telegram_id=88031, username="gmc_sub2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u = User(telegram_id=88031 + 50, username="gmc_u2", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([sub, u])
    await db_session.flush()

    d = Decision(submitter_id=sub.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="counts_fb_test")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=u.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    counts = await get_menu_counts(db_session, u.id)
    assert counts["feedback"] >= 1


@pytest.mark.asyncio
async def test_get_menu_counts_feedback_zero_after_rating(db_session):
    sub = User(telegram_id=88032, username="gmc_sub3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    u = User(telegram_id=88032 + 50, username="gmc_u3", role=RoleEnum.PROJECT_MANAGER, password_hash="x")
    db_session.add_all([sub, u])
    await db_session.flush()

    d = Decision(submitter_id=sub.id, type=DecisionTypeEnum.NORMAL,
                 status=DecisionStatusEnum.EXECUTED, summary="counts_rated_test")
    db_session.add(d)
    await db_session.flush()

    dist = DecisionDistribution(decision_id=d.id, user_id=u.id,
                                distribution_type=DistributionTypeEnum.INFO)
    db_session.add(dist)
    await db_session.flush()

    fb = DecisionFeedback(decision_id=d.id, user_id=u.id, score=5)
    db_session.add(fb)
    await db_session.flush()

    counts = await get_menu_counts(db_session, u.id)
    assert counts["feedback"] == 0
