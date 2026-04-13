"""Project tools for Telegram bot — fetch project data and generate AI summaries."""

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


# ── Helper: model to dict conversion ────────────────────────────────────

def _project_to_dict(project: Project) -> dict:
    """Convert Project ORM object to plain dict with dates as strings."""
    return {
        "id":                    project.id,
        "project_identifier":    project.project_identifier,
        "name":                  project.name or "",
        "project_type":          project.project_type or "",
        "stage":                 project.stage or "",
        "manager":               project.manager or "",
        "weekly_report":         project.weekly_report or "",
        "risks":                 project.risks or "",
        "to_handle":             project.to_handle or "",
        "dev_plan_date":         project.dev_plan_date.strftime("%d/%m/%Y") if project.dev_plan_date else "",
        "estimated_finish_date": project.estimated_finish_date.strftime("%d/%m/%Y") if project.estimated_finish_date else "",
        "last_updated":          project.last_updated.strftime("%d/%m/%Y %H:%M") if project.last_updated else "",
        "is_active":             project.is_active,
    }


# ── DB query tools ─────────────────────────────────────────────────────────

async def get_project_details(identifier: str, session: AsyncSession) -> Optional[dict]:
    """
    Fetch single project by identifier.
    Tries exact match first, then ILIKE on name as fallback.
    """
    from sqlalchemy import or_

    stmt = select(Project).where(
        or_(
            Project.project_identifier == identifier,
            Project.name.ilike(f"%{identifier}%")
        )
    ).limit(1)

    project = (await session.execute(stmt)).scalars().first()
    return _project_to_dict(project) if project else None


async def search_by_manager(manager_name: str, session: AsyncSession) -> list[dict]:
    """Fetch all active projects for a given manager (case-insensitive)."""
    stmt = select(Project).where(
        Project.manager.ilike(f"%{manager_name}%"),
        Project.is_active == True,
    ).order_by(Project.name)

    projects = (await session.execute(stmt)).scalars().all()
    return [_project_to_dict(p) for p in projects]


async def list_risks(session: AsyncSession) -> list[dict]:
    """Fetch all active projects with non-empty risks field."""
    stmt = select(Project).where(
        Project.is_active == True,
        Project.risks.isnot(None),
        Project.risks != "",
    ).order_by(Project.name)

    projects = (await session.execute(stmt)).scalars().all()
    return [_project_to_dict(p) for p in projects]


async def _projects_summary(session: AsyncSession) -> str:
    """Quick summary of all projects (for general 'list all' queries)."""
    stmt = select(Project).where(Project.is_active == True)
    projects = (await session.execute(stmt)).scalars().all()

    if not projects:
        return "אין פרויקטים פעילים במסד הנתונים."

    stages = {}
    risks_count = 0
    for p in projects:
        stages[p.stage] = stages.get(p.stage, 0) + 1
        if p.risks:
            risks_count += 1

    summary = f"סך הכל {len(projects)} פרויקטים פעילים. "
    if stages:
        stages_text = ", ".join([f"{stage or 'לא מוגדר'}: {count}" for stage, count in stages.items()])
        summary += f"חלוקה לפי שלב: {stages_text}. "
    if risks_count > 0:
        summary += f"{risks_count} פרויקטים בסיכון."

    return summary


# ── Intent detection ────────────────────────────────────────────────────────
# Keywords support variations: different spellings, abbreviations, and English

_RISK_KEYWORDS = (
    "סיכון", "סיכונים", "חסם", "חסמים", "בעיה", "בעייה", "בעיות",
    "לטיפול", "לעיבוד", "פעולות", "דחוף", "קריטי", "משימה",
    "risk", "risks", "problem", "challenge", "issue", "blocker", "urgent"
)

_MANAGER_KEYWORDS = (
    'מנה"פ', 'מנה"פים', "מנהל", "מנהלת", "מנהלים", "מנהלות", "אחראי", "אחראית", "אחראים",
    "מנהל פרויקט", "מנהלת פרויקט", "מנהל המכלל", "מי אחראי",
    "manager", "responsible", "in charge", "lead"
)

_PROJECT_KEYWORDS = (
    "פרויקט", "פרוייקט", "פרויקטים", "פרוייקטים",
    "פרויקט שלך", "הפרויקט", "הפרוייקט",
    "עדכון שבועי", "עדכון", "שבועי",
    "שלב", "שלבים", "סטטוס", "מצב",
    "תאריך", "יעד", "דדליין",
    "חשמול", "פיתוח", "כל הפרויקטים",
    "project", "projects", "status", "stage", "update", "weekly", "date", "deadline"
)

# Words that look like identifiers but are actually Hebrew question/filler words
_SKIP_WORDS = frozenset({
    "כמה", "יש", "יש?", "כל", "הם", "הן", "אלה", "אלו",
    "קיים", "קיימים", "קיימות", "בסה", 'בסה"כ', "סה", 'סה"כ',
    # question words
    "אילו", "אלו", "מה", "מי", "מתי", "איך", "למה", "מדוע",
    "האם", "היכן", "אין", "מהם", "מהן", "מאיזה",
    # date/time filler
    "בשנת", "בשנה", "עד", "מאז", "לפני", "אחרי",
    "ב-", "ב", "של", "את", "עם", "לפי",
    "how", "many", "much", "are", "there", "which", "what", "when",
})

# "Count by type" intent — triggered when user asks "כמה X פרויקטים יש?"
_COUNT_KEYWORDS = ("כמה", "how many", "how much", "מספר פרויקטים", "מספר ה")

# Date/year query keywords
_DATE_KEYWORDS = (
    "מסתיים", "מסתיימים", "מסתיימות", "יסתיים", "יסתיימו",
    "מתוכנן", "מתוכננים", "מתוכננות", "יחושמל", "יחושמלו",
    "חשמול", "סיום", "יעד", "דדליין", "תאריך",
    "finish", "complete", "end", "deadline",
)

# Project type synonyms (what types users might ask about)
_TYPE_SYNONYMS: dict[str, str] = {
    "הקמה": "הקמה",
    "שדרוג": "שדרוג",
    "תחזוקה": "תחזוקה",
    "פיתוח": "פיתוח",
    "התחדשות": "התחדשות",
    "replacement": "replacement",
    "upgrade": "שדרוג",
    "maintenance": "תחזוקה",
    "construction": "הקמה",
}


async def count_by_type(type_name: str, session: AsyncSession) -> dict:
    """Count active projects where project_type contains type_name (case-insensitive)."""
    from sqlalchemy import func

    stmt = (
        select(func.count())
        .select_from(Project)
        .where(
            Project.is_active == True,
            Project.project_type.ilike(f"%{type_name}%"),
        )
    )
    count = (await session.execute(stmt)).scalar() or 0
    return {"type": type_name, "count": count}


async def get_projects_by_year(year: int, session: AsyncSession) -> list[dict]:
    """Return active projects whose estimated_finish_date falls in the given year."""
    from sqlalchemy import extract

    stmt = (
        select(Project)
        .where(
            Project.is_active == True,
            Project.estimated_finish_date.isnot(None),
            extract("year", Project.estimated_finish_date) == year,
        )
        .order_by(Project.estimated_finish_date)
    )
    projects = (await session.execute(stmt)).scalars().all()
    return [_project_to_dict(p) for p in projects]


def _extract_year(text: str) -> Optional[int]:
    """Extract a 4-digit year (2020–2040) from the text, if present."""
    import re
    match = re.search(r"\b(20[2-4]\d)\b", text)
    return int(match.group(1)) if match else None


def _extract_type_from_count_query(text: str) -> Optional[str]:
    """
    Extract the project type from a count question like "כמה פרויקטי הקמה יש?".
    Returns the type string, or None if only a total count is asked.
    """
    text_stripped = text.strip("?״'\"").strip()

    # Remove count trigger words
    for kw in _COUNT_KEYWORDS:
        text_stripped = text_stripped.replace(kw, "").strip()

    # Remove generic filler/question words
    words = [
        w.strip("()[],.!?;:;״'\"").strip()
        for w in text_stripped.split()
        if w.strip("()[],.!?;:;״'\"").strip().lower() not in _SKIP_WORDS
    ]

    # Remove project-generic keywords (but keep type-specific words like הקמה)
    _generic = {
        "פרויקט", "פרוייקט", "פרויקטים", "פרוייקטים", "פרויקטי", "פרוייקטי",
        "project", "projects",
    }
    words = [w for w in words if w.lower() not in _generic]

    if not words:
        return None  # pure total-count question

    # Check against known type synonyms first
    for word in words:
        for synonym, canonical in _TYPE_SYNONYMS.items():
            if synonym in word or word in synonym:
                return canonical

    # Fall back to the first remaining meaningful word
    candidate = words[0] if words else None
    return candidate if candidate and len(candidate) >= 2 else None


async def _ai_detect_intent(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Use the LLM to extract query intent and parameter from free-form Hebrew text.
    Returns (intent, param) or (None, None) on failure — caller falls back to keyword matching.
    """
    import json as _json
    import re as _re
    from app.services.llm_router import llm_chat

    prompt = (
        "אתה מחלץ כוונה ופרמטר משאלות על פרויקטים בעברית.\n"
        "החזר JSON בלבד בפורמט: {\"intent\": \"...\", \"param\": \"...\"}\n\n"
        "אפשרויות intent:\n"
        "- by_manager: השאלה עוסקת בפרויקטים שמנהל/ת אדם ספציפי → param = שם מלא של המנהל/ת\n"
        "- by_identifier: השאלה עוסקת בפרויקט ספציפי לפי שם או מזהה → param = שם/מזהה הפרויקט\n"
        "- count_by_type: כמה פרויקטים מסוג מסוים יש → param = סוג הפרויקט (או null לספירה כוללת)\n"
        "- list_risks: השאלה עוסקת בסיכונים/חסמים → param = null\n"
        "- by_year: פרויקטים שמסתיימים בשנה מסוימת → param = שנה כמחרוזת\n"
        "- general: כל שאלה כללית אחרת על פרויקטים → param = null\n\n"
        f"שאלה: {text}\n\nJSON:"
    )

    try:
        response = await llm_chat(
            "intent_detection",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.0,
        )
        match = _re.search(r'\{[^}]+\}', response)
        if match:
            data = _json.loads(match.group())
            intent = data.get("intent") or None
            param = data.get("param") or None
            if param in ("null", "", "None"):
                param = None
            valid_intents = {"by_manager", "by_identifier", "count_by_type", "list_risks", "by_year", "general"}
            if intent in valid_intents:
                logger.info(f"AI intent detection: intent={intent}, param={param!r}")
                return (intent, param)
    except Exception as e:
        logger.warning(f"AI intent detection failed: {e}")

    return (None, None)


def _detect_intent(text: str, user_data: dict) -> tuple[str, Optional[str]]:
    """
    Detect query intent from text.
    Returns (intent, param) where:
      intent ∈ {"by_identifier", "by_manager", "list_risks", "count_by_type", "general"}
      param = identifier / manager name / type_name / None
    """
    text_lower = text.lower()

    # Count query? ("כמה פרויקטי הקמה יש?")  — must check BEFORE project-keyword branch
    if any(kw in text_lower for kw in _COUNT_KEYWORDS):
        # If manager keyword is present, this is "כמה פרויקטים מנהלת X?" → by_manager
        if any(kw in text_lower for kw in _MANAGER_KEYWORDS):
            if " של " in text:
                name = text.split(" של ", 1)[-1].strip().rstrip("?").strip()
            else:
                # Word-level filtering — avoids corrupting words that contain keyword substrings
                # e.g. "מנהלת" must not be mangled by removing "מנהל" as a substring
                _remove_words = (
                    set(_COUNT_KEYWORDS)
                    | set(_MANAGER_KEYWORDS)
                    | {"פרויקטים", "פרויקטי", "פרויקט", "יש"}
                )
                words = [
                    w.strip("?.,;:!") for w in text.split()
                    if w.strip("?.,;:!") and w.strip("?.,;:!") not in _SKIP_WORDS
                    and w.strip("?.,;:!") not in _remove_words
                ]
                name = " ".join(words)
            if name and len(name) > 1:
                return ("by_manager", name)

        type_name = _extract_type_from_count_query(text)
        return ("count_by_type", type_name)  # type_name may be None → total count

    # Date/year query? ("אילו פרויקטים מסתיימים בשנת 2026?")
    year = _extract_year(text)
    if year and any(kw in text_lower for kw in _DATE_KEYWORDS):
        return ("by_year", str(year))

    # Risk query?
    if any(kw in text_lower for kw in _RISK_KEYWORDS):
        return ("list_risks", None)

    # Manager query?
    if any(kw in text_lower for kw in _MANAGER_KEYWORDS):
        # Try to extract manager name (simple heuristic: words after "של" or standalone name)
        if " של " in text:
            name = text.split(" של ", 1)[-1].strip()
        else:
            # Remove keywords and try to find the name
            name = text
            for kw in _MANAGER_KEYWORDS:
                name = name.replace(kw, "").strip()
            name = name.split()[0] if name.split() else ""

        if name and len(name) > 1:
            return ("by_manager", name)

    # Project query (by identifier)?
    if any(kw in text_lower for kw in _PROJECT_KEYWORDS):
        # Try to extract project identifier (usually short alphanumeric, Hebrew, or mixed)
        words = text.split()
        for word in words:
            w = word.strip("()[],.!?;:;״'\"").strip()
            w_lower = w.lower()

            # Skip question/filler words
            if w_lower in _SKIP_WORDS:
                continue

            # Skip if it's a known keyword
            if any(kw in w_lower for kw in _PROJECT_KEYWORDS + _MANAGER_KEYWORDS + _RISK_KEYWORDS):
                continue

            # Valid identifier: 2-10 chars, has alphanumeric or Hebrew characters
            if 2 <= len(w) <= 10 and any(ord(c) >= 1488 or c.isalnum() for c in w):
                return ("by_identifier", w)

    # Follow-up about same project?
    if "last_project" in user_data and user_data.get("last_project"):
        return ("by_identifier", user_data["last_project"])

    # Bare project name (no keywords matched): use the whole text as the search term.
    # Strip trailing punctuation and question marks; only apply for short inputs.
    clean = text.strip().rstrip("?.,;:!").strip()
    words = clean.split()
    if 1 <= len(words) <= 5:
        hebrew_chars = sum(1 for c in clean if "\u05d0" <= c <= "\u05ea")
        if hebrew_chars >= 2:
            return ("by_identifier", clean)

    return ("general", None)


# ── Main entry point ───────────────────────────────────────────────────────

async def answer_project_query(
    text: str,
    session: AsyncSession,
    user_data: dict,
    user_id: Optional[int] = None,
) -> str:
    """
    Main function: detect intent, fetch data, and generate Hebrew summary via AI.
    Updates user_data["last_project"] for conversation context.
    Writes a QueryLog entry so the question appears in the dashboard logs tab.
    """
    # Primary: ask the LLM to understand the question
    intent, param = await _ai_detect_intent(text)
    # Fallback: keyword-based detection if AI failed or returned nothing useful
    if not intent or intent == "general":
        kw_intent, kw_param = _detect_intent(text, user_data)
        if kw_intent != "general" or not intent:
            intent, param = kw_intent, kw_param

    # Detect if this is a bare name lookup (no question words, just a name)
    _q = text.strip().rstrip("?").strip()
    _bare_name = (
        intent == "by_identifier"
        and not any(kw in text.lower() for kw in (*_PROJECT_KEYWORDS, *_RISK_KEYWORDS, "מה", "סטטוס", "שלב"))
        and len(_q.split()) <= 5
    )

    context_str = ""
    current_project_id = None

    try:
        if intent == "by_year":
            year_int = int(param)
            data = await get_projects_by_year(year_int, session)
            if data:
                compact = [
                    {
                        "זיהוי": p["project_identifier"],
                        "שם": p["name"],
                        "שלב": p["stage"],
                        "יעד חשמול": p["estimated_finish_date"],
                        "מנהל": p["manager"],
                    }
                    for p in data
                ]
                context_str = (
                    f"פרויקטים פעילים עם יעד חשמול בשנת {year_int}: {len(data)}\n\n"
                    + json.dumps(compact, ensure_ascii=False, indent=2)
                )
            else:
                context_str = f"לא נמצאו פרויקטים פעילים עם יעד חשמול בשנת {year_int}."

        elif intent == "count_by_type":
            if param:
                result = await count_by_type(param, session)
                context_str = (
                    f"מספר פרויקטי {result['type']} פעילים במסד: {result['count']}."
                )
            else:
                # Total count
                context_str = await _projects_summary(session)

        elif intent == "by_identifier":
            data = await get_project_details(param, session)
            if data:
                user_data["last_project"] = data["project_identifier"]
                current_project_id = data["project_identifier"]
                context_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                context_str = f"לא נמצא פרויקט בזיהוי '{param}'."

        elif intent == "by_manager":
            data = await search_by_manager(param, session)
            if data:
                count = len(data)
                # Build a compact list: identifier + name + stage (no weekly_report to save tokens)
                compact = [
                    {"זיהוי": p["project_identifier"], "שם": p["name"], "שלב": p["stage"]}
                    for p in data
                ]
                context_str = (
                    f"מנהל/ת: {param}\n"
                    f"מספר פרויקטים פעילים: {count}\n\n"
                    + json.dumps(compact, ensure_ascii=False, indent=2)
                )
            else:
                context_str = f"לא נמצאו פרויקטים עבור מנהל '{param}'."

        elif intent == "list_risks":
            data = await list_risks(session)
            if data:
                context_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                context_str = "אין פרויקטים בסיכון."

        else:  # general
            context_str = await _projects_summary(session)

    except Exception as e:
        logger.warning(f"project_tools query failed: {e}")
        return f"שגיאה בקבלת נתוני פרויקט: {str(e)[:100]}"

    # Load learned instructions (same source as knowledge_service uses)
    try:
        from app.services.knowledge_service import _get_learned_instructions
        learned_instructions = await _get_learned_instructions(session)
    except Exception:
        learned_instructions = []

    if learned_instructions:
        logger.info(f"project_tools: injecting {len(learned_instructions)} learned instructions")

    # Call LLM to analyze and summarize in Hebrew
    try:
        _DELIM_OPEN = "%%%תשובה%%%"
        _DELIM_CLOSE = "%%%סוף%%%"

        instructions_addon = ""
        if learned_instructions:
            instructions_text = "\n".join(f"- {inst}" for inst in learned_instructions)
            instructions_addon = (
                "\n\n## הוראות שנלמדו מפידבק קודם (חובה לקיים):\n"
                + instructions_text
            )

        if _bare_name:
            system_content = (
                "אתה עוזר מומחה בניהול פרויקטים תשתיות חשמל. "
                "המשתמש כתב שם פרויקט בלבד — הצג כרטיס סטטוס מלא. "
                f"כתוב את התשובה הסופית בין הסמנים {_DELIM_OPEN} ו-{_DELIM_CLOSE} בלבד. "
                "כל מחשבה, הכנה, או בדיקה — לפני הסמן הראשון בלבד (לא בפלט). "
                "בין הסמנים: עברית בלבד, ישירות, ללא הקדמות. "
                "הצג את כל השדות הקיימים:\n"
                "• שם הפרויקט ומזהה\n"
                "• שלב / סטטוס\n"
                "• מנהל פרויקט\n"
                "• תאריך יעד חשמול\n"
                "• תאריך תכנית פיתוח\n"
                "• עדכון שבועי (שורה אחת)\n"
                "• סיכונים וחסמים (אם קיימים — הדגש)\n"
                "• לטיפול (אם קיים)\n"
                "שדה ריק — כתוב '—'. אל תמציא מידע."
                + instructions_addon
            )
        else:
            system_content = (
                "אתה עוזר מומחה בניהול פרויקטים תשתיות חשמל. "
                "קיבלת נתוני פרויקט מהמסד. "
                f"כתוב את התשובה הסופית בין הסמנים {_DELIM_OPEN} ו-{_DELIM_CLOSE} בלבד. "
                "כל מחשבה, הכנה, או בדיקה — לפני הסמן הראשון בלבד (לא בפלט). "
                "בין הסמנים: עברית בלבד, ישירות, ממוקד. "
                "אם יש סיכונים — הדגש אותם. אם יש עדכון שבועי — סכם בשורה אחת. "
                "אל תמציא מידע שלא קיים בנתונים."
                + instructions_addon
            )

        summary = await llm_chat(
            "project_query",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"שאלה: {text}\n\nנתונים:\n{context_str}"},
            ],
            max_tokens=800,
            temperature=0.2,
        )
        answer = _strip_thinking(summary)
        await _log_query(text, answer, intent, current_project_id, session, user_id)
        return answer

    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}")
        fallback = context_str[:1000] if context_str else "לא הצלחתי לעבד את בקשתך."
        await _log_query(text, fallback, intent, current_project_id, session, user_id)
        return fallback


async def _log_query(
    question: str,
    answer: str,
    intent: str,
    project_id: Optional[str],
    session: AsyncSession,
    user_id: Optional[int],
) -> None:
    """Write a QueryLog entry for a project_tools query."""
    try:
        from app.models import QueryLog
        from app.services.llm_router import get_last_llm_meta
        provider, is_fb = get_last_llm_meta()
        sources = [{"source": "projects_db", "intent": intent}]
        if project_id:
            sources.append({"project": project_id})
        log = QueryLog(
            question=question,
            ai_response=answer,
            sources_used=sources,
            user_id=user_id,
            llm_provider=provider or None,
            is_fallback=is_fb or None,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
    except Exception as exc:
        logger.warning(f"project_tools: failed to write QueryLog: {exc}")


def _strip_thinking(text: str) -> str:
    """
    Remove leaked chain-of-thought / reasoning blocks that some models output
    before the actual Hebrew answer.

    Patterns stripped:
    - <think>...</think> blocks (Gemma / DeepSeek thinking tags)
    - Leading English bullet-point blocks ("* Role:", "* Input:", "* Observation:", etc.)
      followed eventually by Hebrew content — keep only from the first Hebrew line onward.
    """
    import re

    _DELIM_OPEN = "%%%תשובה%%%"
    _DELIM_CLOSE = "%%%סוף%%%"

    # 1. Extract delimited answer block (most reliable — model wrote between markers)
    if _DELIM_OPEN in text:
        start = text.index(_DELIM_OPEN) + len(_DELIM_OPEN)
        end = text.index(_DELIM_CLOSE) if _DELIM_CLOSE in text else len(text)
        return text[start:end].strip()

    # 2. Strip explicit <think>...</think> blocks (DeepSeek / some Gemma models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 3. Discard leading reasoning blocks.
    #    Strategy: find the LAST contiguous block of Hebrew-dominant lines.
    #    This handles cases where the model outputs Hebrew planning notes THEN the real answer.
    lines = text.splitlines()

    def _hebrew_ratio(line: str) -> float:
        chars = [c for c in line if c.isalpha()]
        if not chars:
            return 0.0
        heb = sum(1 for c in chars if "\u05d0" <= c <= "\u05ea")
        return heb / len(chars)

    # Find the last run of ≥3 consecutive Hebrew-dominant lines
    best_start = None
    run_start = None
    run_len = 0
    for i, line in enumerate(lines):
        if _hebrew_ratio(line) >= 0.5 or not line.strip():
            if line.strip():  # non-empty Hebrew line
                if run_start is None:
                    run_start = i
                run_len += 1
            # blank lines don't break a run
        else:
            if run_len >= 2:
                best_start = run_start
            run_start = None
            run_len = 0
    if run_len >= 2:
        best_start = run_start

    if best_start is not None and best_start > 0:
        text = "\n".join(lines[best_start:]).strip()

    return text
