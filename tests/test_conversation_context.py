"""Tests for multi-turn conversation context (A2)."""
import time
import pytest


def test_append_and_get_context():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345001
    clear_context(tid)

    append_context(tid, "user", "מה פרויקט רעות?")
    append_context(tid, "assistant", "פרויקט רעות בשלב הקמה.")

    ctx = get_context(tid)
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[0]["content"] == "מה פרויקט רעות?"
    assert ctx[1]["role"] == "assistant"
    clear_context(tid)


def test_context_maxlen():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345002
    clear_context(tid)

    for i in range(7):
        append_context(tid, "user", f"msg {i}")
    ctx = get_context(tid)
    # get_context returns last 3 of the stored 5
    assert len(ctx) == 3
    assert ctx[-1]["content"] == "msg 6"
    clear_context(tid)


def test_context_ttl_expired():
    from app.services import telegram_state as ts
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345003
    clear_context(tid)

    append_context(tid, "user", "test")
    # Manually expire by backdating the timestamp
    ctx_deque = ts._conversation_context[tid]
    last = ctx_deque[-1]
    ctx_deque[-1] = {**last, "ts": time.time() - 1801}

    result = get_context(tid)
    assert result == []
    assert tid not in ts._conversation_context


def test_clear_context():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345004
    append_context(tid, "user", "hello")
    clear_context(tid)
    assert get_context(tid) == []
