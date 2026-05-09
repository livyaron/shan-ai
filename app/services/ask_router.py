"""Single entry point for answering a user question.

Used by the /dashboard/ask web router, the Telegram polling handler, and the
per-question repair loop. Eval = production from this module on.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.knowledge_service import normalize_hebrew

logger = logging.getLogger(__name__)


@dataclass
class AnswerResult:
    answer: str
    sources_used: list[dict]
    log_id: Optional[int]
    path: str          # "correction_pin" | "decision" | "project_tools" | "rag"
    intent: Optional[str]
    param: Optional[str]
    has_files: bool = False
    has_decisions: bool = False
    file_names: list[str] = field(default_factory=list)
    sources_text: str = ""


def _normalize_q_hash(question: str) -> str:
    """sha256 of Hebrew-normalized question. Used as a hash key for pin/override lookups."""
    return hashlib.sha256(normalize_hebrew(question.strip()).encode("utf-8")).hexdigest()


_DECISION_KEYWORDS = ("החלטה", "החלטות", "ההחלטה", "ההחלטות")


async def route(
    question: str,
    session: AsyncSession,
    user_id: int,
    *,
    log_to_db: bool = True,
    snapshot_mode: bool = False,
) -> AnswerResult:
    """Route a question to the right answerer and return a uniform AnswerResult.

    Order of dispatch:
      1. Decision keyword → answer_decisions_question
      2. _is_project_query → project_tools.answer_project_query
      3. Default → knowledge_service.answer_with_full_context

    Phase 1 will insert correction-pin lookup, alias resolve, and intent-override
    BEFORE step 1. This task ports existing behavior only.
    """
    # Lazy imports keep the module light and avoid cycles.
    from app.services import knowledge_service as ks
    from app.services.telegram_routing import _is_project_query
    from app.services import project_tools

    # ── Pre-rules (Phase 1) ───────────────────────────────────────────────
    # Refresh DB-backed caches before pre-rule lookup.
    await ks._ensure_eval_caches(session)

    q_hash = _normalize_q_hash(question)

    # 0a. Intent override (hash-keyed; exact match on normalized question)
    intent_overrides = {**ks._DB_INTENT_OVERRIDES_CACHE, **ks._shadow_intent_overrides.get()}
    pinned = intent_overrides.get(q_hash)
    if pinned:
        answer, log_id = await project_tools.answer_project_query(
            question, session, {},
            user_id=user_id,
            precomputed_intent=pinned["forced_intent"],
            precomputed_param=pinned["forced_param"],
        )
        return AnswerResult(
            answer=answer,
            sources_used=[{"source": "intent_override", "q_hash": q_hash}],
            log_id=log_id,
            path="project_tools",
            intent=pinned["forced_intent"],
            param=pinned["forced_param"],
            has_files=True,
            has_decisions=False,
            file_names=[],
            sources_text="📂 מסד הפרויקטים",
        )

    # 0b. Project alias resolve — inject hint into question text so downstream
    # find_projects_by_identifier can pick the exact project.
    aliases = {**ks._DB_PROJECT_ALIASES_CACHE, **ks._shadow_project_aliases.get()}
    if aliases:
        norm_q = ks.normalize_hebrew(question)
        for normalized_alias, project_id in aliases.items():
            if normalized_alias and normalized_alias in norm_q:
                question = f"{question} (project_alias_id={project_id})"
                logger.info(f"alias resolve: '{normalized_alias}' -> project {project_id}")
                break  # one alias hit is enough
    # ── End pre-rules ─────────────────────────────────────────────────────

    # 1. Decision history queries
    if any(kw in question for kw in _DECISION_KEYWORDS):
        decisions_ctx = await ks.get_decisions_context(session, user_id)
        if decisions_ctx:
            answer = await ks.answer_decisions_question(question, decisions_ctx)
        else:
            answer = "לא נמצאו החלטות עבורך במסד הנתונים."
        log_id = await _log_query(session, question, answer,
                                  [{"source": "decisions_db"}], user_id, log_to_db)
        return AnswerResult(
            answer=answer,
            sources_used=[{"source": "decisions_db"}],
            log_id=log_id,
            path="decision",
            intent=None,
            param=None,
            has_files=False,
            has_decisions=bool(decisions_ctx),
            file_names=[],
            sources_text="📋 מסד ההחלטות" if decisions_ctx else "",
        )

    # 2. Project queries
    if _is_project_query(question):
        try:
            answer, log_id = await project_tools.answer_project_query(
                question, session, {}, user_id=user_id,
            )
            return AnswerResult(
                answer=answer,
                sources_used=[{"source": "projects_db"}],
                log_id=log_id,
                path="project_tools",
                intent=None,
                param=None,
                has_files=True,        # parity with original ask.py
                has_decisions=False,
                file_names=[],
                sources_text="📂 מסד הפרויקטים",
            )
        except Exception:
            logger.warning("project_tools failed, falling through to RAG", exc_info=True)

    # 3. Default RAG
    result = await ks.answer_with_full_context(
        question, session, user_id, log_to_db=log_to_db,
    )
    return AnswerResult(
        answer=result.get("answer", ""),
        sources_used=[{"source": "rag"}],
        log_id=result.get("log_id"),
        path="rag",
        intent=None,
        param=None,
        has_files=bool(result.get("has_files")),
        has_decisions=bool(result.get("has_decisions")),
        file_names=list(result.get("file_names", []) or []),
        sources_text=result.get("sources_text", "") or "",
    )


async def _log_query(
    session: AsyncSession,
    question: str,
    answer: str,
    sources: list[dict],
    user_id: int,
    log_to_db: bool,
) -> int | None:
    """Write a QueryLog row and return its id. No-op when log_to_db=False."""
    if not log_to_db:
        return None
    from app.models import QueryLog
    from app.services.llm_router import get_last_llm_meta
    provider, is_fb = get_last_llm_meta()
    log = QueryLog(
        question=question, ai_response=answer,
        sources_used=sources, user_id=user_id,
        llm_provider=provider or None, is_fallback=is_fb or None,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log.id
