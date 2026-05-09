"""ask_router unit tests."""
from app.services.ask_router import AnswerResult, _normalize_q_hash


def test_answer_result_fields_present():
    r = AnswerResult(
        answer="x", sources_used=[], log_id=None,
        path="rag", intent=None, param=None,
    )
    assert r.answer == "x"
    assert r.path == "rag"


def test_normalize_q_hash_is_stable():
    a = _normalize_q_hash("באיזה שלב נמצא פרויקט בית הגדי?")
    b = _normalize_q_hash("באיזה שלב נמצא פרויקט בית הגדי?")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_normalize_q_hash_ignores_final_letters():
    # Hebrew final-form letters (ם → מ etc.) must normalize to same hash.
    a = _normalize_q_hash("שלום")
    b = _normalize_q_hash("שלומ")  # final-mem stripped to mem
    assert a == b


import pytest
from unittest.mock import patch, AsyncMock

from app.services.ask_router import route


@pytest.mark.asyncio
async def test_route_decision_keyword(db_session):
    """A question containing 'החלטה' must take the decision path."""
    with patch("app.services.knowledge_service.get_decisions_context",
               new=AsyncMock(return_value="ctx")), \
         patch("app.services.knowledge_service.answer_decisions_question",
               new=AsyncMock(return_value="תשובה")):
        result = await route("מה ההחלטה האחרונה?", db_session, user_id=1, log_to_db=False)
    assert result.path == "decision"


@pytest.mark.asyncio
async def test_route_project_query(db_session):
    """A short Hebrew question with project keyword goes to project_tools."""
    with patch("app.services.project_tools.answer_project_query",
               new=AsyncMock(return_value=("res", 99))):
        result = await route("פרויקט יזרעאל", db_session, user_id=1, log_to_db=False)
    assert result.path == "project_tools"


@pytest.mark.asyncio
async def test_route_default_rag(db_session):
    """A question that matches no keyword falls through to RAG."""
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value={
                   "answer": "x", "sources_text": "",
                   "has_files": False, "has_decisions": False,
                   "file_names": [], "log_id": 1,
               })):
        result = await route("Tell me something general",
                             db_session, user_id=1, log_to_db=False)
    assert result.path == "rag"
