"""AI decision analysis service — routes to configured LLM (Groq or Gemma) via llm_router."""

import json
import logging
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

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
}"""


CLASSIFY_PROMPT = """אתה שומר סף למערכת ניהול החלטות בארגון תשתיות חשמל.
תפקידך: לבדוק אם הטקסט שנשלח הוא **החלטה ארגונית** הדורשת ניתוח, או לא.

כללים:
- DECISION: כל אחד מהבאים — גם אם מנוסח כהצהרה ולא כשאלה:
  * תיאור בעיה, פעולה נדרשת, או צומת החלטה שדורשת ניתוח — גם אם קצרה או חלקית
  * הכרזה ארגונית: עדכון על פרויקט, מכרז, קבלן, תחנת משנה, ציוד חשמלי, לוחות זמנים
  * עדכון מצב: "X ייצא בנפרד", "X יידחה", "X אושר", "X בוטל" — אלה מייצגים החלטות ארגוניות
  * כל משפט שמתאר שינוי, אישור, ביטול, דחייה, או הפרדה בהקשר עבודה
- NOT_DECISION: רק אחד מהבאים:
  * ברכה, שיחה חברתית, בדיחה, מילה בודדת ללא הקשר עבודה
  * שאלת מידע על נתונים קיימים במערכת (כמה החלטות יש, מה הסטטוס, תן לי סיכום, כמה קריטיות וכו׳)
  * שאלות על קבצים, נהלים, מסמכים
- UNCLEAR: יש תיאור של בעיה ארגונית אמיתית אך חסר מידע קריטי להבין מה ההחלטה הנדרשת.

חשוב מאוד:
- הצהרות ארגוניות (גם ללא סימן שאלה) = DECISION. "המכרז X ייצא בנפרד" = DECISION.
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
        """
        raw = await llm_chat(
            "decision_analysis",
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
            temperature=0.1,
            json_mode=True,
        )
        return json.loads(raw)

    async def analyze(self, problem: str, user_role: str, past_context: str = "") -> dict:
        """Send problem to configured LLM and return parsed decision JSON."""
        # Replace straight quotes with Hebrew geresh to avoid breaking JSON
        clean_problem = problem.replace('"', '״').replace("'", "׳")
        user_message = f"תפקיד המגיש: {user_role}\n\n"
        if past_context:
            user_message += f"החלטות עבר רלוונטיות:\n{past_context}\n\n"
        user_message += f"בעיה/החלטה:\n{clean_problem}"

        logger.info(f"שולח ל-LLM: {problem[:80]}...")

        raw = await llm_chat(
            "decision_analysis",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        )
        logger.info(f"תגובת Groq: {raw[:200]}")

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        decision = json.loads(raw)
        self._validate(decision)
        return decision

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
