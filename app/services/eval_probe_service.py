"""Agent 1 — Probe.

Generates Hebrew test questions for the eval loop. Mixes:
  60% novel probes derived from corpus stats (project names, managers, stages, dates)
  40% rephrasings of recent failed/thumbs-down questions (so we re-test fixes).

The probe set drives downstream Judge → Repair → Verify stages.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, asdict
from typing import Any

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, QueryLog
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


@dataclass
class ProbeQuestion:
    question: str
    target_project: str | None = None
    target_field: str | None = None        # Hebrew field name from FIELD_ALIAS_MAP
    expected_kind: str | None = None       # "lookup" | "aggregation" | "date" | "edge"
    seeded_from_log_id: int | None = None  # if rephrased from a failure

    def to_dict(self) -> dict:
        return asdict(self)


async def gather_corpus_stats(session: AsyncSession, sample_n: int = 8) -> dict[str, Any]:
    """Read distinct managers, stages, types + a random project sample from Project table."""
    managers_q = select(Project.manager).where(Project.manager.isnot(None), Project.is_active.is_(True)).distinct()
    stages_q = select(Project.stage).where(Project.stage.isnot(None), Project.is_active.is_(True)).distinct()
    types_q = select(Project.project_type).where(Project.project_type.isnot(None), Project.is_active.is_(True)).distinct()

    managers = [m for m in (await session.execute(managers_q)).scalars().all() if m]
    stages = [s for s in (await session.execute(stages_q)).scalars().all() if s]
    types = [t for t in (await session.execute(types_q)).scalars().all() if t]

    sample_q = (
        select(Project.name, Project.manager, Project.stage,
               Project.estimated_finish_date, Project.dev_plan_date)
        .where(Project.is_active.is_(True), Project.name.isnot(None))
        .order_by(func.random())
        .limit(sample_n)
    )
    sample_rows = (await session.execute(sample_q)).all()
    sample = [
        {
            "name": r[0],
            "manager": r[1],
            "stage": r[2],
            "estimated_finish_date": r[3].isoformat() if r[3] else None,
            "dev_plan_date": r[4].isoformat() if r[4] else None,
        }
        for r in sample_rows
    ]

    total = (await session.execute(select(func.count(Project.id)).where(Project.is_active.is_(True)))).scalar() or 0

    return {
        "n_projects": int(total),
        "managers": managers[:30],
        "stages": stages[:20],
        "project_types": types[:20],
        "sample_projects": sample,
    }


async def collect_recent_failures(session: AsyncSession, limit: int = 30) -> list[QueryLog]:
    """Recent QueryLog rows worth re-probing — explicit thumbs-down OR a failed judge verdict."""
    stmt = (
        select(QueryLog)
        .where(or_(
            QueryLog.user_feedback == -1,
            QueryLog.is_accurate.is_(False),
            QueryLog.judge_verdict == "FAIL",
        ))
        .order_by(QueryLog.timestamp.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


_PROBE_SYSTEM_PROMPT = """אתה יוצר שאלות בדיקה לבוט עברית של חברת תשתית חשמל.
מטרה: לחשוף תקלות במנוע ה-Q&A — שמות פרויקטים מבולבלים, ראשי תיבות שאינם מתורגמים,
שדות שלא נשלפים נכון, אגרגציות שגויות.

קלט: סטטיסטיקה של מאגר הפרויקטים + (אופציונלי) רשימת שאלות שכשלו לאחרונה.
פלט: JSON בלבד עם רשימת שאלות בעברית במבנה:
{
  "probes": [
    {
      "question": "שאלה בעברית",
      "target_project": "<שם פרויקט מהמאגר או null>",
      "target_field": "<שם שדה כמו 'מנהל' / 'תאריך חישמול' / 'סטטוס' או null>",
      "expected_kind": "lookup" | "aggregation" | "date" | "edge"
    }
  ]
}

הנחיות:
- 60% מהשאלות חדשות לחלוטין על נתוני המאגר (שמות פרויקטים אמיתיים מהדוגמאות).
- 40% מהשאלות וריאציות פרזיולוגיות של השאלות שכשלו (אותו צורך, ניסוח אחר).
- כלול 2-3 שאלות "edge": שם פרויקט שלא קיים, שדה ריק, ראשי תיבות לא סטנדרטיים.
- ערב שאלות lookup פשוטות ("מי מנהל פרויקט X?") עם aggregations ("כמה פרויקטים בסטטוס Y?").
- שאלות בעברית בלבד.
- ללא מרקדאון. JSON בלבד.
"""


def _format_failures(failures: list[QueryLog]) -> list[dict]:
    out = []
    for f in failures[:12]:
        out.append({
            "log_id": f.id,
            "question": (f.question or "")[:200],
            "ai_response": (f.ai_response or "")[:200],
            "failure_type": f.failure_type,
        })
    return out


async def generate_probes(
    session: AsyncSession,
    n: int = 20,
    seed_failures: bool = True,
) -> list[ProbeQuestion]:
    stats = await gather_corpus_stats(session)
    failures = await collect_recent_failures(session) if seed_failures else []
    payload = {
        "n_requested": n,
        "stats": stats,
        "recent_failures": _format_failures(failures),
    }

    try:
        raw = await llm_chat(
            "eval_probe",
            messages=[
                {"role": "system", "content": _PROBE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.7,
            max_tokens=2500,
            json_mode=True,
        )
        data = json.loads(raw)
        items = data.get("probes") or []
    except Exception as e:
        logger.error(f"generate_probes LLM call failed: {e}")
        # Fallback: synthesize a tiny deterministic set from corpus stats.
        return _fallback_probes(stats, n)

    probes: list[ProbeQuestion] = []
    failure_qs = {f.question.strip(): f.id for f in failures if f.question}
    for it in items[:n]:
        if not isinstance(it, dict):
            continue
        q = (it.get("question") or "").strip()
        if not q:
            continue
        seed_id = None
        # If the model rephrased an existing failure, link it back via fuzzy match.
        for fq, fid in failure_qs.items():
            if fq and (fq[:30] in q or q[:30] in fq):
                seed_id = fid
                break
        probes.append(ProbeQuestion(
            question=q,
            target_project=(it.get("target_project") or None),
            target_field=(it.get("target_field") or None),
            expected_kind=(it.get("expected_kind") or None),
            seeded_from_log_id=seed_id,
        ))

    if not probes:
        return _fallback_probes(stats, n)
    return probes


def _fallback_probes(stats: dict, n: int) -> list[ProbeQuestion]:
    """Deterministic safety net when LLM probe generation fails."""
    probes: list[ProbeQuestion] = []
    sample = stats.get("sample_projects") or []
    for s in sample[:max(1, n // 3)]:
        name = s.get("name")
        if not name:
            continue
        probes.append(ProbeQuestion(
            question=f"מי המנהל של פרויקט {name}?",
            target_project=name,
            target_field="מנהל",
            expected_kind="lookup",
        ))
        probes.append(ProbeQuestion(
            question=f"מה תאריך החישמול של {name}?",
            target_project=name,
            target_field="תאריך חישמול",
            expected_kind="date",
        ))
    managers = stats.get("managers") or []
    if managers:
        probes.append(ProbeQuestion(
            question=f"כמה פרויקטים יש למנהל {random.choice(managers)}?",
            target_field="מנהל",
            expected_kind="aggregation",
        ))
    return probes[:n]
