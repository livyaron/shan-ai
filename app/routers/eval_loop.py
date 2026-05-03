"""Eval & Self-Repair Loop router.

Exposes the 4-agent loop (probe → judge → repair → verify) as admin endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_maker
from app.models import (
    User, QueryLog, EvalRun, RepairProposal, SystemFlag,
)
from app.routers.login import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["json_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
logger = logging.getLogger(__name__)

_FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "eval_questions.json"
MAX_INNER_PASSES = 3

# In-memory live log per run_id — populated by run_cycle, polled by frontend
_run_live_logs: dict[int, list] = {}


def _emit_log(run_id: int, entry: dict) -> None:
    logs = _run_live_logs.setdefault(run_id, [])
    logs.append(entry)
    if len(logs) > 200:
        _run_live_logs[run_id] = logs[-200:]


# ─────────────────────────────────────────────────────────────────────────────
# Kill-switch helpers
# ─────────────────────────────────────────────────────────────────────────────
KILL_SWITCH_KEY = "eval_paused"


async def _is_paused(session: AsyncSession) -> bool:
    row = (await session.execute(
        select(SystemFlag).where(SystemFlag.key == KILL_SWITCH_KEY)
    )).scalar_one_or_none()
    return bool(row and (row.value or "").lower() == "true")


async def _set_pause(session: AsyncSession, paused: bool) -> None:
    row = (await session.execute(
        select(SystemFlag).where(SystemFlag.key == KILL_SWITCH_KEY)
    )).scalar_one_or_none()
    if row:
        row.value = "true" if paused else "false"
    else:
        session.add(SystemFlag(key=KILL_SWITCH_KEY, value="true" if paused else "false"))
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Cycle orchestrator — single entry point shared by manual + cron triggers
# ─────────────────────────────────────────────────────────────────────────────
async def run_cycle(
    n_probes: int = 20,
    seed_failures: bool = True,
    triggered_by_user_id: int | None = None,
) -> int:
    """Execute one full pass of the loop. Returns the EvalRun.id.

    Always uses its own session — safe to call from cron without a request context.
    """
    from app.services.eval_probe_service import generate_probes
    from app.services.eval_judge_service import judge_answer
    from app.services.eval_repair_service import (
        cluster_failures, propose_fix, FailureItem,
    )
    from app.services.eval_verifier_service import verify_proposal

    async with async_session_maker() as session:
        # Kill-switch check
        if await _is_paused(session):
            logger.warning("eval_loop: paused — skipping cycle")
            return -1

        # Concurrency guard: skip if another run is already active
        active = (await session.execute(
            select(EvalRun).where(EvalRun.status == "running")
        )).scalars().first()
        if active:
            logger.warning(f"eval_loop: another run #{active.id} is in progress — skipping")
            return active.id

        run = EvalRun(
            status="running",
            n_probes=n_probes,
            triggered_by_user_id=triggered_by_user_id,
            config_json={"seed_failures": seed_failures, "audit": []},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    try:
        # --- Phase 1: probe ---------------------------------------------------
        async with async_session_maker() as session:
            if await _is_paused(session):
                return await _abort_run(run_id, "paused before probe")
            probes = await generate_probes(session, n=n_probes, seed_failures=seed_failures)
            logger.info(f"eval_loop run #{run_id}: generated {len(probes)} probes")

        # --- Phase 2: ask + judge --------------------------------------------
        from app.services.knowledge_service import answer_with_full_context
        failure_items: list[FailureItem] = []
        n_pass = n_partial = n_fail = 0
        # Track last answer per question across the whole cycle (initial + retries)
        last_answers: dict[str, dict] = {}
        # Track which proposal ids actually got applied (for summary)
        applied_proposal_ids: list[int] = []

        async with async_session_maker() as session:
            for probe in probes:
                if await _is_paused(session):
                    return await _abort_run(run_id, "paused mid-judge")
                try:
                    result = await answer_with_full_context(
                        question=probe.question,
                        session=session,
                        user_id=triggered_by_user_id or 0,
                        log_to_db=False,
                    )
                    answer = result.get("answer") or ""
                except Exception as e:
                    logger.warning(f"answer failed for probe '{probe.question[:40]}': {e}")
                    answer = ""

                try:
                    verdict = await judge_answer(
                        session=session,
                        question=probe.question,
                        answer=answer,
                        target_project=probe.target_project,
                        target_field=probe.target_field,
                    )
                except Exception as e:
                    logger.warning(f"judge_answer crashed for '{probe.question[:40]}': {e}")
                    from app.services.eval_judge_service import Verdict as _Verdict
                    verdict = _Verdict("FAIL", "WRONG_DATA", f"judge crash: {type(e).__name__}", 3)

                if verdict.verdict == "PASS":
                    n_pass += 1
                elif verdict.verdict == "PARTIAL":
                    n_partial += 1
                    failure_items.append(FailureItem(probe=probe, verdict=verdict, log_id=probe.seeded_from_log_id, answer=answer))
                else:
                    n_fail += 1
                    failure_items.append(FailureItem(probe=probe, verdict=verdict, log_id=probe.seeded_from_log_id, answer=answer))

                last_answers[probe.question] = {"answer": answer, "verdict": verdict.verdict}

                _emit_log(run_id, {
                    "type": "probe",
                    "probe": probe.question,
                    "answer": (answer or "")[:250],
                    "verdict": verdict.verdict,
                    "failure_type": verdict.failure_type or "",
                    "evidence": (verdict.evidence or "")[:140],
                })

        # --- Phase 3: cluster + propose (keep cluster↔proposal pairing) -------
        clusters = cluster_failures(failure_items)
        proposal_pairs: list[tuple[int, list[dict]]] = []  # (proposal_id, failing_qs)
        async with async_session_maker() as session:
            for c in clusters[:5]:  # cap proposals per cycle
                if await _is_paused(session):
                    break
                p = await propose_fix(session, c, eval_run_id=run_id)
                if not p:
                    continue
                failing_qs = [
                    {
                        "question": it.probe.question,
                        "target_project": it.probe.target_project,
                        "target_field": it.probe.target_field,
                        "log_id": it.log_id,
                    }
                    for it in c.items[:8]
                ]
                proposal_pairs.append((p.id, failing_qs))

        # --- Phase 4: verify each proposal -----------------------------------
        n_applied = 0
        total_proposals = 0

        async def _run_phase_4(pairs: list[tuple[int, list[dict]]]) -> tuple[int, int]:
            """Run verify for a batch of proposal_pairs. Returns (applied, total)."""
            _applied = _total = 0
            for proposal_id, failing_qs in pairs:
                async with async_session_maker() as session:
                    if await _is_paused(session):
                        break
                    fresh = await session.get(RepairProposal, proposal_id)
                    if not fresh:
                        continue
                    _total += 1
                    try:
                        res = await verify_proposal(
                            session, fresh, failing_qs, user_id=triggered_by_user_id,
                        )
                        if res.decision == "applied":
                            _applied += 1
                            applied_proposal_ids.append(proposal_id)
                    except Exception as e:
                        logger.error(f"verify_proposal failed for #{fresh.id}: {e}")
                        fresh.status = "rejected"
                        fresh.reject_reason = f"verifier error: {type(e).__name__}: {e}"
                        await session.commit()
            return _applied, _total

        _a, _t = await _run_phase_4(proposal_pairs)
        n_applied += _a
        total_proposals += _t

        # --- Phase 5: retry loop — re-evaluate still-failing, propose again ---
        iterations_log: list[dict] = []
        remaining_failures = list(failure_items)  # all items that started as FAIL/PARTIAL

        for inner_pass in range(1, MAX_INNER_PASSES + 1):
            async with async_session_maker() as session:
                if await _is_paused(session):
                    break

            # Which questions from failure_items are still failing after applied proposals?
            # Re-evaluate them using the live (now-patched) answering pipeline.
            still_failing_qs = [
                {
                    "question": it.probe.question,
                    "target_project": it.probe.target_project,
                    "target_field": it.probe.target_field,
                    "log_id": it.log_id,
                }
                for it in remaining_failures
            ]
            if not still_failing_qs:
                break

            _emit_log(run_id, {
                "type": "iteration",
                "pass": inner_pass,
                "n_still_failing": len(still_failing_qs),
            })

            # Re-answer + re-judge with fresh state (patches now applied to DB)
            from app.services.knowledge_service import answer_with_full_context
            new_failures: list[FailureItem] = []
            n_fixed_this_pass = 0

            async with async_session_maker() as session:
                for q in still_failing_qs:
                    try:
                        result = await answer_with_full_context(
                            question=q["question"],
                            session=session,
                            user_id=triggered_by_user_id or 0,
                            log_to_db=False,
                        )
                        answer = result.get("answer") or ""
                    except Exception as e:
                        logger.warning(f"retry answer failed '{q['question'][:40]}': {e}")
                        answer = ""

                    try:
                        verdict = await judge_answer(
                            session=session,
                            question=q["question"],
                            answer=answer,
                            target_project=q.get("target_project"),
                            target_field=q.get("target_field"),
                        )
                    except Exception as e:
                        logger.warning(f"retry judge failed '{q['question'][:40]}': {e}")
                        from app.services.eval_judge_service import Verdict as _Verdict
                        verdict = _Verdict("FAIL", "WRONG_DATA", f"judge crash: {type(e).__name__}", 3)

                    last_answers[q["question"]] = {"answer": answer, "verdict": verdict.verdict}

                    _emit_log(run_id, {
                        "type": "retry",
                        "pass": inner_pass,
                        "probe": q["question"],
                        "answer": (answer or "")[:250],
                        "verdict": verdict.verdict,
                        "failure_type": verdict.failure_type or "",
                        "evidence": (verdict.evidence or "")[:140],
                    })

                    if verdict.verdict == "PASS":
                        n_pass += 1
                        n_fail -= 1
                        n_fixed_this_pass += 1
                    else:
                        # Still failing — carry forward to next inner pass
                        matching = [it for it in remaining_failures if it.probe.question == q["question"]]
                        new_failures.extend(matching)

            iterations_log.append({
                "pass": inner_pass,
                "n_still_failing": len(still_failing_qs),
                "n_fixed": n_fixed_this_pass,
            })

            if not new_failures:
                break  # all fixed

            # Propose new repairs for still-failing items
            remaining_clusters = cluster_failures(new_failures)
            new_pairs: list[tuple[int, list[dict]]] = []
            async with async_session_maker() as session:
                for c in remaining_clusters[:3]:
                    if await _is_paused(session):
                        break
                    p = await propose_fix(session, c, eval_run_id=run_id)
                    if not p:
                        continue
                    fqs = [
                        {
                            "question": it.probe.question,
                            "target_project": it.probe.target_project,
                            "target_field": it.probe.target_field,
                            "log_id": it.log_id,
                        }
                        for it in c.items[:8]
                    ]
                    new_pairs.append((p.id, fqs))

            _a2, _t2 = await _run_phase_4(new_pairs)
            n_applied += _a2
            total_proposals += _t2
            remaining_failures = new_failures

        # --- Finalize EvalRun + emit summary entry ----------------------------
        applied_summary: list[dict] = []
        if applied_proposal_ids:
            async with async_session_maker() as session:
                rows = (await session.execute(
                    select(RepairProposal).where(RepairProposal.id.in_(applied_proposal_ids))
                )).scalars().all()
                applied_summary = [
                    {
                        "id": r.id,
                        "type": r.type,
                        "patch": r.patch_json,
                        "rationale": r.rationale,
                        "delta": r.delta_pass_rate,
                    }
                    for r in rows
                ]

        # Build still-failing list with last answer per question
        still_failing_summary = [
            {
                "question": it.probe.question,
                "last_answer": (last_answers.get(it.probe.question, {}).get("answer") or "")[:300],
            }
            for it in remaining_failures
        ]

        _emit_log(run_id, {
            "type": "summary",
            "applied": applied_summary,
            "iterations": iterations_log,
            "n_pass": n_pass,
            "n_partial": n_partial,
            "n_fail": n_fail,
            "still_failing": still_failing_summary,
        })

        async with async_session_maker() as session:
            run = await session.get(EvalRun, run_id)
            if run:
                run.finished_at = datetime.utcnow()
                run.n_pass = n_pass
                run.n_partial = n_partial
                run.n_fail = n_fail
                run.n_proposals_created = len(proposal_pairs) + total_proposals
                run.n_proposals_applied = n_applied
                run.status = "completed"
                cfg = run.config_json or {}
                cfg["iterations"] = iterations_log
                cfg["applied_summary"] = applied_summary
                run.config_json = cfg
                await session.commit()

        logger.info(f"eval_loop run #{run_id} completed: probes={len(probes)} "
                    f"pass={n_pass} fail={n_fail} proposals={len(proposal_pairs)} applied={n_applied}")
        return run_id

    except Exception as e:
        logger.exception(f"eval_loop run #{run_id} crashed: {e}")
        async with async_session_maker() as session:
            run = await session.get(EvalRun, run_id)
            if run:
                run.status = "failed"
                run.error = f"{type(e).__name__}: {e}"
                run.finished_at = datetime.utcnow()
                await session.commit()
        return run_id


async def _abort_run(run_id: int, reason: str) -> int:
    async with async_session_maker() as session:
        run = await session.get(EvalRun, run_id)
        if run:
            run.status = "aborted"
            run.error = reason
            run.finished_at = datetime.utcnow()
            await session.commit()
    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoints
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/dashboard/eval", response_class=HTMLResponse)
async def eval_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    runs = (await session.execute(
        select(EvalRun).order_by(desc(EvalRun.started_at)).limit(20)
    )).scalars().all()
    proposals = (await session.execute(
        select(RepairProposal).order_by(desc(RepairProposal.created_at)).limit(50)
    )).scalars().all()
    paused = await _is_paused(session)

    last_delta = None
    for r in runs:
        if r.status == "completed" and r.n_proposals_applied:
            # Avg delta of applied proposals from this run
            applied = (await session.execute(
                select(func.avg(RepairProposal.delta_pass_rate))
                .where(RepairProposal.eval_run_id == r.id, RepairProposal.status == "applied")
            )).scalar()
            if applied is not None:
                last_delta = float(applied)
                break

    return templates.TemplateResponse("eval.html", {
        "request": request,
        "current_user": current_user,
        "runs": runs,
        "proposals": proposals,
        "paused": paused,
        "last_delta": last_delta,
    })


@router.post("/dashboard/eval/run")
async def trigger_run(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    n = int(body.get("n_probes") or 20)
    seed = bool(body.get("seed_failures", True))

    # Run in background so the HTTP request returns quickly
    asyncio.create_task(run_cycle(
        n_probes=n,
        seed_failures=seed,
        triggered_by_user_id=current_user.id,
    ))
    return JSONResponse({"started": True, "n_probes": n, "seed_failures": seed})


@router.get("/dashboard/eval/runs")
async def list_runs(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    runs = (await session.execute(
        select(EvalRun).order_by(desc(EvalRun.started_at)).limit(50)
    )).scalars().all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "n_probes": r.n_probes,
            "n_pass": r.n_pass,
            "n_partial": r.n_partial,
            "n_fail": r.n_fail,
            "n_proposals_created": r.n_proposals_created,
            "n_proposals_applied": r.n_proposals_applied,
            "triggered_by_user_id": r.triggered_by_user_id,
            "error": r.error,
        }
        for r in runs
    ]


@router.get("/dashboard/eval/proposals")
async def list_proposals(
    status: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    stmt = select(RepairProposal).order_by(desc(RepairProposal.created_at)).limit(100)
    if status:
        stmt = stmt.where(RepairProposal.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": p.id,
            "type": p.type,
            "patch": p.patch_json,
            "rationale": p.rationale,
            "risk": p.risk,
            "status": p.status,
            "delta_pass_rate": p.delta_pass_rate,
            "regression_count": p.regression_count,
            "before": p.before_snapshot,
            "after": p.after_snapshot,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "applied_at": p.applied_at.isoformat() if p.applied_at else None,
            "reject_reason": p.reject_reason,
        }
        for p in rows
    ]


@router.post("/dashboard/eval/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.services.eval_verifier_service import apply_proposal
    proposal = await session.get(RepairProposal, proposal_id)
    if not proposal:
        raise HTTPException(404, "proposal not found")
    if proposal.status not in ("pending", "awaiting_approval"):
        raise HTTPException(400, f"cannot approve proposal in status '{proposal.status}'")
    await apply_proposal(session, proposal, user_id=current_user.id)
    return {"ok": True, "id": proposal.id, "status": proposal.status}


@router.post("/dashboard/eval/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "rejected by admin")[:500]
    proposal = await session.get(RepairProposal, proposal_id)
    if not proposal:
        raise HTTPException(404, "proposal not found")
    if proposal.status not in ("pending", "awaiting_approval"):
        raise HTTPException(400, f"cannot reject proposal in status '{proposal.status}'")
    proposal.status = "rejected"
    proposal.reject_reason = reason
    await session.commit()
    return {"ok": True, "id": proposal.id, "status": proposal.status}


@router.post("/dashboard/eval/proposals/{proposal_id}/rollback")
async def rollback_endpoint(
    proposal_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.services.eval_verifier_service import rollback_proposal
    proposal = await session.get(RepairProposal, proposal_id)
    if not proposal:
        raise HTTPException(404, "proposal not found")
    if proposal.status != "applied":
        raise HTTPException(400, f"cannot rollback proposal in status '{proposal.status}'")
    await rollback_proposal(session, proposal)
    return {"ok": True, "id": proposal.id, "status": proposal.status}


@router.post("/dashboard/eval/kill")
async def kill_switch(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    paused = bool(body.get("paused", True))
    await _set_pause(session, paused)
    return {"ok": True, "paused": paused}


# ─────────────────────────────────────────────────────────────────────────────
# Live feed + question management endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/eval/runs/latest/log")
async def get_latest_run_log(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    run = (await session.execute(
        select(EvalRun).order_by(desc(EvalRun.started_at)).limit(1)
    )).scalars().first()
    if not run:
        return {"run_id": None, "status": "none", "entries": [], "n_pass": 0, "n_fail": 0, "n_partial": 0}
    return {
        "run_id": run.id,
        "status": run.status,
        "entries": _run_live_logs.get(run.id, []),
        "n_pass": run.n_pass or 0,
        "n_fail": run.n_fail or 0,
        "n_partial": run.n_partial or 0,
        "n_probes": run.n_probes or 0,
    }


@router.get("/dashboard/eval/questions")
async def get_questions(current_user: User = Depends(get_current_user)):
    if not _FIXTURE.exists():
        return []
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data.get("questions", [])


@router.post("/dashboard/eval/questions")
async def save_questions(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    body = await request.json()
    questions = body.get("questions", [])
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    data["questions"] = questions
    _FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(questions)}


@router.get("/dashboard/eval/questions/suggestions")
async def suggest_questions(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Return recent bad QueryLog rows not already in the fixture."""
    rows = (await session.execute(
        select(QueryLog)
        .where(or_(
            QueryLog.user_feedback == 0,
            QueryLog.is_accurate == False,
            QueryLog.judge_verdict == "FAIL",
        ))
        .order_by(desc(QueryLog.timestamp))
        .limit(40)
    )).scalars().all()

    existing_qs: set[str] = set()
    try:
        data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        existing_qs = {q["question"] for q in data.get("questions", [])}
    except Exception:
        pass

    result = []
    seen: set[str] = set()
    for r in rows:
        q = (r.question or "").strip()
        if not q or q in existing_qs or q in seen:
            continue
        seen.add(q)
        result.append({
            "id": f"log-{r.id}",
            "question": q,
            "expected_route": "knowledge",
            "notes": f"מהלוג #{r.id} — feedback={r.user_feedback}, verdict={r.judge_verdict}",
            "log_id": r.id,
            "answer_preview": (r.ai_response or "")[:220],
        })
    return result


@router.post("/dashboard/eval/feedback")
async def submit_feedback(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """User marks a Q&A pair as correct (PASS) or wrong (FAIL).

    This overrides the automated judge verdict. The judge checks user_feedback
    first on every future evaluation of the same question.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    question = (body.get("question") or "").strip()
    answer = (body.get("answer") or "").strip()
    verdict = (body.get("verdict") or "").upper()
    log_id = body.get("log_id")

    if verdict not in ("PASS", "FAIL"):
        raise HTTPException(400, "verdict must be PASS or FAIL")
    if not question:
        raise HTTPException(400, "question required")

    # Try to find existing QueryLog row
    log_row = None
    if log_id:
        try:
            log_row = await session.get(QueryLog, int(log_id))
        except Exception:
            pass
    if log_row is None and question:
        log_row = (await session.execute(
            select(QueryLog)
            .where(QueryLog.question == question)
            .order_by(QueryLog.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

    if log_row is None:
        # Create a minimal row so future judge runs pick up the feedback
        from datetime import datetime as _dt
        log_row = QueryLog(
            question=question,
            ai_response=answer or None,
            user_id=current_user.id,
            timestamp=_dt.utcnow(),
        )
        session.add(log_row)

    log_row.user_feedback = 1 if verdict == "PASS" else 0
    log_row.judge_verdict = verdict
    log_row.is_accurate = (verdict == "PASS")
    await session.commit()
    return {"ok": True, "question": question, "verdict": verdict}
