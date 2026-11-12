"""The answer-plan engine (stage C, 2026-11-12). No database, no model.

One call picks a tool and its argument; ask_router runs exactly that tool. The
old routing stays as the fallback whenever a plan cannot be made.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import answer_agent as agent
from app.services import ask_router
from app.services.answer_agent import Plan, parse_plan


# ------------------------------------------------------------------ the catalogue

def test_every_project_tool_maps_to_an_intent_project_tools_knows():
    known = {"by_identifier", "by_manager", "by_year", "count_by_type",
             "list_delayed", "list_risks", "general"}
    project = [t for t in agent.TOOLS if t.kind == "project"]
    assert {t.intent for t in project} == known
    assert all(t.intent is None for t in agent.TOOLS if t.kind != "project")
    assert len({t.name for t in agent.TOOLS}) == len(agent.TOOLS)


def test_the_prompt_lists_every_tool_and_every_example_is_a_valid_plan():
    prompt = agent.build_prompt("מה סטטוס רעות?")
    for tool in agent.TOOLS:
        assert f'"{tool.name}"' in prompt
    assert prompt.rstrip().endswith("JSON:") and "מה סטטוס רעות?" in prompt
    import json
    for question, answer in agent._EXAMPLES:
        assert parse_plan(json.dumps(answer, ensure_ascii=False)) is not None, question


# ------------------------------------------------------------------ parsing the model's reply

@pytest.mark.parametrize("raw,expected", [
    ('{"tool": "project", "arg": "רעות"}', Plan("project", "רעות")),
    ('```json\n{"tool":"manager_projects","arg":"רחלי"}\n```', Plan("manager_projects", "רחלי")),
    ('{"tool":"count_projects","arg":null}', Plan("count_projects", None)),
    ('{"tool":"count_projects","arg":"null"}', Plan("count_projects", None)),
    ('{"tool":"projects_by_year","arg":"בשנת 2026"}', Plan("projects_by_year", "2026")),
    ('{"tool":"risk_projects","arg":"משהו"}', Plan("risk_projects", None)),      # takes no arg
    ('{"tool":"knowledge","arg":null,"search":"נוהל שנאי"}', Plan("knowledge", None, search="נוהל שנאי")),
    ('{"tool":"chat","arg":null,"reply":"שלום!"}', Plan("chat", None, reply="שלום!")),
    ('{"tool":"project","arg":"x","search":"s","reply":"r"}', Plan("project", "x")),  # extras dropped
])
def test_a_valid_reply_becomes_a_plan(raw, expected):
    assert parse_plan(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "אין לי מושג", "{not json}", '["project"]',
    '{"tool": "delete_everything", "arg": "x"}',
    '{"tool": "project", "arg": null}',             # a lookup with nothing to look up
    '{"tool": "manager_projects", "arg": ""}',
    '{"tool": "projects_by_year", "arg": "בקרוב"}',  # no year in it
    '{"route": "project", "intent": "by_identifier"}',
])
def test_a_reply_that_makes_no_sense_is_no_plan(raw):
    assert parse_plan(raw) is None


def test_plan_speaks_the_handlers_routing_shape():
    assert Plan("project", "רעות").as_routing() == {"route": "project", "intent": "by_identifier", "param": "רעות"}
    assert Plan("my_decisions").as_routing()["route"] == "knowledge"   # answered by ask_router
    assert Plan("record_decision").as_routing()["route"] == "decision"
    assert Plan("chat").as_routing()["route"] is None


# ------------------------------------------------------------------ making the plan

@pytest.mark.parametrize("text", ["יש לסגור את פרויקט X", "החלטנו לדחות את ההפעלה",
                                  "בכל תהליך תכנון תבוצע פגישת התנעה"])
def test_decision_openers_need_no_model(text):
    assert agent.shortcut_plan(text) == Plan("record_decision")


@pytest.mark.parametrize("text", ["מה סטטוס רעות?", "מתי תבוצע ההפעלה", "רעות", "יש לסגור את הפרויקט?"])
def test_questions_are_not_short_cut(text):
    assert agent.shortcut_plan(text) is None


@pytest.mark.asyncio
async def test_make_plan_is_one_model_call(monkeypatch):
    llm = AsyncMock(return_value='{"tool":"delayed_projects","arg":"הרחבה"}')
    monkeypatch.setattr("app.services.llm_router.llm_chat", llm)
    plan = await agent.make_plan("אילו פרויקטי הרחבה מאחרים?",
                                 conversation_context=[{"role": "user", "content": "קודם"}])
    assert plan == Plan("delayed_projects", "הרחבה")
    llm.assert_awaited_once()
    assert llm.call_args.args[0] == "message_routing"
    assert "קודם" in llm.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_a_failed_or_unparsed_plan_is_none_so_the_old_router_takes_over(monkeypatch):
    monkeypatch.setattr("app.services.llm_router.llm_chat", AsyncMock(side_effect=RuntimeError("down")))
    assert await agent.make_plan("מה סטטוס רעות?") is None
    monkeypatch.setattr("app.services.llm_router.llm_chat", AsyncMock(return_value="לא יודע"))
    assert await agent.make_plan("מה סטטוס רעות?") is None


# ------------------------------------------------------------------ the switch

@pytest.mark.parametrize("mode,is_admin,on", [
    ("off", True, False), ("off", False, False),
    ("admins", True, True), ("admins", False, False),
    ("all", True, True), ("all", False, True),
    (None, True, True), (None, False, False),          # unset → DEFAULT_MODE (admins)
    ("garbage", False, False),
])
def test_who_gets_the_engine(mode, is_admin, on):
    assert agent.engine_applies(mode, SimpleNamespace(is_admin=is_admin)) is on


@pytest.mark.asyncio
async def test_an_unreadable_flag_means_the_old_path(monkeypatch):
    monkeypatch.setattr(agent, "_mode_cache", None)

    class _Broken:
        async def scalar(self, *a, **k):
            raise RuntimeError("db down")

    assert await agent.engine_mode(_Broken()) == "off"


@pytest.mark.asyncio
async def test_set_mode_refuses_an_unknown_value():
    with pytest.raises(ValueError):
        await agent.set_engine_mode(None, "everyone")


# ------------------------------------------------------------------ ask_router obeys the plan

class _Session:
    """No pins, overrides or aliases: every pre-rule lookup comes back empty."""
    async def execute(self, *a, **k):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None),
                               scalar_one_or_none=lambda: None, first=lambda: None, all=lambda: [])

    async def scalar(self, *a, **k):
        return None


@pytest.fixture
def quiet_router(monkeypatch):
    """ask_router with its DB-backed pre-rules and trace switched off."""
    monkeypatch.setattr(ask_router, "_write_trace", AsyncMock())
    from app.services import knowledge_service as ks, project_tools
    monkeypatch.setattr(ks, "_ensure_eval_caches", AsyncMock())
    monkeypatch.setattr(ks, "_DB_CORRECTION_PINS_CACHE", {})
    monkeypatch.setattr(ks, "_DB_INTENT_OVERRIDES_CACHE", {})
    monkeypatch.setattr(ks, "_DB_PROJECT_ALIASES_CACHE", {}, raising=False)
    monkeypatch.setattr("app.services.memory_service.get_relevant_memories", AsyncMock(return_value=[]))
    calls = {}

    async def project(question, session, user_data, **kw):
        calls["project"] = kw
        return "PROJECT", 1

    async def rag(question, session, user_id, **kw):
        calls["rag"] = kw
        return {"answer": "RAG", "log_id": 2}

    monkeypatch.setattr(project_tools, "answer_project_query", project)
    monkeypatch.setattr(ks, "answer_with_full_context", rag)
    monkeypatch.setattr(ks, "get_decisions_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(ks, "answer_decisions_question", AsyncMock(return_value="DECISIONS"))
    monkeypatch.setattr(ask_router, "_log_query", AsyncMock(return_value=3))
    return calls


@pytest.mark.asyncio
async def test_the_plan_beats_the_keyword_lists(quiet_router):
    # "החלטות" in the text used to force the decisions branch whatever was asked.
    result = await ask_router.route("אילו פרויקטים מאחרים בגלל החלטות רגולציה?", _Session(), 1,
                                    log_to_db=False, plan=Plan("delayed_projects", None))
    assert result.path == "project_tools"
    assert quiet_router["project"]["precomputed_intent"] == "list_delayed"
    assert quiet_router["project"]["intent_is_final"] is True


@pytest.mark.asyncio
async def test_overview_is_final_and_not_detected_again(quiet_router):
    await ask_router.route("ספר לי על הפרויקטים", _Session(), 1, log_to_db=False,
                           plan=Plan("projects_overview"))
    assert quiet_router["project"]["precomputed_intent"] == "general"
    assert quiet_router["project"]["intent_is_final"] is True


@pytest.mark.asyncio
async def test_knowledge_plan_skips_project_tools_and_carries_its_search_terms(quiet_router):
    # "מנהל" is a project keyword: the old dispatch sent this to project_tools.
    result = await ask_router.route("מה הנוהל למינוי מנהל פרויקט?", _Session(), 1, log_to_db=False,
                                    plan=Plan("knowledge", search="נוהל מינוי מנהל פרויקט"))
    assert result.path == "rag" and "project" not in quiet_router
    assert quiet_router["rag"]["search_terms"] == "נוהל מינוי מנהל פרויקט"


@pytest.mark.asyncio
async def test_decisions_plan_runs_the_decisions_branch_without_the_keyword(quiet_router):
    result = await ask_router.route("מה ממתין לאישור שלי?", _Session(), 1, log_to_db=False,
                                    plan=Plan("my_decisions"))
    assert result.path == "decision" and result.answer == "DECISIONS"


@pytest.mark.asyncio
async def test_without_a_plan_the_old_dispatch_is_untouched(quiet_router):
    result = await ask_router.route("מה סטטוס פרויקט רעות?", _Session(), 1, log_to_db=False)
    assert result.path == "project_tools"
    assert quiet_router["project"]["intent_is_final"] is False
    result = await ask_router.route("xyz qqq?", _Session(), 1, log_to_db=False)
    assert result.path == "rag" and quiet_router["rag"]["search_terms"] is None


# ------------------------------------------------------------------ wiring

def test_the_bot_and_the_web_screen_use_the_engine_with_the_old_router_as_fallback():
    from app.routers import ask
    from app.services import telegram_polling as tp
    handler = inspect.getsource(type(tp.telegram_bot).handle_message)
    assert "_agent.engine_on(session, user)" in handler
    assert "plan.as_routing() if plan is not None else" in handler
    assert "plan=plan" in handler
    web = inspect.getsource(ask.ask_query)
    assert "answer_agent.engine_on(session, current_user)" in web and "plan=plan" in web


def test_the_plan_search_terms_replace_the_expansion_call():
    from app.services import knowledge_service as ks
    src = inspect.getsource(ks.answer_with_full_context)
    assert src.index("elif search_terms:") < src.index("await _expand_query(search_question)")


# ------------------------------------------------------------------ a project name on its own

@pytest.mark.parametrize("text", ["צחר", "רמת חובב", "תחנת משנה רעות", "  טרומן  "])
def test_a_short_name_is_a_bare_name(text):
    from app.services.telegram_routing import looks_like_bare_name
    assert looks_like_bare_name(text)


@pytest.mark.parametrize("text", [
    "שלום", "תודה", "כן", "ok", "צחר?", "מה סטטוס צחר", "מתי מסתיים טרומן",
    "החלטנו לדחות את צחר", "יש לסגור את הפרויקט", "", "123",
    "זה משפט ארוך מדי בשביל להיות שם של פרויקט",
])
def test_everything_else_is_not(text):
    from app.services.telegram_routing import looks_like_bare_name
    assert not looks_like_bare_name(text)


@pytest.mark.asyncio
async def test_a_project_name_gets_its_card_without_asking_the_model(monkeypatch):
    """The regression: "צחר" was planned as record_decision by the model."""
    found = AsyncMock(return_value=[{"project_identifier": "WBE-1", "name": "צחר"}])
    monkeypatch.setattr("app.services.project_tools.find_projects_by_identifier", found)
    llm = AsyncMock(return_value='{"tool":"record_decision","arg":null}')
    monkeypatch.setattr("app.services.llm_router.llm_chat", llm)

    plan = await agent.make_plan("צחר", session=object())

    assert plan == Plan("project", "צחר") and plan.intent == "by_identifier"
    llm.assert_not_awaited()
    found.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_short_text_that_is_no_project_still_goes_to_the_model(monkeypatch):
    monkeypatch.setattr("app.services.project_tools.find_projects_by_identifier", AsyncMock(return_value=[]))
    llm = AsyncMock(return_value='{"tool":"chat","arg":null,"reply":"בוקר טוב!"}')
    monkeypatch.setattr("app.services.llm_router.llm_chat", llm)
    assert await agent.make_plan("בוקר אור", session=object()) == Plan("chat", reply="בוקר טוב!")
    llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_or_two_words_are_never_a_decision_whatever_the_model_says(monkeypatch):
    monkeypatch.setattr("app.services.project_tools.find_projects_by_identifier", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.llm_router.llm_chat",
                        AsyncMock(return_value='{"tool":"record_decision","arg":null}'))
    assert await agent.make_plan("צחרר", session=object()) == Plan("project", "צחרר")
    # a real sentence is still a decision
    assert await agent.make_plan("צריך להחליף שנאי ישן בתחנה חמש בהקדם", session=object()) \
        == Plan("record_decision")


@pytest.mark.asyncio
async def test_a_failed_lookup_does_not_cost_the_answer(monkeypatch):
    monkeypatch.setattr("app.services.project_tools.find_projects_by_identifier",
                        AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr("app.services.llm_router.llm_chat",
                        AsyncMock(return_value='{"tool":"project","arg":"צחר"}'))
    assert await agent.make_plan("צחר", session=object()) == Plan("project", "צחר")


def test_both_callers_hand_the_session_to_the_planner():
    from app.routers import ask
    from app.services import telegram_polling as tp
    assert "session=session)" in inspect.getsource(type(tp.telegram_bot).handle_message).split("_agent.make_plan(")[1][:120]
    assert "make_plan(body.question, session=session)" in inspect.getsource(ask.ask_query)
