"""Phase 0 gate: 20-question smoke set must run end-to-end without exceptions.

We only assert that route() returns a non-empty answer string and a valid
path; correctness of the answer is the job of Phase 1.
"""
import pytest

from app.services.ask_router import route

SMOKE_QUESTIONS = [
    # decision path
    "מה ההחלטה האחרונה?",
    "כמה החלטות יש סה\"כ?",
    # project_tools path
    "פרויקט יזרעאל",
    "ניר יצחק",  # project name that looks like a manager name
    "כמה פרויקטי הקמה פעילים?",
    "מי המנהל של פרויקט נתניה?",
    "באיזה שלב נמצא פרויקט בית הגדי?",  # the spec reproducer
    "פרויקטים מאחרים",
    "פרויקטי 2026",
    "סיכונים",
    "מנה\"פ של חולה",
    "תחמ\"ש",
    "פרויקט חולה",
    "מה השלב של פרויקט יזרעאל?",
    "מי אחראי על פרויקט נתניה?",
    "מתי יסתיים פרויקט יזרעאל?",
    # rag fallback path
    "Tell me about the system architecture",
    "What is RAG?",
    "How do I upload a file?",
    "מהו תהליך עבודת המערכת",
    "מה זה pgvector",
]

VALID_PATHS = {"correction_pin", "decision", "project_tools", "rag", "disambiguation"}


@pytest.mark.parametrize("question", SMOKE_QUESTIONS)
async def test_phase0_smoke_question_returns_answer(db_session, question):
    result = await route(question, db_session, user_id=1, log_to_db=False)
    assert result.path in VALID_PATHS, f"unknown path: {result.path}"
    assert isinstance(result.answer, str), "answer must be a string"
    assert len(result.answer) > 0, f"empty answer for: {question}"
