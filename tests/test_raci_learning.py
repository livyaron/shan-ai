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
