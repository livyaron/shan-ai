"""AI decision analysis service - uses Groq (Llama 3.3 70B)."""

import json
import logging
from groq import AsyncGroq
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """אתה מנוע בינה מלאכותית לניהול החלטות עבור מערכת Shan-AI — פלטפורמה ארגונית לניהול החלטות טכניות בחטיבת תשתיות חשמל, טרנספורמטורים ותחנות משנה בקנה מידה גדול.

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

חשוב מאוד: כל שדות הטקסט (summary, recommended_action, assumptions, risks) חייבים להיות כתובים בעברית בלבד.

השב אך ורק עם אובייקט JSON תקין — ללא markdown, ללא הסברים, ללא טקסט מחוץ ל-JSON:

{
  "type": "INFO|NORMAL|CRITICAL|UNCERTAIN",
  "summary": "תיאור קצר בעברית",
  "recommended_action": "פעולה מומלצת בעברית",
  "confidence": 0.0-1.0,
  "requires_approval": true/false,
  "self_critique": {
    "assumptions": ["הנחה 1", "הנחה 2"],
    "risks": ["סיכון 1", "סיכון 2"]
  },
  "measurability": "MEASURABLE|PARTIAL|NOT_MEASURABLE"
}"""


class ClaudeService:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    async def analyze(self, problem: str, user_role: str, past_context: str = "") -> dict:
        """Send problem to Groq and return parsed decision JSON."""
        # Replace straight quotes with Hebrew geresh to avoid breaking JSON
        clean_problem = problem.replace('"', '״').replace("'", "׳")
        user_message = f"תפקיד המגיש: {user_role}\n\n"
        if past_context:
            user_message += f"החלטות עבר רלוונטיות:\n{past_context}\n\n"
        user_message += f"בעיה/החלטה:\n{clean_problem}"

        logger.info(f"שולח ל-Groq: {problem[:80]}...")

        response = await self.client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        )

        raw = response.choices[0].message.content.strip()
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
        required = {"type", "summary", "recommended_action", "confidence",
                    "requires_approval", "self_critique", "measurability"}
        missing = required - decision.keys()
        if missing:
            raise ValueError(f"תגובה חסרת שדות: {missing}")
        if decision["type"] not in ("INFO", "NORMAL", "CRITICAL", "UNCERTAIN"):
            raise ValueError(f"סוג החלטה לא תקין: {decision['type']}")
        if decision["measurability"] not in ("MEASURABLE", "PARTIAL", "NOT_MEASURABLE"):
            raise ValueError(f"ערך measurability לא תקין: {decision['measurability']}")
