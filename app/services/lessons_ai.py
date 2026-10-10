"""מערכת לקחים — the six AI functions, on Shan-AI's own LLM router.

The Lovable app called Lovable's AI gateway (`LOVABLE_API_KEY`, gemini-3-flash)
from Supabase edge functions. That key is a Lovable-internal secret that cannot
be read or reused outside Lovable, so the same functions now run through
`llm_router.llm_chat("lessons_ai", …)`: Groq first, Google Gemma on failure
(switchable on the "🤖 מודל AI" page without code).

Ported from acumen-spark-hub/supabase/functions/*: prompts are kept word for
word. Two mechanical differences:
- Tool calling → JSON mode: the tool's schema is spelled out in the prompt and
  the reply is parsed and normalised to the same shape the React code reads.
- Context is packed to a character budget: Groq's on_demand tier counts
  prompt + max_tokens against 8,000 tokens/minute (CLAUDE.md, 2026-10-07).

Pure builders (`build_*`) are tested without a model or a database.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

USAGE = "lessons_ai"
# Hebrew runs ~2 chars/token (conservative, as insight_ai). 9,000 chars of
# context + system prompt + MAX_TOKENS stays under Groq's 8,000 TPM.
CONTEXT_CHARS = 9000
MAX_TOKENS = 1500

JSON_ONLY = "\n\nהחזר JSON תקין בלבד, בלי טקסט לפניו או אחריו, במבנה הבא:\n"


class AIUnavailable(Exception):
    """Both providers failed (quota, network). Shown to the user as a retry-later."""


# --------------------------------------------------------------------- helpers

def clip_lines(lines: list[str], budget: int) -> str:
    """Join lines until the budget; say how many were left out instead of cutting mid-line."""
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            out.append(f"... (עוד {len(lines) - i} שורות הושמטו בגלל אורך)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def parse_json(text: str) -> dict[str, Any]:
    """The model's JSON object, also when wrapped in ```json fences or prose."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {}
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                val = json.loads(text[start:end + 1])
                return val if isinstance(val, dict) else {}
            except ValueError:
                pass
    return {}


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def prefs_block(prefs: list[dict]) -> str:
    if not prefs:
        return ""
    return ("\n\n===== הוראות חובה — העדפות אישיות של המשתמש =====\n"
            "חובה עליך ליישם את כל ההעדפות הבאות. אם לא תיישם אותן, הניתוח ייחשב ככושל:\n"
            + "\n".join(f"• {p.get('preference_key')}: {p.get('preference_value')}" for p in prefs)
            + "\n===== סוף העדפות אישיות =====")


def insights_block(insights: list[dict]) -> str:
    if not insights:
        return ""
    lines = []
    for g in insights:
        ctx = g.get("context_type") or ""
        if g.get("context_value"):
            ctx += f": {g['context_value']}"
        lines.append(f"• [{ctx}] {g.get('insight_text')}")
    return ("\n\n===== הוראות חובה — תובנות גלובליות מאושרות =====\n"
            "חובה להתחשב בתובנות הבאות בניתוח. תובנות אלו אושרו על ידי מנהלי מערכת ומייצגות ידע ארגוני מבוסס:\n"
            + "\n".join(lines) + "\n===== סוף תובנות גלובליות =====")


def quality_scores(lesson_ids: list[int], impl_rows: list[dict]) -> dict[int, dict]:
    """Bayesian-shrunk implementation quality per lesson (analyze-lessons, verbatim maths)."""
    shrink_k, prior = 3, 1.0
    grouped: dict[int, dict] = {}
    for r in impl_rows:
        if not r.get("responded_at") or r.get("is_relevant") is None:
            continue
        score = 0 if r["is_relevant"] is False else (2 if r.get("is_implemented") else 1)
        g = grouped.setdefault(r["lesson_id"], {"sum": 0, "n": 0, "impl": 0})
        g["sum"] += score
        g["n"] += 1
        if r["is_relevant"] and r.get("is_implemented"):
            g["impl"] += 1
    out = {}
    for lid in lesson_ids:
        g = grouped.get(lid)
        if not g:
            out[lid] = {"responses": 0, "avg": None, "rate": None, "shrunk": prior}
        else:
            avg = g["sum"] / g["n"]
            out[lid] = {"responses": g["n"], "avg": avg, "rate": g["impl"] / g["n"],
                        "shrunk": (avg * g["n"] + prior * shrink_k) / (g["n"] + shrink_k)}
    return out


# --------------------------------------------------------------------- summarize-lessons

SUMMARY_FORMAT = """

כתוב בפורמט הבא:
📈 **מגמות מפתח**
(מגמות מבוססות פרויקטים ונתונים בפועל)

⚠️ **סיכונים**
(ציין חומרה: גבוה / בינוני / נמוך)

🛠️ **המלצות פעולה**
(המלצות ספציפיות וניתנות לביצוע)

📌 **נקודות קריטיות**
(דגשים חשובים לתשומת לב)

💡 **תובנות חוצות-פרויקטים**
(רק אם רלוונטי)

🎯 **Top 3 עדיפויות**
(3 הפעולות הכי חשובות כרגע)

ציון ביטחון: X% (0-100)"""


def _project_line(p: dict) -> str:
    return f"- {p.get('name')} (שלב: {p.get('stage')}, סוג: {p.get('type')}, ציוד: {', '.join(_list(p.get('equipment')))})"


def build_summarize(body: dict, prefs: list[dict], insights: list[dict]) -> tuple[str, str]:
    is_admin = body.get("role") == "admin"
    extra = prefs_block(prefs) + insights_block(insights)
    if is_admin:
        system = ("אתה מנתח לקחים ארגוניים מומחה. עליך לנתח את כל הלקחים שהתקבלו ולספק סיכום תובנות בעברית עבור מנהל המערכת.\n"
                  "חשוב: בסס את הניתוח אך ורק על נתונים אמיתיים. אל תכליל הצהרות גנריות או כלליות שאינן מבוססות על הלקחים.\n"
                  f"היה ביקורתי, מקצועי וישיר. כל המלצה חייבת להיות ניתנת לביצוע.{extra}{SUMMARY_FORMAT}")
    else:
        system = (f"אתה מנתח לקחים ארגוניים מומחה. עליך לנתח את הלקחים שהתקבלו ולספק סיכום תובנות בעברית עבור מנהל פרויקט בשם {body.get('userName')}.\n"
                  "חשוב מאוד: התמקד אך ורק בפרויקטים שהמנהל אחראי עליהם. אל תיתן תובנות על פרויקטים אחרים.\n"
                  "חשוב: בסס את הניתוח אך ורק על נתונים אמיתיים. אל תכליל הצהרות גנריות.\n"
                  f"היה ביקורתי, מקצועי וישיר. כל המלצה חייבת להיות ניתנת לביצוע.{extra}{SUMMARY_FORMAT}")
    lessons = [f'- "{l.get("title")}" (פרויקט: {l.get("project")}, שלב: {l.get("stage")}, קטגוריה: {l.get("category")}, סיכון: {l.get("risk")}, סטטוס: {l.get("status")})'
               for l in _list(body.get("lessons"))]
    projects = [_project_line(p) for p in _list(body.get("projects"))]
    lessons_text = clip_lines(lessons, CONTEXT_CHARS * 2 // 3)
    projects_text = clip_lines(projects, CONTEXT_CHARS // 3)
    if is_admin:
        user = (f"להלן רשימת כל הלקחים במערכת:\n{lessons_text}\n\nכל הפרויקטים הפעילים:\n{projects_text}\n\n"
                "אנא ספק סיכום תובנות כללי על כל הפרויקטים.")
    else:
        mine = _list(body.get("userProjects"))
        user = (f"להלן רשימת כל הלקחים במערכת (לצורך השוואה והפקת תובנות חוצות-פרויקטים):\n{lessons_text}\n\n"
                f"הפרויקטים שאני ({body.get('userName')}) מנהל:\n{clip_lines([_project_line(p) for p in mine], 1500)}\n\n"
                f"כל הפרויקטים הפעילים (לרקע):\n{projects_text}\n\n"
                f"אנא ספק סיכום תובנות הממוקד בפרויקטים שלי: {', '.join(_str(p.get('name')) for p in mine)}. "
                "ציין גם לקחים רלוונטיים מפרויקטים אחרים שכדאי שאשים לב אליהם.")
    return system, user


def sse_body(text: str) -> str:
    """One OpenAI-style SSE chunk + [DONE] — exactly what Dashboard.tsx parses."""
    chunk = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
    return f"data: {chunk}\n\ndata: [DONE]\n\n"


# --------------------------------------------------------------------- review-lesson

REVIEW_SCHEMA = ('{"quality_score": מספר 1-10, "is_duplicate": true/false, "duplicate_of": "כותרת הלקח הדומה או ריק", '
                 '"improvements": ["..."], "strengths": ["..."], "summary": "הערכה כללית קצרה בעברית"}')
DISTRIBUTION_SCHEMA = ('{"recommended_projects": [{"project_name": "...", "reason": "...", "priority": "high|medium|low"}], '
                       '"recommended_referents": [{"referent_id": "...", "referent_name": "...", "reason": "..."}], '
                       '"implementation_steps": ["..."], "attention_points": ["..."], "summary": "המלצה כללית קצרה בעברית"}')


def _projects_text(projects: list) -> str:
    if not projects:
        return "אין פרויקטים"
    return clip_lines([f'- "{p.get("name")}" (שלב: {p.get("stage")}, סוג: {p.get("type")}, תחנה: {p.get("station")}, '
                       f'ציוד: {", ".join(_list(p.get("equipment"))) or "לא צוין"})' for p in projects], CONTEXT_CHARS // 2)


def _referents_text(referents: list) -> str:
    if not referents:
        return "אין רפרנטים"
    return "\n".join(f'- "{r.get("name")}" [ID:{r.get("id")}] (שלבים: {", ".join(map(str, _list(r.get("stages")))) or "לא צוינו"})'
                     for r in referents)


def build_review(body: dict) -> tuple[str, str] | None:
    """(system, user) for the three review types, or None for an unknown type."""
    kind = body.get("type")
    lesson = body.get("lesson") or {}
    if kind == "pre_submit":
        system = """אתה מערכת AI לבדיקת איכות לקחים בפרויקטי בנייה ותשתיות.
תפקידך לבדוק לקח חדש לפני שליחתו לאישור ולתת המלצות.

בדוק:
1. האם הכותרת ברורה ומתארת את הלקח בצורה טובה?
2. האם התיאור מספיק מפורט וכולל הקשר, סיבות ופתרונות?
3. האם רמת הסיכון מתאימה לתוכן?
4. האם הקטגוריה נכונה?
5. האם הציוד/נושאים שנבחרו רלוונטיים?
6. האם יש לקחים דומים קיימים (כפילויות אפשריות)?
7. הצעות לשיפור הניסוח והמבנה.

היה ממוקד ותמציתי. כתוב בעברית.""" + JSON_ONLY + REVIEW_SCHEMA
        existing = _list(body.get("existingLessons"))
        existing_text = ("\nלקחים קיימים (לבדיקת כפילויות):\n" + clip_lines(
            [f'- "{l.get("title")}" ({l.get("category")}, {l.get("stage")})' for l in existing], CONTEXT_CHARS)
                         ) if existing else ""
        eq_ids = set(_list(lesson.get("equipmentIds")))
        eq_names = [e.get("name") for e in _list(body.get("equipment")) if e.get("id") in eq_ids]
        user = (f'לקח חדש לבדיקה:\nכותרת: "{lesson.get("title")}"\nתיאור: "{lesson.get("description") or "(ריק)"}"\n'
                f'פרויקט: "{lesson.get("projectName")}"\nשלב: "{lesson.get("stage")}"\nקטגוריה: "{lesson.get("category")}"\n'
                f'רמת סיכון: "{lesson.get("risk")}"\nציוד/נושאים: {", ".join(eq_names) if eq_names else "לא נבחרו"}\n{existing_text}')
        return system, user
    if kind not in {"post_approve", "re_analyze"}:
        return None
    head = (f'כותרת: "{lesson.get("title")}"\nתיאור: "{lesson.get("description")}"\nפרויקט מקור: "{lesson.get("projectName")}"\n'
            f'שלב: "{lesson.get("stage")}"\nקטגוריה: "{lesson.get("category")}"\nרמת סיכון: "{lesson.get("risk")}"\n'
            f'ציוד/נושאים: {", ".join(_list(lesson.get("equipmentNames"))) or "לא צוינו"}')
    projects = _projects_text(_list(body.get("projects")))
    referents = _referents_text(_list(body.get("referents")))
    if kind == "post_approve":
        system = """אתה מערכת AI לניהול לקחים בפרויקטי בנייה ותשתיות.
לקח אושר זה עתה. תפקידך לתת המלצות הפצה והטמעה, תוך הקפדה על הכללים הבאים:

1. המלץ רק על פרויקטים שנמצאים בדיוק באותו שלב של הלקח. אין להמליץ על פרויקטים בשלב מוקדם יותר או מאוחר יותר.
2. המלץ על רפרנטים רק אם הם גם רלוונטיים עסקית ללקח וגם משויכים לאותו שלב בדיוק.
3. עבור כל פרויקט ורפרנט, הסבר בקצרה למה הלקח רלוונטי.
4. ציין עדיפות הטמעה (גבוהה/בינונית/נמוכה) והמלצות יישום קונקרטיות.
5. ציין נקודות תשומת לב מיוחדות.

היה ממוקד ותמציתי. כתוב בעברית.""" + JSON_ONLY + DISTRIBUTION_SCHEMA
        user = (f"לקח שאושר:\n{head}\n\nפרויקטים פעילים:\n{projects}\n\nרפרנטים זמינים:\n{referents}\n\n"
                f'המלץ רק על פרויקטים ורפרנטים שמתאימים בדיוק לשלב "{lesson.get("stage")}".')
        return system, user
    system = """אתה מערכת AI לניהול לקחים בפרויקטי בנייה ותשתיות.
לקח כבר הופץ לפרויקטים ומנהלי פרויקטים נתנו פידבק עליו.
תפקידך לנתח מחדש את הלקח בהתחשב בפידבק שהתקבל ולעדכן את ההמלצות, תוך הקפדה על הכללים הבאים:

1. התחשב בפידבק של מנהלי הפרויקטים - מה היה רלוונטי ומה לא.
2. המלץ רק על פרויקטים שנמצאים בדיוק באותו שלב של הלקח.
3. המלץ על רפרנטים רק אם הם גם רלוונטיים עסקית וגם משויכים לאותו שלב בדיוק.
4. שפר את צעדי היישום בהתאם למה שעבד ומה שלא.
5. עדכן נקודות תשומת לב בהתאם לפידבק.

היה ממוקד ותמציתי. כתוב בעברית.""" + JSON_ONLY + DISTRIBUTION_SCHEMA
    fb = _list(body.get("feedback"))
    fb_text = clip_lines([f'- פרויקט "{f.get("projectName")}" ({f.get("respondedBy")}): {"רלוונטי" if f.get("isRelevant") else "לא רלוונטי"}, '
                          f'{"יושם" if f.get("isImplemented") else "לא יושם"}' + (f', הערות: "{f.get("notes")}"' if f.get("notes") else "")
                          for f in fb], CONTEXT_CHARS // 3) if fb else "אין פידבק"
    user = (f"לקח לניתוח מחדש:\n{head}\n\nפידבק מנהלי פרויקטים:\n{fb_text}\n\n"
            f"פרויקטים פעילים (לשקול הפצה נוספת):\n{projects}\n\nרפרנטים זמינים:\n{referents}\n\n"
            "אנא עדכן את ההמלצות בהתחשב בפידבק שהתקבל. אם מנהלים ציינו שהלקח לא רלוונטי לפרויקט שלהם, שקול להסיר אותו מההמלצות. "
            "אם ציינו שהוא רלוונטי ויושם, הדגש זאת כהצלחה. "
            f'המלץ רק על פרויקטים ורפרנטים שמתאימים בדיוק לשלב "{lesson.get("stage")}".')
    return system, user


def normalize_review(kind: str, data: dict) -> dict:
    """The exact keys the React code reads, whatever the model left out."""
    if kind == "pre_submit":
        try:
            score = float(data.get("quality_score"))
        except (TypeError, ValueError):
            score = 0
        return {"quality_score": max(0, min(10, score)), "is_duplicate": bool(data.get("is_duplicate")),
                "duplicate_of": _str(data.get("duplicate_of")), "improvements": [_str(x) for x in _list(data.get("improvements"))],
                "strengths": [_str(x) for x in _list(data.get("strengths"))], "summary": _str(data.get("summary"))}
    return {
        "recommended_projects": [{"project_name": _str(p.get("project_name")), "reason": _str(p.get("reason")),
                                  "priority": p.get("priority") if p.get("priority") in {"high", "medium", "low"} else "medium"}
                                 for p in _list(data.get("recommended_projects")) if isinstance(p, dict)],
        "recommended_referents": [{"referent_id": _str(r.get("referent_id")), "referent_name": _str(r.get("referent_name")),
                                   "reason": _str(r.get("reason"))}
                                  for r in _list(data.get("recommended_referents")) if isinstance(r, dict)],
        "implementation_steps": [_str(x) for x in _list(data.get("implementation_steps"))],
        "attention_points": [_str(x) for x in _list(data.get("attention_points"))],
        "summary": _str(data.get("summary")),
    }


# --------------------------------------------------------------------- analyze-lessons

ANALYZE_SCHEMA = ('{"project_analysis": {"summary": "סיכום מצב הפרויקט", "strengths": ["..."], "weaknesses": ["..."], '
                  '"recommendations": ["..."]}, "relevant_lessons": [{"lesson_id": מספר, "reason": "...", "priority": "high|medium|low"}], '
                  '"relevant_referents": [{"referent_id": "...", "reason": "..."}]}')


def build_analyze(body: dict, prefs: list[dict], insights: list[dict], quality: dict[int, dict]) -> tuple[str, str]:
    eq = _list(body.get("projectEquipment"))
    eq_text = f"\nציוד ונושאים משויכים לפרויקט: {', '.join(map(str, eq))}" if eq else ""
    own = _list(body.get("projectLessons"))
    own_text = clip_lines([f'{i + 1}. "{l.get("title")}" — קטגוריה: {l.get("category")}, שלב: {l.get("stage")}, סיכון: {l.get("risk")}, '
                           f'סטטוס: {l.get("status")}, תיאור: {l.get("description") or "אין"}, המלצה: {l.get("recommendation") or "אין"}'
                           for i, l in enumerate(own)], CONTEXT_CHARS // 3) if own else "אין לקחים בפרויקט זה עדיין."
    others = []
    for i, l in enumerate(_list(body.get("allLessons"))):
        q = quality.get(l.get("id"))
        q_text = ""
        if q:
            q_text = f", איכות: ניקוד_מצטבר={q['shrunk']:.2f}/2 ({q['responses']} תגובות"
            if q["avg"] is not None:
                q_text += f", ממוצע גולמי={q['avg']:.2f}"
            if q["rate"] is not None:
                q_text += f", אחוז יישום={q['rate'] * 100:.0f}%"
            q_text += ")"
        equip = _list(l.get("equipment"))
        others.append(f'{i + 1}. [ID:{l.get("id")}] "{l.get("title")}" — פרויקט: {l.get("projectName") or "לא ידוע"}, '
                      f'קטגוריה: {l.get("category")}, שלב: {l.get("stage")}, סיכון: {l.get("risk")}, סטטוס: {l.get("status")}'
                      + (f", ציוד: {', '.join(map(str, equip))}" if equip else "") + q_text)
    fb = _list(body.get("feedbackHistory"))
    fb_text = ("\n\nפידבק קודם ממנהלי פרויקטים:\n" + clip_lines(
        [f'- לקח "{f.get("lessonTitle")}": {"רלוונטי" if f.get("isRelevant") else "לא רלוונטי"}, {"יושם" if f.get("isImplemented") else "לא יושם"}'
         + (f", הערות: {f.get('notes')}" if f.get("notes") else "") for f in fb], 1200)) if fb else ""
    refs = _list(body.get("referents"))
    ref_text = ("\n\nרפרנטים במערכת:\n" + "\n".join(
        f"- {r.get('name')} (שלבים: {', '.join(map(str, _list(r.get('stages')))) or 'לא הוגדרו'})" for r in refs)) if refs else ""
    system = """אתה מערכת AI חכמה לניהול לקחים בפרויקטי בנייה ותשתיות חשמל.
תפקידך לבצע שלושה דברים:

1. **ניתוח הפרויקט**: נתח את הפרויקט והלקחים שלו. תן תובנות, דגשים, נקודות חוזק וחולשה, ומגמות שזיהית. התייחס לשלב הנוכחי, לסוג הפרויקט, לציוד ולסיכונים.

2. **המלצה על לקחים מפרויקטים אחרים**: מתוך רשימת כל הלקחים במערכת (שאינם של הפרויקט הזה), זהה לקחים רלוונטיים שכדאי ליישם בפרויקט הזה. התחשב בשלב, בציוד, בקטגוריה, ובסיכון.

3. **המלצה על רפרנטים רלוונטיים**: מתוך רשימת הרפרנטים במערכת, זהה רפרנטים שהשלבים שלהם רלוונטיים לפרויקט ולקחיו, וכדאי שיקבלו את הלקחים הרלוונטיים.

כללים:
- בסס את הניתוח אך ורק על נתונים אמיתיים. אל תכליל הצהרות גנריות.
- היה ביקורתי, מקצועי וישיר.
- התחשב בשלב הפרויקט: לקחים משלבים דומים או קודמים רלוונטיים יותר
- התחשב בקטגוריה ובציוד: לקחים מקטגוריות דומות עם ציוד דומה רלוונטיים יותר
- התחשב ברמת סיכון: לקחים בסיכון גבוה חשובים יותר
- התחשב בפידבק קודם: אם מנהלים אחרים סימנו לקח כלא רלוונטי, שקול זאת
- **תיעדוף לקחים מומלצים**: שקלל ~60% התאמה הקשרית (שלב/ציוד/קטגוריה/סיכון) ו-~40% איכות מצטברת (ניקוד_מצטבר על סקלה 0..2 שמופיע ליד כל לקח). לקחים עם ניקוד_מצטבר גבוה יותר ועם מספר תגובות גדול יותר אמינים יותר ויש להעדיפם.
- אל תוריד לקחים בעלי 0 תגובות אוטומטית — הם נחשבים ניטרליים.
- המלץ רק על לקחים שבאמת יכולים לעזור
- המלץ על רפרנטים שהשלבים שלהם חופפים לשלב הפרויקט""" + prefs_block(prefs) + insights_block(insights) + JSON_ONLY + ANALYZE_SCHEMA
    user = (f'פרויקט: "{body.get("projectName")}"\nשלב נוכחי: "{body.get("projectStage")}"\n'
            f'סוג פרויקט: {body.get("projectType") or "לא ידוע"}\nסוג תחנה: {body.get("stationType") or "לא ידוע"}\n'
            f'רמת סיכון: {body.get("risk") or "לא ידוע"}{eq_text}\n\nלקחים של הפרויקט:\n{own_text}\n\n'
            f"לקחים מפרויקטים אחרים במערכת:\n{clip_lines(others, CONTEXT_CHARS // 2)}\n{fb_text}\n{ref_text}\n\n"
            "בצע ניתוח מקיף של הפרויקט, המלץ על לקחים רלוונטיים מפרויקטים אחרים, וזהה רפרנטים רלוונטיים.")
    return system, user


def normalize_analyze(data: dict, known_lesson_ids: set[int], known_referent_ids: set[str]) -> dict:
    """Same shape as the edge function; ids the model invented are dropped."""
    pa = data.get("project_analysis") if isinstance(data.get("project_analysis"), dict) else {}
    lessons = []
    for item in _list(data.get("relevant_lessons")):
        if not isinstance(item, dict):
            continue
        try:
            lid = int(item.get("lesson_id"))
        except (TypeError, ValueError):
            continue
        if lid in known_lesson_ids:
            lessons.append({"lesson_id": lid, "reason": _str(item.get("reason")),
                            "priority": item.get("priority") if item.get("priority") in {"high", "medium", "low"} else "medium"})
    refs = [{"referent_id": _str(r.get("referent_id")), "reason": _str(r.get("reason"))}
            for r in _list(data.get("relevant_referents"))
            if isinstance(r, dict) and (not known_referent_ids or _str(r.get("referent_id")) in known_referent_ids)]
    return {"project_analysis": {"summary": _str(pa.get("summary")),
                                 "strengths": [_str(x) for x in _list(pa.get("strengths"))],
                                 "weaknesses": [_str(x) for x in _list(pa.get("weaknesses"))],
                                 "recommendations": [_str(x) for x in _list(pa.get("recommendations"))]},
            "relevant_lessons": lessons, "relevant_referents": refs}


# --------------------------------------------------------------------- classify-ai-feedback

CLASSIFY_SYSTEM = """אתה מערכת סיווג פידבק על ניתוחי AI בפרויקטי בנייה ותשתיות חשמל.
תפקידך לנתח פידבק שמשתמש נתן על ניתוח AI ולסווג כל פריט בנפרד.

יש שני סוגי סיווג:
- "public" — שינוי פומבי/כללי: טעויות בניתוח, סיכונים שלא זוהו, הנחות שגויות, תובנות חסרות. ישפיע על כל המשתמשים לאחר אישור מנהל.
- "personal" — העדפות אישיות: סגנון כתיבה, אורך, פוקוס, פורמט. יוחל מיידית רק על המשתמש ששלח.

חלץ מהפידבק:
1. פריטים פומביים (public_items) — תובנות, טעויות, תיקונים מקצועיים
2. פריטים אישיים (personal_items) — העדפות סגנון, פורמט, אורך

פידבק אחד יכול להכיל גם פריטים פומביים וגם אישיים. סווג כל פריט בנפרד.""" + JSON_ONLY + (
    '{"public_items": [{"text": "תיאור הטעות/התיקון", "context_type": "project_type|domain|stage|general", "context_value": "ערך ההקשר או ריק"}], '
    '"personal_items": [{"key": "style|length|focus|format|detail_level", "value": "ערך ההעדפה"}]}')


def normalize_classify(data: dict) -> tuple[list[dict], list[dict]]:
    public = [{"text": _str(i.get("text")), "context_type": _str(i.get("context_type")) or "general",
               "context_value": _str(i.get("context_value")) or None}
              for i in _list(data.get("public_items")) if isinstance(i, dict) and _str(i.get("text")).strip()]
    personal = [{"key": _str(i.get("key")), "value": _str(i.get("value"))}
                for i in _list(data.get("personal_items"))
                if isinstance(i, dict) and _str(i.get("key")).strip() and _str(i.get("value")).strip()]
    return public, personal


# --------------------------------------------------------------------- suggest-categories / admin-insights

def build_suggest_categories(body: dict) -> tuple[str, str]:
    existing = ", ".join(map(str, _list(body.get("existingCategories"))))
    lessons = _list(body.get("lessons"))[:10]
    examples = ("הנה כמה דוגמאות ללקחים קיימים:\n" + "\n".join(
        f"- {l.get('title')} (קטגוריה נוכחית: {l.get('category')})" for l in lessons)) if lessons else ""
    user = (f"אתה מומחה לניהול לקחים בפרויקטי תשתית והנדסה.\nהנה הקטגוריות הקיימות של הלקחים: {existing}\n\n{examples}\n\n"
            "הצע 3-5 קטגוריות חדשות שיכולות להיות שימושיות לניהול לקחים בפרויקטים. הקטגוריות צריכות להיות שונות מהקיימות.\n"
            "עבור כל קטגוריה תן שם קצר ותיאור קצר.")
    system = ("אתה עוזר לניהול לקחים בפרויקטים. ענה בעברית בלבד." + JSON_ONLY
              + '{"suggestions": [{"name": "שם הקטגוריה", "description": "תיאור קצר"}]}')
    return system, user


def build_admin_insights(body: dict) -> tuple[str, str]:
    system = ('אתה אנליסט נתונים. עבור כל דפוס שמסופק, נסח משפט ניהולי אחד תמציתי בעברית עסקית. אסור לך להמציא מספרים — '
              'השתמש רק במספרים שמופיעים ב-evidence. החזר JSON תקין בלבד בפורמט {"phrasings":[{"id":"...","sentence":"..."}]}.')
    pats = _list(body.get("patterns"))
    user = "דפוסים:\n" + clip_lines([f"- id={p.get('id')} | kind={p.get('kind')} | title={p.get('title')} | evidence={p.get('evidence')}"
                                       for p in pats if isinstance(p, dict)], CONTEXT_CHARS)
    return system, user


# --------------------------------------------------------------------- model call

async def ask(system: str, user: str, *, json_mode: bool, max_tokens: int = MAX_TOKENS) -> str:
    from app.services.llm_router import llm_chat
    try:
        return await llm_chat(USAGE, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                              max_tokens=max_tokens, temperature=0.3, json_mode=json_mode)
    except Exception as e:  # both providers failed; the caller answers 503
        logger.warning("lessons AI unavailable: %s", type(e).__name__)
        raise AIUnavailable(str(e)) from e
