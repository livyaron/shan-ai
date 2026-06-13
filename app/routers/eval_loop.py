"""Eval loop router — gold-truth curation, per-question cycle, SSE live feed."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, get_db_session
from app.models import EvalGoldAnswer, EvalRun, QueryLog, RepairProposal, SystemFlag, User
from app.routers.login import get_current_user
from app.services import gold_truth_service as gts
from app.services import judge_backfill_service
from app.services.gold_truth_service import question_hash
from app.services.per_question_loop_service import run_cycle

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/dashboard", tags=["eval-loop"])


# Extend this list to add new eval questions. They must be approved via the curate UI before they affect the cycle.
SEED_QUESTIONS: list[str] = [
    "מי המנהל של פרויקט בת ים?",
    "מה הסטטוס של פרויקט נתניה?",
    "מתי יסתיים פרויקט קצרין?",
    "מי מנה\"פ של תחמ\"ש קצרין?",
    "אילו סיכונים יש בפרויקט אלון תבור?",
    "מה השלב הנוכחי של פרויקט חולה?",
    "תן לי דוח שבועי של פרויקט יאסיף",
    "מה תאריך הסיום המוערך של פרויקט בית שאן?",
    "מי המנהל של פרויקט קריית גת?",
    "מה צריך לטפל בפרויקט עפולה?",
]


# ───────────────────────── live event stream ─────────────────────────

_events: deque[dict] = deque(maxlen=2000)
_event_wake = asyncio.Event()
_seq = 0


def _emit_log(event: dict) -> None:
    global _seq
    _seq += 1
    enriched = {"seq": _seq, "ts": datetime.utcnow().isoformat(), **event}
    _events.append(enriched)
    _event_wake.set()


async def _sse_iter():
    last_seq = 0
    # Flush any backlog first
    for ev in list(_events):
        if ev["seq"] > last_seq:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            last_seq = ev["seq"]
    while True:
        _event_wake.clear()
        try:
            await asyncio.wait_for(_event_wake.wait(), timeout=15)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        for ev in list(_events):
            if ev["seq"] > last_seq:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                last_seq = ev["seq"]


# ───────────────────────── pages ─────────────────────────

@router.get("/eval", response_class=HTMLResponse)
async def eval_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("eval.html", {"request": request, "current_user": current_user})


@router.get("/eval/curate", response_class=HTMLResponse)
async def eval_curate_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("eval_curate.html", {"request": request, "current_user": current_user})


# ───────────────────────── gold curation API ─────────────────────────

@router.get("/eval/gold/proposals")
async def gold_proposals(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    question: str | None = None,
):
    existing_rows = await gts.list_gold(session)
    existing = {r.question_hash: r for r in existing_rows}

    # Single-question mode: return proposal for just this question
    if question:
        q = question.strip()
        h = question_hash(q)
        existing_row = existing.get(h)
        if existing_row:
            item = {
                "question_hash": h,
                "question": q,
                "proposed_answer": existing_row.gold_answer,
                "source": existing_row.source,
                "target_project": existing_row.target_project,
                "target_field": existing_row.target_field,
                "approved": True,
                "approved_at": existing_row.approved_at.isoformat() if existing_row.approved_at else None,
            }
        else:
            try:
                proposal = await gts.propose_gold(session, q)
            except Exception as e:
                logger.warning(f"propose_gold failed for {q!r}: {e}")
                proposal = {"answer": "", "source": "manual", "target_project": None, "target_field": None}
            item = {
                "question_hash": h,
                "question": q,
                "proposed_answer": proposal["answer"],
                "source": proposal["source"],
                "target_project": proposal["target_project"],
                "target_field": proposal["target_field"],
                "approved": False,
                "approved_at": None,
            }
        return JSONResponse({"proposals": [item], "approved_count": 1 if item["approved"] else 0, "total": 1})

    out = []
    seen_hashes: set[str] = set()
    for q in SEED_QUESTIONS:
        h = question_hash(q)
        seen_hashes.add(h)
        existing_row = existing.get(h)
        if existing_row:
            out.append({
                "question_hash": h,
                "question": q,
                "proposed_answer": existing_row.gold_answer,
                "source": existing_row.source,
                "target_project": existing_row.target_project,
                "target_field": existing_row.target_field,
                "approved": True,
                "approved_at": existing_row.approved_at.isoformat() if existing_row.approved_at else None,
            })
        else:
            try:
                # Fast path: skip LLM fallback so the page loads instantly.
                # User can request LLM per-row via /eval/gold/propose_one.
                proposal = await gts.propose_gold(session, q, use_llm=False)
            except Exception as e:
                logger.warning(f"propose_gold failed for {q!r}: {e}")
                proposal = {"answer": "", "source": "manual", "target_project": None, "target_field": None}
            out.append({
                "question_hash": h,
                "question": q,
                "proposed_answer": proposal["answer"],
                "source": proposal["source"],
                "target_project": proposal["target_project"],
                "target_field": proposal["target_field"],
                "approved": False,
                "approved_at": None,
            })

    # Include any pre-existing gold rows not in the seed list
    for h, row in existing.items():
        if h in seen_hashes:
            continue
        out.append({
            "question_hash": h,
            "question": row.question,
            "proposed_answer": row.gold_answer,
            "source": row.source,
            "target_project": row.target_project,
            "target_field": row.target_field,
            "approved": True,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        })
    return JSONResponse({"proposals": out, "approved_count": sum(1 for p in out if p["approved"]), "total": len(out)})


@router.post("/eval/gold/save")
async def gold_save(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    question = (body.get("question") or "").strip()
    answer = (body.get("gold_answer") or "").strip()
    if not question or not answer:
        raise HTTPException(400, "question and gold_answer required")
    row = await gts.save_gold(
        session,
        question=question,
        gold_answer=answer,
        user_id=current_user.id,
        target_project=body.get("target_project"),
        target_field=body.get("target_field"),
        source=body.get("source") or "manual",
    )
    return JSONResponse({"ok": True, "question_hash": row.question_hash})


@router.post("/eval/gold/bulk_approve")
async def gold_bulk_approve(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Approve every seed question whose proposed answer came from a DB lookup."""
    approved = 0
    for q in SEED_QUESTIONS:
        h = question_hash(q)
        existing = await session.scalar(select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
        if existing:
            continue
        try:
            proposal = await gts.propose_gold(session, q, use_llm=False)
        except Exception as e:
            logger.warning(f"bulk_approve propose_gold failed for {q!r}: {e}")
            continue
        if proposal["source"] != "db_lookup":
            continue
        await gts.save_gold(
            session,
            question=q,
            gold_answer=proposal["answer"],
            user_id=current_user.id,
            target_project=proposal["target_project"],
            target_field=proposal["target_field"],
            source="db_lookup",
        )
        approved += 1
    return JSONResponse({"approved": approved})


@router.post("/eval/gold/propose_one")
async def gold_propose_one(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Force a single LLM proposal for a question (slow — used per-row on demand)."""
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question required")
    try:
        proposal = await gts.propose_gold(session, question, use_llm=True)
    except Exception as e:
        logger.warning(f"propose_one failed for {question!r}: {e}")
        raise HTTPException(500, str(e))
    return JSONResponse(proposal)


@router.delete("/eval/gold/{q_hash}")
async def gold_delete(
    q_hash: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    ok = await gts.delete_gold(session, q_hash)
    if not ok:
        raise HTTPException(404, "not found")
    return JSONResponse({"ok": True})


# ───────────────────────── cycle control ─────────────────────────

_cycle_task: asyncio.Task | None = None
_backfill_task: asyncio.Task | None = None


@router.post("/eval/run")
async def eval_run(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    global _cycle_task
    if _cycle_task and not _cycle_task.done():
        raise HTTPException(409, "cycle already running")

    # Reset event stream for a fresh run
    _events.clear()
    _event_wake.set()

    user_id = current_user.id

    async def _runner():
        async with async_session_maker() as own:
            try:
                await run_cycle(own, user_id=user_id, emit=_emit_log)
            except Exception as e:
                logger.exception("eval cycle failed")
                _emit_log({"type": "cycle_error", "error": str(e)})

    _cycle_task = asyncio.create_task(_runner())
    _emit_log({"type": "cycle_triggered", "user_id": user_id})
    return JSONResponse({"ok": True})


@router.post("/eval/abort")
async def eval_abort(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == "eval_kill"))
    if flag:
        flag.value = "1"
    else:
        session.add(SystemFlag(key="eval_kill", value="1"))
    await session.commit()
    _emit_log({"type": "abort_requested", "user_id": current_user.id})
    return JSONResponse({"ok": True})


@router.get("/eval/stream")
async def eval_stream(current_user: User = Depends(get_current_user)):
    return StreamingResponse(_sse_iter(), media_type="text/event-stream")


@router.get("/eval/runs")
async def eval_runs(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    rows = (await session.execute(select(EvalRun).order_by(EvalRun.started_at.desc()).limit(20))).scalars().all()
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "n_probes": r.n_probes,
            "n_pass": r.n_pass,
            "n_fail": r.n_fail,
            "n_proposals_applied": r.n_proposals_applied,
        })
    return JSONResponse({"runs": out})


# ───────────────────────── judge backfill ─────────────────────────

@router.post("/eval/backfill")
async def start_backfill(
    limit: int = 200,
    current_user: User = Depends(get_current_user),
):
    """Kick off judge backfill in the background; UI polls /eval/backfill/status."""
    global _backfill_task
    if _backfill_task is not None and not _backfill_task.done():
        return {"status": "already_running"}
    if judge_backfill_service.get_progress()["running"]:
        return {"status": "already_running"}

    async def _run():
        async with async_session_maker() as s:
            await judge_backfill_service.run_backfill(s, limit=limit)

    _backfill_task = asyncio.create_task(_run())
    return {"status": "started", "limit": limit}


@router.get("/eval/backfill/status")
async def backfill_status(current_user: User = Depends(get_current_user)):
    return judge_backfill_service.get_progress()


# ───────────────────────── quality dashboard ─────────────────────────

@router.get("/quality", response_class=HTMLResponse)
async def quality_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("quality.html", {"request": request, "current_user": current_user})


@router.get("/quality/data")
async def quality_data(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    runs = (await session.execute(
        select(EvalRun).where(EvalRun.status == "completed").order_by(EvalRun.started_at)
    )).scalars().all()
    run_trend = [
        {"date": r.started_at.strftime("%d/%m"), "pass_rate": round(r.n_pass / r.n_probes * 100) if r.n_probes else 0}
        for r in runs
    ]

    verdicts = dict((await session.execute(
        select(QueryLog.judge_verdict, func.count()).where(QueryLog.judge_verdict.isnot(None)).group_by(QueryLog.judge_verdict)
    )).all())

    failures = [
        {"type": ft, "count": c}
        for ft, c in (await session.execute(
            select(QueryLog.failure_type, func.count()).where(QueryLog.failure_type.isnot(None))
            .group_by(QueryLog.failure_type).order_by(func.count().desc())
        )).all()
    ]

    fb_weekly = [
        {"week": w.strftime("%d/%m"), "up": up, "down": down, "none": none}
        for w, up, down, none in (await session.execute(
            select(
                func.date_trunc("week", QueryLog.timestamp).label("w"),
                func.count().filter(QueryLog.user_feedback == 1),
                func.count().filter(QueryLog.user_feedback == -1),
                func.count().filter(QueryLog.user_feedback == 0),
            ).group_by("w").order_by("w")
        )).all()
    ]

    worst = [
        {
            "id": r.id,
            "question": r.question,
            "answer": (r.ai_response or "")[:200],
            "failure_type": r.failure_type,
            "ts": r.timestamp.strftime("%d/%m/%Y %H:%M"),
        }
        for r in (await session.execute(
            select(QueryLog).where(QueryLog.judge_verdict == "FAIL")
            .order_by(QueryLog.timestamp.desc()).limit(20)
        )).scalars().all()
    ]

    return {"run_trend": run_trend, "verdicts": verdicts, "failures": failures,
            "feedback_weekly": fb_weekly, "worst": worst}


# ───────────────────────── gold candidates ─────────────────────────

@router.get("/eval/gold/candidates")
async def gold_candidates(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Ranked gold candidates from production logs.

    Rank 1: human-rated rows; rank 2: judge FAIL/PARTIAL; rank 3: frequent questions.
    Dedup by normalized question text; exclude questions already in gold.
    """
    gold_hashes = {g.question_hash for g in await gts.list_gold(session)}

    rows = (await session.execute(
        select(
            QueryLog.id, QueryLog.question, QueryLog.ai_response,
            QueryLog.user_feedback, QueryLog.judge_verdict, QueryLog.failure_type,
        )
        .where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
        .limit(1000)
    )).all()

    freq: dict[str, int] = {}
    best: dict[str, any] = {}
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key:
            continue
        freq[key] = freq.get(key, 0) + 1
        best.setdefault(key, r)

    out = []
    for key, r in best.items():
        if question_hash(r.question) in gold_hashes:
            continue
        out.append({
            "log_id": r.id,
            "question": r.question,
            "ai_response": r.ai_response,
            "count": freq[key],
            "user_feedback": r.user_feedback,
            "judge_verdict": r.judge_verdict,
            "failure_type": r.failure_type,
        })
    out.sort(key=lambda d: (
        0 if (d["user_feedback"] or 0) != 0 else 1,
        0 if d["judge_verdict"] in ("FAIL", "PARTIAL") else 1,
        -d["count"],
    ))
    return {"candidates": out[:50]}
