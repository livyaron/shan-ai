"""Auto-seed gold answers from production query_logs.

For each distinct frequent question without gold, ask propose_gold for a
DB-only (deterministic) answer. If one exists, save it as gold automatically
(source="db_lookup"). Questions needing an LLM answer are left for human
curation (web curate UI or Telegram /gold) and counted as needs_manual.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import propose_gold, save_gold, list_gold, question_hash

logger = logging.getLogger(__name__)


async def seed_from_production(session: AsyncSession, user_id: int | None, scan: int = 1000) -> dict:
    """Returns {seeded, needs_manual, total_candidates}."""
    gold_hashes = {g.question_hash for g in await list_gold(session)}

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc()).limit(scan)
    )).scalars().all()

    seen: set[str] = set()
    questions: list[str] = []
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if question_hash(r.question) in gold_hashes:
            continue
        questions.append(r.question)

    seeded = 0
    needs_manual = 0
    for q in questions:
        try:
            proposal = await propose_gold(session, q, use_llm=False)
        except Exception as e:
            logger.warning(f"seed: propose_gold failed for {q!r}: {e}")
            needs_manual += 1
            continue
        if proposal.get("source") == "db_lookup" and (proposal.get("answer") or "").strip():
            await save_gold(
                session, question=q, gold_answer=proposal["answer"], user_id=user_id,
                target_project=proposal.get("target_project"),
                target_field=proposal.get("target_field"), source="db_lookup",
            )
            seeded += 1
        else:
            needs_manual += 1

    return {"seeded": seeded, "needs_manual": needs_manual, "total_candidates": len(questions)}
