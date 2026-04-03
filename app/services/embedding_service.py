"""Embedding service - generates vectors for pgvector RAG using fastembed."""

import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Decision, DecisionStatusEnum

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
MODEL_NAME = "intfloat/multilingual-e5-small"

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        logger.info(f"טוען מודל embeddings: {MODEL_NAME}")
        _model = TextEmbedding(MODEL_NAME)
        logger.info("מודל embeddings נטען בהצלחה")
    return _model


def _embed_sync(text: str) -> list[float]:
    model = _get_model()
    return list(list(model.embed([text]))[0])


async def embed(text: str) -> list[float]:
    """Generate embedding vector for text (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_sync, text)


async def get_similar_decisions(session: AsyncSession, query_text: str, limit: int = 3) -> list[Decision]:
    """Find similar past decisions using cosine distance in pgvector."""
    try:
        query_vector = await embed(query_text)

        stmt = (
            select(Decision)
            .where(Decision.embedding.isnot(None))
            .where(Decision.status.in_([DecisionStatusEnum.EXECUTED, DecisionStatusEnum.APPROVED]))
            .where(Decision.feedback_score.isnot(None))
            .order_by(Decision.embedding.cosine_distance(query_vector))
            .limit(limit)
        )
        result = await session.execute(stmt)
        return result.scalars().all()
    except Exception as e:
        logger.warning(f"pgvector similarity search failed: {e}")
        return []


def format_past_context(decisions: list[Decision]) -> str:
    """Format similar past decisions as context string for Groq."""
    if not decisions:
        return ""
    lines = ["החלטות עבר דומות (למידת ניסיון):"]
    for d in decisions:
        score = f" | ציון פידבק: {d.feedback_score}/5" if d.feedback_score else ""
        lines.append(f"• [{d.type.value.upper()}] {d.summary} → {d.recommended_action}{score}")
        if d.feedback_notes:
            lines.append(f"  לקח: {d.feedback_notes}")
    return "\n".join(lines)
