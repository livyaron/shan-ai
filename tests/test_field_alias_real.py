"""Tests for field_alias_real fix-type — write a sentinel-row alias, verify
_detect_field consumes it BEFORE the static _FIELD_KEYWORDS dict."""
import pytest
from sqlalchemy import select, text

from app.models import QuerySynonym, RepairProposal
from app.services.per_question_loop_service import (
    FIX_TYPES, _patch_to_shadow, _apply_patch,
)
from app.services import knowledge_service as ks
from app.services.gold_truth_service import _detect_field


def test_fix_types_includes_field_alias_real():
    assert "field_alias_real" in FIX_TYPES


def test_patch_to_shadow_field_alias_real():
    patch = {"alias": "מנה\"פ", "field": "manager"}
    shadow = _patch_to_shadow("field_alias_real", patch)
    assert shadow == {"field_aliases": {'מנה"פ': "manager"}}


@pytest.mark.asyncio
async def test_apply_field_alias_real_writes_sentinel(db_session):
    proposal = RepairProposal(
        type="field_alias_real",
        patch_json={"alias": "אחראי", "field": "manager"},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    sentinel = await db_session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__field_aliases__"))
    assert sentinel is not None
    assert any(e.startswith("אחראי=manager") for e in sentinel.synonyms)

    await db_session.refresh(proposal)
    assert proposal.status == "applied"


@pytest.mark.asyncio
async def test_detect_field_uses_db_alias_before_static(db_session):
    """Insert an alias that maps 'תיכן' to 'stage' and verify _detect_field
    picks it up via the DB cache."""
    await db_session.execute(text(
        "INSERT INTO query_synonyms (original, synonyms, source) "
        "VALUES ('__field_aliases__', CAST(:s AS jsonb), 'ai') "
        "ON CONFLICT (original) DO UPDATE SET synonyms = EXCLUDED.synonyms"
    ), {"s": '["תיכן=stage"]'})
    await db_session.commit()
    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    field = _detect_field("מה ה-תיכן של פרויקט X?")
    assert field == "stage", f"expected stage, got {field!r}"
