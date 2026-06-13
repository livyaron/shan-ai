"""Telegram /gold manager curation: pick the next ungolded production question
and build the approve/edit/skip/stop keyboard.

Candidate "id" is the QueryLog row id of a representative occurrence — used only
to carry the question through the callback; gold is keyed by question_hash."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog, RoleEnum, EvalGoldAnswer
from app.services.gold_truth_service import question_hash

_MANAGER_ROLES = {RoleEnum.DEPARTMENT_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER, RoleEnum.DIVISION_MANAGER}


def is_manager(user) -> bool:
    return bool(user and getattr(user, "role", None) in _MANAGER_ROLES)


def gold_keyboard(candidate_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ אשר", callback_data=f"gold:approve:{candidate_id}"),
         InlineKeyboardButton("✏️ תקן", callback_data=f"gold:edit:{candidate_id}")],
        [InlineKeyboardButton("⏭ דלג", callback_data=f"gold:skip:{candidate_id}"),
         InlineKeyboardButton("⏹ סיום", callback_data=f"gold:stop:{candidate_id}")],
    ])


async def next_candidate(session: AsyncSession, exclude_questions: set[str]) -> dict | None:
    """Return {id, question} for the next frequent production question that has
    no gold and is not in exclude_questions (normalized keys already shown this
    session). None when the queue is empty."""
    gold_hashes = set((await session.execute(select(EvalGoldAnswer.question_hash))).scalars().all())

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc()).limit(1000)
    )).scalars().all()

    seen: set[str] = set()
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_questions:
            continue
        if question_hash(r.question) in gold_hashes:
            continue
        return {"id": r.id, "question": r.question}
    return None
