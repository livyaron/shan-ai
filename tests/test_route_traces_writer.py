"""Verify ask_router.route() writes RouteTrace rows per path with the
correct path/intent/param/applied_rule_ids."""
import pytest
from sqlalchemy import select, text, func
from unittest.mock import patch, AsyncMock

from app.models import RouteTrace, ProjectAlias, QueryLog
from app.services.ask_router import route
from app.services import knowledge_service as _ks


@pytest.mark.asyncio
async def test_route_writes_trace_for_project_alias_branch(db_session):
    """When alias-resolve path fires + answer_project_query returns a log_id,
    the trace row links the alias's project."""
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()

    from app.services.knowledge_service import normalize_hebrew
    alias = ProjectAlias(
        project_id=pid, alias_text="phase4-trace-alias",
        normalized_alias=normalize_hebrew("phase4-trace-alias"),
        source="manual",
    )
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)

    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    # Mock answer_project_query to return a real QueryLog id (so FK resolves).
    log = QueryLog(question="q-trace-002", ai_response="x", sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        return ("answer-ok", log.id)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        result = await route("phase4-trace-alias yo", db_session, user_id=1, log_to_db=True)

    assert result.path == "project_tools"
    assert result.log_id == log.id

    trace = await db_session.scalar(
        select(RouteTrace).where(RouteTrace.query_log_id == log.id))
    assert trace is not None, "expected route_traces row linked to the QueryLog"
    assert trace.path == "project_tools"
    assert trace.intent == "by_identifier"
    assert trace.param == f"project_alias_id={pid}"
    assert trace.applied_rule_ids == [f"project_alias:project={pid}"]
    assert trace.ms_total is not None and trace.ms_total >= 0


@pytest.mark.asyncio
async def test_route_no_trace_when_log_to_db_false(db_session):
    """Eval-loop calls route(log_to_db=False) — MUST NOT write trace rows."""
    fake_rag = {
        "answer": "x", "sources_text": "", "has_files": False,
        "has_decisions": False, "file_names": [], "log_id": None,
    }
    pre = await db_session.scalar(select(func.count(RouteTrace.id)))
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value=fake_rag)):
        await route("eval-mode q", db_session, user_id=1, log_to_db=False)
    post = await db_session.scalar(select(func.count(RouteTrace.id)))
    assert pre == post, "no new trace row should be written when log_to_db=False"


@pytest.mark.asyncio
async def test_route_no_trace_when_log_id_none(db_session):
    """When the answerer returns log_id=None (e.g. correction_pin or a path
    that didn't persist), don't try to write a trace (FK would fail)."""
    fake_rag = {
        "answer": "x", "sources_text": "", "has_files": False,
        "has_decisions": False, "file_names": [], "log_id": None,
    }
    pre = await db_session.scalar(select(func.count(RouteTrace.id)))
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value=fake_rag)):
        await route("any-rag-q", db_session, user_id=1, log_to_db=True)
    post = await db_session.scalar(select(func.count(RouteTrace.id)))
    assert pre == post, "no trace when log_id is None"
