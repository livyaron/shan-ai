"""Single entry point for answering a user question.

Used by the /dashboard/ask web router, the Telegram polling handler, and the
per-question repair loop. Eval = production from this module on.
"""
from __future__ import annotations

import hashlib
import logging
import time
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
    conversation_context: list[dict] | None = None,
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

    start = time.perf_counter()

    async def _finish(result: AnswerResult, applied_rule_ids: list[str]) -> AnswerResult:
        if log_to_db and result.log_id is not None:
            ms_total = int((time.perf_counter() - start) * 1000)
            await _write_trace(
                session, result.log_id,
                result.path, result.intent, result.param,
                applied_rule_ids, ms_total, None,
            )
        return result

    # ── Pre-rules (Phase 1) ───────────────────────────────────────────────
    # Refresh DB-backed caches before pre-rule lookup.
    await ks._ensure_eval_caches(session)

    q_hash = _normalize_q_hash(question)

    # 0. Correction-pin (highest priority — verbatim answer, zero LLM calls)
    pins = {**ks._DB_CORRECTION_PINS_CACHE, **ks._shadow_correction_pins.get()}
    pin_entry = pins.get(q_hash)
    if pin_entry:
        return await _finish(AnswerResult(
            answer=pin_entry["pinned_answer"],
            sources_used=[{"source": "correction_pin", "q_hash": q_hash}],
            log_id=None,
            path="correction_pin",
            intent=None,
            param=None,
            has_files=False,
            has_decisions=False,
            file_names=[],
            sources_text="📌 תשובה מאושרת",
        ), ["correction_pin"])

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
        return await _finish(AnswerResult(
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
        ), ["intent_override"])

    # 0b. Project alias resolve — bypass LLM intent detection, call
    # answer_project_query with precomputed by_identifier+project_alias_id hint.
    # find_projects_by_identifier extracts the id directly and returns the project.
    aliases = {**ks._DB_PROJECT_ALIASES_CACHE, **ks._shadow_project_aliases.get()}
    if aliases:
        norm_q = ks.normalize_hebrew(question)
        for normalized_alias, project_id in aliases.items():
            if normalized_alias and normalized_alias in norm_q:
                logger.info(f"alias resolve: '{normalized_alias}' -> project {project_id}")
                hint_param = f"project_alias_id={project_id}"
                answer, log_id = await project_tools.answer_project_query(
                    question, session, {},
                    user_id=user_id,
                    precomputed_intent="by_identifier",
                    precomputed_param=hint_param,
                )
                return await _finish(AnswerResult(
                    answer=answer,
                    sources_used=[{"source": "project_alias", "project_id": project_id}],
                    log_id=log_id,
                    path="project_tools",
                    intent="by_identifier",
                    param=hint_param,
                    has_files=True,
                    has_decisions=False,
                    file_names=[],
                    sources_text="📂 מסד הפרויקטים",
                ), [f"project_alias:project={project_id}"])
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
        return await _finish(AnswerResult(
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
        ), [])

    # 2. Project queries
    if _is_project_query(question):
        try:
            import json as _json
            answer, log_id = await project_tools.answer_project_query(
                question, session, {}, user_id=user_id,
            )
            if isinstance(answer, str) and answer.startswith("__DISAMBIG__:"):
                candidates = _json.loads(answer[len("__DISAMBIG__:"):])
                return await _finish(AnswerResult(
                    answer=_json.dumps(candidates, ensure_ascii=False),
                    sources_used=[{"source": "disambiguation", "candidates": candidates}],
                    log_id=None,
                    path="disambiguation",
                    intent="by_identifier",
                    param=None,
                    has_files=False,
                    has_decisions=False,
                    file_names=[],
                    sources_text="",
                ), [])
            return await _finish(AnswerResult(
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
            ), [])
        except Exception:
            logger.warning("project_tools failed, falling through to RAG", exc_info=True)

    # 3. Default RAG
    result = await ks.answer_with_full_context(
        question, session, user_id, log_to_db=log_to_db,
        conversation_context=conversation_context,
    )
    return await _finish(AnswerResult(
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
    ), [])


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


async def _write_trace(
    session: AsyncSession,
    log_id: int,
    path: str,
    intent: Optional[str],
    param: Optional[str],
    applied_rule_ids: list[str],
    ms_total: int,
    ms_llm: Optional[int],
) -> None:
    """Insert a RouteTrace row linked to the given QueryLog. Errors are
    logged but never raised — telemetry must never break user-facing responses."""
    try:
        from app.models import RouteTrace
        trace = RouteTrace(
            query_log_id=log_id,
            path=path,
            intent=intent,
            param=param,
            applied_rule_ids=applied_rule_ids or [],
            ms_total=ms_total,
            ms_llm=ms_llm,
        )
        session.add(trace)
        await session.commit()
    except Exception as e:
        logger.warning(f"_write_trace failed: {e}", exc_info=True)
