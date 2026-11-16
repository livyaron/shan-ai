"""👎 → "✍️ כתוב בעצמי" (2026-11-16). No database.

The user's next message is saved on the answer's log as free-text feedback,
re-opens the log for the nightly run_optimization, and never becomes gold.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services import telegram_state as ts
from app.services.answer_feedback_service import record_answer_comment


@pytest.fixture(autouse=True)
def _clean():
    ts._awaiting_answer_comment.clear()
    yield
    ts._awaiting_answer_comment.clear()


def test_the_press_is_taken_once():
    ts.expect_answer_comment(7, 55, now=100.0)
    assert ts.take_answer_comment(7, now=101.0) == 55
    assert ts.take_answer_comment(7, now=102.0) is None


def test_an_old_press_is_forgotten():
    ts.expect_answer_comment(7, 55, now=100.0)
    assert ts.take_answer_comment(7, now=100.0 + ts.NEW_DECISION_WAIT_SECONDS + 1) is None


class _FakeSession:
    def __init__(self, log):
        self.log, self.added, self.committed = log, [], False

    async def get(self, _model, _id):
        return self.log

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


def test_comment_is_saved_and_reopens_the_log():
    log = SimpleNamespace(user_feedback=-1, analyzed=True)
    s = _FakeSession(log)
    fb = asyncio.run(record_answer_comment(s, 9, 3, "  זה פרויקט אחר  "))
    assert fb is not None and fb.vote == "down" and fb.correction_text == "זה פרויקט אחר"
    assert fb.gold_id is None              # a comment is not a gold answer
    assert log.analyzed is False and log.user_feedback == -1
    assert s.committed


def test_empty_comment_or_missing_log_saves_nothing():
    assert asyncio.run(record_answer_comment(_FakeSession(None), 9, 3, "טקסט")) is None
    s = _FakeSession(SimpleNamespace(user_feedback=0, analyzed=True))
    assert asyncio.run(record_answer_comment(s, 9, 3, "   ")) is None
    assert not s.added
