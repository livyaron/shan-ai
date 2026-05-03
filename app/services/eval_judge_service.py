"""Agent 2 — Judge.

Compares an AI-produced answer against authoritative ground truth pulled directly
from the Project table (bypassing RAG). Returns a structured Verdict and writes
judge_verdict + failure_type back to QueryLog.

Priority chain:
  1. User explicit feedback (user_feedback on QueryLog) — always wins.
  2. Cheap rule checks (string-contains, date equality).
  3. LLM judgment with full ground truth payload.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, QueryLog
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


# Hebrew → DB column name. The right-hand side must be a real Project attribute.
FIELD_ALIAS_MAP: dict[str, str] = {
    "manager":             "manager",
    "מנהל":                "manager",
    "מנהל פרויקט":         "manager",
    "מנה\"פ":              "manager",
    "stage":               "stage",
    "סטטוס":               "stage",
    "שלב":                 "stage",
    "מצב":                 "stage",
    "estimated_finish_date": "estimated_finish_date",
    "תאריך חישמול":        "estimated_finish_date",
    "תאריך חשמול":         "estimated_finish_date",
    "יעד חשמול":           "estimated_finish_date",
    "יעד חשמול מסתמן":     "estimated_finish_date",
    "מתי יחושמל":          "estimated_finish_date",
    "dev_plan_date":       "dev_plan_date",
    "תאריך תוכנית פיתוח":  "dev_plan_date",
    "תאריך ת\"פ":          "dev_plan_date",
    "יעד ת\"פ":            "dev_plan_date",
    "תאריך תכנית פיתוח":   "dev_plan_date",
    "weekly_report":       "weekly_report",
    "פירוט שבועי":         "weekly_report",
    "עדכון שבועי":         "weekly_report",
    "to_handle":           "to_handle",
    "risks":               "risks",
    "סיכונים":             "risks",
    "project_type":        "project_type",
}

VERDICTS = {"PASS", "PARTIAL", "FAIL"}
FAILURE_TYPES = {
    "NO_DATA", "WRONG_DATA", "HALLUCINATION",
    "RETRIEVAL_MISS", "FORMAT", "FIELD_MISMATCH",
}


@dataclass
class Verdict:
    verdict: str                 # PASS | PARTIAL | FAIL
    failure_type: str | None     # one of FAILURE_TYPES, or None when PASS
    evidence: str
    severity: int                # 1-5

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_field_column(target_field: str | None) -> str | None:
    if not target_field:
        return None
    return FIELD_ALIAS_MAP.get(target_field.strip())


# ─────────────────────────────────────────────────────────────────────────────
# Project name cache — avoids a DB round-trip per question during eval runs.
# ─────────────────────────────────────────────────────────────────────────────
_PROJECT_NAME_CACHE: list[str] = []
_PROJECT_CACHE_TS: float = 0.0
_PROJECT_CACHE_TTL: float = 60.0


async def _load_project_names(session: AsyncSession) -> list[str]:
    global _PROJECT_NAME_CACHE, _PROJECT_CACHE_TS
    if time.time() - _PROJECT_CACHE_TS < _PROJECT_CACHE_TTL and _PROJECT_NAME_CACHE:
        return _PROJECT_NAME_CACHE
    rows = (await session.execute(
        select(Project.name).where(Project.is_active.is_(True))
    )).scalars().all()
    _PROJECT_NAME_CACHE = [r for r in rows if r]
    _PROJECT_CACHE_TS = time.time()
    return _PROJECT_NAME_CACHE


async def _extract_project_from_question(session: AsyncSession, question: str) -> str | None:
    """Scan question text for a known project name. Longest match wins."""
    names = await _load_project_names(session)
    q_lower = question.lower()
    # Sort longest-first to avoid "גליל" matching before "גליל מזרחי"
    for name in sorted(names, key=len, reverse=True):
        if name.lower() in q_lower:
            return name
    return None


# ─────────────────────────────────────────────────────────────────────────────
# User feedback override — user wins over any automated judge.
# ─────────────────────────────────────────────────────────────────────────────
async def _check_user_feedback(session: AsyncSession, question: str) -> Verdict | None:
    """Return a Verdict if the user has already given explicit feedback on this question."""
    row = (await session.execute(
        select(QueryLog)
        .where(QueryLog.question == question, QueryLog.user_feedback.isnot(None))
        .order_by(QueryLog.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    if row.user_feedback == 1:
        return Verdict("PASS", None, "user gave thumbs-up on this question", 1)
    if row.user_feedback == 0:
        return Verdict("FAIL", "WRONG_DATA", "user gave thumbs-down on this question", 4)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Ground truth fetch
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_ground_truth(
    session: AsyncSession,
    target_project: str | None,
    target_field: str | None,
) -> dict[str, Any] | None:
    """Direct DB fetch of authoritative project data, bypassing RAG."""
    if not target_project:
        return None

    needle = target_project.strip()
    stmt = select(Project).where(
        or_(
            Project.name.ilike(f"%{needle}%"),
            Project.project_identifier.ilike(f"%{needle}%"),
        ),
        Project.is_active.is_(True),
    ).limit(5)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None

    candidates = [_project_to_dict(r) for r in rows]
    primary = candidates[0]

    column = _resolve_field_column(target_field)
    expected = primary.get(column) if column else None
    return {
        "primary": primary,
        "candidates": candidates,
        "target_column": column,
        "expected_value": expected,
    }


def _project_to_dict(p: Project) -> dict[str, Any]:
    return {
        "name": p.name,
        "project_identifier": p.project_identifier,
        "project_type": p.project_type,
        "stage": p.stage,
        "manager": p.manager,
        "weekly_report": (p.weekly_report or "")[:400] or None,
        "risks": (p.risks or "")[:200] or None,
        "to_handle": (p.to_handle or "")[:200] or None,
        "dev_plan_date": p.dev_plan_date.isoformat() if isinstance(p.dev_plan_date, (date, datetime)) else None,
        "estimated_finish_date": p.estimated_finish_date.isoformat() if isinstance(p.estimated_finish_date, (date, datetime)) else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rule checks — cheap deterministic shortcuts before calling the LLM.
# ─────────────────────────────────────────────────────────────────────────────
def _rule_check(answer: str, expected: Any) -> Verdict | None:
    if expected is None:
        return None
    if not answer:
        return Verdict("FAIL", "NO_DATA", "answer is empty", 5)

    answer_norm = answer.strip()

    # Date equality — the answer often contains the date in a different format
    if isinstance(expected, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", expected):
        y, m, d = expected.split("-")
        candidates = [
            expected,
            f"{d}/{m}/{y}",
            f"{d}.{m}.{y}",
            f"{int(d)}/{int(m)}/{y}",
            f"{int(d)}.{int(m)}.{y}",
        ]
        if any(c in answer_norm for c in candidates):
            return Verdict("PASS", None, f"answer contains expected date {expected}", 1)
        return None

    # Exact-string match for short fields (manager, stage)
    if isinstance(expected, str) and len(expected) <= 64:
        if expected in answer_norm:
            return Verdict("PASS", None, f"answer contains expected '{expected}'", 1)
        return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# LLM judge — called only when rule checks don't give a definitive answer.
# ─────────────────────────────────────────────────────────────────────────────
_JUDGE_SYSTEM_PROMPT = """אתה שופט אובייקטיבי לתשובות AI על נתוני פרויקטים הנדסיים.
קיבלת: שאלה, תשובת AI, ונתוני אמת מתוך מסד הנתונים (ground_truth).
משימה: שפוט אם תשובת ה-AI נכונה לפי נתוני האמת בלבד — לא לפי ידע כללי.

הגדרות verdict:
- PASS: הנתון העיקרי שנשאל עליו נכון ומדויק.
- PARTIAL: חלק מהנתון נכון, חלק חסר או שגוי (למשל: שם מנהל נכון אבל ללא תעודת זהות).
- FAIL: הנתון שגוי, לא קיים בתשובה, או הומצא.

הגדרות failure_type (רק כאשר verdict != PASS):
- NO_DATA: התשובה אומרת "לא נמצא"/"אין מידע" אבל הנתון קיים ב-ground_truth.
- WRONG_DATA: התשובה מספקת ערך שונה מהאמת (לדוגמה: מנהל שגוי, תאריך לא נכון).
- HALLUCINATION: התשובה מציגה מידע שאינו קיים ב-ground_truth ואינו נכון.
- RETRIEVAL_MISS: התשובה תקינה לוגית אבל מבוססת על פרויקט שגוי (שם אחר).
- FORMAT: הנתון נכון אבל הפורמט מבלבל (לדוגמה: תאריך בפורמט לא סטנדרטי).
- FIELD_MISMATCH: התשובה מתייחסת לשדה אחר מאשר זה שנשאל.

כללים:
1. אם תשובת ה-AI ריקה — תמיד FAIL עם failure_type=NO_DATA.
2. אם ground_truth הוא null (אין נתון) — שפוט לפי ידע הגיוני בלבד, PARTIAL.
3. הדגמת עדות (evidence) חייבת לצטט ישירות מ-ground_truth או מהתשובה.
4. אל תתן PASS אם יש אי-התאמה ברורה.

החזר JSON בלבד (ללא מרקדאון):
{
  "verdict": "PASS" | "PARTIAL" | "FAIL",
  "failure_type": "NO_DATA" | "WRONG_DATA" | "HALLUCINATION" | "RETRIEVAL_MISS" | "FORMAT" | "FIELD_MISMATCH" | null,
  "evidence": "<ציטוט ישיר מהתשובה או מה-ground_truth המצביע על ההחלטה>",
  "severity": 1-5
}
"""


async def _llm_judge(
    question: str,
    answer: str,
    ground_truth: dict | None,
) -> Verdict:
    if not answer or not answer.strip():
        return Verdict("FAIL", "NO_DATA", "answer is empty", 5)

    payload = {
        "question": question,
        "ai_answer": answer,
        "ground_truth": ground_truth,
    }
    user_msg = json.dumps(payload, ensure_ascii=False)
    try:
        raw = await llm_chat(
            "eval_judge",
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=400,
            json_mode=True,
        )
        data = json.loads(raw)
        v = (data.get("verdict") or "").upper().strip()
        if v not in VERDICTS:
            v = "FAIL"
        ft = data.get("failure_type")
        if isinstance(ft, str):
            ft = ft.upper().strip()
            if ft not in FAILURE_TYPES:
                ft = None
        elif ft is not None:
            ft = None
        sev = data.get("severity") or 3
        try:
            sev = max(1, min(5, int(sev)))
        except (TypeError, ValueError):
            sev = 3
        evidence = (data.get("evidence") or "")[:500]
        if v == "PASS":
            ft = None
        elif ft is None:
            ft = "WRONG_DATA"
        return Verdict(v, ft, evidence, sev)
    except Exception as e:
        logger.warning(f"_llm_judge failed: {e}")
        return Verdict("FAIL", "WRONG_DATA", f"judge error: {type(e).__name__}", 3)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────
async def judge_answer(
    session: AsyncSession,
    question: str,
    answer: str,
    target_project: str | None = None,
    target_field: str | None = None,
    sources_used: list | None = None,
) -> Verdict:
    """Score one (question, answer) pair.

    Priority: user feedback override → rule check → LLM judge.
    """
    # 1. User override — highest priority
    try:
        user_v = await _check_user_feedback(session, question)
        if user_v is not None:
            return user_v
    except Exception as e:
        logger.debug(f"_check_user_feedback failed (non-fatal): {e}")

    # 2. Auto-extract project name from question if not provided
    if not target_project:
        try:
            target_project = await _extract_project_from_question(session, question)
        except Exception as e:
            logger.debug(f"_extract_project_from_question failed (non-fatal): {e}")

    # 3. Fetch ground truth
    try:
        gt = await fetch_ground_truth(session, target_project, target_field)
    except Exception as e:
        logger.warning(f"fetch_ground_truth failed: {e}")
        gt = None

    expected = gt.get("expected_value") if gt else None

    # 4. Special case: project targeted but not found in DB
    if target_project and gt is None:
        if any(s in (answer or "") for s in ("לא נמצא", "אין מידע", "לא קיים")):
            return Verdict("PASS", None, f"correctly reported missing project '{target_project}'", 1)
        return Verdict("FAIL", "HALLUCINATION", f"project '{target_project}' not in DB but answer was given", 4)

    # 5. Rule check
    rule_v = _rule_check(answer, expected)
    if rule_v is not None:
        return rule_v

    # 6. LLM judge
    try:
        return await _llm_judge(question, answer, gt)
    except Exception as e:
        logger.warning(f"judge_answer: _llm_judge raised unexpectedly: {e}")
        return Verdict("FAIL", "WRONG_DATA", f"judge error: {type(e).__name__}", 3)


async def judge_log(
    session: AsyncSession,
    log: QueryLog,
    target_project: str | None = None,
    target_field: str | None = None,
) -> Verdict:
    """Judge a stored QueryLog row and persist verdict back onto it."""
    verdict = await judge_answer(
        session,
        question=log.question,
        answer=log.ai_response,
        target_project=target_project,
        target_field=target_field,
        sources_used=log.sources_used,
    )
    log.judge_verdict = verdict.verdict
    if verdict.verdict == "PASS":
        log.is_accurate = True
        log.failure_type = None
    elif verdict.verdict == "FAIL":
        log.is_accurate = False
        log.failure_type = verdict.failure_type
    else:
        log.is_accurate = None
        log.failure_type = verdict.failure_type
    await session.commit()
    return verdict
