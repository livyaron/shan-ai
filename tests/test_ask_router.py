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

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None, memory_context=""):
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
async def test_route_project_alias_bypasses_intent_detection(db_session):
    """When an alias matches the question, route() must skip LLM intent detection
    and call answer_project_query with precomputed_intent=by_identifier and
    precomputed_param=project_alias_id=<pid>. find_projects_by_identifier then
    extracts the pid from the param directly."""
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

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None, memory_context=""):
        captured["intent"] = precomputed_intent
        captured["param"] = precomputed_param
        return ("ok", 1)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        result = await route("באיזה שלב נמצא פרויקט בית הגדי טסט?",
                             db_session, user_id=1, log_to_db=False)

    assert captured["intent"] == "by_identifier"
    assert captured["param"] == f"project_alias_id={proj_id}"
    assert result.path == "project_tools"
    assert result.intent == "by_identifier"
    assert result.sources_used == [{"source": "project_alias", "project_id": proj_id}]


@pytest.mark.asyncio
async def test_route_correction_pin_short_circuits(db_session):
    """A pinned answer must return verbatim with zero LLM calls and
    path='correction_pin'."""
    q = "PIN-Q-001: questionable question for pin"
    h = _normalize_q_hash(q)
    await db_session.execute(_sql_text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source) "
        "VALUES (:h, :ans, 'manual')"
    ), {"h": h, "ans": "verbatim pinned answer"})
    await db_session.commit()
    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    # Patch every downstream call site that route() could fall into. None
    # should be hit when the pin lookup wins.
    apq = AsyncMock(return_value=("should-not-fire", 0))
    awfc = AsyncMock(return_value={"answer": "rag-should-not-fire"})
    adq = AsyncMock(return_value="decision-should-not-fire")
    with patch("app.services.project_tools.answer_project_query", new=apq), \
         patch("app.services.knowledge_service.answer_with_full_context", new=awfc), \
         patch("app.services.knowledge_service.answer_decisions_question", new=adq):
        result = await route(q, db_session, user_id=1, log_to_db=False)

    assert result.path == "correction_pin"
    assert result.answer == "verbatim pinned answer"
    apq.assert_not_called()
    awfc.assert_not_called()
    adq.assert_not_called()
