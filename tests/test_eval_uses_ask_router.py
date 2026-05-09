"""The eval loop's _answer() must go through ask_router.route(), not raw RAG.

This is the load-bearing claim of Phase 0: 'eval = production' depends on this.
"""
import pytest
from unittest.mock import patch, AsyncMock

from app.services.per_question_loop_service import _answer


@pytest.mark.asyncio
async def test_answer_routes_through_ask_router():
    fake = AsyncMock(return_value=type("R", (), {
        "answer": "from-router", "sources_used": [], "log_id": None,
        "path": "project_tools", "intent": None, "param": None,
        "has_files": True, "has_decisions": False,
        "file_names": [], "sources_text": "📂 מסד הפרויקטים",
    })())
    with patch("app.services.ask_router.route", new=fake):
        out = await _answer("שאלה", user_id=1)
    assert out == "from-router"
    fake.assert_awaited_once()
    # Critical: log_to_db must be False so eval runs don't pollute QueryLog.
    _, kwargs = fake.call_args
    assert kwargs.get("log_to_db") is False
