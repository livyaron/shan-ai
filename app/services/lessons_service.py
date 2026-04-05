"""Lessons learned service — extracts, stores, and retrieves organizational lessons from completed decisions."""

import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.models import LessonLearned, Decision, DecisionRaciRole, RaciRoleEnum, User, KnowledgeSummary

logger = logging.getLogger(__name__)


async def extract_and_save_lesson(decision_id: int) -> None:
    """
    After a decision receives feedback, extract a structured lesson via Groq and store it.
    Opens its own DB session. Never raises — all failures are logged and swallowed.
    """
    from app.database import async_session_maker

    try:
        async with async_session_maker() as session:
            decision = await session.get(Decision, decision_id)
            if not decision:
                return
            if not decision.feedback_notes and not decision.feedback_score:
                logger.info(f"extract_and_save_lesson: no feedback yet for decision {decision_id}, skipping")
                return

            # Check if lesson already extracted for this decision
            existing = await session.scalar(
                select(LessonLearned).where(LessonLearned.decision_id == decision_id)
            )
            if existing:
                logger.info(f"extract_and_save_lesson: lesson already exists for decision {decision_id}")
                return

            # Fetch RACI context
            raci_rows = (await session.execute(
                select(DecisionRaciRole, User)
                .join(User, DecisionRaciRole.user_id == User.id)
                .where(DecisionRaciRole.decision_id == decision_id)
            )).all()
            raci_by_role: dict[str, list[str]] = {"R": [], "A": [], "C": [], "I": []}
            for raci_row, user in raci_rows:
                raci_by_role[raci_row.role.value].append(user.username)
            raci_str = " | ".join(
                f"{role}: {', '.join(names)}"
                for role, names in raci_by_role.items() if names
            ) or "לא הוקצה RACI"

            prompt = f"""אתה מנתח החלטות ארגוניות. על בסיס ההחלטה המושלמת הבאה, חלץ לקח מנחה אחד שיסייע לניתוח החלטות עתידיות דומות.

סוג ההחלטה: {decision.type.value}
סיכום: {decision.summary or '—'}
בעיה: {decision.problem_description or '—'}
פעולה שבוצעה: {decision.recommended_action or '—'}
סטטוס סופי: {decision.status.value}
ציון פידבק: {decision.feedback_score or '—'}/5
הערות פידבק: {decision.feedback_notes or '—'}
RACI: {raci_str}

הנחיות:
- חלץ לקח אחד ממוקד (2-3 משפטים) בעברית
- הלקח צריך להיות שימושי לניתוח החלטות עתידיות מאותו סוג
- כלול: מה עבד טוב, מה ניתן לשפר, ומה כדאי לשים לב אליו
- בחר 2-4 תגיות (מילות מפתח) רלוונטיות

החזר JSON בלבד:
{{"lesson": "הלקח כאן...", "tags": ["תגית1", "תגית2"], "decision_type": "{decision.type.value}"}}"""

            from app.services.groq_client import groq_chat
            raw = await groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                json_mode=True,
            )

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                start, end = raw.find("{"), raw.rfind("}") + 1
                if start == -1 or end == 0:
                    logger.warning(f"extract_and_save_lesson: no JSON in response for decision {decision_id}")
                    return
                parsed = json.loads(raw[start:end])

            lesson_text = parsed.get("lesson", "").strip()
            tags = parsed.get("tags", [])
            if not lesson_text:
                logger.warning(f"extract_and_save_lesson: empty lesson for decision {decision_id}")
                return

            # Embed the lesson
            from app.services.embedding_service import embed
            embedding = await embed(lesson_text)

            lesson = LessonLearned(
                decision_id=decision_id,
                lesson_text=lesson_text,
                decision_type=decision.type.value,
                tags=json.dumps(tags, ensure_ascii=False),
                embedding=embedding,
            )
            session.add(lesson)
            await session.commit()
            logger.info(f"extract_and_save_lesson: saved lesson for decision {decision_id}: {lesson_text[:80]}")

    except Exception as e:
        logger.error(f"extract_and_save_lesson: failed for decision {decision_id}: {e}", exc_info=True)


async def get_relevant_lessons(query_text: str, session: AsyncSession, limit: int = 3) -> list[LessonLearned]:
    """Find the most relevant lessons for a query using cosine similarity."""
    try:
        from app.services.embedding_service import embed
        query_vector = await embed(query_text)
        result = await session.execute(
            select(LessonLearned)
            .where(LessonLearned.embedding.isnot(None))
            .order_by(LessonLearned.embedding.cosine_distance(query_vector))
            .limit(limit)
        )
        return result.scalars().all()
    except Exception as e:
        logger.warning(f"get_relevant_lessons: search failed: {e}")
        return []


def format_lessons_context(lessons: list[LessonLearned]) -> str:
    """Format lessons as context string for the AI analysis prompt."""
    if not lessons:
        return ""
    lines = ["לקחים מהניסיון הארגוני (למד מהם):"]
    for i, lesson in enumerate(lessons, 1):
        tags = ""
        try:
            tag_list = json.loads(lesson.tags or "[]")
            if tag_list:
                tags = f" [{', '.join(tag_list)}]"
        except Exception:
            pass
        lines.append(f"{i}. {lesson.lesson_text}{tags}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 2 — RACI patterns, risk patterns, calibration
# ---------------------------------------------------------------------------

async def get_raci_patterns(decision_type: str, session: AsyncSession) -> str:
    """
    Find which specific users (with their responsibilities) were assigned which RACI roles
    in past high-feedback decisions of the same type.
    Returns a formatted string for the RACI assignment prompt.
    """
    try:
        from app.models import DecisionRaciRole, Decision as _Decision, User as _User
        from sqlalchemy import func as _func

        # Per-user counts: how many times each user was in each RACI role for this type (high feedback)
        rows = (await session.execute(
            select(
                DecisionRaciRole.role,
                _User.id.label("user_id"),
                _User.username,
                _User.job_title,
                _User.responsibilities,
                _func.count().label("cnt"),
            )
            .join(_Decision, DecisionRaciRole.decision_id == _Decision.id)
            .join(_User, DecisionRaciRole.user_id == _User.id)
            .where(_Decision.type == decision_type)
            .where(_Decision.feedback_score >= 4)
            .group_by(DecisionRaciRole.role, _User.id, _User.username, _User.job_title, _User.responsibilities)
            .order_by(_func.count().desc())
            .limit(20)
        )).all()

        if not rows:
            return ""

        RACI_HE = {"R": "ביצוע", "A": "סמכות", "C": "יועץ", "I": "לידיעה"}

        by_raci: dict[str, list[str]] = {}
        for raci_role, user_id, username, job_title, responsibilities, cnt in rows:
            role_val = raci_role.value if hasattr(raci_role, "value") else str(raci_role)
            desc = username
            if job_title:
                desc += f" ({job_title})"
            if responsibilities:
                desc += f" — {responsibilities}"
            label = f"{desc} [{cnt}×]"
            by_raci.setdefault(role_val, []).append(label)

        lines = [f"דפוסי RACI מוצלחים עבור החלטות מסוג {decision_type} (פידבק ≥4):"]
        for raci_role, labels in by_raci.items():
            lines.append(f"  {raci_role} ({RACI_HE.get(raci_role, raci_role)}): {', '.join(labels[:3])}")
        lines.append("→ כאשר משתמש עם תחום דומה קיים ברשימה — העדף אותו לאותו תפקיד RACI.")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"get_raci_patterns failed: {e}")
        return ""


async def get_risk_patterns(decision_type: str, session: AsyncSession) -> str:
    """
    Extract the most recurring risks from past high-feedback decisions of the same type.
    Returns a formatted string for the analysis prompt.
    """
    try:
        from app.models import Decision as _Decision

        rows = (await session.execute(
            select(_Decision.risks)
            .where(_Decision.type == decision_type)
            .where(_Decision.feedback_score >= 3)
            .where(_Decision.risks.isnot(None))
            .order_by(_Decision.created_at.desc())
            .limit(20)
        )).scalars().all()

        if not rows:
            return ""

        risk_counts: dict[str, int] = {}
        for risks_json in rows:
            try:
                for risk in json.loads(risks_json):
                    r = risk.strip()
                    if r:
                        risk_counts[r] = risk_counts.get(r, 0) + 1
            except Exception:
                continue

        top_risks = sorted(risk_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        if not top_risks:
            return ""

        lines = [f"סיכונים נפוצים בהחלטות {decision_type} מהעבר:"]
        for risk, cnt in top_risks:
            lines.append(f"  • {risk} ({cnt}×)")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"get_risk_patterns failed: {e}")
        return ""


async def get_calibration_hint(decision_type: str, session: AsyncSession) -> str:
    """Calibration hints disabled (no longer using confidence metric)."""
    return ""


# ---------------------------------------------------------------------------
# Phase 4 — Batch extraction & knowledge summaries
# ---------------------------------------------------------------------------

async def get_pending_extraction_count(session: AsyncSession) -> int:
    """Count decisions with feedback but no lesson extracted yet."""
    try:
        extracted_ids = select(LessonLearned.decision_id)
        result = await session.scalar(
            select(func.count())
            .select_from(Decision)
            .where(Decision.feedback_score.isnot(None))
            .where(Decision.id.notin_(extracted_ids))
        )
        return result or 0
    except Exception as e:
        logger.warning(f"get_pending_extraction_count failed: {e}")
        return 0


async def run_batch_extraction() -> dict:
    """
    Find all decisions with feedback but no lesson, extract lessons for each,
    then regenerate knowledge summaries for affected types.
    Opens its own DB session. Returns summary stats.
    """
    from app.database import async_session_maker

    processed = 0
    failed = 0
    affected_types: set[str] = set()

    try:
        async with async_session_maker() as session:
            extracted_ids = select(LessonLearned.decision_id)
            rows = (await session.execute(
                select(Decision.id, Decision.type)
                .where(Decision.feedback_score.isnot(None))
                .where(Decision.id.notin_(extracted_ids))
                .order_by(Decision.created_at.asc())
            )).all()

        for decision_id, decision_type in rows:
            try:
                await extract_and_save_lesson(decision_id)
                processed += 1
                affected_types.add(decision_type.value if hasattr(decision_type, "value") else str(decision_type))
            except Exception as e:
                logger.warning(f"run_batch_extraction: failed for decision {decision_id}: {e}")
                failed += 1

        # Regenerate summaries for all affected types
        for dtype in affected_types:
            try:
                await generate_knowledge_summary(dtype)
            except Exception as e:
                logger.warning(f"run_batch_extraction: summary generation failed for {dtype}: {e}")

        logger.info(f"run_batch_extraction: processed={processed}, failed={failed}, types={affected_types}")
        return {"processed": processed, "failed": failed, "types": list(affected_types)}

    except Exception as e:
        logger.error(f"run_batch_extraction: outer error: {e}", exc_info=True)
        return {"processed": processed, "failed": failed, "types": []}


async def generate_knowledge_summary(decision_type: str) -> None:
    """
    Aggregate all lessons for a decision type into a structured knowledge guide via Groq.
    Upserts a KnowledgeSummary row.
    Opens its own DB session.
    """
    from app.database import async_session_maker

    try:
        async with async_session_maker() as session:
            lessons = (await session.execute(
                select(LessonLearned)
                .where(LessonLearned.decision_type == decision_type)
                .order_by(LessonLearned.created_at.desc())
                .limit(50)
            )).scalars().all()

            if not lessons:
                return

            lesson_count = len(lessons)
            lessons_text = "\n".join(
                f"{i+1}. {ll.lesson_text}" for i, ll in enumerate(lessons)
            )

            TYPE_HE = {
                "info": "מידע", "normal": "רגיל",
                "critical": "קריטי", "uncertain": "לא ודאי",
            }
            type_he = TYPE_HE.get(decision_type, decision_type)

            prompt = f"""אתה מנתח ידע ארגוני. להלן {lesson_count} לקחים שנלמדו מהחלטות מסוג "{type_he}" בארגון.

{lessons_text}

צור סיכום ידע ארגוני מובנה בעברית שיכלול:
1. עקרונות מפתח לקבלת החלטות מסוג זה (3-5 נקודות)
2. סיכונים נפוצים שיש להיזהר מהם
3. גורמי הצלחה — מה עובד טוב בדרך כלל
4. המלצה לניתוח AI — כיצד לשפר את הניתוח

החזר JSON בלבד:
{{"principles": ["עיקרון 1", "עיקרון 2", ...], "risks": ["סיכון 1", ...], "success_factors": ["גורם 1", ...], "ai_guidance": "הנחיה לשיפור ה-AI"}}"""

            from app.services.groq_client import groq_chat
            raw = await groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                json_mode=True,
            )

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                start, end = raw.find("{"), raw.rfind("}") + 1
                if start == -1 or end == 0:
                    return
                parsed = json.loads(raw[start:end])

            summary_text = json.dumps(parsed, ensure_ascii=False)

            # Upsert
            existing = await session.scalar(
                select(KnowledgeSummary).where(KnowledgeSummary.decision_type == decision_type)
            )
            if existing:
                existing.summary_text = summary_text
                existing.lesson_count = lesson_count
                existing.updated_at = __import__("datetime").datetime.utcnow()
            else:
                session.add(KnowledgeSummary(
                    decision_type=decision_type,
                    summary_text=summary_text,
                    lesson_count=lesson_count,
                ))
            await session.commit()
            logger.info(f"generate_knowledge_summary: saved summary for {decision_type} ({lesson_count} lessons)")

    except Exception as e:
        logger.error(f"generate_knowledge_summary failed for {decision_type}: {e}", exc_info=True)


async def get_knowledge_summaries(session: AsyncSession) -> list[KnowledgeSummary]:
    """Fetch all knowledge summaries ordered by decision type."""
    try:
        return (await session.execute(
            select(KnowledgeSummary).order_by(KnowledgeSummary.updated_at.desc())
        )).scalars().all()
    except Exception as e:
        logger.warning(f"get_knowledge_summaries failed: {e}")
        return []
