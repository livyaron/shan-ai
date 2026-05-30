"""Tests for the is_relevant attribute on Decision."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models import Decision


def test_decision_has_is_relevant_column():
    cols = {c.key for c in Decision.__table__.columns}
    assert "is_relevant" in cols
    assert "irrelevant_reason" in cols
    assert "irrelevant_at" in cols
    assert "irrelevant_by_id" in cols


def test_is_relevant_defaults_true():
    col = Decision.__table__.c["is_relevant"]
    assert col.default.arg is True or col.server_default is not None


@pytest.mark.asyncio
async def test_set_irrelevant_updates_fields():
    from app.services.decision_service import DecisionService

    session = AsyncMock()
    svc = DecisionService.__new__(DecisionService)
    svc.session = session

    d = MagicMock()
    d.is_relevant = True
    d.submitter_id = 1
    session.get.return_value = d
    session.scalar.return_value = None  # no RACI A

    actor = MagicMock()
    actor.id = 1
    actor.is_admin = False

    success, msg = await svc.set_decision_relevance(99, actor, is_relevant=False, reason="בוטל")
    assert success
    assert d.is_relevant is False
    assert d.irrelevant_reason == "בוטל"
    assert d.irrelevant_by_id == 1


@pytest.mark.asyncio
async def test_restore_relevant_clears_fields():
    from app.services.decision_service import DecisionService

    session = AsyncMock()
    svc = DecisionService.__new__(DecisionService)
    svc.session = session

    d = MagicMock()
    d.is_relevant = False
    d.submitter_id = 1
    session.get.return_value = d
    session.scalar.return_value = None

    actor = MagicMock()
    actor.id = 1
    actor.is_admin = False

    success, msg = await svc.set_decision_relevance(99, actor, is_relevant=True)
    assert success
    assert d.is_relevant is True
    assert d.irrelevant_reason is None
    assert d.irrelevant_at is None
    assert d.irrelevant_by_id is None
