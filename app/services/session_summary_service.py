"""Cross-session conversation memory (second brain, Option D).

The in-memory conversation deque dies with the process and with time — this
service gives the bot continuity across days: a nightly job folds each active
user's last-day exchanges into a rolling summary, and a fresh session (empty
in-memory context) starts with that summary injected as conversation context.

The `messages` table stores only the user side (bot replies are not persisted),
so the exchange log is reconstructed by merging `messages` with `query_logs`
answers chronologically.

Kill switch: SystemFlag `session_summary_kill` = "1".
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationSummary, Message, QueryLog, SystemFlag, User

logger = logging.getLogger(__name__)

SUMMARY_KILL_FLAG = "session_summary_kill"
MAX_SUMMARY_CHARS = 800
LOOKBACK_HOURS = 26   # nightly job + slack

_SUMMARY_PROMPT = """אתה מסכם שיחות עבודה בין משתמש לבוט ניהול פרויקטים של תשתיות חשמל.
עדכן את הסיכום המצטבר כך שישקף את הנושאים הפתוחים והעדכניים: מה המשתמש עסק בו, החלטות שדוברו, פרויקטים שנשאלו עליהם.
עד 800 תווים, בעברית, ללא הקדמות. שמור נושאים חשובים מהסיכום הקודם שעדיין רלוונטיים; השמט מה שהסתיים."""


async def _summaries_enabled(session: AsyncSession) -> bool:
    try:
        flag = await session.scalar(
            select(SystemFlag).where(SystemFlag.key == SUMMARY_KILL_FLAG))
        return not (flag and flag.value == "1")
    except Exception:
        return True


async def build_exchange_log(user_id: int, session: AsyncSession,
                             since: datetime) -> str:
    """Chronological user/bot exchange text from messages + query_logs."""
    msgs = (await session.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.created_at >= since)
        .order_by(Message.created_at.asc())
        .limit(30)
    )).scalars().all()
    answers = (await session.execute(
        select(QueryLog)
        .where(QueryLog.user_id == user_id, QueryLog.timestamp >= since)
        .order_by(QueryLog.timestamp.asc())
        .limit(30)
    )).scalars().all()

    entries: list[tuple[datetime, str]] = []
    for m in msgs:
        if m.content:
            entries.append((m.created_at, f"משתמש: {m.content[:250]}"))
    for q in answers:
        if q.ai_response:
            entries.append((q.timestamp, f"בוט: {q.ai_response[:250]}"))
    entries.sort(key=lambda e: e[0] or datetime.min)
    return "\n".join(text for _ts, text in entries)


async def summarize_user(user_id: int, session: AsyncSession) -> Optional[str]:
    """Fold the user's recent exchanges into their rolling summary. Returns it."""
    since = datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
    log = await build_exchange_log(user_id, session, since)
    if not log:
        return None

    existing = await session.scalar(
        select(ConversationSummary).where(ConversationSummary.user_id == user_id))
    prev = existing.summary if existing else ""

    from app.services.llm_router import llm_chat
    user_content = (
        (f"סיכום קודם:\n{prev}\n\n" if prev else "")
        + f"השיחות מהיממה האחרונה:\n{log}"
    ).replace('"', "״")
    summary = (await llm_chat(
        "session_summary",
        messages=[
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=400,
        temperature=0.2,
    ) or "").strip()[:MAX_SUMMARY_CHARS]
    if not summary:
        return None

    if existing:
        existing.summary = summary
        existing.updated_at = datetime.utcnow()
    else:
        session.add(ConversationSummary(user_id=user_id, summary=summary))
    await session.commit()
    return summary


async def run_daily_summaries() -> int:
    """Nightly job: refresh summaries for users active in the last day. Own session."""
    from app.database import async_session_maker
    from app.services import job_guard

    done = 0
    try:
        async with async_session_maker() as session:
            if not await _summaries_enabled(session):
                return 0
            if not await job_guard.claim(session, "session_summaries"):
                return 0
            since = datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
            user_ids = (await session.execute(
                select(Message.user_id)
                .where(Message.created_at >= since)
                .group_by(Message.user_id)
            )).scalars().all()
            for uid in user_ids:
                try:
                    if await summarize_user(uid, session):
                        done += 1
                except Exception as e:
                    logger.warning(f"session summary failed for user {uid}: {e}")
            logger.info(f"session summaries: refreshed {done}/{len(user_ids)}")
    except Exception as e:
        logger.error(f"run_daily_summaries failed: {e}", exc_info=True)
    return done


async def get_summary(user_id: int, session: AsyncSession) -> Optional[str]:
    """The user's rolling summary, or None. Never raises."""
    try:
        row = await session.scalar(
            select(ConversationSummary).where(ConversationSummary.user_id == user_id))
        return row.summary if row else None
    except Exception:
        return None
