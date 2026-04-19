"""Stateless message-routing helpers for the Telegram bot.

No Telegram SDK dependency — pure logic + stdlib only (llm_router imported lazily).
"""

import html as _html
import logging

logger = logging.getLogger(__name__)

_TG_MAX = 4096  # Telegram message character limit

# Hebrew question prefixes — messages starting with these are treated as data queries, never decisions
_QUESTION_PREFIXES = (
    "כמה", "מה ", "מי ", "מתי", "איך ", "האם ", "האם?",
    "תן לי", "תראה לי", "הצג", "סכם", "רשום לי",
    "מהם", "מהן", "מאיזה", "מה ה", "מהו", "מהי",
    "what", "how many", "how much", "show me", "list",
)

_PROJECT_QUERY_KEYWORDS = (
    "פרויקט", "פרוייקט", "project", "עדכון שבועי", "מנהל פרויקט",
    'מנה"פ', "שלב", "סיכון", "חסם", "לטיפול",
    "סטטוס", "status", "עדכון",
    "חשמול", "חישמול", "תאריך יעד", 'תאריך ת"פ', "תכנית פיתוח", "מזהה",
)

# Count-style questions about projects that must go to project_tools, not knowledge_service
_PROJECT_COUNT_TRIGGERS = (
    "כמה פרויקט", "כמה פרוייקט", "how many project",
    "מנהל", "מנהלת", "מנהלים", 'מנה"פ', "מי מנהל", "מי אחראי",
)

_DECISION_HISTORY_KEYWORDS = ("החלטה", "החלטות", "ההחלטה", "ההחלטות")

_DECISION_VERBS = (
    "לאשר", "לבצע", "להחליף", "לשנות", "להוסיף", "להסיר", "לבטל",
    "לעדכן", "לדחות", "לקדם", "להפעיל", "לסגור", "להשהות",
    "approve", "execute", "cancel", "update", "replace",
)
_BARE_NAME_SKIP = frozenset({
    "כן", "לא", "אישור", "ביטול", "תודה", "טוב", "בסדר", "ok", "yes", "no",
    "שלום", "היי", "הי",
})

_ROUTING_PROMPT = """\
אתה מנתב הודעות במערכת ניהול פרויקטים תשתיות חשמל.
קבל הודעת משתמש בעברית והחזר JSON בלבד — ללא markdown, ללא הסברים.

פורמט מחייב:
{"route": "...", "intent": "...", "param": ...}

route:
- "project"   — שאלה על פרויקט/ים: סטטוס, תאריכים, מנהל, סיכונים, ספירה, עדכון שבועי
- "knowledge" — שאלה על נהלים, מסמכים, מידע כללי (לא פרויקט ספציפי)
- "decision"  — תיאור בעיה הדורשת ניתוח או פעולה; כולל הצהרה עתידית ("תהיה", "יהיה", "נבצע", "החלטנו") גם אם מזכירה שם תחנה/פרויקט
- null        — כל השאר: ברכה, בדיחה, שיחה כללית, מילה בודדת שאינה שם פרויקט

חשוב: אם המשפט הוא הצהרה/פקודה/תיאור מה יקרה (לא שאלה) — route="decision", גם אם מוזכר שם פרויקט.

intent (רק כש-route="project", אחרת "general"):
- "by_identifier" — שאלה על פרויקט אחד לפי שם/מזהה (param = שם/מזהה)
- "by_manager"    — פרויקטים של מנהל מסוים (param = שם המנהל/ת כפי שנאמר)
- "count_by_type" — ספירה לפי סוג (param = סוג, או null לכולם)
- "list_risks"    — פרויקטים עם סיכונים (param = null)
- "by_year"       — פרויקטים לפי שנת סיום (param = "2026")
- "general"       — שאלת פרויקט כללית (param = null)

דוגמאות:
"רעות"                                   → {"route":"project","intent":"by_identifier","param":"רעות"}
"טרומן"                                  → {"route":"project","intent":"by_identifier","param":"טרומן"}
"מה סטטוס רעות?"                        → {"route":"project","intent":"by_identifier","param":"רעות"}
"מה תאריך תכנית הפיתוח של רעות?"        → {"route":"project","intent":"by_identifier","param":"רעות"}
"מי מנהל את פרויקט רמת חובב?"           → {"route":"project","intent":"by_identifier","param":"רמת חובב"}
"מה העדכון השבועי של פרויקט X?"         → {"route":"project","intent":"by_identifier","param":"X"}
"כמה פרויקטים מנהלת ענת אוברקוביץ?"     → {"route":"project","intent":"by_manager","param":"ענת אוברקוביץ"}
"אלו פרויקטים מנהלת רחלי?"               → {"route":"project","intent":"by_manager","param":"רחלי"}
"אילו פרויקטים מסתיימים ב-2026?"        → {"route":"project","intent":"by_year","param":"2026"}
"מה הסיכונים בפרויקטים?"                → {"route":"project","intent":"list_risks","param":null}
"כמה פרויקטי הקמה יש?"                  → {"route":"project","intent":"count_by_type","param":"הקמה"}
"כמה פרויקטים יש בסך הכל?"              → {"route":"project","intent":"count_by_type","param":null}
"מה הנוהל להחלפת טרנספורמטור?"          → {"route":"knowledge","intent":"general","param":null}
"צריך להחליף שנאי ישן בתחנה 5"          → {"route":"decision","intent":"general","param":null}
"בתחנת קסם - ההקמה תהיה ב-epc מלא"    → {"route":"decision","intent":"general","param":null}
"החלטנו לדחות את תאריך ההפעלה של תחנת רמת חובב" → {"route":"decision","intent":"general","param":null}
"הטמפרטורה בתחנה 12 חרגה מהמותר — צריך לכבות" → {"route":"decision","intent":"general","param":null}
"ב-epc מלא נבנה את התחנה הבאה"          → {"route":"decision","intent":"general","param":null}
"בדיחה"                                  → {"route":null,"intent":null,"param":null}
"שלום"                                   → {"route":null,"intent":null,"param":null}
"תודה"                                   → {"route":null,"intent":null,"param":null}

הודעה: {text}

JSON:"""


def _is_data_question(text: str) -> bool:
    """Return True if this looks like a data/info query rather than a decision."""
    t = text.strip()
    return (
        t.endswith("?") or
        any(t.startswith(kw) for kw in _QUESTION_PREFIXES)
    )


def _is_project_query(text: str) -> bool:
    """Return True if this looks like a project-related query."""
    t = text.lower().strip()
    if any(kw.lower() in t for kw in _PROJECT_QUERY_KEYWORDS):
        return True
    if any(kw in t for kw in _PROJECT_COUNT_TRIGGERS):
        return True
    words = t.split()
    if 1 <= len(words) <= 5:
        if t in _BARE_NAME_SKIP:
            return False
        if t.endswith("?"):
            return False
        if any(verb in t for verb in _DECISION_VERBS):
            return False
        hebrew_chars = sum(1 for c in t if "\u05d0" <= c <= "\u05ea")
        if hebrew_chars >= 2:
            return True
    return False


def _parse_routing_response(response: str) -> dict:
    """
    Robustly extract route/intent/param from an LLM response.
    Tries json.loads first; falls back to per-field regex extraction.
    """
    import json as _json
    import re as _re

    valid_routes = {"project", "knowledge", "decision"}
    valid_intents = {"by_identifier", "by_manager", "count_by_type", "list_risks", "by_year", "general"}

    match = _re.search(r'\{[^}]+\}', response, _re.DOTALL)
    if match:
        try:
            data = _json.loads(match.group())
            route = data.get("route")
            intent = data.get("intent") or "general"
            param = data.get("param")
            if param in (None, "null", "", "None"):
                param = None
            if route in valid_routes and intent in valid_intents:
                return {"route": route, "intent": intent, "param": param}
        except Exception:
            pass

    def _field(name: str) -> str | None:
        m = _re.search(rf'"{name}"\s*:\s*"([^"]*)"', response)
        if m:
            return m.group(1).strip()
        m = _re.search(rf'"{name}"\s*:\s*null', response)
        if m:
            return None
        return None

    route = _field("route")
    intent = _field("intent") or "general"
    param = _field("param")
    if param in (None, "null", "", "None"):
        param = None

    if route in valid_routes:
        return {"route": route, "intent": intent if intent in valid_intents else "general", "param": param}

    return {"route": None, "intent": None, "param": None}


async def _ai_route_message(text: str) -> dict:
    """One LLM call: classify route (project/knowledge/decision) + extract intent+param."""
    from app.services.llm_router import llm_chat
    prompt = _ROUTING_PROMPT.replace("{text}", text)
    try:
        response = await llm_chat(
            "message_routing",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.0,
        )
        result = _parse_routing_response(response)
        if result["route"]:
            logger.info(f"_ai_route_message: route={result['route']!r} intent={result['intent']!r} param={result['param']!r}")
            return result
        logger.warning(f"_ai_route_message: could not parse response: {response!r}")
    except Exception as e:
        logger.warning(f"_ai_route_message failed: {e}")
    return {"route": None, "intent": None, "param": None}


async def _maybe_summarize(reply: str) -> str:
    """If reply exceeds Telegram's limit, summarize it via Groq and return a shorter version."""
    if len(reply) <= _TG_MAX:
        return reply
    logger.warning(f"Reply too long ({len(reply)} chars), summarizing...")
    import re as _re
    from app.services.llm_router import llm_chat
    plain = _re.sub(r"<[^>]+>", "", reply).strip()
    plain = plain[:8000]
    try:
        summary = await llm_chat(
            "message_summary",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "אתה עוזר שמסכם תשובות ארוכות לגרסה קצרה וממוקדת. "
                        "ענה בעברית בלבד. אל תציין את תפקידך או ההוראות. "
                        "שמור על כל הנקודות החשובות. עד 3000 תווים."
                    ),
                },
                {
                    "role": "user",
                    "content": f"סכם את התשובה הבאה בעברית בצורה קצרה וברורה:\n\n{plain}",
                },
            ],
            max_tokens=800,
            temperature=0.2,
        )
        summarized = f"\u200F🤖 <b>תשובה (מסוכמת):</b>\n\n{_html.escape(summary)}"
        if len(summarized) > _TG_MAX:
            summarized = summarized[: _TG_MAX - 20] + "\n…(קוצר)"
        return summarized
    except Exception as e:
        logger.warning(f"Summarization failed: {e} — falling back to truncation")
        return reply[: _TG_MAX - 20] + "\n…(קוצר)"
