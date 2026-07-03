"""Tests for distribution_service._rule_based_suggestion — the fallback that
decides who receives each decision when the LLM path is unavailable."""
from unittest.mock import MagicMock

from app.models import DecisionTypeEnum, RoleEnum
from app.services.distribution_service import _rule_based_suggestion


def _user(uid, role, manager_id=None, username=None):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.manager_id = manager_id
    u.username = username or f"user{uid}"
    u.job_title = ""
    return u


def _org():
    """submitter(1, PM) → mgr(2, dept) → boss(3, deputy); peer(4) shares mgr;
    report(5) reports to submitter."""
    users = {
        1: _user(1, RoleEnum.PROJECT_MANAGER, manager_id=2),
        2: _user(2, RoleEnum.DEPARTMENT_MANAGER, manager_id=3),
        3: _user(3, RoleEnum.DEPUTY_DIVISION_MANAGER),
        4: _user(4, RoleEnum.PROJECT_MANAGER, manager_id=2),
        5: _user(5, RoleEnum.PROJECT_MANAGER, manager_id=1),
    }
    return users


def _decision(dtype):
    d = MagicMock()
    d.type = dtype
    return d


def _by_user(suggestions):
    return {s["user_id"]: s["dist_type"] for s in suggestions}


def test_info_decision_notifies_manager_and_peers():
    users = _org()
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.INFO), users[1], users))
    assert out[2] == "info"   # direct manager
    assert out[4] == "info"   # peer under same manager


def test_normal_decision_assigns_execution_to_reports():
    users = _org()
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.NORMAL), users[1], users))
    assert out[2] == "info"        # manager informed
    assert out[5] == "execution"   # direct report executes


def test_critical_decision_escalates_two_levels():
    users = _org()
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.CRITICAL), users[1], users))
    assert out[2] == "approval"    # direct manager must approve
    assert out[3] == "info"        # manager's manager informed


def test_uncertain_decision_no_peer_noise():
    users = _org()
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.UNCERTAIN), users[1], users))
    assert out[2] == "approval"
    assert out[3] == "info"
    assert 4 not in out            # peers NOT notified for uncertain
    assert 5 not in out


def test_manager_inferred_from_role_hierarchy_when_unset():
    """Submitter without manager_id: fall back to any user with the superior role."""
    users = {
        1: _user(1, RoleEnum.PROJECT_MANAGER, manager_id=None),
        2: _user(2, RoleEnum.DEPARTMENT_MANAGER, manager_id=None),
    }
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.INFO), users[1], users))
    assert out.get(2) == "info"


def test_approval_never_downgraded_to_info():
    """A user reachable both as approver and as peer keeps the approval role."""
    users = _org()
    users[2].manager_id = None  # boss found via role fallback instead
    out = _by_user(_rule_based_suggestion(_decision(DecisionTypeEnum.CRITICAL), users[1], users))
    assert out[2] == "approval"


def test_suggestions_skip_unknown_user_ids():
    """manager_id pointing outside all_users must not produce a suggestion row."""
    users = {1: _user(1, RoleEnum.PROJECT_MANAGER, manager_id=77)}
    out = _rule_based_suggestion(_decision(DecisionTypeEnum.INFO), users[1], users)
    assert all(s["user_id"] in users for s in out)
