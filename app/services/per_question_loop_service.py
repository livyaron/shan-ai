"""Per-question repair loop — gold-truth eval, shadow patch, regression-gated apply."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models import (
    EvalGoldAnswer,
    EvalRun,
    PromptOverride,
    QuerySynonym,
    RepairProposal,
    SystemFlag,
)
from app.services import knowledge_service as ks
from app.services.gold_truth_service import compare_to_gold, question_hash
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


EmitFn = Callable[[dict], Any]

FIX_TYPES = ["add_abbreviation", "add_synonym", "stop_word_remove", "field_alias", "prompt_patch"]


@dataclass
class QuestionResult:
    question_hash: str
    question: str
    gold_answer: str
    status: str  # passed_first_try | fixed | unfixable | no_gold | error | aborted
    attempts: int = 0
    score_initial: float = 0.0
    score_final: float = 0.0
    final_answer: str = ""
    applied_fixes: list[dict] = field(default_factory=list)
    rejected_fixes: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_event(self) -> dict:
        return asdict(self)


# ───────────────────────── shadow_config ─────────────────────────

@asynccontextmanager
async def shadow_config(patch: dict):
    """Set knowledge_service shadow ContextVars for the duration of the block.

    patch keys: abbrevs (dict), synonyms (dict[str, list[str]]),
                stop_word_drops (set[str]), prompt_override (dict[str, str]).
    """
    tokens = []
    try:
        if "abbrevs" in patch:
            tokens.append(("abbrevs", ks._shadow_abbrevs.set(dict(patch["abbrevs"]))))
        if "synonyms" in patch:
            tokens.append(("synonyms", ks._shadow_synonyms.set(dict(patch["synonyms"]))))
        if "stop_word_drops" in patch:
            tokens.append(("stop_word_drops", ks._shadow_stop_word_drops.set(set(patch["stop_word_drops"]))))
        if "prompt_override" in patch:
            tokens.append(("prompt_override", ks._shadow_prompt_override.set(dict(patch["prompt_override"]))))
        yield
    finally:
        for name, tok in reversed(tokens):
            cv = {
                "abbrevs": ks._shadow_abbrevs,
                "synonyms": ks._shadow_synonyms,
                "stop_word_drops": ks._shadow_stop_word_drops,
                "prompt_override": ks._shadow_prompt_override,
            }[name]
            cv.reset(tok)


# ───────────────────────── helpers ─────────────────────────

async def _kill_switch_on(session: AsyncSession) -> bool:
    flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == "eval_kill"))
    return bool(flag and flag.value == "1")


async def _clear_kill_switch(session: AsyncSession) -> None:
    flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == "eval_kill"))
    if flag:
        flag.value = "0"
        await session.commit()


def _patch_to_shadow(proposal_type: str, patch_json: dict) -> dict:
    """Translate a RepairProposal patch_json into the shadow_config patch dict."""
    if proposal_type == "add_abbreviation":
        return {"abbrevs": patch_json.get("abbrevs", {})}
    if proposal_type == "add_synonym":
        return {"synonyms": patch_json.get("synonyms", {})}
    if proposal_type == "stop_word_remove":
        return {"stop_word_drops": set(patch_json.get("words", []))}
    if proposal_type == "field_alias":
        return {"synonyms": patch_json.get("synonyms", {})}
    if proposal_type == "prompt_patch":
        return {"prompt_override": patch_json.get("prompt_override", {})}
    return {}


async def _answer(question: str, user_id: int) -> str:
    """Run the production answering pipeline without DB logging."""
    async with async_session_maker() as s:
        result = await ks.answer_with_full_context(question, s, user_id, log_to_db=False)
    return result.get("answer", "")


async def _snapshot_passing(
    gold_rows: list[EvalGoldAnswer],
    user_id: int,
    threshold: float,
    exclude_hash: str,
) -> set[str]:
    """Return set of question_hash for gold rows whose AI answer currently scores >= threshold.
    Runs in the calling task's context (so any active shadow_config applies)."""
    passing: set[str] = set()
    for g in gold_rows:
        if g.question_hash == exclude_hash:
            continue
        try:
            ans = await _answer(g.question, user_id)
            score = await compare_to_gold(g.question, ans, g.gold_answer)
            if score >= threshold:
                passing.add(g.question_hash)
        except Exception as e:
            logger.warning(f"snapshot for {g.question_hash[:8]} failed: {e}")
    return passing


# ───────────────────────── repair proposal ─────────────────────────

_REPAIR_SYS = (
    "You are a repair agent for a Hebrew RAG system. Given a question, the AI's wrong answer, and the gold answer, "
    "propose ONE minimal config patch that would make the AI produce the gold answer. "
    "Available fix types: "
    "add_abbreviation (expand a Hebrew abbreviation, patch_json={'abbrevs': {'מנה\\\"פ': 'מנהל פרויקט'}}), "
    "add_synonym (expand a term, patch_json={'synonyms': {'תחמ\\\"ש': ['תחנת משנה']}}), "
    "stop_word_remove (remove word from stop list so it's used as a search term, patch_json={'words': ['מנהל']}), "
    "field_alias (alias a field, patch_json={'synonyms': {'מנהפ': ['manager']}}), "
    "prompt_patch (rewrite the rag_specific or rag_list system prompt, patch_json={'prompt_override': {'rag_specific': '...'}}). "
    "Reply ONLY with strict JSON: {\"type\": \"...\", \"patch_json\": {...}, \"rationale\": \"...\", \"risk\": \"low|medium|high\"} "
    "or {\"type\": null} if no patch can plausibly help."
)


async def propose_targeted_fix(
    session: AsyncSession,
    question: str,
    ai_answer: str,
    gold_answer: str,
    eval_run_id: int | None,
) -> RepairProposal | None:
    user = json.dumps({
        "question": question,
        "ai_answer": ai_answer,
        "gold_answer": gold_answer,
        "available_fix_types": FIX_TYPES,
    }, ensure_ascii=False)
    try:
        raw = await llm_chat(
            "eval_repair",
            messages=[{"role": "system", "content": _REPAIR_SYS}, {"role": "user", "content": user}],
            max_tokens=400,
            temperature=0.0,
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"propose_targeted_fix failed: {e}")
        return None

    fix_type = data.get("type")
    if not fix_type or fix_type not in FIX_TYPES:
        return None

    proposal = RepairProposal(
        eval_run_id=eval_run_id,
        type=fix_type,
        patch_json=data.get("patch_json") or {},
        rationale=data.get("rationale"),
        risk=data.get("risk") or "low",
        status="pending",
    )
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return proposal


# ───────────────────────── apply patch (DB writes) ─────────────────────────

async def _apply_patch(session: AsyncSession, proposal: RepairProposal, user_id: int | None) -> None:
    """Persist a proposal's patch to DB so production picks it up via _ensure_eval_caches."""
    p = proposal
    pj = p.patch_json or {}

    if p.type == "add_abbreviation":
        new_pairs = pj.get("abbrevs", {})
        await _upsert_synonym_sentinel(session, "__hebrew_abbrevs__", merge_pairs=new_pairs)

    elif p.type == "add_synonym" or p.type == "field_alias":
        for original, syns in (pj.get("synonyms") or {}).items():
            await _upsert_synonym(session, original, syns)

    elif p.type == "stop_word_remove":
        words = pj.get("words", [])
        await _upsert_synonym_sentinel(session, "__stop_word_drops__", merge_words=words)

    elif p.type == "prompt_patch":
        for usage, content in (pj.get("prompt_override") or {}).items():
            # Deactivate any currently-active row for this usage
            existing_active = await session.scalar(
                select(PromptOverride).where(PromptOverride.usage == usage, PromptOverride.active.is_(True))
            )
            if existing_active:
                existing_active.active = False
            session.add(PromptOverride(
                usage=usage,
                content=content,
                active=True,
                source_proposal_id=p.id,
            ))

    p.status = "applied"
    p.applied_at = datetime.utcnow()
    p.applied_by_id = user_id
    await session.commit()
    ks.invalidate_eval_caches()


async def _upsert_synonym(session: AsyncSession, original: str, synonyms: list[str]) -> None:
    row = await session.scalar(select(QuerySynonym).where(QuerySynonym.original == original))
    if row:
        existing = list(row.synonyms or [])


        for s in synonyms:
            if s not in existing:
                existing.append(s)
        row.synonyms = existing
    else:
        session.add(QuerySynonym(original=original, synonyms=list(synonyms), source="ai"))


async def _upsert_synonym_sentinel(
    session: AsyncSession,
    sentinel: str,
    *,
    merge_pairs: dict | None = None,
    merge_words: list[str] | None = None,
) -> None:
    """Sentinel rows are stored as JSON list. For abbrevs we use ['k=v', ...]; for stop drops just words."""
    row = await session.scalar(select(QuerySynonym).where(QuerySynonym.original == sentinel))
    items = list(row.synonyms or []) if row else []
    if merge_pairs:
        existing_keys = set()
        for entry in items:
            if isinstance(entry, str) and "=" in entry:
                existing_keys.add(entry.split("=", 1)[0].strip())
        for k, v in merge_pairs.items():
            if k not in existing_keys:
                items.append(f"{k}={v}")
                existing_keys.add(k)
    if merge_words:
        for w in merge_words:
            if w not in items:
                items.append(w)
    if row:
        row.synonyms = items
    else:
        session.add(QuerySynonym(original=sentinel, synonyms=items, source="ai"))


# ───────────────────────── per-question loop ─────────────────────────

async def run_one_question(
    session: AsyncSession,
    gold: EvalGoldAnswer,
    user_id: int,
    all_gold: list[EvalGoldAnswer],
    eval_run_id: int | None,
    max_repairs: int = 3,
    threshold: float = 0.8,
    emit: EmitFn = lambda e: None,
) -> QuestionResult:
    res = QuestionResult(
        question_hash=gold.question_hash,
        question=gold.question,
        gold_answer=gold.gold_answer,
        status="error",
    )

    emit({"type": "question_start", "question_hash": gold.question_hash, "question": gold.question, "gold": gold.gold_answer})

    try:
        ans = await _answer(gold.question, user_id)
    except Exception as e:
        res.status = "error"
        res.error = f"initial answer failed: {e}"
        emit({"type": "question_done", "result": res.to_event()})
        return res

    score = await compare_to_gold(gold.question, ans, gold.gold_answer)
    res.attempts = 1
    res.score_initial = score
    res.score_final = score
    res.final_answer = ans
    emit({"type": "attempt", "question_hash": gold.question_hash, "answer": ans, "score": score, "attempt": 1})

    if score >= threshold:
        res.status = "passed_first_try"
        emit({"type": "question_done", "result": res.to_event()})
        return res

    for attempt in range(1, max_repairs + 1):
        if await _kill_switch_on(session):
            res.status = "aborted"
            emit({"type": "question_done", "result": res.to_event()})
            return res

        passing_before = await _snapshot_passing(all_gold, user_id, threshold, exclude_hash=gold.question_hash)

        proposal = await propose_targeted_fix(session, gold.question, ans, gold.gold_answer, eval_run_id)
        if not proposal:
            break

        emit({
            "type": "repair_proposed",
            "question_hash": gold.question_hash,
            "proposal_id": proposal.id,
            "fix_type": proposal.type,
            "patch": proposal.patch_json,
            "rationale": proposal.rationale,
            "attempt": attempt,
        })

        shadow_patch = _patch_to_shadow(proposal.type, proposal.patch_json or {})
        async with shadow_config(shadow_patch):
            new_ans = await _answer(gold.question, user_id)
            new_score = await compare_to_gold(gold.question, new_ans, gold.gold_answer)
            passing_after = await _snapshot_passing(all_gold, user_id, threshold, exclude_hash=gold.question_hash)

        regressions = sorted(passing_before - passing_after)
        res.attempts = attempt + 1
        res.final_answer = new_ans
        res.score_final = new_score

        if new_score >= threshold and not regressions:
            await _apply_patch(session, proposal, user_id)
            proposal.regression_count = 0
            proposal.delta_pass_rate = (new_score - score)
            await session.commit()
            res.applied_fixes.append({
                "proposal_id": proposal.id,
                "type": proposal.type,
                "patch": proposal.patch_json,
            })
            res.status = "fixed"
            emit({"type": "repair_applied", "question_hash": gold.question_hash, "proposal_id": proposal.id})
            emit({"type": "question_done", "result": res.to_event()})
            return res

        # Reject this proposal
        proposal.status = "rejected"
        if regressions:
            proposal.reject_reason = f"regressions: {len(regressions)} questions"
            proposal.regression_count = len(regressions)
        else:
            proposal.reject_reason = f"score {new_score:.2f} below threshold {threshold}"
        await session.commit()

        rejected_entry = {
            "proposal_id": proposal.id,
            "type": proposal.type,
            "patch": proposal.patch_json,
            "reject_reason": proposal.reject_reason,
            "regressions": regressions,
            "new_score": new_score,
        }
        res.rejected_fixes.append(rejected_entry)
        emit({"type": "repair_rejected", "question_hash": gold.question_hash, **rejected_entry})

    res.status = "unfixable"
    emit({"type": "question_done", "result": res.to_event()})
    return res


async def run_cycle(
    session: AsyncSession,
    user_id: int,
    threshold: float = 0.8,
    max_repairs: int = 3,
    emit: EmitFn = lambda e: None,
) -> dict:
    """Run the full per-question cycle. Creates an EvalRun row; partial-unique index
    on EvalRun.status='running' enforces single concurrent cycle."""
    await _clear_kill_switch(session)

    eval_run = EvalRun(
        triggered_by_user_id=user_id,
        config_json={"mode": "per_question", "threshold": threshold, "max_repairs": max_repairs},
    )
    session.add(eval_run)
    await session.commit()
    await session.refresh(eval_run)

    gold_rows = (await session.execute(select(EvalGoldAnswer).order_by(EvalGoldAnswer.id))).scalars().all()
    gold_rows = list(gold_rows)
    eval_run.n_probes = len(gold_rows)
    await session.commit()

    emit({"type": "cycle_start", "eval_run_id": eval_run.id, "total": len(gold_rows)})

    results: list[QuestionResult] = []
    counts = {"passed_first_try": 0, "fixed": 0, "unfixable": 0, "no_gold": 0, "error": 0, "aborted": 0}
    applied_proposal_ids: list[int] = []

    try:
        for g in gold_rows:
            if await _kill_switch_on(session):
                emit({"type": "aborted", "eval_run_id": eval_run.id})
                eval_run.status = "aborted"
                break

            r = await run_one_question(
                session, g, user_id, gold_rows, eval_run.id,
                max_repairs=max_repairs, threshold=threshold, emit=emit,
            )
            results.append(r)
            counts[r.status] = counts.get(r.status, 0) + 1
            for fix in r.applied_fixes:
                applied_proposal_ids.append(fix["proposal_id"])

        eval_run.n_pass = counts["passed_first_try"] + counts["fixed"]
        eval_run.n_fail = counts["unfixable"] + counts["error"]
        eval_run.n_proposals_applied = len(applied_proposal_ids)
        if eval_run.status != "aborted":
            eval_run.status = "completed"
        eval_run.finished_at = datetime.utcnow()
        await session.commit()
    except Exception as e:
        logger.exception("run_cycle failed")
        eval_run.status = "failed"
        eval_run.error = str(e)
        eval_run.finished_at = datetime.utcnow()
        await session.commit()
        emit({"type": "cycle_done", "eval_run_id": eval_run.id, "error": str(e)})
        raise

    summary = {
        "eval_run_id": eval_run.id,
        "total": len(gold_rows),
        "counts": counts,
        "applied_fix_ids": applied_proposal_ids,
        "results": [r.to_event() for r in results],
        "pass_rate": (eval_run.n_pass / max(1, len(gold_rows))),
    }
    emit({"type": "cycle_done", **summary})
    return summary
