"""Tests for smart project disambiguation (A4)."""
import pytest
import json


def test_disambig_sentinel_is_string():
    """Sentinel format is stable."""
    sentinel = f"__DISAMBIG__:{json.dumps(['רעות', 'רהט'], ensure_ascii=False)}"
    assert sentinel.startswith("__DISAMBIG__:")
    candidates = json.loads(sentinel[len("__DISAMBIG__:"):])
    assert candidates == ["רעות", "רהט"]


@pytest.mark.asyncio
async def test_awaiting_disambiguation_state():
    """_awaiting_disambiguation dict is importable and mutable."""
    from app.services.telegram_state import _awaiting_disambiguation
    _awaiting_disambiguation[999] = "מה פרויקט X?"
    assert _awaiting_disambiguation[999] == "מה פרויקט X?"
    del _awaiting_disambiguation[999]
    assert 999 not in _awaiting_disambiguation
