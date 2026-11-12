"""Finding a manager's projects by the person's name (2026-11-12). No database.

"כמה פרויקטים מנהל איתן אוחיון?" answered "לא נמצאו תוצאות": the master file
spells managers its own way, and only the file's spelling was searched. The
lookup now also goes user → admin-confirmed manager_aliases → projects, and a
miss lists the similar names that do exist instead of a dead end.
"""
from types import SimpleNamespace

import pytest

from app.models import Project
from app.services import project_tools as pt


class _Session:
    """Answers each execute() with the next scripted result."""
    def __init__(self, *results):
        self.results, self.statements = list(results), []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        rows = self.results.pop(0)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _project(name, manager):
    return Project(name=name, project_identifier=f"WBE-{name}", manager=manager, is_active=True)


@pytest.mark.asyncio
async def test_the_files_own_spelling_still_wins():
    session = _Session([_project("רעות", "אוחיון איתן")])
    found = await pt.search_by_manager("איתן אוחיון", session)
    assert [p["name"] for p in found] == ["רעות"]
    assert len(session.statements) == 1      # no alias lookup needed


@pytest.mark.asyncio
async def test_a_person_is_found_through_the_confirmed_alias():
    session = _Session(
        [],                                   # the name as typed is not in the file
        [17],                                 # exactly one system user by that name
        ["א. אוחיון"],                        # what an admin linked to that user
        [_project("קסם", "א. אוחיון"), _project("רעות", "א. אוחיון")],
    )
    found = await pt.search_by_manager("איתן אוחיון", session)
    assert [p["name"] for p in found] == ["קסם", "רעות"]
    assert "manager_aliases" in session.statements[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("users", [[], [17, 18]])
async def test_no_user_or_two_users_by_that_name_is_not_a_match(users):
    session = _Session([], users)
    assert await pt.search_by_manager("איתן אוחיון", session) == []
    assert len(session.statements) == 2      # never reached the aliases


@pytest.mark.asyncio
async def test_a_user_without_confirmed_aliases_finds_nothing():
    session = _Session([], [17], [])
    assert await pt.search_by_manager("איתן אוחיון", session) == []


@pytest.mark.asyncio
async def test_similar_names_come_from_the_files_managers():
    session = _Session(["אוחיון א.", "איתן כהן"])
    assert await pt.similar_manager_names("איתן אוחיון", session) == ["אוחיון א.", "איתן כהן"]
    assert await pt.similar_manager_names("אב", _Session()) == []   # too short to mean anything


def test_a_miss_offers_the_names_that_exist():
    text = pt.manager_not_found_text("איתן אוחיון", ["אוחיון א.", "איתן כהן"])
    assert "איתן אוחיון" in text and "• אוחיון א." in text and "• איתן כהן" in text
    bare = pt.manager_not_found_text("איתן אוחיון", [])
    assert "•" not in bare and "איתן אוחיון" in bare
    assert "&lt;b&gt;" in pt.manager_not_found_text("<b>", [])       # typed text is escaped
