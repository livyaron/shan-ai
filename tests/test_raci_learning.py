"""Tests for RACI learning loop helpers."""
import pytest
from app.models import RACISuggestionStatusEnum


def test_diff_outcome_identical_is_accepted():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "R"}]
    final = [{"user_id": 2, "role": "R"}, {"user_id": 1, "role": "A"}]  # order-independent
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.ACCEPTED


def test_diff_outcome_changed_role_is_edited():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "R"}]
    final = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "C"}]
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.EDITED


def test_diff_outcome_added_user_is_edited():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}]
    final = [{"user_id": 1, "role": "A"}, {"user_id": 3, "role": "I"}]
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.EDITED


@pytest.mark.asyncio
async def test_record_raci_outcome_creates_edited_row(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.services.raci_service as rs

    suggestion = MagicMock()
    suggestion.suggested_assignments = [{"user_id": 1, "role": "A"}]
    suggestion.outcome = None
    suggestion.final_assignments = None
    suggestion.reason_analyzed = True

    session = AsyncMock()
    session.scalar.return_value = suggestion

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=session)
    sess_cm.__aexit__ = AsyncMock(return_value=False)
    import app.database as dbmod
    monkeypatch.setattr(dbmod, "async_session_maker", lambda: sess_cm, raising=False)

    await rs.record_raci_outcome(99, [{"user_id": 1, "role": "C"}])

    from app.models import RACISuggestionStatusEnum
    assert suggestion.outcome == RACISuggestionStatusEnum.EDITED
    assert suggestion.final_assignments == [{"user_id": 1, "role": "C"}]
    assert suggestion.reason_analyzed is False
    session.commit.assert_awaited_once()
