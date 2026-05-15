"""Gold-truth service — propose, store, and compare against human-approved gold answers."""

import hashlib
import logging
import re
from datetime import datetime, date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvalGoldAnswer, Project
from app.services.knowledge_service import normalize_hebrew
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

RTL = "‏"

_FIELD_KEYWORDS = {
    "manager":       ["מנהל", "מנה\"פ", "מנהפ", "מי אחראי", "מי מנהל"],
    "stage":         ["סטטוס", "שלב", "מצב", "באיזה שלב"],
    "risks":         ["סיכון", "סיכונים", "סוגיות", "חסם", "חסמים"],
    "weekly_report": ["דוח שבועי", "דוח", "מה קורה", "עדכון", "מה המצב"],
    "to_handle":     ["לטפל", "מה צריך", "לעשות"],
    "estimated_finish_date": ["מתי יסתיים", "תאריך סיום", "סיום", "מועד סיום"],
    "dev_plan_date":         ["תכנון", "תאריך תכנון", "מתי מתוכנן"],
}


def question_hash(q: str) -> str:
    """Stable hash for use as URL-safe id and unique key. Uses normalized Hebrew."""
    return hashlib.sha256(normalize_hebrew(q).encode("utf-8")).hexdigest()


async def get_gold(session: AsyncSession, question: str) -> EvalGoldAnswer | None:
    h = question_hash(question)
    return await session.scalar(select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))


async def list_gold(session: AsyncSession) -> list[EvalGoldAnswer]:
    rows = await session.execute(select(EvalGoldAnswer).order_by(EvalGoldAnswer.id))
    return list(rows.scalars())


async def delete_gold(session: AsyncSession, q_hash: str) -> bool:
    row = await session.scalar(select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == q_hash))
    if not row:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def save_gold(
    session: AsyncSession,
    *,
    question: str,
    gold_answer: str,
    user_id: int | None,
    target_project: str | None = None,
    target_field: str | None = None,
    source: str = "manual",
) -> EvalGoldAnswer:
    h = question_hash(question)
    existing = await session.scalar(select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    if existing:
        existing.gold_answer = gold_answer
        existing.target_project = target_project
        existing.target_field = target_field
        existing.source = source
        existing.approved_by_user_id = user_id
        existing.approved_at = datetime.utcnow()
        row = existing
    else:
        row = EvalGoldAnswer(
            question_hash=h,
            question=question,
            gold_answer=gold_answer,
            target_project=target_project,
            target_field=target_field,
            source=source,
            approved_by_user_id=user_id,
            approved_at=datetime.utcnow(),
        )
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _detect_field(question: str) -> str | None:
    nq = normalize_hebrew(question)
    # Phase 3: DB-backed + shadow overrides take precedence over the static dict
    from app.services import knowledge_service as ks
    effective = {**ks._DB_FIELD_ALIASES_CACHE, **ks._shadow_field_aliases.get()}
    for alias, field in effective.items():
        if normalize_hebrew(alias) in nq:
            return field
    # Fall through to static keyword map
    for field, keywords in _FIELD_KEYWORDS.items():
        for kw in keywords:
            if normalize_hebrew(kw) in nq:
                return field
    return None


async def _detect_project(session: AsyncSession, question: str) -> Project | None:
    nq_forms = set(normalize_hebrew(question).split())
    rows = (await session.execute(select(Project).where(Project.is_active.is_(True)))).scalars()
    best: tuple[int, Project] | None = None
    for p in rows:
        candidates = [p.project_identifier or "", p.name or ""]
        for c in candidates:
            if not c:
                continue
            tokens = {t for t in normalize_hebrew(c).split() if len(t) >= 2}
            overlap = len(tokens & nq_forms)
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, p)
    return best[1] if best else None


def _format_field_answer(project: Project, field: str) -> str | None:
    val = getattr(project, field, None)
    if val is None or val == "":
        return None
    if isinstance(val, (date, datetime)):
        val = val.strftime("%d/%m/%Y")
    label = {
        "manager": "מנהל הפרויקט",
        "stage": "הסטטוס",
        "risks": "סיכונים",
        "weekly_report": "דוח שבועי",
        "to_handle": "לטיפול",
        "estimated_finish_date": "תאריך סיום מוערך",
        "dev_plan_date": "תאריך תכנון",
    }.get(field, field)
    return f"{RTL}{label}: {val}"


async def propose_gold(session: AsyncSession, question: str) -> dict:
    """Return {answer, source, target_project, target_field} for a question.

    Tries DB lookup first (cheap, deterministic); falls back to a constrained LLM call.
    """
    project = await _detect_project(session, question)
    field = _detect_field(question)

    if project and field:
        formatted = _format_field_answer(project, field)
        if formatted:
            return {
                "answer": formatted,
                "source": "db_lookup",
                "target_project": project.project_identifier,
                "target_field": field,
            }

    # LLM fallback: give it the project rows as JSON, instruct strict grounding
    rows = (await session.execute(select(Project).where(Project.is_active.is_(True)).limit(80))).scalars()
    ground_truth = []
    for p in rows:
        ground_truth.append({
            "project": p.project_identifier,
            "name": p.name,
            "manager": p.manager,
            "stage": p.stage,
            "risks": (p.risks or "")[:200],
            "weekly_report_brief": p.weekly_report_brief,
            "estimated_finish_date": p.estimated_finish_date.strftime("%d/%m/%Y") if p.estimated_finish_date else None,
        })

    sys = (
        "אתה עוזר אבחון. ענה על השאלה אך ורק על בסיס ה-ground_truth שניתן. "
        "אם אין מידע מספיק, החזר בדיוק 'אין מידע'. "
        "החזר תשובה קצרה בעברית, ללא ציטוטים מלאים."
    )
    import json
    user = json.dumps({"ground_truth": ground_truth, "question": question}, ensure_ascii=False)
    try:


        answer = await llm_chat(
            "eval_judge",
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            max_tokens=200,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"propose_gold LLM call failed: {e}")
        answer = "אין מידע"

    return {
        "answer": f"{RTL}{answer.strip()}",
        "source": "llm_proposed",
        "target_project": project.project_identifier if project else None,
        "target_field": field,
    }


# ───────────────────────── compare_to_gold ─────────────────────────

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), lambda m: (int(m.group(3)), int(m.group(2)), int(m.group(1)))),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), lambda m: (int(m.group(3)), int(m.group(2)), int(m.group(1)))),
]


def _extract_dates(text: str) -> set[tuple[int, int, int]]:
    out: set[tuple[int, int, int]] = set()
    for pat, fn in _DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                out.add(fn(m))
            except Exception:
                pass
    return out


def _rule_check(ai_answer: str, gold_answer: str) -> float | None:
    a = normalize_hebrew(ai_answer).strip()
    g = normalize_hebrew(gold_answer).strip()
    if not a or not g:
        return 0.0 if (bool(a) ^ bool(g)) else None

    # Substring containment
    if g in a or a in g:
        return 1.0

    # Strip RTL marks and label-prefixed content like "הסטטוס:" before comparing values
    def _value_only(s: str) -> str:
        s = s.replace("‏", "").strip()
        if ":" in s:
            s = s.split(":", 1)[1].strip()
        return s
    av = _value_only(ai_answer)
    gv = _value_only(gold_answer)
    if av and gv and (normalize_hebrew(av) in normalize_hebrew(gv) or normalize_hebrew(gv) in normalize_hebrew(av)):
        return 1.0

    # Date-equivalence
    dates_a = _extract_dates(ai_answer)
    dates_g = _extract_dates(gold_answer)
    if dates_g and dates_a and dates_g & dates_a:
        return 1.0
    if dates_g and not (dates_a & dates_g):
        return 0.0

    return None  # inconclusive — defer to LLM


async def compare_to_gold(question: str, ai_answer: str, gold_answer: str) -> float:
    """Return similarity score 0.0..1.0. Tries cheap rule check first, then LLM judge."""
    rule = _rule_check(ai_answer, gold_answer)
    if rule is not None:
        return rule

    sys = (
        "You are a strict semantic-equivalence judge for Hebrew answers. "
        "Two answers are EQUIVALENT only if they convey the same factual claim. "
        "Differences in phrasing, ordering, or extra context are OK. "
        "Different facts, different people, different dates → NOT equivalent. "
        "Reply with exactly one word: YES or NO."
    )
    user = (
        f"שאלה: {question}\n\n"
        f"תשובה א (AI):\n{ai_answer}\n\n"
        f"תשובה ב (gold):\n{gold_answer}\n\n"
        "Equivalent? YES or NO."
    )
    try:
        verdict = await llm_chat(
            "eval_judge",
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            max_tokens=4,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"compare_to_gold LLM call failed: {e}")
        return 0.0
    return 1.0 if verdict.strip().upper().startswith("Y") else 0.0
