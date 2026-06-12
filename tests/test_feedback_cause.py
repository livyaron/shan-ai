"""👎 follow-up: cause keyboard structure and taxonomy mapping."""
from app.services.telegram_polling import _cause_keyboard, CAUSE_MAP


def test_cause_keyboard_has_three_causes_plus_skip():
    kb = _cause_keyboard(42)
    flat = [b for row in kb.inline_keyboard for b in row]
    datas = [b.callback_data for b in flat]
    assert "lfc:42:WRONG_PROJECT" in datas
    assert "lfc:42:MISSING_DATA" in datas
    assert "lfc:42:HALLUCINATION" in datas
    assert "lfc:42:SKIP" in datas


def test_cause_map_values_match_taxonomy():
    assert set(CAUSE_MAP.keys()) == {"WRONG_PROJECT", "MISSING_DATA", "HALLUCINATION"}
