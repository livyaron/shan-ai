from app.models import RoleEnum
from app.services.decision_service import SUPERIOR_ROLE


def test_viewer_role_exists():
    assert RoleEnum.VIEWER == "viewer"


def test_viewer_not_in_superior_hierarchy():
    assert RoleEnum.VIEWER not in SUPERIOR_ROLE


def test_viewer_in_dashboard_role_labels():
    from app.routers.dashboard import ROLE_LABELS
    assert "viewer" in ROLE_LABELS
    assert ROLE_LABELS["viewer"] == "צופה"


def test_keyboard_for_viewer_has_one_button():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    viewer = MagicMock()
    viewer.role = RoleEnum.VIEWER
    kb = _keyboard_for_user(viewer)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 1
    assert "פרוייקטים" in buttons[0].text


def test_keyboard_for_operational_has_two_buttons():
    from app.services.telegram_polling import _keyboard_for_user
    from app.models import RoleEnum
    from unittest.mock import MagicMock
    user = MagicMock()
    user.role = RoleEnum.PROJECT_MANAGER
    kb = _keyboard_for_user(user)
    buttons = [b for row in kb.keyboard for b in row]
    assert len(buttons) == 2
