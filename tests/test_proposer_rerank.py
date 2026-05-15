"""Verify _candidate_projects ranks rows by question-name overlap."""
import pytest
from sqlalchemy import text

from app.services.per_question_loop_service import _candidate_projects


@pytest.mark.asyncio
async def test_candidate_projects_ranks_name_matches_first(db_session):
    """Insert 3 projects: 2 with the same manager, 1 with name containing
    the question token. The name-match must come first in the returned list."""
    await db_session.execute(text("""
        INSERT INTO projects (project_identifier, name, manager, is_active)
        VALUES ('RNK-A', 'תל אביב מרכז', 'משה כהן', true),
               ('RNK-B', 'בת ים אזורי', 'משה כהן', true),
               ('RNK-C', 'חיפה צפון', 'משה כהן', true)
    """))
    await db_session.commit()

    rows = await _candidate_projects(
        db_session,
        "מי המנהל של פרויקט בת ים?",
        limit=10,
    )

    assert len(rows) >= 1
    assert "בת ים" in rows[0]["name"], f"expected name-match first, got {rows[0]['name']!r}"
