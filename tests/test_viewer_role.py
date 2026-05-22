from app.models import RoleEnum


def test_viewer_role_exists():
    assert RoleEnum.VIEWER == "viewer"


def test_viewer_not_in_superior_hierarchy():
    from app.services.decision_service import SUPERIOR_ROLE
    assert RoleEnum.VIEWER not in SUPERIOR_ROLE
