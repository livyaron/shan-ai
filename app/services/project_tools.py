"""Project tools for Telegram bot — fetch project data and generate AI summaries."""

import json
import logging
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


# ── Helper: model to dict conversion ────────────────────────────────────

def _compute_delay(dev_plan_date, estimated_finish_date) -> int | None:
    """Signed months: negative = delayed, positive = buffer, None = missing data."""
    if not dev_plan_date or not estimated_finish_date:
        return None
    return (dev_plan_date.year - estimated_finish_date.year) * 12 + \
           (dev_plan_date.month - estimated_finish_date.month)


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
        "delay_months":          _compute_delay(project.dev_plan_date, project.estimated_finish_date),
    }


# ── DB query tools ─────────────────────────────────────────────────────────

async def find_projects_by_identifier(identifier: str, session: AsyncSession) -> list[dict]:
    """
    Fetch all projects matching identifier (exact code OR name substring).
    Hebrew construct-form fallback strips last char if primary is empty.
    Exact-code match short-circuits to that single row (preserves short-code UX
    when a unique code collides with a common substring).
    """
    stmt = select(Project).where(
        or_(
            Project.project_identifier == identifier,
            Project.name.ilike(f"%{identifier}%"),
        )
    ).order_by(Project.name).limit(10)

    rows = (await session.execute(stmt)).scalars().all()
    if rows:
        exact = [p for p in rows if p.project_identifier == identifier]
        if exact:
            return [_project_to_dict(exact[0])]
        return [_project_to_dict(p) for p in rows]

    if len(identifier) > 2:
        prefix = identifier[:-1]
        stmt2 = select(Project).where(
            Project.name.ilike(f"%{prefix}%")
        ).order_by(Project.name).limit(10)
        rows = (await session.execute(stmt2)).scalars().all()
        return [_project_to_dict(p) for p in rows]

    return []


async def get_project_details(identifier: str, session: AsyncSession) -> Optional[dict]:
    """Single-project lookup wrapper. Returns first match or None."""
    rows = await find_projects_by_identifier(identifier, session)
    return rows[0] if rows else None


async def search_by_manager(manager_name: str, session: AsyncSession) -> list[dict]:
    """Fetch all active projects for a given manager (case-insensitive).

    Searches word-by-word with OR so partial names and any word order work.
    Falls back to stripping a trailing י (Hebrew diminutive) if no match found.
    """
    async def _search_tokens(name: str) -> list:
        tokens = [t for t in name.split() if t]
        if not tokens:
            return []
        conditions = [Project.manager.ilike(f"%{t}%") for t in tokens]
        stmt = select(Project).where(
            or_(*conditions),
            Project.is_active,
        ).order_by(Project.name)
        return (await session.execute(stmt)).scalars().all()

    projects = await _search_tokens(manager_name)
    # Nickname fallback: "רחלי" → "רחל", "דני" → "דן", etc.
    if not projects and manager_name.endswith("י") and len(manager_name) > 2:
        projects = await _search_tokens(manager_name[:-1])
    return [_project_to_dict(p) for p in projects]


async def list_risks(session: AsyncSession) -> list[dict]:
    """Fetch all active projects with non-empty risks field."""
    stmt = select(Project).where(
        Project.is_active,
        Project.risks.isnot(None),
        Project.risks != "",
    ).order_by(Project.name)

    projects = (await session.execute(stmt)).scalars().all()
    return [_project_to_dict(p) for p in projects]


async def list_delayed_projects(session: AsyncSession, type_name: Optional[str] = None) -> list[dict]:
    """Fetch active projects with delay_months < 0, optionally filtered by type."""
    conditions = [Project.is_active]
    if type_name:
        conditions.append(Project.project_type.ilike(f"%{type_name}%"))

    stmt = select(Project).where(*conditions).order_by(Project.name)
    projects = (await session.execute(stmt)).scalars().all()
    return [
        d for p in projects
        if (d := _project_to_dict(p))["delay_months"] is not None and d["delay_months"] < 0
    ]


async def _projects_summary(session: AsyncSession) -> str:
    """Quick summary of all projects (for general 'list all' queries)."""
    stmt = select(Project).where(Project.is_active)
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

# Delay intent keywords
_DELAY_KEYWORDS = (
    "עיכוב", "עיכובים", "מאחר", "מאחרים", "מאחרות", "באיחור",
    "מאוחר", "מאוחרים", "מאוחרות", "שבעיכוב", "בעיכוב",
    "delayed", "delay", "late", "behind schedule",
)

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
            Project.is_active,
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
            Project.is_active,
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
        "1. by_manager: 'כמה פרויקטים מנהלת X?' או 'איזה פרויקטים של Y?' → param = שם המנהל כפי שמופיע בשאלה (שם פרטי בלבד, שם משפחה בלבד, או שניהם)\n"
        "2. by_identifier: 'מה סטטוס X?' או 'איך עומד הפרויקט X?' או שם פרויקט בודד → param = שם/מזהה הפרויקט\n"
        "3. count_by_type: 'כמה פרויקטי הקמה?' או 'כמה שדרוגים יש?' → param = סוג הפרויקט (או null לספירה כוללת)\n"
        "4. list_risks: 'מה הסיכונים?' או 'אילו פרויקטים בסיכון?' → param = null\n"
        "5. by_year: 'אילו מסתיימים בשנת 2026?' → param = שנה כמחרוזת\n"
        "6. list_delayed: 'אילו פרויקטים בעיכוב?' או 'פרויקטי הקמה מאחרים' → param = סוג פרויקט אם הוזכר, אחרת null\n"
        "7. general: כל שאלה כללית אחרת → param = null\n\n"
        "הוראות חשובות:\n"
        "- אם השאלה היא על פרויקט בודד (שם או מזהה), תשובה = by_identifier\n"
        "- אם השאלה היא על כמות או ספירה, תשובה = count_by_type או by_manager\n"
        "- שמות מנהלים: העתק את השם בדיוק כפי שהוא מופיע בשאלה, אל תוסיף מה שאינו שם. אם רק שם פרטי — רשום רק שם פרטי.\n"
        "- שמות פרויקטים: כפי שהם מופיעים בשאלה (בלי להוסיף או להסיר מילים)\n\n"
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
            valid_intents = {"by_manager", "by_identifier", "count_by_type", "list_risks", "by_year", "list_delayed", "general"}
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

    # Delay query? ("פרויקטי הקמה שבעיכוב", "אילו פרויקטים מאחרים?")
    if any(kw in text_lower for kw in _DELAY_KEYWORDS):
        # Try to extract a project type from the same query
        type_param = None
        for synonym in _TYPE_SYNONYMS:
            if synonym in text_lower:
                type_param = _TYPE_SYNONYMS[synonym]
                break
        return ("list_delayed", type_param)

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
    precomputed_intent: Optional[str] = None,
    precomputed_param: Optional[str] = None,
) -> tuple[str, Optional[int]]:
    """
    Main function: detect intent, fetch data, and generate Hebrew summary via AI.
    Updates user_data["last_project"] for conversation context.
    Writes a QueryLog entry so the question appears in the dashboard logs tab.

    If precomputed_intent is provided (from the top-level router), use it directly
    and skip the second LLM call. Falls back to _ai_detect_intent → _detect_intent.
    """
    if precomputed_intent and precomputed_intent != "general":
        intent, param = precomputed_intent, precomputed_param
        logger.info(f"answer_project_query: using precomputed intent={intent!r}, param={param!r}")
    else:
        # Primary: ask the LLM to understand the question
        intent, param = await _ai_detect_intent(text)
        # Fallback: keyword-based detection if AI failed or returned nothing useful
        if not intent or intent == "general":
            kw_intent, kw_param = _detect_intent(text, user_data)
            if kw_intent != "general" or not intent:
                intent, param = kw_intent, kw_param

    # For single-project lookups, always show the full structured card
    _bare_name = (intent == "by_identifier")

    context_str = ""
    current_project_id = None
    by_identifier_mode = "card"

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
            matches = await find_projects_by_identifier(param, session)
            if not matches:
                context_str = f"לא נמצא פרויקט בזיהוי '{param}'."
            elif len(matches) == 1:
                data = matches[0]
                user_data["last_project"] = data["project_identifier"]
                current_project_id = data["project_identifier"]
                context_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                user_data.pop("last_project", None)
                current_project_id = None
                overflow_note = "\n\n⚠️ מוצגים 10 ראשונים בלבד — יש עוד תוצאות." if len(matches) == 10 else ""
                divider = "\n━━━━━━━━━━━━━━━━━━\n"
                cards = divider.join(
                    _format_project_card(p, i + 1, len(matches))
                    for i, p in enumerate(matches)
                )
                answer = cards + overflow_note
                log_id = await _log_query(text, answer, intent, None, session, user_id)
                return answer, log_id

        elif intent == "by_manager":
            if not param:
                _, param = _detect_intent(text, user_data)
            if not param:
                log_id = await _log_query(text, "לא הצלחתי לזהות את שם המנהל מהשאלה.", intent, None, session, user_id)
                return "לא הצלחתי לזהות את שם המנהל מהשאלה.", log_id
            data = await search_by_manager(param, session)
            if data:
                compact = [
                    {
                        "שם": p["name"],
                        "זיהוי": p["project_identifier"],
                        "שלב": p["stage"],
                        "מנהל": p["manager"],
                        "עיכוב בחודשים": p["delay_months"],
                    }
                    for p in data
                ]
                context_str = (
                    f"פרויקטים של {param} ({len(data)}):\n\n"
                    + json.dumps(compact, ensure_ascii=False, indent=2)
                )
            else:
                # No manager match — try as project identifier fallback
                project = await get_project_details(param, session)
                if project:
                    user_data["last_project"] = project["project_identifier"]
                    current_project_id = project["project_identifier"]
                    context_str = json.dumps(project, ensure_ascii=False, indent=2)
                else:
                    answer = f"לא נמצאו תוצאות עבור '{param}'."
                    log_id = await _log_query(text, answer, intent, None, session, user_id)
                    return answer, log_id

        elif intent == "list_risks":
            data = await list_risks(session)
            if data:
                context_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                context_str = "אין פרויקטים בסיכון."

        elif intent == "list_delayed":
            data = await list_delayed_projects(session, type_name=param)
            if data:
                type_label = f" מסוג {param}" if param else ""
                compact = [
                    {
                        "שם": p["name"],
                        "זיהוי": p["project_identifier"],
                        "שלב": p["stage"],
                        "מנהל": p["manager"],
                        "תאריך תכנית": p["dev_plan_date"],
                        "תאריך סיום משוער": p["estimated_finish_date"],
                        "עיכוב בחודשים": abs(p["delay_months"]),
                    }
                    for p in data
                ]
                context_str = (
                    f"פרויקטים{type_label} בעיכוב ({len(data)}):\n\n"
                    + json.dumps(compact, ensure_ascii=False, indent=2)
                )
            else:
                type_label = f" מסוג {param}" if param else ""
                context_str = f"לא נמצאו פרויקטים{type_label} בעיכוב."

        else:  # general
            context_str = await _projects_summary(session)

    except Exception as e:
        logger.warning(f"project_tools query failed: {e}")
        return f"שגיאה בקבלת נתוני פרויקט: {str(e)[:100]}", None

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
        instructions_addon = ""
        if learned_instructions:
            instructions_text = "\n".join(f"- {inst}" for inst in learned_instructions)
            instructions_addon = (
                "\n\n## ⚠️ הוראות מחייבות (עדיפות עליונה — דורסות את כל הכללים למעלה):\n"
                + instructions_text
                + "\nבמקרה של סתירה בין הוראה כאן לכלל פורמט למעלה — ההוראה כאן גוברת.\n"
            )

        CARD_RULES = (
            "\nהצג את כל הנתונים בפורמט הבא בדיוק — שורה אחת לכל שדה, ללא שורות ריקות בין שדות:\n"
            "📌 <b>שם השדה:</b> ערך\n"
            "כללים:\n"
            "- כל שדה בשורה נפרדת (\\n בלבד, לא \\n\\n)\n"
            "- תגיות <b>שם:</b> לכל שדה — חובה\n"
            "- תגיות <u>ערך</u> לתאריכים, סיכונים, פריטים לטיפול\n"
            "- אימוג׳י מתאים לפני כל שורה\n"
            "- שדה ריק → —\n"
            "- אל תוסיף הקדמה, סיכום, או שורות ריקות\n"
            "- אם 'delay_months' שלילי — הוסף בשורה הראשונה: ⚠️ <b>הפרויקט מאחר ב-X חודשים</b> (החלף X)\n"
            "- אם 'delay_months' חיובי — הוסף בשורה הראשונה: ✅ <b>יש X חודשי מרווח</b> (החלף X)\n"
            "- אם 'delay_months' הוא null — אל תציין עיכוב או מרווח"
        )
        COUNT_RULES = (
            "\nענה במשפט אחד בלבד עם המספר הסופי. "
            "דוגמה: 'יש 33 פרויקטי הקמה פעילים.' "
            "ללא רשימה, ללא פירוט פרויקטים, ללא כרטיסים."
        )
        LIST_RULES = (
            "\nהצג רשימה קצרה: שורה אחת לכל פרויקט, בפורמט:\n"
            "N. <b>שם</b> (זיהוי) — שלב\n"
            "אם 'עיכוב בחודשים' שלילי — הוסף בסוף השורה: ⚠️ מאחר Xח'\n"
            "אם 'עיכוב בחודשים' חיובי — הוסף בסוף השורה: ✅ Xח' מרווח\n"
            "ללא כרטיס מפורט, ללא שורות ריקות בין פריטים."
        )
        PROSE_RULES = "\nתשובה חופשית וקצרה. ללא כרטיסי פרויקטים, ללא רשימות מפורטות."
        MULTI_CARD_RULES = (
            "\nבמקור הנתונים קיימת רשימה של פרויקטים (JSON array). "
            "הצג כרטיס מלא ונפרד לכל פרויקט ברשימה לפי אותם הכללים של כרטיס יחיד:\n"
            + CARD_RULES +
            "\nהפרד בין פרויקטים בשורה: ━━━━━━━━━━━━━━━━━━\n"
            "לפני כל כרטיס הוסף כותרת: 📁 <b>פרויקט N מתוך K</b> (החלף N ו-K למספרים בפועל).\n"
            "אל תוסיף סיכום או הקדמה מעל הכרטיס הראשון פרט למה שמבוקש בהנחיות הלמודות.\n"
            "אל תחסיר שדות — הצג את כל השדות של כל פרויקט."
        )

        _format_by_intent = {
            "by_identifier": CARD_RULES,
            "count_by_type": COUNT_RULES,
            "list_delayed":  LIST_RULES,
            "list_risks":    LIST_RULES,
            "by_year":       LIST_RULES,
            "by_manager":    LIST_RULES,
            "general":       PROSE_RULES,
        }
        format_rules = _format_by_intent.get(intent, CARD_RULES)
        if intent == "by_identifier" and by_identifier_mode == "multi_card":
            format_rules = MULTI_CARD_RULES

        system_content = (
            "אתה עוזר מומחה בניהול פרויקטים תשתיות חשמל. ענה בעברית בלבד."
            + format_rules
            + instructions_addon
        )

        summary = await llm_chat(
            "project_query",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"שאלה: {text}\n\nנתונים:\n{context_str}"},
            ],
            max_tokens=4000 if by_identifier_mode == "multi_card" else 2000,
            temperature=0.2,
        )
        answer = _strip_thinking(summary)
        log_id = await _log_query(text, answer, intent, current_project_id, session, user_id)
        return answer, log_id

    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}")
        fallback = context_str[:1000] if context_str else "לא הצלחתי לעבד את בקשתך."
        log_id = await _log_query(text, fallback, intent, current_project_id, session, user_id)
        return fallback, log_id


async def _log_query(
    question: str,
    answer: str,
    intent: str,
    project_id: Optional[str],
    session: AsyncSession,
    user_id: Optional[int],
) -> Optional[int]:
    """Write a QueryLog entry for a project_tools query. Returns the new log id."""
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
        return log.id
    except Exception as exc:
        logger.warning(f"project_tools: failed to write QueryLog: {exc}")
        return None


_CARD_FIELD_ORDER = [
    ("project_identifier", "🔑", "זיהוי"),
    ("name",               "📋", "שם"),
    ("project_type",       "🏗️", "סוג"),
    ("stage",              "📍", "שלב"),
    ("manager",            "👤", "מנהל"),
    ("weekly_report",      "📝", "עדכון שבועי"),
    ("risks",              "⚡", "סיכונים"),
    ("to_handle",          "🔧", "לטיפול"),
    ("dev_plan_date",      "📅", "תאריך ת\"פ"),
    ("estimated_finish_date", "🎯", "תאריך חישמול"),
    ("last_updated",       "🕐", "עדכון אחרון"),
]


def _format_project_card(p: dict, index: int, total: int) -> str:
    """Render a single project dict as a Telegram HTML card string."""
    lines = []
    delay = p.get("delay_months")
    if delay is not None:
        if delay < 0:
            lines.append(f"⚠️ <b>הפרויקט מאחר ב-{abs(delay)} חודשים</b>")
        elif delay > 0:
            lines.append(f"✅ <b>יש {delay} חודשי מרווח</b>")
    for key, emoji, label in _CARD_FIELD_ORDER:
        val = p.get(key, "")
        if val is None or val == "":
            val = "—"
        if key in ("dev_plan_date", "estimated_finish_date", "last_updated", "risks", "to_handle"):
            lines.append(f"{emoji} <b>{label}:</b> <u>{val}</u>")
        else:
            lines.append(f"{emoji} <b>{label}:</b> {val}")
    header = f"📁 <b>פרויקט {index} מתוך {total}</b>"
    return header + "\n" + "\n".join(lines)


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

    # 1. Strip explicit <think>...</think> blocks (DeepSeek / some Gemma models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 1b. Replace <br> tags (unsupported by Telegram HTML) with newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # 2. Strip all %%% delimiter artifacts — remove every %%%WORD%%% block and any leftover %%%
    text = re.sub(r"%%%[^%\n]*%%%", "", text)  # remove %%%CONTENT%%% pairs
    text = text.replace("%%%", "").strip()      # remove any remaining %%% fragments

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
