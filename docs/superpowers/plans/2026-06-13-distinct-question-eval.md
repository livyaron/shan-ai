# Distinct-Question Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report eval pass-rate over distinct questions (one verdict per `question_hash`, latest row wins) instead of duplicate-heavy log rows, plus a re-judge of the representative set after gold changes.

**Architecture:** A pure aggregation service groups existing `query_logs` verdicts by normalized question hash, keeping the latest row per hash. A data endpoint + dashboard panel surface the distinct-question pass-rate as the headline. A `rejudge_distinct` function re-judges one representative per distinct question, reusing the existing `_rejudge_progress` machinery and concurrency guard.

**Tech Stack:** FastAPI, async SQLAlchemy, Chart.js, pytest (+pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-06-13-distinct-question-eval-design.md`

**Conventions (every task):**
- Deploy target is **Railway** (local Docker deprecated). Tests still run in the local container for speed during dev (`docker exec shan-ai-api pytest ...`), but final verification + the data run happen on Railway. NOTE: if local containers are stopped, start just what you need: `docker-compose start postgres fastapi` (do NOT run the bot long-term if Railway is live — polling conflict; for test-only runs it is fine briefly, but prefer `docker-compose up -d` only when needed and `docker-compose stop` after).
- `question_hash` from `app/services/gold_truth_service.py` is the canonical normalizer — use it everywhere (gold uses it too, so verdict↔gold alignment holds).
- Never `docker-compose down -v`.
- Hebrew UI strings prefixed `‏` (U+200F).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/services/distinct_eval_service.py` | Create | Group query_logs by question_hash (latest wins); summary stats |
| `tests/test_distinct_eval.py` | Create | Aggregation + summary tests |
| `app/services/judge_backfill_service.py` | Modify | + `rejudge_distinct(session)` (representatives only) |
| `tests/test_judge_gold_backed.py` | Modify | + rejudge_distinct test |
| `app/routers/eval_loop.py` | Modify | + `/quality/distinct` data endpoint, `/eval/rejudge-distinct` |
| `app/templates/quality.html` | Modify | distinct panel + most-asked table + rejudge-distinct button |

---

### Task 1: `distinct_eval_service` — aggregation + summary

**Files:**
- Create: `app/services/distinct_eval_service.py`
- Test: `tests/test_distinct_eval.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_distinct_eval.py
"""Distinct-question aggregation: one verdict per question_hash, latest row wins."""
import pytest
from datetime import datetime, timedelta

from app.models import QueryLog
from app.services import distinct_eval_service as des


@pytest.mark.asyncio
async def test_distinct_groups_by_question_latest_wins(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback, EvalGoldAnswer
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        QueryLog(question="כמה פרויקטים?", ai_response="a", judge_verdict="FAIL",
                 judged_against_gold=True, timestamp=base),
        QueryLog(question="כמה פרויקטים?", ai_response="b", judge_verdict="PASS",
                 judged_against_gold=True, timestamp=base + timedelta(hours=1)),  # latest
        QueryLog(question="מי המנהל?", ai_response="c", judge_verdict="FAIL",
                 failure_type="WRONG_PROJECT", judged_against_gold=False, timestamp=base),
    ])
    await db_session.commit()

    rows = await des.distinct_question_eval(db_session)
    by_q = {r["question"]: r for r in rows}

    assert len(rows) == 2                              # 2 distinct questions
    assert by_q["כמה פרויקטים?"]["verdict"] == "PASS"  # latest row won
    assert by_q["כמה פרויקטים?"]["count"] == 2         # two raw rows collapsed
    assert by_q["מי המנהל?"]["verdict"] == "FAIL"
    assert by_q["מי המנהל?"]["count"] == 1


@pytest.mark.asyncio
async def test_distinct_summary_counts_each_question_once(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback, EvalGoldAnswer
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    # 3 rows of one PASS question + 1 FAIL question → pass_rate must be 50%, not 75%
    db_session.add_all([
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base),
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base + timedelta(minutes=1)),
        QueryLog(question="q1", ai_response="x", judge_verdict="PASS", judged_against_gold=True, timestamp=base + timedelta(minutes=2)),
        QueryLog(question="q2", ai_response="y", judge_verdict="FAIL", judged_against_gold=True, timestamp=base),
    ])
    await db_session.commit()

    s = await des.distinct_summary(db_session)
    assert s["distinct_total"] == 2
    assert s["distinct_pass"] == 1
    assert s["distinct_fail"] == 1
    assert s["pass_rate"] == 50
    assert s["gold_backed"] == 2
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_distinct_eval.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/distinct_eval_service.py
"""Distinct-question aggregation over query_logs.

Collapses duplicate-heavy traffic to one representative per normalized question
(latest row wins), so eval metrics reflect the spread of questions rather than
the volume of repeats. Pure reads — no judging, no writes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import question_hash


async def _representatives(session: AsyncSession) -> list[dict]:
    """One dict per distinct question_hash, from the LATEST row, with dup count."""
    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
    )).scalars().all()

    seen: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in rows:
        h = question_hash(r.question)
        counts[h] = counts.get(h, 0) + 1
        if h not in seen:                      # first encountered = latest (desc order)
            seen[h] = {
                "question": r.question,
                "question_hash": h,
                "verdict": r.judge_verdict,
                "failure_type": r.failure_type,
                "judged_against_gold": r.judged_against_gold,
                "_rep_id": r.id,
            }
    for h, d in seen.items():
        d["count"] = counts[h]
    return list(seen.values())


async def distinct_question_eval(session: AsyncSession) -> list[dict]:
    """Public: list of distinct-question entries (latest verdict, dup count)."""
    reps = await _representatives(session)
    for d in reps:
        d.pop("_rep_id", None)
    return reps


async def distinct_summary(session: AsyncSession) -> dict:
    reps = await _representatives(session)
    total = len(reps)
    passed = sum(1 for d in reps if d["verdict"] == "PASS")
    failed = sum(1 for d in reps if d["verdict"] == "FAIL")
    unjudged = sum(1 for d in reps if d["verdict"] is None)
    gold_backed = sum(1 for d in reps if d["judged_against_gold"] is True)
    judged = passed + failed
    pass_rate = round(passed / judged * 100) if judged else 0
    return {
        "distinct_total": total,
        "distinct_pass": passed,
        "distinct_fail": failed,
        "distinct_unjudged": unjudged,
        "gold_backed": gold_backed,
        "pass_rate": pass_rate,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_distinct_eval.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/distinct_eval_service.py tests/test_distinct_eval.py
git commit -m "feat(quality): distinct-question aggregation service"
```

---

### Task 2: `rejudge_distinct` — re-judge representatives

**Files:**
- Modify: `app/services/judge_backfill_service.py`
- Test: `tests/test_judge_gold_backed.py` (append)

- [ ] **Step 1: Write failing test**

```python
# append to tests/test_judge_gold_backed.py
@pytest.mark.asyncio
async def test_rejudge_distinct_judges_one_per_question(db_session):
    from sqlalchemy import delete
    from datetime import datetime, timedelta
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    base = datetime(2026, 6, 1, 12, 0, 0)
    db_session.add_all([
        QueryLog(question="ש1", ai_response="a", judge_verdict="FAIL", timestamp=base),
        QueryLog(question="ש1", ai_response="b", judge_verdict="FAIL", timestamp=base + timedelta(hours=1)),
        QueryLog(question="ש2", ai_response="c", judge_verdict="FAIL", timestamp=base),
    ])
    await db_session.commit()

    with patch.object(jbs, "judge_one",
                      new=AsyncMock(return_value=("PASS", None, True))) as j:
        stats = await jbs.rejudge_distinct(db_session)

    assert j.await_count == 2          # one representative per distinct question (ש1, ש2)
    assert stats["judged"] == 2
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py::test_rejudge_distinct_judges_one_per_question -v`
Expected: FAIL — `rejudge_distinct` not defined.

- [ ] **Step 3: Implement**

Add to `app/services/judge_backfill_service.py` after `rejudge_gold_covered`:

```python
async def rejudge_distinct(session: AsyncSession) -> dict:
    """Re-judge ONE representative (latest) row per distinct question, OVERWRITING
    its verdict. Uses the shared _rejudge_progress so it shares the rejudge guard."""
    from app.services.gold_truth_service import question_hash

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
    )).scalars().all()

    reps = []
    seen: set[str] = set()
    for r in rows:
        h = question_hash(r.question)
        if h in seen:
            continue
        seen.add(h)
        reps.append(r)

    _rejudge_progress.update({"running": True, "total": len(reps), "done": 0, "judged": 0, "errors": 0})
    try:
        for log in reps:
            try:
                verdict, failure, gold_backed = await judge_one(session, log)
                log.judge_verdict = verdict
                log.failure_type = failure
                log.judged_against_gold = gold_backed
                await session.commit()
                _rejudge_progress["judged"] += 1
            except Exception as e:
                await session.rollback()
                _rejudge_progress["errors"] += 1
                logger.warning(f"rejudge_distinct: row {log.id} failed: {e}")
                await asyncio.sleep(2)
                if not session.is_active:
                    logger.error("rejudge_distinct: session no longer active, aborting")
                    break
            finally:
                _rejudge_progress["done"] += 1
    finally:
        _rejudge_progress["running"] = False

    stats = get_rejudge_progress()
    logger.info(f"rejudge_distinct: finished {stats}")
    return stats
```

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/judge_backfill_service.py tests/test_judge_gold_backed.py
git commit -m "feat(quality): rejudge_distinct — re-judge one representative per question"
```

---

### Task 3: Endpoints — `/quality/distinct` + `/eval/rejudge-distinct`

**Files:**
- Modify: `app/routers/eval_loop.py`

- [ ] **Step 1: Add endpoints**

Append after the existing `/quality/data` endpoint (auth dependency `current_user: User = Depends(get_current_user)` matches siblings; `select`, `func`, `QueryLog` already imported):

```python
from app.services import distinct_eval_service


@router.get("/quality/distinct")
async def quality_distinct(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    reps = await distinct_eval_service.distinct_question_eval(session)
    summary = await distinct_eval_service.distinct_summary(session)

    fail_counts: dict[str, int] = {}
    for d in reps:
        if d["verdict"] == "FAIL" and d["failure_type"]:
            fail_counts[d["failure_type"]] = fail_counts.get(d["failure_type"], 0) + 1
    failures = sorted(
        [{"type": t, "count": c} for t, c in fail_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )

    most_asked = sorted(reps, key=lambda d: d["count"], reverse=True)[:15]
    most_asked = [
        {"question": d["question"], "count": d["count"],
         "verdict": d["verdict"], "failure_type": d["failure_type"]}
        for d in most_asked
    ]

    return {"summary": summary, "failures": failures, "most_asked": most_asked}


@router.post("/eval/rejudge-distinct")
async def start_rejudge_distinct(current_user: User = Depends(get_current_user)):
    if judge_backfill_service.get_rejudge_progress()["running"]:
        return {"status": "already_running"}

    global _rejudge_task
    if _rejudge_task is not None and not _rejudge_task.done():
        return {"status": "already_running"}

    async def _run():
        from app.database import async_session_maker
        async with async_session_maker() as s:
            await judge_backfill_service.rejudge_distinct(s)

    _rejudge_task = asyncio.create_task(_run())
    return {"status": "started"}
```

(Status is read via the existing `GET /eval/rejudge/status` — shared `_rejudge_progress`. No new status endpoint.)

- [ ] **Step 2: Verify**

```bash
docker-compose up -d 2>/dev/null; sleep 8
U_ID=$(docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT id FROM users ORDER BY id LIMIT 1;")
curl -s -c /tmp/jar.txt -o /dev/null -X POST http://localhost:8000/login -d "user_id=$U_ID&password=1234"
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality/distinct | python -c "import sys,json; d=json.load(sys.stdin); print('summary:', d['summary']); print('failures:', d['failures'][:3]); print('most_asked[0]:', d['most_asked'][0] if d['most_asked'] else None)"
```
Expected: summary with `distinct_total`, failures list, most_asked top entry. No 500. (Local DB may be empty/stale — a zeros summary is acceptable here; real numbers come from Railway in Task 5.)

Regression: `docker exec shan-ai-api pytest tests/test_distinct_eval.py tests/test_judge_gold_backed.py tests/test_eval_uses_ask_router.py -q`

- [ ] **Step 3: Commit**

```bash
git add app/routers/eval_loop.py
git commit -m "feat(quality): /quality/distinct + /eval/rejudge-distinct endpoints"
```

---

### Task 4: Dashboard — distinct panel + most-asked table + button

**Files:**
- Modify: `app/templates/quality.html`

- [ ] **Step 1: Add markup**

Right under the `page-title` row (above the existing charts row / `gold-cov` line), add a distinct headline + most-asked table:

```html
  <div id="distinct-headline" style="font-size:1.05rem;color:var(--cyan);margin-bottom:.5rem;"></div>
  <div class="d-flex gap-2 align-items-center mb-3 flex-wrap">
    <button class="btn-outline-dim" onclick="startRejudgeDistinct()">♻️ שיפוט מחדש (שאלות ייחודיות)</button>
    <span id="rjd-status" style="color:var(--text-2);font-size:.85rem;"></span>
  </div>
  <div class="card p-3 mb-3">
    <h6>שאלות נפוצות (לפי מספר חזרות)</h6>
    <table class="table table-sm mb-0"><thead><tr><th>שאלה</th><th>חזרות</th><th>פסק דין</th><th>כשל</th></tr></thead>
    <tbody id="most-asked"></tbody></table>
  </div>
```

- [ ] **Step 2: Add JS**

Add a loader for the distinct data (call it from the existing `load()` or as its own `loadDistinct()` invoked at the bottom near `load()`):

```javascript
async function loadDistinct() {
  const res = await fetch("/dashboard/quality/distinct");
  if (!res.ok) return;
  const d = await res.json();
  const s = d.summary;
  document.getElementById("distinct-headline").textContent =
    `שאלות ייחודיות: ${s.distinct_total} | הצלחה: ${s.distinct_pass}/${s.distinct_pass + s.distinct_fail} (${s.pass_rate}%) | מגובי-gold: ${s.gold_backed}`;
  const tb = document.getElementById("most-asked");
  tb.innerHTML = "";
  for (const m of d.most_asked) {
    const tr = document.createElement("tr");
    const t1 = document.createElement("td"); t1.textContent = m.question;
    const t2 = document.createElement("td"); t2.textContent = m.count;
    const t3 = document.createElement("td"); t3.textContent = m.verdict || "—";
    const t4 = document.createElement("td"); t4.textContent = m.failure_type || "—";
    tr.append(t1, t2, t3, t4);
    tb.appendChild(tr);
  }
}
async function startRejudgeDistinct() {
  await fetch("/dashboard/eval/rejudge-distinct", {method:"POST"});
  pollRejudgeDistinct();
}
async function pollRejudgeDistinct() {
  const res = await fetch("/dashboard/eval/rejudge/status");
  if (!res.ok) return;
  const s = await res.json();
  document.getElementById("rjd-status").textContent =
    s.running ? `שיפוט מחדש… ${s.done}/${s.total}` : (s.total ? `הסתיים: ${s.judged} עודכנו` : "");
  if (s.running) setTimeout(pollRejudgeDistinct, 2000); else if (s.total) { loadDistinct(); load(); }
}
loadDistinct();
```

- [ ] **Step 3: Verify**

```bash
docker-compose up -d 2>/dev/null; sleep 8
curl -s -b /tmp/jar.txt -o /dev/null -w "%{http_code}\n" http://localhost:8000/dashboard/quality   # 200
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality | grep -c "distinct-headline\|most-asked\|startRejudgeDistinct"   # >=3
```

- [ ] **Step 4: Commit**

```bash
git add app/templates/quality.html
git commit -m "feat(quality): distinct-question panel + most-asked table on dashboard"
```

---

### Task 5: Ship to Railway + verify on production data

- [ ] **Step 1: Full suite**

Run: `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3`
Expected: green except the 14 known pre-existing failures (test_weekly_report ×9, test_project_report_service ×3, test_project_learning ×1, test_viewer_role ×1).

- [ ] **Step 2: Merge to master + push**

```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: distinct-question eval"
git push origin master
```

- [ ] **Step 3: Stop local bot, deploy Railway**

```bash
docker-compose stop          # avoid Telegram polling conflict; keep postgres for psql if needed: docker-compose start postgres
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Wait for new build live**

Poll until the new endpoint exists (old build 404s it):
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
# repeat until 303 (auth redirect = route exists): unknown route returns 404
curl -s -m 10 -o /dev/null -w "%{http_code}\n" "$URL/dashboard/quality/distinct"
```
Expected eventually: 303 (route exists). 404 = old build still serving.

- [ ] **Step 5: Verify distinct metric on Railway**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/distinct" | python -c "import sys,json; d=json.load(sys.stdin); print('summary:', d['summary']); print('failures:', d['failures']); [print(m['count'], m['verdict'], m['question'][:40]) for m in d['most_asked'][:8]]"
```
Expected: `distinct_total` ≈ 85; failures ranked by distinct question (the "38 HALLUCINATION" collapses to 1); most_asked shows בת-ים (161) and אשלים (76) at the top.

- [ ] **Step 6: Report**

Summarize the distinct-question pass-rate vs the old per-row 55%/71%, the corrected failure ranking (per distinct question), and confirm the most-asked skew is now visible. Note the manual is now accurate and ready to send.
