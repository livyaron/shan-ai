"""Project dossiers — living per-project briefs (second brain phase 2).

Each dossier aggregates: the project's master-file row, project-linked memory
notes, and recent snapshot history — summarized by Groq into one Hebrew brief.

Token-budget design (per the research doc): dossiers are NEVER regenerated in
bulk. Sync/memory hooks only mark them dirty; a drip cron regenerates K per
cycle, and only when the content hash of the inputs actually changed — a dirty
flag with an unchanged hash clears without an LLM call.
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryNote, Project, ProjectDossier, ProjectSnapshot, SystemFlag

logger = logging.getLogger(__name__)

# SystemFlag key — value "1" pauses dossier generation (not display) without a deploy.
DOSSIER_KILL_FLAG = "dossier_kill"

# Drip size per cron cycle — protects the Groq TPD budget (233 projects ≠ 233 calls).
DRIP_BATCH = 2

_DOSSIER_PROMPT = """אתה עורך תיקי פרויקטים בארגון תשתיות חשמל. צור תיק פרויקט תמציתי בעברית (עד 1200 תווים) מהנתונים בלבד — אל תמציא דבר.

מבנה:
📌 מצב נוכחי — 2-3 משפטים (שלב, מנהל, תאריכי יעד)
🔄 שינויים אחרונים — אם קיימים בנתונים
🧠 עובדות מהצוות — אם קיימות
⚡ סיכונים ונקודות פתוחות — אם קיימים

ללא הקדמות וללא סיכום. שדה חסר — דלג על השורה."""

_DOSSIER_REQUEST_RE = re.compile(r"^תיק\s+(?:ה?פרויקט|פרוייקט)\s*[:\-]?\s*(.*)$")


def extract_dossier_request(text: str) -> Optional[str]:
    """Return the project name if `text` is a "תיק פרויקט X" request, else None.
    An empty string means the command was sent without a project name."""
    t = (text or "").strip().lstrip("‏‎").rstrip("?")
    m = _DOSSIER_REQUEST_RE.match(t)
    if not m:
        return None
    return m.group(1).strip()


async def _generation_enabled(session: AsyncSession) -> bool:
    try:
        flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == DOSSIER_KILL_FLAG))
        return not (flag and flag.value == "1")
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Dirty marking (cheap — no LLM, called from sync/memory hooks)
# ---------------------------------------------------------------------------

async def mark_dirty(project_ids: list[int]) -> None:
    """Upsert dossier rows as dirty. Opens its own session; never raises."""
    if not project_ids:
        return
    from app.database import async_session_maker
    try:
        async with async_session_maker() as session:
            for pid in set(project_ids):
                dossier = await session.scalar(
                    select(ProjectDossier).where(ProjectDossier.project_id == pid))
                if dossier:
                    dossier.is_dirty = True
                else:
                    session.add(ProjectDossier(project_id=pid, is_dirty=True))
            await session.commit()
            logger.info(f"dossier: marked {len(set(project_ids))} project(s) dirty")
    except Exception as e:
        logger.warning(f"dossier mark_dirty failed: {e}")


# ---------------------------------------------------------------------------
# Source assembly + hashing
# ---------------------------------------------------------------------------

def _fmt(val) -> str:
    if val is None or val == "":
        return "—"
    return str(val)


async def build_source(project: Project, session: AsyncSession) -> str:
    """Deterministic input text for the dossier. Same inputs → same hash → no LLM call."""
    lines = [
        f"פרויקט: {_fmt(project.name)} (זיהוי {_fmt(project.project_identifier)})",
        f"סוג: {_fmt(project.project_type)} | שלב: {_fmt(project.stage)} | מנהל: {_fmt(project.manager)}",
        f"תאריך תכנית פיתוח: {_fmt(project.dev_plan_date)} | תאריך חשמול משוער: {_fmt(project.estimated_finish_date)}",
        f"סיכונים: {_fmt(project.risks)}",
        f"לטיפול: {_fmt(project.to_handle)}",
        f"עדכון שבועי: {_fmt(project.weekly_report_brief or (project.weekly_report or '')[:400])}",
    ]

    notes = (await session.execute(
        select(MemoryNote)
        .where(
            MemoryNote.project_id == project.id,
            MemoryNote.status == "active",
            MemoryNote.superseded_by_id.is_(None),
        )
        .order_by(MemoryNote.created_at.desc())
        .limit(10)
    )).scalars().all()
    if notes:
        lines.append("עובדות מהזיכרון הארגוני:")
        for n in notes:
            stamp = n.created_at.strftime("%d.%m.%Y") if n.created_at else ""
            lines.append(f"- [{stamp}] {(n.content or '')[:250]}")

    snaps = (await session.execute(
        select(ProjectSnapshot)
        .where(ProjectSnapshot.project_id == project.id)
        .order_by(ProjectSnapshot.snapshot_date.desc())
        .limit(5)
    )).scalars().all()
    if snaps:
        lines.append("היסטוריית מצב (חדש→ישן):")
        for s in snaps:
            lines.append(
                f"- {s.snapshot_date}: שלב {_fmt(s.stage)}, חשמול {_fmt(s.estimated_finish_date)}"
            )

    return "\n".join(lines).replace('"', "״")


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Generation (drip)
# ---------------------------------------------------------------------------

async def regenerate_one(dossier: ProjectDossier, session: AsyncSession) -> bool:
    """Rebuild one dossier. Returns True if an LLM call was made.
    Unchanged source hash → clears the dirty flag without an LLM call."""
    project = await session.get(Project, dossier.project_id)
    if not project:
        dossier.is_dirty = False
        dossier.last_error = "project missing"
        await session.commit()
        return False

    source = await build_source(project, session)
    h = source_hash(source)
    if h == dossier.source_hash and dossier.content:
        dossier.is_dirty = False
        await session.commit()
        logger.info(f"dossier: {project.project_identifier} unchanged — skipped LLM")
        return False

    from app.services.llm_router import llm_chat
    try:
        content = await llm_chat(
            "project_dossier",
            messages=[
                {"role": "system", "content": _DOSSIER_PROMPT},
                {"role": "user", "content": source},
            ],
            max_tokens=800,
            temperature=0.2,
        )
        dossier.content = (content or "").strip()[:3500]
        dossier.source_hash = h
        dossier.is_dirty = False
        dossier.last_error = None
        dossier.generated_at = datetime.utcnow()
        await session.commit()
        logger.info(f"dossier: regenerated for {project.project_identifier}")
        return True
    except Exception as e:
        # Keep dirty for the next drip; record why.
        dossier.last_error = str(e)[:500]
        await session.commit()
        logger.warning(f"dossier: generation failed for {project.project_identifier}: {e}")
        raise


async def process_dirty_dossiers(batch: int = DRIP_BATCH) -> int:
    """Drip worker: regenerate up to `batch` dirty dossiers, oldest first.
    Stops the cycle on the first LLM failure (provider likely exhausted)."""
    from app.database import async_session_maker

    made_calls = 0
    try:
        async with async_session_maker() as session:
            if not await _generation_enabled(session):
                return 0
            dirty = (await session.execute(
                select(ProjectDossier)
                .where(ProjectDossier.is_dirty.is_(True))
                .order_by(ProjectDossier.updated_at.asc())
                .limit(batch)
            )).scalars().all()
            for dossier in dirty:
                try:
                    if await regenerate_one(dossier, session):
                        made_calls += 1
                except Exception:
                    break  # provider trouble — retry next drip cycle
    except Exception as e:
        logger.warning(f"process_dirty_dossiers failed: {e}")
    return made_calls


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------

async def get_dossier_text(project_id: int, session: AsyncSession) -> Optional[str]:
    try:
        dossier = await session.scalar(
            select(ProjectDossier).where(ProjectDossier.project_id == project_id))
        return dossier.content if dossier and dossier.content else None
    except Exception as e:
        logger.warning(f"get_dossier_text failed: {e}")
        return None


async def get_dossier_text_by_identifier(identifier: str, session: AsyncSession) -> Optional[str]:
    try:
        project = await session.scalar(
            select(Project).where(Project.project_identifier == identifier))
        if not project:
            return None
        return await get_dossier_text(project.id, session)
    except Exception as e:
        logger.warning(f"get_dossier_text_by_identifier failed: {e}")
        return None
