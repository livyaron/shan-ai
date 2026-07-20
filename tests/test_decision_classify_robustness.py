"""Decision-flow hardening — classify()/analyze() must survive malformed LLM replies.

Regression tests for the "decision didn't work" failure: fallback providers wrap
JSON in markdown fences or prose, and straight quotes in user text leak into the
JSON reply — classify() used bare json.loads and lost the decision to an error.
"""
import json
from unittest.mock import patch

import pytest

from app.services.claude_service import ClaudeService, _extract_json


_VALID_ANALYZE_JSON = json.dumps({
    "type": "NORMAL",
    "summary": "סיכום",
    "recommended_action": "פעולה",
    "requires_approval": False,
    "self_critique": {"assumptions": [], "risks": []},
    "measurability": "PARTIAL",
}, ensure_ascii=False)


def _classify_patch(reply: str):
    async def _fake(usage, messages=None, **kw):
        _fake.messages = messages
        return reply
    return patch("app.services.claude_service.llm_chat", side_effect=_fake), _fake


async def test_classify_parses_clean_json():
    p, _ = _classify_patch('{"verdict": "DECISION"}')
    with p:
        assert (await ClaudeService().classify("צריך להחליף שנאי"))["verdict"] == "DECISION"


async def test_classify_parses_fenced_json():
    p, _ = _classify_patch('```json\n{"verdict": "NOT_DECISION", "reply": "שלום"}\n```')
    with p:
        result = await ClaudeService().classify("שלום")
    assert result["verdict"] == "NOT_DECISION"
    assert result["reply"] == "שלום"


async def test_classify_parses_json_wrapped_in_prose():
    p, _ = _classify_patch('הנה הסיווג: {"verdict": "UNCLEAR", "clarifying_question": "מה?"} בהצלחה')
    with p:
        assert (await ClaudeService().classify("בעיה"))["verdict"] == "UNCLEAR"


async def test_classify_garbage_defaults_to_decision():
    """A decision must never be lost to a parse error — worst case it goes to
    analysis and the user approves/dismisses the preview."""
    p, _ = _classify_patch("אני לא יכול לענות על זה")
    with p:
        assert (await ClaudeService().classify("צריך לעצור את העבודות"))["verdict"] == "DECISION"


async def test_classify_recovers_verdict_from_broken_json():
    p, _ = _classify_patch('{"verdict": "NOT_DECISION", "reply": "תשובה עם "ציטוט" שבור"}')
    with p:
        assert (await ClaudeService().classify("שלום"))["verdict"] == "NOT_DECISION"


async def test_classify_sanitizes_quotes_in_outgoing_text():
    p, fake = _classify_patch('{"verdict": "DECISION"}')
    with p:
        await ClaudeService().classify('הקבלן "אלקטרה" יחליף את השנאי')
    user_msg = fake.messages[1]["content"]
    assert '"' not in user_msg
    assert "״" in user_msg


async def test_analyze_parses_json_wrapped_in_prose():
    async def _fake(usage, messages=None, **kw):
        return "בטח! הנה הניתוח:\n" + _VALID_ANALYZE_JSON
    with patch("app.services.claude_service.llm_chat", side_effect=_fake):
        decision = await ClaudeService().analyze("צריך להחליף שנאי", "project_manager")
    assert decision["type"] == "NORMAL"


def test_extract_json_variants():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('טקסט {"a": 1} עוד') == {"a": 1}
    with pytest.raises(json.JSONDecodeError):
        _extract_json("אין כאן JSON")
