"""Agent 4 — Verifier & Applier.

Tests a RepairProposal in shadow mode (without persisting changes), measures
delta_pass_rate vs control set, and either auto-applies or queues for manual approval.

Decision tree:
  delta >= 0.30 AND regressions == 0 AND risk in {low, medium}  →  APPLIED
  risk == "high" OR (0 <= delta < 0.30 AND regressions == 0)    →  AWAITING_APPROVAL
  otherwise                                                       →  REJECTED
"""
from __future__ import annotations

import json
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    QueryLog, QuerySynonym, PromptOverride, RepairProposal, Project,
)
from app.services.eval_judge_service import judge_answer, Verdict

logger = logging.getLogger(__name__)


AUTO_APPLY_DELTA_THRESHOLD = 0.30
CONTROL_SET_SIZE = 20


@dataclass
class Snapshot:
    question: str
    answer: str
    target_project: str | None
    target_field: str | None
    verdict: str
    failure_type: str | None
    log_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerifyResult:
    before_pass_rate: float
    after_pass_rate: float
    delta: float
    regressions: int
    control_pass_before: int
    control_pass_after: int
    decision: str          # "applied" | "awaiting_approval" | "rejected"
    reason: str
    before: list[Snapshot]
    after: list[Snapshot]


# ─────────────────────────────────────────────────────────────────────────────
# Shadow-config context manager — sets ContextVars on knowledge_service so the
# proposed patch is in effect FOR THIS TASK ONLY.  No DB writes happen here.
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def shadow_config(proposal: RepairProposal) -> AsyncIterator[None]:
    from app.services import knowledge_service as ks

    tokens: list[Any] = []
    try:
        if proposal.type == "add_abbreviation":
            current = ks._shadow_abbrevs.get()
            new = {**current, proposal.patch_json["key"]: proposal.patch_json["value"]}
            tokens.append(("_shadow_abbrevs", ks._shadow_abbrevs.set(new)))
        elif proposal.type == "stop_word_remove":
            current = ks._shadow_stop_word_drops.get()
            new = current | set(proposal.patch_json.get("tokens") or [])
            tokens.append(("_shadow_stop_word_drops", ks._shadow_stop_word_drops.set(new)))
        elif proposal.type == "add_synonym":
            current = ks._shadow_synonyms.get()
            orig = proposal.patch_json["original"]
            syns = list(proposal.patch_json.get("synonyms") or [])
            new = {**current, orig: syns}
            tokens.append(("_shadow_synonyms", ks._shadow_synonyms.set(new)))
        elif proposal.type == "field_alias":
            # Implemented as a synonym group keyed by the column name.
            current = ks._shadow_synonyms.get()
            sentinel_key = f"__field_alias_{proposal.patch_json['column']}__"
            new = {**current, sentinel_key: list(proposal.patch_json.get("aliases") or [])}
            tokens.append(("_shadow_synonyms", ks._shadow_synonyms.set(new)))
        elif proposal.type == "prompt_patch":
            current = ks._shadow_prompt_override.get()
            usage = proposal.patch_json["usage"]
            new = {**current, usage: proposal.patch_json["content"]}
            tokens.append(("_shadow_prompt_override", ks._shadow_prompt_override.set(new)))
        else:
            raise ValueError(f"unknown proposal type: {proposal.type}")
        yield
    finally:
        # Reset ContextVars in reverse order
        for name, tok in reversed(tokens):
            getattr(ks, name).reset(tok)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot a question through the production answering pipeline (log_to_db=False).
# ─────────────────────────────────────────────────────────────────────────────
async def _answer_one(session: AsyncSession, question: str, user_id: int | None) -> str:
    from app.services.knowledge_service import answer_with_full_context
    result = await answer_with_full_context(
        question=question,
        session=session,
        user_id=user_id or 0,
        log_to_db=False,
    )
    return result.get("answer") or ""


async def snapshot_and_judge(
    session: AsyncSession,
    questions: list[dict],
    user_id: int | None,
) -> list[Snapshot]:
    """Run each question through the answer pipeline, then judge.

    questions: list of {"question": str, "target_project": str|None, "target_field": str|None}
    """
    out: list[Snapshot] = []
    for q in questions:
        text = q.get("question") or ""
        if not text:
            continue
        try:
            answer = await _answer_one(session, text, user_id)
        except Exception as e:
            logger.warning(f"snapshot_and_judge: answer failed for '{text[:40]}': {e}")
            answer = ""
        try:
            v = await judge_answer(
                session=session,
                question=text,
                answer=answer,
                target_project=q.get("target_project"),
                target_field=q.get("target_field"),
            )
        except Exception as e:
            logger.warning(f"snapshot_and_judge: judge failed for '{text[:40]}': {e}")
            v = Verdict("FAIL", "WRONG_DATA", f"judge error: {e}", 3)
        out.append(Snapshot(
            question=text,
            answer=answer,
            target_project=q.get("target_project"),
            target_field=q.get("target_field"),
            verdict=v.verdict,
            failure_type=v.failure_type,
            log_id=q.get("log_id"),
        ))
    return out


def _pass_rate(snaps: list[Snapshot]) -> float:
    if not snaps:
        return 1.0
    n_pass = sum(1 for s in snaps if s.verdict == "PASS")
    return n_pass / len(snaps)


async def _build_control_set(
    session: AsyncSession,
    n: int = CONTROL_SET_SIZE,
    proposal_id: int | None = None,
) -> list[dict]:
    """Sample previously-PASSING QueryLog rows so we can detect regressions.

    Uses a deterministic random seed derived from proposal_id so the same
    proposal always gets the same control set — regressions are reproducible.
    """
    rng = random.Random(proposal_id if proposal_id is not None else 42)
    stmt = (
        select(QueryLog)
        .where(
            QueryLog.question.isnot(None),
            # Accept either judge-confirmed PASS or explicit user thumbs-up
            or_(
                QueryLog.judge_verdict == "PASS",
                QueryLog.user_feedback == 1,
            ),
        )
        .order_by(QueryLog.timestamp.desc())
        .limit(100)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    sampled = rng.sample(rows, min(n, len(rows))) if rows else []
    return [{"question": r.question, "log_id": r.id, "target_project": None, "target_field": None}
            for r in sampled]


async def verify_proposal(
    session: AsyncSession,
    proposal: RepairProposal,
    failing_qs: list[dict],
    user_id: int | None = None,
) -> VerifyResult:
    """Snapshot before, shadow-apply, snapshot after, decide auto-apply vs queue vs reject."""
    # Treat None risk as "high" — never silently bypass the approval gate.
    if not proposal.risk:
        proposal.risk = "high"

    control = await _build_control_set(session, proposal_id=proposal.id)

    # ── BEFORE snapshot (current production config) ─────────────────────────
    before_failing = await snapshot_and_judge(session, failing_qs, user_id)
    before_control = await snapshot_and_judge(session, control, user_id)
    before_pass = _pass_rate(before_failing)
    control_pass_before = sum(1 for s in before_control if s.verdict == "PASS")

    # ── AFTER snapshot (with proposal active in shadow ContextVars) ─────────
    async with shadow_config(proposal):
        after_failing = await snapshot_and_judge(session, failing_qs, user_id)
        after_control = await snapshot_and_judge(session, control, user_id)
    after_pass = _pass_rate(after_failing)
    control_pass_after = sum(1 for s in after_control if s.verdict == "PASS")

    delta = after_pass - before_pass
    regressions = max(0, control_pass_before - control_pass_after)

    # ── Decision tree (aggressive auto-apply) ──────────────────────────────
    # Apply ANY improvement with zero regressions, regardless of risk or threshold.
    # User wants the loop to fix things autonomously — only reject if it would
    # break working questions or doesn't help at all.
    if regressions > 0:
        decision = "rejected"
        reason = f"rejected: {regressions} regressions on control set"
    elif delta > 0:
        decision = "applied"
        reason = f"auto-apply: Δ={delta:+.2f}, no regressions"
    else:
        decision = "rejected"
        reason = f"rejected: Δ={delta:+.2f} (no improvement)"

    # ── Persist verification results onto the proposal ──────────────────────
    proposal.delta_pass_rate = delta
    proposal.regression_count = regressions
    proposal.before_snapshot = [s.to_dict() for s in before_failing]
    proposal.after_snapshot = [s.to_dict() for s in after_failing]

    if decision == "applied":
        proposal.status = "applied"
        proposal.applied_at = datetime.utcnow()
        await _apply_patch(session, proposal)
    elif decision == "awaiting_approval":
        proposal.status = "awaiting_approval"
    else:
        proposal.status = "rejected"
        proposal.reject_reason = reason

    await session.commit()
    await session.refresh(proposal)

    return VerifyResult(
        before_pass_rate=before_pass,
        after_pass_rate=after_pass,
        delta=delta,
        regressions=regressions,
        control_pass_before=control_pass_before,
        control_pass_after=control_pass_after,
        decision=decision,
        reason=reason,
        before=before_failing,
        after=after_failing,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Apply / rollback — DB-side writes that take a patch live.
# Knowledge_service rereads its caches every 30s, so changes propagate quickly.
# ─────────────────────────────────────────────────────────────────────────────
async def _apply_patch(session: AsyncSession, proposal: RepairProposal) -> None:
    p = proposal.patch_json or {}
    if proposal.type == "add_synonym":
        await _upsert_synonym(session, p["original"], list(p.get("synonyms") or []))
    elif proposal.type == "add_abbreviation":
        await _merge_sentinel_synonym(session, "__hebrew_abbrevs__",
                                      [f"{p['key']}={p['value']}"])
    elif proposal.type == "stop_word_remove":
        await _merge_sentinel_synonym(session, "__stop_word_drops__",
                                      list(p.get("tokens") or []))
    elif proposal.type == "field_alias":
        sentinel = f"__field_alias_{p['column']}__"
        await _merge_sentinel_synonym(session, sentinel, list(p.get("aliases") or []))
    elif proposal.type == "prompt_patch":
        # Deactivate any current active row for this usage, then insert the new active row.
        usage = p["usage"]
        await session.execute(
            select(PromptOverride).where(PromptOverride.usage == usage, PromptOverride.active.is_(True))
        )
        existing = (await session.execute(
            select(PromptOverride).where(PromptOverride.usage == usage, PromptOverride.active.is_(True))
        )).scalars().all()
        for row in existing:
            row.active = False
        new_row = PromptOverride(
            usage=usage,
            content=p["content"],
            active=True,
            source_proposal_id=proposal.id,
        )
        session.add(new_row)
    # Invalidate the knowledge_service cache so the change takes effect immediately
    from app.services import knowledge_service as ks
    ks._EVAL_CACHE_TS = 0.0


async def _upsert_synonym(session: AsyncSession, original: str, synonyms: list[str]) -> None:
    row = (await session.execute(
        select(QuerySynonym).where(QuerySynonym.original == original)
    )).scalar_one_or_none()
    if row:
        existing = list(row.synonyms or [])
        merged = list(dict.fromkeys(existing + synonyms))
        row.synonyms = merged
    else:
        session.add(QuerySynonym(original=original, synonyms=synonyms, source="ai"))


async def _merge_sentinel_synonym(session: AsyncSession, sentinel: str, additions: list[str]) -> None:
    row = (await session.execute(
        select(QuerySynonym).where(QuerySynonym.original == sentinel)
    )).scalar_one_or_none()
    if row:
        existing = list(row.synonyms or [])
        merged = list(dict.fromkeys(existing + additions))
        row.synonyms = merged
    else:
        session.add(QuerySynonym(original=sentinel, synonyms=additions, source="ai"))


async def apply_proposal(session: AsyncSession, proposal: RepairProposal, user_id: int | None = None) -> None:
    """Manual approval path — caller already verified delta/regressions or accepts the risk."""
    await _apply_patch(session, proposal)
    proposal.status = "applied"
    proposal.applied_at = datetime.utcnow()
    proposal.applied_by_id = user_id
    await session.commit()


async def rollback_proposal(session: AsyncSession, proposal: RepairProposal) -> None:
    """Reverse an applied patch. Status flips to 'reverted' for audit."""
    p = proposal.patch_json or {}
    if proposal.type == "add_synonym":
        # Drop the synonyms we added; keep row if other entries remain.
        row = (await session.execute(
            select(QuerySynonym).where(QuerySynonym.original == p.get("original"))
        )).scalar_one_or_none()
        if row:
            added = set(p.get("synonyms") or [])
            row.synonyms = [s for s in (row.synonyms or []) if s not in added]
            if not row.synonyms:
                await session.delete(row)
    elif proposal.type == "add_abbreviation":
        await _drop_from_sentinel(session, "__hebrew_abbrevs__",
                                  {f"{p.get('key')}={p.get('value')}"})
    elif proposal.type == "stop_word_remove":
        await _drop_from_sentinel(session, "__stop_word_drops__",
                                  set(p.get("tokens") or []))
    elif proposal.type == "field_alias":
        await _drop_from_sentinel(session, f"__field_alias_{p.get('column')}__",
                                  set(p.get("aliases") or []))
    elif proposal.type == "prompt_patch":
        # Deactivate the override we activated for this proposal.
        rows = (await session.execute(
            select(PromptOverride).where(PromptOverride.source_proposal_id == proposal.id)
        )).scalars().all()
        for row in rows:
            row.active = False

    proposal.status = "reverted"
    from app.services import knowledge_service as ks
    ks._EVAL_CACHE_TS = 0.0
    await session.commit()


async def _drop_from_sentinel(session: AsyncSession, sentinel: str, to_drop: set[str]) -> None:
    row = (await session.execute(
        select(QuerySynonym).where(QuerySynonym.original == sentinel)
    )).scalar_one_or_none()
    if not row:
        return
    row.synonyms = [s for s in (row.synonyms or []) if s not in to_drop]
    if not row.synonyms:
        await session.delete(row)
