"""Tests for correction_pin fix-type — awaiting_approval gate, manual approve."""
import pytest
from sqlalchemy import select

from app.models import CorrectionPin, RepairProposal
from app.services.per_question_loop_service import (
    FIX_TYPES, _patch_to_shadow, _apply_patch, approve_pin,
)
from app.services.ask_router import _normalize_q_hash


def test_fix_types_includes_correction_pin():
    assert "correction_pin" in FIX_TYPES


def test_patch_to_shadow_correction_pin():
    patch = {
        "question": "test-pin-q-001",
        "pinned_answer": "the answer",
        "scope_project_id": None,
        "ttl_days": 30,
    }
    shadow = _patch_to_shadow("correction_pin", patch)
    h = _normalize_q_hash("test-pin-q-001")
    assert h in shadow["correction_pins"]
    assert shadow["correction_pins"][h]["pinned_answer"] == "the answer"


@pytest.mark.asyncio
async def test_apply_correction_pin_creates_awaiting_approval(db_session):
    """_apply_patch for correction_pin must NOT write the CorrectionPin row.
    It only marks the proposal awaiting_approval — admin must approve."""
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={
            "question": "pin-q-002",
            "pinned_answer": "verbatim text",
            "ttl_days": 30,
        },
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    h = _normalize_q_hash("pin-q-002")
    pin = await db_session.scalar(
        select(CorrectionPin).where(CorrectionPin.question_hash == h))
    assert pin is None, "pin row should NOT yet exist — awaiting approval"

    await db_session.refresh(proposal)
    assert proposal.status == "awaiting_approval"


@pytest.mark.asyncio
async def test_approve_pin_creates_correction_pin_row(db_session):
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={
            "question": "pin-q-003",
            "pinned_answer": "verbatim text 2",
            "ttl_days": 14,
        },
        status="awaiting_approval",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await approve_pin(db_session, proposal.id, user_id=None)

    h = _normalize_q_hash("pin-q-003")
    pin = await db_session.scalar(
        select(CorrectionPin).where(CorrectionPin.question_hash == h))
    assert pin is not None
    assert pin.pinned_answer == "verbatim text 2"
    assert pin.expires_at is not None

    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.applied_artifact_id == pin.id


@pytest.mark.asyncio
async def test_approve_pin_rejects_non_awaiting(db_session):
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={"question": "pin-q-004", "pinned_answer": "x"},
        status="rejected",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    with pytest.raises(ValueError, match="not awaiting"):
        await approve_pin(db_session, proposal.id, user_id=None)
