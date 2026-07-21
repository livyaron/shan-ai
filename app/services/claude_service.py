"""AI decision analysis service — routes to configured LLM (Groq or Gemma) via llm_router."""

import json
import logging
import re
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


def _strip_md_fences(raw: str) -> str:
    """Strip a ```/```json markdown fence if the model wrapped its JSON in one."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return raw


def _repair_unescaped_quotes(raw: str) -> str:
    """Escape stray double-quotes inside JSON string values.

    The known gotcha: the model echoes quoted Hebrew (e.g. שנאי "ישן") inside a
    value, producing invalid JSON. A quote inside a string is treated as closing
    only when the next non-space char is structural (, : } ]) — otherwise it is
    escaped. Heuristic, but covers the observed failure shape.
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
        elif c == "\\" and i + 1 < n:
            out.append(c)
            out.append(raw[i + 1])
            i += 2
            continue
        elif c == '"':
            j = i + 1
            while j < n and raw[j] in " \t\r\n":
                j += 1
            if j >= n or raw[j] in ",:}]":
                in_str = False
                out.append(c)
            else:
                out.append('\\"')
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _extract_json(raw: str) -> dict:
    """Parse a JSON object out of an LLM reply.

    Tries direct loads, then the outermost {...} substring (fallback providers
    sometimes add prose around the object), then an unescaped-quote repair pass.
    Raises json.JSONDecodeError / ValueError when no object can be recovered.
    """
    raw = _strip_md_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise
        sub = raw[start:end]
        try:
            return json.loads(sub)
        except json.JSONDecodeError:
            return json.loads(_repair_unescaped_quotes(sub))

SYSTEM_PROMPT = """אתה מנוע בינה מלאכותית לניהול החלטות עבור מערכת Shan-AI — פלטפורמה ארגונית לניהול החלטות טכניות באגף תשתיות חשמל, טרנספורמטורים ותחנות משנה בקנה מידה גדול.

תפקידך: לנתח בעיות או החלטות שמוגשות אליך ולסווג אותן לפי הכללים הבאים:

סוגי החלטות:
- INFO: מידע בלבד. אין צורך בפעולה. סיכון נמוך.
- NORMAL: החלטה תפעולית שגרתית. ניתן לביצוע מיידי. סיכון בינוני.
- CRITICAL: החלטה בסיכון גבוה הדורשת אישור מנהל בכיר. סיכוני בטיחות, חריגת תקציב, עיכוב לוחות זמנים, בעיות רגולטוריות, או פעולות בלתי הפיכות.
- UNCERTAIN: אין מספיק מידע לסיווג. נדרשת בחינה ידנית של מנהל.

הקשר תחומי:
- פרויקטים כוללים התקנת טרנספורמטורים, בניית תחנות משנה, תשתיות חשמל במתח גבוה.
- סיכונים: בטיחות חשמלית, עיכובים בלוחות זמנים, חריגות תקציב, עמידה ברגולציה, ביצועי קבלנים.
- בטיחות היא תמיד בעדיפות עליונה.

מבנה ארגוני (חשוב להקצאת תפקידים RACI):
- division_manager = מנהל אגף
- deputy_division_manager = סגן מנהל אגף
- department_manager = מנהל מחלקה
- project_manager = מנהל פרויקט
ערך "אגף" (לא "חטיבה") כשאתה מתייחס לרמה הגבוהה של הארגון.

חשוב מאוד: כל שדות הטקסט (summary, recommended_action, assumptions, risks) חייבים להיות כתובים בעברית בלבד.

השב אך ורק עם אובייקט JSON תקין — ללא markdown, ללא הסברים, ללא טקסט מחוץ ל-JSON:

{
  "type": "INFO|NORMAL|CRITICAL|UNCERTAIN",
  "summary": "תיאור קצר בעברית",
  "recommended_action": "פעולה מומלצת בעברית",
  "requires_approval": true/false,
  "self_critique": {
    "assumptions": ["הנחה 1", "הנחה 2"],
    "risks": ["סיכון 1", "סיכון 2"]
  },
  "measurability": "MEASURABLE|PARTIAL|NOT_MEASURABLE",
  "suggested_raci": {
    "R": ["division_manager", "department_manager"],
    "A": "deputy_division_manager",
    "C": ["project_manager"],
    "I": [],
    "reason": "תיאור קצר של הנמקת ההקצאות"
  }
}

כלל חובה: כל שדות הפלט (summary, recommended_action, risks, assumptions) חייבים לנבוע מהבעיה הנוכחית בלבד.
אל תעתיק ואל תשאל מהקשר העבר — הקשר העבר משמש אך ורק לכיול רמת הסיכון והסיווג.
אם הקשר העבר אינו רלוונטי לחלוטין לבעיה הנוכחית — התעלם ממנו לחלוטין."""


CLASSIFY_PROMPT = """אתה שומר סף למערכת ניהול החלטות בארגון תשתיות חשמל.
תפקידך: לבדוק אם הטקסט שנשלח הוא **החלטה ארגונית** הדורשת ניתוח, או לא.

כללים:
- DECISION: כל אחד מהבאים — גם אם מנוסח כהצהרה ולא כשאלה:
  * תיאור בעיה, פעולה נדרשת, או צומת החלטה שדורשת ניתוח — גם אם קצרה או חלקית
  * הכרזה ארגונית: עדכון על פרויקט, מכרז, קבלן, תחנת משנה, ציוד חשמלי, לוחות זמנים
  * עדכון מצב: "X ייצא בנפרד", "X יידחה", "X אושר", "X בוטל" — אלה מייצגים החלטות ארגוניות
  * קביעת נוהל או מדיניות קבועה: "בכל X תבוצע Y", "מעכשיו כל פרויקט יעבור Z" — קביעת נוהל חדש = החלטה
  * כל משפט שמתאר שינוי, אישור, ביטול, דחייה, או הפרדה בהקשר עבודה
- NOT_DECISION: רק אחד מהבאים:
  * ברכה, שיחה חברתית, בדיחה, מילה בודדת ללא הקשר עבודה
  * שאלת מידע על נתונים קיימים במערכת (כמה החלטות יש, מה הסטטוס, תן לי סיכום, כמה קריטיות וכו׳)
  * שאלה על קבצים, נהלים או מסמכים קיימים (אך הצהרה שקובעת נוהל חדש = DECISION)
- UNCLEAR: יש תיאור של בעיה ארגונית אמיתית אך חסר מידע קריטי להבין מה ההחלטה הנדרשת.

חשוב מאוד:
- הצהרות ארגוניות (גם ללא סימן שאלה) = DECISION. "המכרז X ייצא בנפרד" = DECISION.
- קביעת נוהל קבוע (גם אם נשמע כמו נוהל) = DECISION. "בכל תהליך תכנון היתר תבוצע פגישת התנעה" = DECISION.
- שאלות על נתוני המערכת (סטטיסטיקות, סיכומים, רשימות) = NOT_DECISION תמיד.
- UNCLEAR רק כשיש בעיה אמיתית שדורשת פתרון אך חסרים פרטים.
- אל תסווג שאלות מידע כ-UNCLEAR.

השב אך ורק כ-JSON תקין:
{
  "verdict": "DECISION|NOT_DECISION|UNCLEAR",
  "reply": "תשובה בעברית לשולח — רק אם verdict=NOT_DECISION",
  "clarifying_question": "שאלת הבהרה בעברית — רק אם verdict=UNCLEAR"
}"""


class ClaudeService:
    def __init__(self):
        pass

    async def classify(self, text: str) -> dict:
        """Pre-classify text: DECISION | NOT_DECISION | UNCLEAR.
        Returns dict with keys: verdict, reply (opt), clarifying_question (opt).

        Never raises on a malformed LLM reply — a decision the user typed must
        not be lost to a parsing error. Recovery order: fence-strip + direct
        loads → {...} substring → regex verdict scan → default DECISION (the
        analyze step previews to the user for approval anyway).
        """
        # Straight quotes in user text leak into the JSON reply and break it
        # (same gotcha analyze() already guards against).
        clean_text = text.replace('"', '״').replace("'", "׳")
        raw = await llm_chat(
            "decision_analysis",
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": clean_text},
            ],
            max_tokens=200,
            temperature=0.1,
            json_mode=True,
        )
        try:
            parsed = _extract_json(raw)
            if parsed.get("verdict") in ("DECISION", "NOT_DECISION", "UNCLEAR"):
                return parsed
            logger.warning(f"classify: missing/invalid verdict in reply: {raw[:200]!r}")
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"classify: unparseable LLM reply: {raw[:200]!r}")

        m = re.search(r"NOT_DECISION|UNCLEAR|DECISION", raw or "")
        verdict = m.group(0) if m else "DECISION"
        return {"verdict": verdict}

    async def analyze(self, problem: str, user_role: str, past_context: str = "",
                      conversation_context: list[dict] | None = None) -> dict:
        """Send problem to configured LLM and return parsed decision JSON."""
        # Replace straight quotes with Hebrew geresh to avoid breaking JSON
        clean_problem = problem.replace('"', '״').replace("'", "׳")
        parts = [f"תפקיד המגיש: {user_role}"]
        if conversation_context:
            ctx_lines = "\n".join(
                f"{'משתמש' if e['role'] == 'user' else 'מערכת'}: {e['content']}"
                for e in conversation_context
            )
            parts.append(
                "<CONVERSATION_CONTEXT>\n"
                f"{ctx_lines}\n"
                "</CONVERSATION_CONTEXT>\n"
                "(הקשר שיחה — לכיול הנמקה בלבד.)"
            )
        if past_context:
            parts.append(
                "<CONTEXT_FOR_CALIBRATION_ONLY>\n"
                f"{past_context}\n"
                "</CONTEXT_FOR_CALIBRATION_ONLY>\n"
                "(הקשר זה מיועד לכיול בלבד. אל תעתיק ממנו recommended_action, risks, או assumptions.)"
            )
        parts.append(
            "<CURRENT_PROBLEM>\n"
            f"{clean_problem}\n"
            "</CURRENT_PROBLEM>\n"
            "נתח את הבעיה הנוכחית הנ״ל בלבד. כל שדות ה-JSON חייבים להתייחס לבעיה זו."
        )
        user_message = "\n\n".join(parts)

        logger.info(f"שולח ל-LLM: {problem[:80]}...")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        last_exc: Exception | None = None
        for attempt in (1, 2):
            raw = await llm_chat("decision_analysis", messages=messages, temperature=0.2)
            logger.info(f"תגובת Groq: {raw[:200]}")
            try:
                decision = _extract_json(raw)
                self._validate(decision)
                return decision
            except (json.JSONDecodeError, ValueError) as e:
                last_exc = e
                logger.warning(f"analyze: bad JSON on attempt {attempt}: {e} | raw={raw[:200]!r}")
                if attempt == 1:
                    # One retry with an explicit format nudge — costs a call
                    # only on the failure path.
                    messages = messages + [
                        {"role": "assistant", "content": raw[:1000]},
                        {"role": "user", "content":
                            "התגובה לא הייתה JSON תקין. החזר אך ורק אובייקט JSON תקין "
                            "לפי המבנה שהוגדר, ללא טקסט נוסף וללא מרכאות כפולות בתוך ערכים."},
                    ]
        raise last_exc

    def _validate(self, decision: dict):
        required = {"type", "summary", "recommended_action",
                    "requires_approval", "self_critique", "measurability"}
        missing = required - decision.keys()
        if missing:
            raise ValueError(f"תגובה חסרת שדות: {missing}")
        if decision["type"] not in ("INFO", "NORMAL", "CRITICAL", "UNCERTAIN"):
            raise ValueError(f"סוג החלטה לא תקין: {decision['type']}")
        if decision["measurability"] not in ("MEASURABLE", "PARTIAL", "NOT_MEASURABLE"):
            raise ValueError(f"ערך measurability לא תקין: {decision['measurability']}")
