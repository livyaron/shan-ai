"""Learned answering rules (__global_instructions__) — quality gate, 2026-11-17. No database.

The first nightly feedback run stored generic rules ("ודא שהמידע מדויק"), a UI
request no prompt can satisfy (a button), and — when the merge LLM failed — a
blind set() append that scrambled the order the bot injects from.
"""
import asyncio
from types import SimpleNamespace

from app.services import optimization_service as opt
from app.services.optimization_service import clean_instructions, is_actionable_instruction

# Verbatim from the 17/11/2026 02:30 UTC run.
VAGUE = [
    "כאשר צריך לספק מספרים, ודא שהנתונים מעודכנים ומדויקים",
    "כאשר צריך לספק מידע, ודא שהמידע מדויק ומבוסס על נתונים אמיתיים",
    "כאשר משתמש שואל על פרויקט ספציפי, ודא שהפרויקט המתואר הוא הנכון",
    "כאשר משתמש מזין מזהה פרויקט, ודא שהמזהה מתייחס לפרויקט הנכון",
    "כאשר מציגים פרויקט, כלול כפתור או קישור שמאפשר כניסה לפרויקט",
    "ודא שהתשובה היא קצרה",
]
GOOD = [
    "כאשר המשתמש מזכיר מיקום גאוגרפי, ספק רשימה מלאה של פרויקטים באזור, לא רק אחד",
    "כאשר שם פרויקט לא מופיע בשורה, חפש אותו בשורות הסמוכות",
    "כאשר מזהה פרויקט, ודא שכל השדות הרלוונטיים מלאים; אם חסרים, ציין 'לא זמין' במקום להשאיר ריק",
]


def test_generic_and_ui_rules_are_dropped():
    assert [r for r in VAGUE if is_actionable_instruction(r)] == []


def test_concrete_rules_survive():
    assert all(is_actionable_instruction(r) for r in GOOD)


def test_clean_keeps_order_and_drops_duplicates():
    items = [GOOD[1], VAGUE[0], GOOD[0], "  " + GOOD[1] + " ", GOOD[2]]
    assert clean_instructions(items) == [GOOD[1], GOOD[0], GOOD[2]]


def test_raw_feedback_tag_is_judged_on_its_text():
    assert clean_instructions(["[פידבק גולמי] " + GOOD[0]]) == ["[פידבק גולמי] " + GOOD[0]]


class _Session:
    def __init__(self, row):
        self.row = row

    async def scalar(self, _q):
        return self.row

    def add(self, _r):
        raise AssertionError("must not create a row")


def test_failed_merge_leaves_the_stored_rules_untouched(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("json_validate_failed")
    monkeypatch.setattr("app.services.llm_router.llm_chat", boom)
    row = SimpleNamespace(synonyms=[GOOD[0], GOOD[1]])
    ok = asyncio.run(opt._merge_and_store_instructions(_Session(row), [GOOD[2]]))
    assert ok is False and row.synonyms == [GOOD[0], GOOD[1]]


def test_merge_output_is_cleaned(monkeypatch):
    async def fake(*a, **k):
        return '{"instructions": ["%s", "%s", "%s"]}' % (GOOD[1], VAGUE[0], GOOD[1])
    monkeypatch.setattr("app.services.llm_router.llm_chat", fake)
    row = SimpleNamespace(synonyms=[GOOD[0]])
    assert asyncio.run(opt._merge_and_store_instructions(_Session(row), [GOOD[1]])) is True
    assert row.synonyms == [GOOD[1]]


def test_bot_injects_the_head_of_the_list():
    from app.services.knowledge_service import _get_learned_instructions
    rules = [f"כאשר מבקשים דוח מספר {i}, הצג את כל השדות" for i in range(10)]
    row = SimpleNamespace(synonyms=rules)
    assert asyncio.run(_get_learned_instructions(_Session(row))) == rules[:6]


def test_retired_rule_is_gone_and_stays_gone():
    retired = "כאשר נשאלת שאלה על כמות, ענה במשפט אחד ללא רשימה מפורטת"
    assert not is_actionable_instruction(retired)
    assert clean_instructions([retired, GOOD[0]]) == [GOOD[0]]


def test_tidy_cleans_the_stored_list_in_order():
    class _S(_Session):
        committed = False

        async def commit(self):
            self.committed = True
    row = SimpleNamespace(synonyms=[VAGUE[0], GOOD[1], VAGUE[4], GOOD[0]])
    s = _S(row)
    assert asyncio.run(opt.tidy_stored_instructions(s)) == (4, 2)
    assert row.synonyms == [GOOD[1], GOOD[0]] and s.committed


def test_read_time_gate_skips_bad_stored_rules():
    from app.services.knowledge_service import _get_learned_instructions
    row = SimpleNamespace(synonyms=[VAGUE[0], GOOD[0], VAGUE[1], GOOD[1]])
    assert asyncio.run(_get_learned_instructions(_Session(row))) == [GOOD[0], GOOD[1]]


# ── relevance pick (2026-11-17): the rules closest to the question, not the first six ──

def test_pick_relevant_keeps_closest_in_priority_order():
    from app.services.knowledge_service import pick_relevant_rules
    rules = ["a", "b", "c", "d"]
    vecs = [[1, 0], [0, 1], [0.9, 0.1], [0.1, 0.9]]
    assert pick_relevant_rules(rules, vecs, [1, 0], k=2) == ["a", "c"]
    assert pick_relevant_rules(rules, vecs, [0, 1], k=2) == ["b", "d"]
    assert pick_relevant_rules(rules[:2], vecs[:2], [0, 1], k=6) == ["a", "b"]


def _ten_rules():
    return [f"כאשר מבקשים דוח מספר {i}, הצג את כל השדות" for i in range(10)]


def test_question_routes_through_the_embedding(monkeypatch):
    from app.services import knowledge_service as ks
    rules = _ten_rules()

    async def fake_rules(rs):
        return [[1.0, 0.0] if i >= 4 else [0.0, 1.0] for i, _ in enumerate(rs)]

    async def fake_embed(_q):
        return [1.0, 0.0]
    monkeypatch.setattr(ks, "_embed_rules", fake_rules)
    monkeypatch.setattr("app.services.embedding_service.embed", fake_embed)
    got = asyncio.run(ks._get_learned_instructions(_Session(SimpleNamespace(synonyms=rules)), "שאלה"))
    assert got == rules[4:10]


def test_embedding_failure_falls_back_to_the_head(monkeypatch):
    from app.services import knowledge_service as ks
    rules = _ten_rules()

    async def boom(_rs):
        raise RuntimeError("model not loaded")
    monkeypatch.setattr(ks, "_embed_rules", boom)
    got = asyncio.run(ks._get_learned_instructions(_Session(SimpleNamespace(synonyms=rules)), "שאלה"))
    assert got == rules[:6]
