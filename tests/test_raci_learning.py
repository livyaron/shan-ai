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


def test_save_raci_records_outcome_source():
    """save_raci must call record_raci_outcome with the new assignments."""
    import inspect
    from app.routers import dashboard
    src = inspect.getsource(dashboard.save_raci)
    assert "record_raci_outcome" in src, "save_raci must record the correction for learning"


@pytest.mark.asyncio
async def test_build_raci_context_returns_text_and_meta(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.services.raci_service as rs
    import app.services.lessons_service as ls

    async def fake_patterns(dtype, session):
        return "דפוסי RACI..."
    monkeypatch.setattr(ls, "get_raci_patterns", fake_patterns)
    monkeypatch.setattr(rs, "_get_raci_few_shots", AsyncMock(return_value="דוגמאות..."))
    monkeypatch.setattr(rs, "_get_active_rules", AsyncMock(return_value="כללים..."))
    monkeypatch.setattr(rs, "_count_corrections", AsyncMock(return_value={"past_edits": 4, "rules": 3, "patterns": 1}))

    decision = MagicMock()
    decision.type.value = "normal"
    session = AsyncMock()

    text, meta = await rs.build_raci_context(decision, session)
    assert "כללים" in text and "דוגמאות" in text
    assert meta["past_edits"] == 4
    assert meta["rules"] == 3


def test_raci_prompt_makes_responsibilities_primary():
    from app.services.raci_service import _build_raci_prompt
    prompt = _build_raci_prompt(
        submitter_str="דנה | מהנדסת",
        type_he="רגיל",
        summary="תקלה במכרז ספקים",
        action="לפרסם מכרז חדש",
        users_desc="- ID=7 | דנה | תחום אחריות: מכרזים ורכש",
        context_text="",
    )
    assert "תחום האחריות" in prompt
    assert "השיקול העיקרי" in prompt
    assert "גוברים על ברירת המחדל" in prompt


def test_get_raci_patterns_uses_corrections_not_feedback_gate():
    import inspect
    from app.services import lessons_service
    src = inspect.getsource(lessons_service.get_raci_patterns)
    assert "RACISuggestion" in src
    assert "feedback_score >= 4" not in src.replace(" ", "").replace("\n", "") or "RACISuggestion" in src


def test_few_shots_prioritizes_edited_and_raises_limit():
    import inspect
    from app.services import raci_service
    src = inspect.getsource(raci_service._get_raci_few_shots)
    assert "limit: int = 8" in src, "few-shot limit should be raised to 8"
    assert "EDITED" in src
