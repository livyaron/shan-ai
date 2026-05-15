"""Round-trip tests for new fix-types in the repair loop."""
import pytest
from sqlalchemy import text, select

from app.models import ProjectAlias, RepairProposal, IntentOverride
from app.services.per_question_loop_service import (
    _apply_patch, _patch_to_shadow, FIX_TYPES,
)


def test_fix_types_includes_project_alias():
    assert "project_alias" in FIX_TYPES
    assert "intent_override" in FIX_TYPES


def test_patch_to_shadow_project_alias():
    from app.services.knowledge_service import normalize_hebrew
    patch = {"alias_text": "בית הגדי", "project_id": 47}
    shadow = _patch_to_shadow("project_alias", patch)
    expected_key = normalize_hebrew("בית הגדי")
    assert shadow == {"project_aliases": {expected_key: 47}}


def test_patch_to_shadow_intent_override():
    from app.services.ask_router import _normalize_q_hash
    q = "באיזה שלב נמצא פרויקט בית הגדי?"
    patch = {"question": q, "forced_intent": "by_identifier", "forced_param": "בית הגדי"}
    shadow = _patch_to_shadow("intent_override", patch)
    h = _normalize_q_hash(q)
    assert shadow == {"intent_overrides": {h: {"forced_intent": "by_identifier", "forced_param": "בית הגדי"}}}


@pytest.mark.asyncio
async def test_apply_project_alias_writes_row(db_session):
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "TestAlias-XYZ", "project_id": pid},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-XYZ")
    )
    assert row is not None
    assert row.project_id == pid

    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.applied_artifact_id == row.id


@pytest.mark.asyncio
async def test_apply_intent_override_writes_row(db_session):
    proposal = RepairProposal(
        type="intent_override",
        patch_json={
            "question": "כמה פרויקטים? TestQ-XYZ",
            "forced_intent": "count_by_type",
            "forced_param": "הקמה",
        },
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    from app.services.ask_router import _normalize_q_hash
    h = _normalize_q_hash("כמה פרויקטים? TestQ-XYZ")
    row = await db_session.scalar(
        select(IntentOverride).where(IntentOverride.question_pattern_hash == h)
    )
    assert row is not None
    assert row.forced_intent == "count_by_type"
    assert row.forced_param == "הקמה"

    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.applied_artifact_id == row.id

# ───────────────── Task 1.4: unapply round-trip tests ─────────────────

from app.services.per_question_loop_service import _unapply_patch


@pytest.mark.asyncio
async def test_unapply_project_alias_deletes_row(db_session):
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "TestAlias-RB", "project_id": pid},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)
    assert proposal.applied_artifact_id is not None

    await _unapply_patch(db_session, proposal)

    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-RB")
    )
    assert row is None, "alias row should have been deleted"
    await db_session.refresh(proposal)
    assert proposal.status == "rolled_back"


@pytest.mark.asyncio
async def test_unapply_intent_override_deletes_row(db_session):
    proposal = RepairProposal(
        type="intent_override",
        patch_json={
            "question": "UnapplyTestQ-XYZ",
            "forced_intent": "by_identifier",
            "forced_param": "TestParam",
        },
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)
    assert proposal.applied_artifact_id is not None

    await _unapply_patch(db_session, proposal)

    from app.services.ask_router import _normalize_q_hash
    h = _normalize_q_hash("UnapplyTestQ-XYZ")
    row = await db_session.scalar(
        select(IntentOverride).where(IntentOverride.question_pattern_hash == h)
    )
    assert row is None, "intent_override row should have been deleted"
    await db_session.refresh(proposal)
    assert proposal.status == "rolled_back"
