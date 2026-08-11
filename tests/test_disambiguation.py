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


def test_type_sort_key_priority():
    """הקמה → 0, הרחבה → 1, everything else → 2."""
    from app.services.project_tools import _type_sort_key
    assert _type_sort_key({"project_type": "הקמה"}) == 0
    assert _type_sort_key({"project_type": "הרחבה"}) == 1
    assert _type_sort_key({"project_type": "שוש"}) == 2
    assert _type_sort_key({"project_type": ""}) == 2
    assert _type_sort_key({}) == 2


@pytest.mark.asyncio
async def test_disambig_candidates_sorted_by_type():
    """Multi-match disambiguation lists הקמה, then הרחבה, then the rest — stable within a type.

    The by_identifier disambiguation branch returns before it ever touches the
    session, so a dummy session keeps this a Postgres-free unit test.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services import project_tools as pt

    def _p(pid, name, ptype):
        return {"id": pid, "project_identifier": pid, "name": name, "project_type": ptype,
                "manager": "", "stage": "", "estimated_finish_date": "", "dev_plan_date": "",
                "to_handle": "", "weekly_report": "", "risks": "", "delay_months": None,
                "last_updated": ""}

    # Deliberately unsorted: הרחבה, other, הקמה
    matches = [_p("B", "בית", "הרחבה"), _p("C", "גן", "שוש"), _p("A", "אבן", "הקמה")]

    with patch.object(pt, "find_projects_by_identifier", new=AsyncMock(return_value=matches)), \
         patch.object(pt, "_log_query", new=AsyncMock(return_value=1)):
        answer, _ = await pt.answer_project_query(
            "רמת", MagicMock(), {}, user_id=3,
            precomputed_intent="by_identifier", precomputed_param="רמת",
        )

    assert answer.startswith("__DISAMBIG__:")
    candidates = json.loads(answer[len("__DISAMBIG__:"):])
    assert [c["id"] for c in candidates] == ["A", "B", "C"]
