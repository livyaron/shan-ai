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


@pytest.mark.asyncio
async def test_route_rag_passes_through_file_names_and_flags(db_session):
    """RAG branch must surface file_names + has_files + has_decisions + sources_text
    from answer_with_full_context, not synthesize them. This guards the JSON-response
    contract that app/templates/ask.html consumes."""
    fake_rag_response = {
        "answer": "x",
        "sources_text": "מקורות: 📁 weekly_report_2026.xlsx",
        "has_files": True,
        "has_decisions": False,
        "file_names": ["weekly_report_2026.xlsx"],
        "log_id": 42,
    }
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value=fake_rag_response)):
        result = await route("Tell me something general",
                             db_session, user_id=1, log_to_db=False)
    assert result.path == "rag"
    assert result.has_files is True
    assert result.has_decisions is False
    assert result.file_names == ["weekly_report_2026.xlsx"]
    assert result.sources_text == "מקורות: 📁 weekly_report_2026.xlsx"
    assert result.log_id == 42


@pytest.mark.asyncio
async def test_route_project_branch_has_files_true(db_session):
    """Project-tools answers should set has_files=True (matches original ask.py)."""
    with patch("app.services.project_tools.answer_project_query",
               new=AsyncMock(return_value=("res", 99))):
        result = await route("פרויקט יזרעאל", db_session, user_id=1, log_to_db=False)
    assert result.has_files is True
    assert result.has_decisions is False
    assert result.file_names == []
    assert result.sources_text == "📂 מסד הפרויקטים"


@pytest.mark.asyncio
async def test_route_decision_no_context_fallback(db_session):
    """When decisions_ctx is empty, has_decisions must be False and the fallback
    string is returned without a sources_text badge."""
    with patch("app.services.knowledge_service.get_decisions_context",
               new=AsyncMock(return_value="")):
        result = await route("מה ההחלטה האחרונה?",
                             db_session, user_id=1, log_to_db=False)
    assert result.path == "decision"
    assert result.has_decisions is False
    assert result.sources_text == ""
    assert result.answer == "לא נמצאו החלטות עבורך במסד הנתונים."


from sqlalchemy import text as _sql_text

from app.services import knowledge_service as _ks
from app.services.ask_router import _normalize_q_hash


@pytest.mark.asyncio
async def test_route_intent_override_skips_llm_intent_detection(db_session):
    q = "באיזה שלב נמצא פרויקט בית הגדי?"
    h = _normalize_q_hash(q)
    await db_session.execute(_sql_text(
        "INSERT INTO intent_overrides "
        "(question_pattern_hash, forced_intent, forced_param, source) "
        "VALUES (:h, 'by_identifier', 'בית הגדי', 'manual')"
    ), {"h": h})
    await db_session.commit()
    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    captured = {}

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        captured["intent"] = precomputed_intent
        captured["param"] = precomputed_param
        return ("ok", 1)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        result = await route(q, db_session, user_id=1, log_to_db=False)

    assert result.path == "project_tools"
    assert captured["intent"] == "by_identifier"
    assert captured["param"] == "בית הגדי"
    assert result.intent == "by_identifier"


@pytest.mark.asyncio
async def test_route_project_alias_enriches_question(db_session):
    """When 'בית הגדי' is a known alias for project 47, route() injects an
    identifier hint that find_projects_by_identifier can latch onto."""
    # Seed an alias pointing to whatever project exists. We need a real id
    # so insert via SELECT and capture it back.
    proj_id = (await db_session.execute(_sql_text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    await db_session.execute(_sql_text(
        "INSERT INTO project_aliases (project_id, alias_text, normalized_alias, source) "
        "VALUES (:pid, 'בית הגדי טסט', 'בית הגדי טסט', 'manual')"
    ), {"pid": proj_id})
    await db_session.commit()
    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    captured = {}

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        captured["text"] = text_
        return ("ok", 1)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        await route("באיזה שלב נמצא פרויקט בית הגדי טסט?",
                    db_session, user_id=1, log_to_db=False)

    assert f"project_alias_id={proj_id}" in captured["text"], \
        f"expected alias hint in question text, got: {captured['text']!r}"
