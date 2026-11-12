"""The answer plan — ONE model call decides which tool answers a message.

Before: a routing call picked project / knowledge / decision, then ask_router
re-decided the same thing from keyword lists, then project_tools asked the model
for the intent a second time, and a knowledge question paid for a separate
query-expansion call. Here the model picks one tool from TOOLS and fills its
argument; code runs the tool. The plan is authoritative: nothing downstream
second-guesses it from keywords.

Rollout is a switch, not a rewrite: SystemFlag ENGINE_FLAG = off | admins | all
(`/engine` in Telegram, admins only). When the plan cannot be made or parsed the
caller falls back to the old routing, so a bad model reply never costs an answer.

Pure helpers (TOOLS, build_prompt, parse_plan, shortcut_plan, engine_applies)
carry the logic and the tests; the two async functions only do I/O.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENGINE_FLAG = "answer_engine"
MODES = ("off", "admins", "all")
# Admins first: they compare real answers before everyone is moved over.
DEFAULT_MODE = "admins"
MODE_CACHE_SECONDS = 30

CHAT_REPLY = "שלום! אפשר לשאול אותי על פרויקטים, נהלים והחלטות — או לכתוב החלטה לתיעוד."


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str                 # project | decisions | knowledge | decision | chat
    intent: str | None        # project_tools intent this tool runs, for kind == "project"
    arg: str                  # what `arg` holds, in the prompt's words ("" = always null)
    description: str


TOOLS: tuple[Tool, ...] = (
    Tool("project", "project", "by_identifier", "שם או מזהה הפרויקט כפי שנכתב",
         "שאלה על פרויקט אחד: סטטוס, שלב, מנהל, תאריכים, סיכונים, עדכון שבועי — או שם פרויקט בלבד"),
    Tool("manager_projects", "project", "by_manager", "שם המנהל/ת כפי שנכתב",
         "הפרויקטים של מנהל/ת מסוים, או כמה פרויקטים הוא/היא מנהל/ת"),
    Tool("projects_by_year", "project", "by_year", 'שנה, למשל "2026"',
         "פרויקטים שמסתיימים / מתחשמלים בשנה מסוימת"),
    Tool("count_projects", "project", "count_by_type", "סוג הפרויקט (הקמה, הרחבה...) או null לכל הפרויקטים",
         "כמה פרויקטים יש — בסך הכל או מסוג מסוים"),
    Tool("delayed_projects", "project", "list_delayed", "סוג הפרויקט אם הוזכר, אחרת null",
         "אילו פרויקטים בעיכוב / מאחרים"),
    Tool("risk_projects", "project", "list_risks", "",
         "אילו פרויקטים בסיכון, מה הסיכונים בפרויקטים"),
    Tool("projects_overview", "project", "general", "",
         "שאלה כללית על כלל הפרויקטים שאינה מתאימה לכלי אחר"),
    Tool("my_decisions", "decisions", None, "",
         "שאלה על החלטות שכבר תועדו: מה הוחלט, אילו החלטות ממתינות, היסטוריית החלטות"),
    Tool("knowledge", "knowledge", None, "",
         "שאלה על נהלים, מסמכים, הנחיות ומידע כללי — לא על פרויקט מסוים"),
    Tool("record_decision", "decision", None, "",
         "ההודעה עצמה היא החלטה / הנחיה / קביעת נוהל / בעיה הדורשת פעולה — לא שאלה"),
    Tool("chat", "chat", None, "",
         "ברכה, תודה, בדיחה, שיחה כללית"),
)
_BY_NAME = {t.name: t for t in TOOLS}

_EXAMPLES: tuple[tuple[str, dict], ...] = (
    ("רעות", {"tool": "project", "arg": "רעות"}),
    ("טרומן", {"tool": "project", "arg": "טרומן"}),
    ("רמת חובב", {"tool": "project", "arg": "רמת חובב"}),
    ("מה סטטוס רעות?", {"tool": "project", "arg": "רעות"}),
    ("מי מנהל את פרויקט רמת חובב?", {"tool": "project", "arg": "רמת חובב"}),
    ("מה העדכון השבועי של טרומן?", {"tool": "project", "arg": "טרומן"}),
    ("כמה פרויקטים מנהלת ענת אוברקוביץ?", {"tool": "manager_projects", "arg": "ענת אוברקוביץ"}),
    ("אלו פרויקטים מנהלת רחלי?", {"tool": "manager_projects", "arg": "רחלי"}),
    ("אילו פרויקטים מסתיימים ב-2026?", {"tool": "projects_by_year", "arg": "2026"}),
    ("כמה פרויקטי הקמה יש?", {"tool": "count_projects", "arg": "הקמה"}),
    ("כמה פרויקטים יש בסך הכל?", {"tool": "count_projects", "arg": None}),
    ("אילו פרויקטי הרחבה מאחרים?", {"tool": "delayed_projects", "arg": "הרחבה"}),
    ("מה הסיכונים בפרויקטים?", {"tool": "risk_projects", "arg": None}),
    ("מה ההחלטות שממתינות לאישור שלי?", {"tool": "my_decisions", "arg": None}),
    ("מה הנוהל להחלפת טרנספורמטור?",
     {"tool": "knowledge", "arg": None, "search": "נוהל החלפת טרנספורמטור שנאי השנאה"}),
    ("צריך להחליף שנאי ישן בתחנה 5", {"tool": "record_decision", "arg": None}),
    ("החלטנו לדחות את תאריך ההפעלה של תחנת רמת חובב", {"tool": "record_decision", "arg": None}),
    ("בכל תהליך תכנון היתר תבוצע פגישת התנעה עם המועצה", {"tool": "record_decision", "arg": None}),
    ("שלום", {"tool": "chat", "arg": None, "reply": "שלום! במה אפשר לעזור?"}),
)


@dataclass(frozen=True)
class Plan:
    tool: str
    arg: str | None = None
    search: str | None = None     # knowledge only: extra search terms (replaces the expansion call)
    reply: str | None = None      # chat only

    @property
    def kind(self) -> str:
        return _BY_NAME[self.tool].kind

    @property
    def intent(self) -> str | None:
        return _BY_NAME[self.tool].intent

    def as_routing(self) -> dict:
        """The shape the Telegram handler already understands (route / intent / param)."""
        route = {"project": "project", "decisions": "knowledge", "knowledge": "knowledge",
                 "decision": "decision", "chat": None}[self.kind]
        return {"route": route, "intent": self.intent or "general", "param": self.arg}


def build_prompt(text: str) -> str:
    tools = "\n".join(
        f'- "{t.name}" — {t.description}. arg = {t.arg or "null"}' for t in TOOLS)
    examples = "\n".join(
        f'"{q}" → {json.dumps(a, ensure_ascii=False)}' for q, a in _EXAMPLES)
    return (
        "אתה מתכנן תשובות במערכת ניהול פרויקטי תשתיות חשמל (תחנות משנה).\n"
        "קבל הודעת משתמש בעברית, בחר כלי אחד והחזר JSON בלבד — ללא markdown, ללא הסברים.\n\n"
        'פורמט מחייב: {"tool": "...", "arg": ...}\n\n'
        f"הכלים:\n{tools}\n\n"
        "כללים:\n"
        "- שם בלבד (מילה אחת עד שלוש, בלי פועל) הוא שם של פרויקט → project. לעולם לא record_decision.\n"
        "- משפט שלם שהוא הצהרה, הנחיה או תיאור של מה שיקרה (לא שאלה) → record_decision, גם אם מוזכר שם פרויקט.\n"
        "- שמות פרויקטים ומנהלים: העתק בדיוק כפי שנכתבו, בלי להוסיף או להסיר מילים.\n"
        '- לכלי knowledge בלבד הוסף "search": מילות חיפוש — מונחי השאלה עם מילים נרדפות וקיצורים רלוונטיים.\n'
        '- לכלי chat בלבד הוסף "reply": תשובה קצרה ומנומסת בעברית.\n\n'
        f"דוגמאות:\n{examples}\n\n"
        f"הודעה: {text}\n\nJSON:"
    )


_YEAR = re.compile(r"(?:19|20)\d{2}")


def _clean(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in ("", "null", "none") else value


def parse_plan(raw: str) -> Plan | None:
    """The model's reply → a valid Plan, or None (the caller falls back to the old router)."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    tool = _BY_NAME.get(str(data.get("tool") or "").strip())
    if tool is None:
        return None
    arg = _clean(data.get("arg")) if tool.arg else None

    if tool.name in ("project", "manager_projects") and not arg:
        return None                       # a lookup with nothing to look up
    if tool.name == "projects_by_year":
        year = _YEAR.search(arg or "")
        if not year:
            return None
        arg = year.group()
    return Plan(
        tool=tool.name,
        arg=arg,
        search=_clean(data.get("search")) if tool.name == "knowledge" else None,
        reply=_clean(data.get("reply")) if tool.name == "chat" else None,
    )


def shortcut_plan(text: str) -> Plan | None:
    """Messages that need no model: the same decision openers the old router short-cuts."""
    from app.services.telegram_routing import (
        _DECISION_PREFIXES, _QUESTION_PREFIXES, _STANDING_DIRECTIVE_VERBS,
    )
    t = text.strip()
    if t.endswith("?"):
        return None
    if any(t.startswith(p) for p in _DECISION_PREFIXES):
        return Plan("record_decision")
    if (not any(t.startswith(q) for q in _QUESTION_PREFIXES)
            and any(v in t for v in _STANDING_DIRECTIVE_VERBS)):
        return Plan("record_decision")
    return None


async def bare_name_plan(text: str, session) -> Plan | None:
    """A project name typed on its own → that project's card, with no model call.

    The most-used gesture in the bot, and the model is the wrong tool for it: the
    first version of this engine asked the model, which read "צחר" as a statement
    and planned record_decision — the user got a decision analysis instead of the
    card (2026-11-12). The database knows whether it is a project; ask it."""
    from app.services.telegram_routing import looks_like_bare_name
    if session is None or not looks_like_bare_name(text):
        return None
    try:
        from app.services.project_tools import find_projects_by_identifier
        found = await find_projects_by_identifier(text.strip(), session)
    except Exception:
        logger.warning("bare-name lookup failed", exc_info=True)
        return None
    return Plan("project", text.strip()) if found else None


async def make_plan(text: str, conversation_context: list[dict] | None = None,
                    session=None) -> Plan | None:
    """At most one model call (usage "message_routing"). None when it failed or made no sense."""
    named = await bare_name_plan(text, session)
    if named is not None:
        logger.info("answer plan: bare project name → project(%r), no model call", named.arg)
        return named
    quick = shortcut_plan(text)
    if quick is not None:
        return quick

    from app.services.llm_router import llm_chat

    if conversation_context:
        lines = "\n".join(
            f"{'User' if e['role'] == 'user' else 'Bot'}: {e['content']}" for e in conversation_context)
        effective = f"הקשר שיחה:\n{lines}\n\nהודעה נוכחית: {text}"
    else:
        effective = text
    try:
        t0 = time.perf_counter()
        raw = await llm_chat(
            "message_routing",
            messages=[{"role": "user", "content": build_prompt(effective)}],
            max_tokens=400,
            temperature=0.0,
            reasoning_effort="low",
        )
        plan = parse_plan(raw)
        # Nobody records a decision in one or two words. A model that says so has
        # misread a name: look it up as a project instead of opening a decision.
        if plan is not None and plan.tool == "record_decision" and len(text.split()) <= 2:
            plan = Plan("project", text.strip())
        logger.info("⏱ answer plan: %dms → %s", int((time.perf_counter() - t0) * 1000),
                    f"{plan.tool}({plan.arg!r})" if plan else f"unparsed {raw[:120]!r}")
        return plan
    except Exception as e:
        logger.warning("answer plan failed: %s", e)
        return None


# ── the switch ───────────────────────────────────────────────────────────────

def engine_applies(mode: str | None, user) -> bool:
    mode = mode if mode in MODES else DEFAULT_MODE
    if mode == "all":
        return True
    return mode == "admins" and bool(getattr(user, "is_admin", False))


_mode_cache: tuple[str, float] | None = None


async def engine_mode(session) -> str:
    """off | admins | all, cached for MODE_CACHE_SECONDS. Unreadable flag → off (the old path)."""
    global _mode_cache
    now = time.monotonic()
    if _mode_cache and now - _mode_cache[1] < MODE_CACHE_SECONDS:
        return _mode_cache[0]
    try:
        from sqlalchemy import select
        from app.models import SystemFlag
        value = await session.scalar(select(SystemFlag.value).where(SystemFlag.key == ENGINE_FLAG))
        mode = value if value in MODES else DEFAULT_MODE
    except Exception:
        logger.warning("answer_engine flag unreadable — using the old path", exc_info=True)
        return "off"
    _mode_cache = (mode, now)
    return mode


async def set_engine_mode(session, mode: str) -> None:
    global _mode_cache
    if mode not in MODES:
        raise ValueError(f"unknown engine mode {mode!r}")
    from app.models import SystemFlag
    row = await session.get(SystemFlag, ENGINE_FLAG)
    if row is None:
        session.add(SystemFlag(key=ENGINE_FLAG, value=mode))
    else:
        row.value = mode
    await session.commit()
    _mode_cache = (mode, time.monotonic())


async def engine_on(session, user) -> bool:
    return engine_applies(await engine_mode(session), user)
