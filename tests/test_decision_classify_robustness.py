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


def test_extract_json_repairs_unescaped_inner_quotes():
    """The classic gotcha: the model echoes quoted Hebrew inside a value."""
    raw = '{"summary": "החלפת שנאי "ישן" בתחנה 12", "type": "NORMAL"}'
    parsed = _extract_json(raw)
    assert parsed["type"] == "NORMAL"
    assert 'שנאי' in parsed["summary"] and 'ישן' in parsed["summary"]


async def test_analyze_retries_once_on_bad_json():
    calls = []

    async def _fake(usage, messages=None, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return "אופס, לא JSON"
        return _VALID_ANALYZE_JSON

    with patch("app.services.claude_service.llm_chat", side_effect=_fake):
        decision = await ClaudeService().analyze("צריך להחליף שנאי", "project_manager")
    assert decision["type"] == "NORMAL"
    assert len(calls) == 2, "must retry exactly once with a format nudge"


async def test_analyze_raises_after_two_bad_replies():
    async def _fake(usage, messages=None, **kw):
        return "עדיין לא JSON"
    with patch("app.services.claude_service.llm_chat", side_effect=_fake):
        with pytest.raises(json.JSONDecodeError):
            await ClaudeService().analyze("צריך להחליף שנאי", "project_manager")


# ---------------------------------------------------------------------------
# analyze_only: a typed decision must never be lost
# ---------------------------------------------------------------------------

async def test_analyze_only_degrades_to_uncertain_on_failure(db_session):
    """Non-overload analysis failure → minimal UNCERTAIN preview, not an error."""
    from app.models import RoleEnum, User
    from app.services.decision_service import DecisionService

    user = User(username="dec_tester", telegram_id=999_000_444,
                role=RoleEnum.PROJECT_MANAGER)
    db_session.add(user)
    await db_session.commit()

    svc = DecisionService(db_session, None)

    async def _boom(*a, **kw):
        raise ValueError("totally broken reply")

    with patch.object(svc.claude, "analyze", side_effect=_boom), \
         patch("app.services.decision_service.embedding_service.get_similar_decisions",
               side_effect=RuntimeError("embed model missing")):
        result = await svc.analyze_only(user, "צריך לעצור את העבודות בתחנה 12")

    assert result["type"] == "UNCERTAIN"
    assert result["degraded"] is True
    assert result["requires_approval"] is True
    assert "תחנה 12" in result["summary"]


async def test_analyze_only_still_raises_on_overload(db_session):
    """Quota exhaustion must propagate so the caller enqueues to pending_decisions."""
    from app.models import RoleEnum, User
    from app.services.decision_service import DecisionService

    user = User(username="dec_tester2", telegram_id=999_000_445,
                role=RoleEnum.PROJECT_MANAGER)
    db_session.add(user)
    await db_session.commit()

    svc = DecisionService(db_session, None)

    async def _quota(*a, **kw):
        raise RuntimeError("429 rate limit exceeded for model")

    with patch.object(svc.claude, "analyze", side_effect=_quota), \
         patch("app.services.decision_service.embedding_service.get_similar_decisions",
               side_effect=RuntimeError("embed model missing")):
        with pytest.raises(RuntimeError):
            await svc.analyze_only(user, "צריך לעצור את העבודות")
