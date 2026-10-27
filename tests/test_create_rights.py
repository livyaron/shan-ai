"""Create rights — the "new" buttons and the create endpoints read one rule.

DB-free: the lessons identity is read through a fake session that records SQL.
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import RoleEnum
from app.routers import dashboard as dash
from app.routers import lessons_gateway as lg
from app.routers import war_room
from app.services import dashboard_domains as dd
from app.services import user_access


def _user(role):
    return SimpleNamespace(id=7, username="u", email="", role=role, is_admin=False)


def test_only_the_viewer_role_is_read_only():
    assert not user_access.can_write(_user(RoleEnum.VIEWER))
    for role in [r for r in RoleEnum if r != RoleEnum.VIEWER] + [None]:
        assert user_access.can_write(_user(role)), role


def test_viewer_is_refused_by_every_create_endpoint():
    with pytest.raises(HTTPException) as e:
        dash._require_decision_writer(_user(RoleEnum.VIEWER))
    assert e.value.status_code == 403
    dash._require_decision_writer(_user(RoleEnum.PROJECT_MANAGER))
    for fn in (dash.analyze_decision, dash.confirm_decision, dash.analyze_preview, dash.suggest_raci_from_text):
        assert "_require_decision_writer(current_user)" in inspect.getsource(fn), fn.__name__
    assert "_require_editor(current_user)" in inspect.getsource(war_room.create_mission_web)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers profile / referent-group reads; records every statement."""

    def __init__(self, profiles=(), groups=()):
        self.profiles, self.groups, self.sql = list(profiles), list(groups), []

    async def execute(self, stmt, params=None):
        q = str(stmt)
        self.sql.append(q)
        return _Rows(self.groups if "referent_members" in q else self.profiles)


def _profile(role, pid="p1"):
    return {"id": pid, "role": role, "shan_user_id": 7, "is_login": True}


def test_read_identity_never_creates_a_profile():
    s = _Session()
    ident = asyncio.run(lg.read_identity(s, _user(None)))
    assert ident.is_viewer
    assert not any("INSERT" in q.upper() for q in s.sql)


@pytest.mark.parametrize("profiles, groups, expected", [
    ([], [], False),                                          # never visited the module
    ([_profile("viewer")], [], False),
    ([_profile("viewer")], [_profile("referent", "g1")], True),   # a referent-group member acts
    ([_profile("project_manager")], [], True),
    ([_profile("admin")], [], True),
])
def test_lessons_button_follows_the_module_rule(profiles, groups, expected):
    rights = asyncio.run(dd.create_rights(_Session(profiles, groups), _user(RoleEnum.PROJECT_MANAGER)))
    assert rights == {"decisions": True, "missions": True, "lessons": expected}


def test_viewer_role_hides_decisions_and_missions():
    rights = asyncio.run(dd.create_rights(_Session([_profile("project_manager")]), _user(RoleEnum.VIEWER)))
    assert rights == {"decisions": False, "missions": False, "lessons": True}


def test_lessons_lookup_failure_hides_only_the_lesson_button():
    class Broken(_Session):
        async def execute(self, stmt, params=None):
            raise RuntimeError("schema missing")
    rights = asyncio.run(dd.create_rights(Broken(), _user(None)))
    assert rights == {"decisions": True, "missions": True, "lessons": False}
