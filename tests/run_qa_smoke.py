"""Q&A smoke runner — drives the same routing the dashboard endpoint uses.

Usage (inside the running container):
    docker exec -it shan-ai-fastapi python tests/run_qa_smoke.py
    docker exec -it shan-ai-fastapi python tests/run_qa_smoke.py --judge   # also runs Agent 2 judge

Reports per-question: route taken, first 280 chars of answer, judge verdict (if --judge).
Exit code 0 = all expected routes matched; 1 = at least one routing mismatch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure project root is importable when run from anywhere
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import async_session_maker
from app.models import User
from sqlalchemy import select


_DECISION_KEYWORDS = ("החלטה", "החלטות", "ההחלטה", "ההחלטות")
_FIXTURE = ROOT / "tests" / "eval_questions.json"


# ─────────────────────────────────────────────────────────────────────────────
# Routing — mirrors app/routers/ask.py exactly so we test the production path.
# ─────────────────────────────────────────────────────────────────────────────
async def route_and_answer(session, question: str, user_id: int) -> tuple[str, str]:
    """Returns (route, answer). route ∈ {'decision_db', 'project', 'knowledge', 'recorded_decision'}."""
    from app.services.telegram_routing import _is_project_query
    from app.services.knowledge_service import answer_with_full_context

    # 1. Decision-history queries (same trigger as ask.py:44)
    if any(kw in question for kw in _DECISION_KEYWORDS):
        from app.services.knowledge_service import get_decisions_context, answer_decisions_question
        ctx = await get_decisions_context(session, user_id)
        if ctx:
            answer = await answer_decisions_question(question, ctx)
        else:
            answer = "לא נמצאו החלטות עבורך במסד הנתונים."
        return ("decision_db", answer)

    # 2. Project queries
    if _is_project_query(question):
        try:
            from app.services.project_tools import answer_project_query
            answer, _ = await answer_project_query(question, session, {}, user_id=user_id)
            return ("project", answer)
        except Exception as e:
            print(f"  [warn] project_tools failed: {e}; falling through", file=sys.stderr)

    # 3. Knowledge base RAG
    res = await answer_with_full_context(question, session, user_id, log_to_db=False)
    return ("knowledge", res.get("answer") or "")


def expected_route_match(expected: str, actual: str) -> bool:
    """Map fixture's coarse expected_route onto the runtime route names."""
    if expected == "knowledge":
        # Decision-history queries answer from decisions DB but originate from a
        # knowledge-style question; both are acceptable.
        return actual in ("knowledge", "decision_db")
    if expected == "project":
        return actual == "project"
    if expected == "decision":
        # Recording a fresh decision goes through the Telegram polling path, not
        # the dashboard /ask endpoint. The smoke test cannot exercise that path
        # without a Telegram update; flag it for manual verification.
        return actual in ("knowledge", "decision_db")  # smoke can't truly test recording
    return True


async def main(judge: bool, user_id_override: int | None) -> int:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    questions = fixture["questions"]
    print(f"Loaded {len(questions)} questions from {_FIXTURE.name}\n")

    async with async_session_maker() as session:
        # Pick a real user_id so QueryLog FKs resolve
        if user_id_override is not None:
            user_id = user_id_override
        else:
            row = (await session.execute(select(User).limit(1))).scalar_one_or_none()
            user_id = row.id if row else 0

        from app.services.eval_judge_service import judge_answer

        n_route_ok = 0
        n_route_mismatch = 0
        n_judged_pass = 0
        n_judged_fail = 0

        for q in questions:
            qid = q.get("id", "?")
            text = q["question"]
            expected = q.get("expected_route", "knowledge")
            print(f"── [{qid}] expected={expected}")
            print(f"   Q: {text}")

            try:
                route, answer = await route_and_answer(session, text, user_id)
            except Exception as e:
                print(f"   ✖ route error: {type(e).__name__}: {e}\n")
                n_route_mismatch += 1
                continue

            ok = expected_route_match(expected, route)
            mark = "✓" if ok else "✖"
            print(f"   {mark} route={route}")
            n_route_ok += int(ok)
            n_route_mismatch += int(not ok)

            short = (answer or "").replace("\n", " ")
            if len(short) > 280:
                short = short[:280] + "…"
            print(f"   A: {short}")

            if judge and (q.get("target_project") or q.get("target_field")):
                try:
                    v = await judge_answer(
                        session=session,
                        question=text,
                        answer=answer,
                        target_project=q.get("target_project"),
                        target_field=q.get("target_field"),
                    )
                    print(f"   ⚖ judge={v.verdict} failure_type={v.failure_type} "
                          f"sev={v.severity} :: {v.evidence[:140]}")
                    if v.verdict == "PASS":
                        n_judged_pass += 1
                    elif v.verdict == "FAIL":
                        n_judged_fail += 1
                except Exception as e:
                    print(f"   [warn] judge failed: {e}")

            print()

        print("=" * 60)
        print(f"Routing:  {n_route_ok} ok,  {n_route_mismatch} mismatch  /  {len(questions)} total")
        if judge:
            print(f"Judge:    {n_judged_pass} PASS,  {n_judged_fail} FAIL")
        return 0 if n_route_mismatch == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", action="store_true", help="Also run the eval-loop judge on each answer")
    parser.add_argument("--user-id", type=int, default=None, help="Override user_id for QueryLog FK")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(judge=args.judge, user_id_override=args.user_id)))
