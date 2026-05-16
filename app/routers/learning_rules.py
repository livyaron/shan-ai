"""Admin CRUD endpoints for the learning-rules page.

All endpoints require is_admin=True.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import (
    ProjectAlias, IntentOverride, CorrectionPin, QuerySynonym, RepairProposal, User
)
from app.routers.login import get_current_user
from app.services.knowledge_service import normalize_hebrew, invalidate_eval_caches
from app.services.ask_router import _normalize_q_hash

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_admin(user: User) -> None:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/dashboard/learning/rules", response_class=HTMLResponse)
async def rules_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)

    aliases  = (await session.execute(select(ProjectAlias))).scalars().all()
    intents  = (await session.execute(select(IntentOverride))).scalars().all()
    pins     = (await session.execute(select(CorrectionPin))).scalars().all()
    synonyms = (await session.execute(select(QuerySynonym))).scalars().all()
    pending  = (await session.execute(
        select(RepairProposal).where(RepairProposal.status == "awaiting_approval")
    )).scalars().all()

    return templates.TemplateResponse("learning_rules.html", {
        "request": request,
        "current_user": current_user,
        "aliases": aliases,
        "intents": intents,
        "pins": pins,
        "synonyms": synonyms,
        "pending": pending,
    })


# ── Aliases ────────────────────────────────────────────────────────
class AliasCreate(BaseModel):
    alias_text: str
    project_id: int


@router.post("/dashboard/learning/rules/aliases")
async def create_alias(
    body: AliasCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = ProjectAlias(
        project_id=body.project_id,
        alias_text=body.alias_text,
        normalized_alias=normalize_hebrew(body.alias_text),
        source="manual",
        created_by_id=current_user.id,
    )
    session.add(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True, "id": row.id}


@router.delete("/dashboard/learning/rules/aliases/{alias_id}")
async def delete_alias(
    alias_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(ProjectAlias, alias_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Intent overrides ───────────────────────────────────────────────
class IntentOverrideCreate(BaseModel):
    question: str
    forced_intent: str
    forced_param: str | None = None


@router.post("/dashboard/learning/rules/intent_overrides")
async def create_intent_override(
    body: IntentOverrideCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = IntentOverride(
        question_pattern_hash=_normalize_q_hash(body.question),
        forced_intent=body.forced_intent,
        forced_param=body.forced_param,
        source="manual",
        created_by_id=current_user.id,
    )
    session.add(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True, "id": row.id}


@router.delete("/dashboard/learning/rules/intent_overrides/{override_id}")
async def delete_intent_override(
    override_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(IntentOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Correction pins ────────────────────────────────────────────────
@router.delete("/dashboard/learning/rules/correction_pins/{pin_id}")
async def delete_correction_pin(
    pin_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(CorrectionPin, pin_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Approve pending proposals (correction_pin only) ────────────────
@router.post("/dashboard/learning/rules/pending/{proposal_id}/approve")
async def approve_pending(
    proposal_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    from app.services.per_question_loop_service import approve_pin
    try:
        await approve_pin(session, proposal_id, current_user.id)
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ── Test-now (no DB write) ─────────────────────────────────────────
class TestNowRequest(BaseModel):
    question: str


@router.post("/dashboard/learning/rules/test_now")
async def test_now(
    body: TestNowRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    from app.services.ask_router import route
    result = await route(body.question, session, current_user.id, log_to_db=False)
    return {
        "path": result.path,
        "intent": result.intent,
        "param": result.param,
        "answer": result.answer[:1000],
        "sources_text": result.sources_text,
    }


@router.get("/dashboard/learning/stats")
async def learning_stats(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Three metrics: 7-day pass-rate (most recent EvalRun), rules applied
    this week, corrections received this week.

    NO admin gate — any logged-in user can see these read-only counters.
    """
    from datetime import datetime as _dt, timedelta as _td
    from app.models import EvalRun, RepairProposal, AnswerFeedback, SystemFlag
    from sqlalchemy import func as _func

    cutoff = _dt.utcnow() - _td(days=7)

    # Most recent EvalRun's pass-rate (within last 7 days)
    eval_run = await session.scalar(
        select(EvalRun)
        .where(EvalRun.status == "completed")
        .where(EvalRun.finished_at >= cutoff)
        .order_by(EvalRun.id.desc())
    )
    pass_rate_7d = None
    if eval_run and eval_run.n_probes:
        pass_rate_7d = round(eval_run.n_pass / eval_run.n_probes, 3)

    # Baseline stored at Phase 1 completion (key=phase1_baseline_pass_rate)
    baseline_row = await session.scalar(
        select(SystemFlag).where(SystemFlag.key == "phase1_baseline_pass_rate")
    )
    pass_rate_baseline = None
    if baseline_row and baseline_row.value:
        try:
            pass_rate_baseline = round(float(baseline_row.value), 3)
        except (TypeError, ValueError):
            pass_rate_baseline = None

    # Rules applied in last 7 days
    rules_applied_7d = await session.scalar(
        select(_func.count(RepairProposal.id))
        .where(RepairProposal.status == "applied")
        .where(RepairProposal.applied_at >= cutoff)
    ) or 0

    # Corrections received in last 7 days
    corrections_7d = await session.scalar(
        select(_func.count(AnswerFeedback.id))
        .where(AnswerFeedback.vote == "down")
        .where(AnswerFeedback.correction_text.isnot(None))
        .where(AnswerFeedback.created_at >= cutoff)
    ) or 0

    return {
        "pass_rate_7d": pass_rate_7d,
        "pass_rate_baseline": pass_rate_baseline,
        "rules_applied_7d": int(rules_applied_7d),
        "corrections_7d": int(corrections_7d),
    }
