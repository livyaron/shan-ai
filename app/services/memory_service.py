"""Organizational memory service — the second brain's spine.

Capture:  explicit user facts ("זכור ש...", the 🧠 save-as-fact button) and
          deterministic project-snapshot diffs written during master-file sync.
Retrieve: cosine search with a distance cutoff + ILIKE keyword fallback for
          Hebrew prefix morphology, gated behind a SystemFlag kill switch.
Injected once per question by ask_router into every answer path (project,
decision, RAG) — never a RAG-only context source.

All retrieval is non-fatal: any failure degrades to "no memories", never an error.
"""

import logging
import re
from datetime import date, datetime
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryNote, Project, ProjectAlias, SystemFlag, User

logger = logging.getLogger(__name__)

# SystemFlag key — value "1" disables memory injection without a deploy.
MEMORY_KILL_FLAG = "memory_kill"

SOURCE_USER = "user_taught"
SOURCE_SNAPSHOT = "snapshot_diff"

# Cosine distance cutoff — top-k over a small table always returns k rows,
# so without a cutoff unrelated memories pollute every answer.
MAX_COSINE_DISTANCE = 0.55
MAX_NOTES_PER_ANSWER = 5

# Project fields whose changes become temporal memory facts (Option G).
TRACKED_PROJECT_FIELDS = {
    "manager": "מנהל הפרויקט",
    "stage": "שלב הפרויקט",
    "estimated_finish_date": "תאריך החשמול המשוער",
    "dev_plan_date": "תאריך תכנית הפיתוח",
}

_HEBREW_PREFIXES = "ובכלמשה"


def _active_clause():
    """Canonical retrieval predicate — the only definition of 'retrievable'."""
    return and_(
        MemoryNote.status == "active",
        MemoryNote.superseded_by_id.is_(None),
        or_(MemoryNote.valid_until.is_(None), MemoryNote.valid_until > datetime.utcnow()),
    )


# ---------------------------------------------------------------------------
# Capture — explicit "זכור ש..." parsing
# ---------------------------------------------------------------------------

_REMEMBER_RE = re.compile(r"^(?:תזכור|תזכרי|זכור|זכרי)\b\s*[:\-–]?\s*(.*)$")
_RECALL_PREFIXES = ("מה אתה זוכר", "מה את זוכרת", "מה אתם זוכרים")


def extract_remember_content(text: str) -> Optional[str]:
    """Return the fact content if `text` is an explicit remember command, else None.

    Handles: "זכור ש...", "זכור: ...", "תזכור כי ...". Deterministic prefix
    match only — softer phrasings are covered by the 🧠 save-as-fact button.
    """
    t = (text or "").strip().lstrip("‏‎")
    m = _REMEMBER_RE.match(t)
    if not m:
        return None
    content = m.group(1).strip()
    # Strip a leading "ש"/"כי" connective ("זכור שדני..." → "דני...")
    if content.startswith("כי "):
        content = content[3:].strip()
    elif content.startswith("ש") and len(content) > 1 and content[1] != " ":
        content = content[1:].strip()
    elif content.startswith("ש "):
        content = content[2:].strip()
    if len(content) < 4:
        return None
    # JSON safety — straight quotes break Groq prompts downstream (CLAUDE.md §5)
    return content.replace('"', "״")


def is_recall_query(text: str) -> bool:
    """True for "מה אתה זוכר [על X]" — list stored memories instead of answering."""
    t = (text or "").strip().lstrip("‏‎")
    return any(t.startswith(p) for p in _RECALL_PREFIXES)


def extract_recall_topic(text: str) -> str:
    """Topic after "מה אתה זוכר על ..." — empty string means "list recent"."""
    t = (text or "").strip().lstrip("‏‎").rstrip("?")
    for p in _RECALL_PREFIXES:
        if t.startswith(p):
            t = t[len(p):].strip()
            break
    if t.startswith("על "):
        t = t[3:].strip()
    return t


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------

async def is_memory_enabled(session: AsyncSession) -> bool:
    """False when the memory_kill SystemFlag is set — disables injection without a deploy."""
    try:
        flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == MEMORY_KILL_FLAG))
        return not (flag and flag.value == "1")
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Save / forget
# ---------------------------------------------------------------------------

async def save_memory(
    session: AsyncSession,
    *,
    content: str,
    user_id: Optional[int],
    project_id: Optional[int] = None,
    source: str = SOURCE_USER,
    tags: Optional[dict] = None,
    valid_until: Optional[datetime] = None,
) -> MemoryNote:
    """Embed and store a memory note. Raises on failure — callers show an error."""
    from app.services.embedding_service import embed

    content = content.strip().replace('"', "״")
    embedding = None
    try:
        embedding = await embed(content)
    except Exception as e:
        logger.warning(f"save_memory: embedding failed, storing without vector: {e}")

    note = MemoryNote(
        content=content,
        embedding=embedding,
        created_by_id=user_id,
        project_id=project_id,
        source=source,
        tags=tags,
        valid_until=valid_until,
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    logger.info(f"save_memory: #{note.id} source={source} project={project_id}: {content[:80]}")
    return note


async def forget_memory(session: AsyncSession, note_id: int, user_id: Optional[int]) -> bool:
    """Soft-delete: status='rejected'. Row kept for provenance/audit."""
    note = await session.get(MemoryNote, note_id)
    if not note or note.status == "rejected":
        return False
    note.status = "rejected"
    note.updated_at = datetime.utcnow()
    await session.commit()
    logger.info(f"forget_memory: #{note_id} rejected by user {user_id}")
    return True


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _keyword_terms(question: str) -> list[str]:
    """Significant Hebrew terms (with prefix-stripped variants) for the ILIKE path."""
    from app.services.knowledge_service import _extract_query_phrases, normalize_hebrew

    _STOP = {"מה", "מי", "מתי", "איך", "האם", "כמה", "איזה", "אילו", "של", "על",
             "את", "יש", "לא", "כן", "זה", "זו", "פרויקט", "הפרויקט", "סטטוס",
             "מנהל", "מנהלת", "שלב", "עדכון", "תאריך", "אתה", "זוכר"}
    terms: list[str] = []
    for p in _extract_query_phrases(question):
        norm = normalize_hebrew(p)
        if len(norm) < 3 or norm in _STOP:
            continue
        terms.append(norm)
        # Prefix morphology: question says "בחדרה", note says "חדרה"
        if " " not in norm and norm[0] in _HEBREW_PREFIXES and len(norm) > 3:
            stripped = norm[1:]
            if stripped not in _STOP:
                terms.append(stripped)
    return terms[:8]


async def get_relevant_memories(
    question: str,
    session: AsyncSession,
    limit: int = MAX_NOTES_PER_ANSWER,
    max_distance: float = MAX_COSINE_DISTANCE,
) -> list[MemoryNote]:
    """Hybrid retrieval: cosine-with-cutoff + ILIKE keyword union. Never raises."""
    try:
        if not await is_memory_enabled(session):
            return []

        hits: list[MemoryNote] = []
        seen: set[int] = set()

        # Path A — vector search with distance cutoff
        try:
            from app.services.embedding_service import embed
            qvec = await embed(question)
            dist = MemoryNote.embedding.cosine_distance(qvec)
            rows = (await session.execute(
                select(MemoryNote, dist.label("dist"))
                .where(_active_clause())
                .where(MemoryNote.embedding.isnot(None))
                .order_by(dist)
                .limit(limit)
            )).all()
            for note, d in rows:
                if d is not None and float(d) <= max_distance:
                    hits.append(note)
                    seen.add(note.id)
        except Exception as e:
            logger.warning(f"get_relevant_memories: vector path failed: {e}")

        # Path B — ILIKE keywords (catches Hebrew prefix forms the vector misses)
        terms = _keyword_terms(question)
        if terms:
            kw_rows = (await session.execute(
                select(MemoryNote)
                .where(_active_clause())
                .where(or_(*[MemoryNote.content.ilike(f"%{t}%") for t in terms]))
                .order_by(MemoryNote.created_at.desc())
                .limit(limit)
            )).scalars().all()
            for note in kw_rows:
                if note.id not in seen:
                    hits.append(note)
                    seen.add(note.id)

        return hits[:limit]
    except Exception as e:
        logger.warning(f"get_relevant_memories failed: {e}")
        return []


def format_memory_context(notes: list[MemoryNote]) -> str:
    """Format notes as a context block. Prepended before chunk context so it
    survives the tail-slice truncation in answer_with_full_context."""
    if not notes:
        return ""
    lines = ["🧠 עובדות מהזיכרון הארגוני (נלמדו מהצוות; אם עובדה סותרת את נתוני קובץ המאסטר — נתוני הקובץ גוברים):"]
    for i, n in enumerate(notes, 1):
        stamp = n.created_at.strftime("%d.%m.%Y") if n.created_at else ""
        content = (n.content or "")[:300]
        lines.append(f"{i}. [{stamp}] {content}")
    return "\n".join(lines)


async def list_memories(
    session: AsyncSession,
    topic: str = "",
    limit: int = 8,
) -> list[MemoryNote]:
    """For "מה אתה זוכר [על X]" — topical (looser cutoff) or recent-first list."""
    try:
        if topic:
            return await get_relevant_memories(topic, session, limit=limit, max_distance=0.75)
        return (await session.execute(
            select(MemoryNote).where(_active_clause())
            .order_by(MemoryNote.created_at.desc()).limit(limit)
        )).scalars().all()
    except Exception as e:
        logger.warning(f"list_memories failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Project linking — high-confidence only (a wrong link is worse than null)
# ---------------------------------------------------------------------------

async def link_project(content: str, session: AsyncSession) -> tuple[Optional[int], Optional[str]]:
    """Link a fact to a project only on an unambiguous alias/name match.

    Returns (project_id, display_label) or (None, None) when zero or multiple
    projects match — ambiguous facts stay general rather than mislinked.
    """
    try:
        from app.services.knowledge_service import normalize_hebrew

        norm = normalize_hebrew(content)
        matches: dict[int, str] = {}

        aliases = (await session.execute(select(ProjectAlias))).scalars().all()
        for a in aliases:
            if a.normalized_alias and a.normalized_alias in norm:
                matches[a.project_id] = a.alias_text

        projects = (await session.execute(
            select(Project).where(Project.is_active.is_(True))
        )).scalars().all()
        for p in projects:
            pname = normalize_hebrew(p.name or "")
            if len(pname) >= 3 and pname in norm:
                matches[p.id] = p.name or p.project_identifier

        if len(matches) == 1:
            pid, label = next(iter(matches.items()))
            return pid, label
        return None, None
    except Exception as e:
        logger.warning(f"link_project failed: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Option G — snapshot-diff temporal facts (deterministic, no LLM)
# ---------------------------------------------------------------------------

def _fmt_value(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, (date, datetime)):
        return val.strftime("%d.%m.%Y")
    return str(val)


def build_change_fact(change: dict, on_date: Optional[date] = None) -> Optional[str]:
    """Hebrew temporal fact for one field change. None for untracked/first-fill."""
    field = change.get("field")
    label = TRACKED_PROJECT_FIELDS.get(field)
    old, new = change.get("old"), change.get("new")
    if not label or old in (None, ""):
        return None
    name = change.get("name") or change.get("identifier") or ""
    when = (on_date or date.today()).strftime("%d.%m.%Y")
    if new in (None, ""):
        return f'בפרויקט {name}: {label} הוסר (היה ״{_fmt_value(old)}״) — עדכון קובץ מאסטר {when}'
    return (f'בפרויקט {name}: {label} התעדכן מ״{_fmt_value(old)}״ ל״{_fmt_value(new)}״'
            f' — עדכון קובץ מאסטר {when}')


async def record_project_changes(changes: list[dict]) -> int:
    """Write snapshot-diff memory notes for master-sync field changes.

    A new note for a (project, field) pair supersedes the previous snapshot_diff
    note for that same pair — deterministic supersession, no similarity guessing.
    Opens its own session (called as a fire-and-forget task after sync).
    """
    from app.database import async_session_maker

    written = 0
    try:
        async with async_session_maker() as session:
            for change in changes:
                fact = build_change_fact(change)
                if not fact:
                    continue
                project_id = change.get("project_id")
                field = change.get("field")

                note = await save_memory(
                    session,
                    content=fact,
                    user_id=None,
                    project_id=project_id,
                    source=SOURCE_SNAPSHOT,
                    tags={"field": field},
                )

                # Supersede the previous note for the same (project, field)
                try:
                    prev_rows = (await session.execute(
                        select(MemoryNote).where(
                            MemoryNote.project_id == project_id,
                            MemoryNote.source == SOURCE_SNAPSHOT,
                            MemoryNote.superseded_by_id.is_(None),
                            MemoryNote.id != note.id,
                        )
                    )).scalars().all()
                    for prev in prev_rows:
                        if (prev.tags or {}).get("field") == field:
                            prev.superseded_by_id = note.id
                            prev.updated_at = datetime.utcnow()
                    await session.commit()
                except Exception as e:
                    logger.warning(f"record_project_changes: supersession failed: {e}")
                written += 1
    except Exception as e:
        logger.error(f"record_project_changes failed: {e}", exc_info=True)
    if written:
        logger.info(f"record_project_changes: {written} temporal facts written")
    return written


# ---------------------------------------------------------------------------
# Display helpers (Telegram)
# ---------------------------------------------------------------------------

async def describe_notes(notes: list[MemoryNote], session: AsyncSession) -> list[str]:
    """Human-readable lines for the recall listing: content + date + author."""
    lines = []
    for n in notes:
        stamp = n.created_at.strftime("%d.%m.%Y") if n.created_at else ""
        author = "סנכרון קובץ" if n.source == SOURCE_SNAPSHOT else ""
        if n.created_by_id:
            try:
                u = await session.get(User, n.created_by_id)
                author = (u.username if u else "") or author
            except Exception:
                pass
        suffix = f" ({stamp}" + (f", {author}" if author else "") + ")"
        lines.append(f"{(n.content or '')[:200]}{suffix}")
    return lines
