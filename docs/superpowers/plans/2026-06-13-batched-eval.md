# Batched Spaced Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stable current pass-rate via small spaced eval batches that persist per-question verdicts and aggregate the latest-per-question.

**Architecture:** Add `last_live_*` columns to `EvalGoldAnswer`; `run_cycle(batch=N)` judges the N oldest-checked questions and writes their verdict; `/eval/run?batch=` + a 3-hourly cron drive it; the quality dashboard aggregates the latest verdict per question.

**Tech Stack:** FastAPI, async SQLAlchemy, APScheduler, Chart.js, pytest.

**Spec:** `docs/superpowers/specs/2026-06-13-batched-eval-design.md`

**Conventions:** Railway deploy target; Hebrew `‏`; never `docker-compose down -v`. Grounded facts: `run_cycle` gold select at `per_question_loop_service.py:746` (`ORDER BY EvalGoldAnswer.id`); `QuestionResult` has `.question_hash`, `.status` (PASS-ish: `passed_first_try`/`fixed`; FAIL: `unfixable`/`error`), `.score_final`; `EvalRun` uses `JSON`; eval_cron `start_scheduler` registers jobs ~line 40-54; `EvalGoldAnswer` model ~models.py:480.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/models.py` | Modify | + `EvalGoldAnswer.last_live_verdict/score/at` |
| `CLAUDE.md` | Modify | migration guardrail |
| `app/services/per_question_loop_service.py` | Modify | `run_cycle(batch=...)` selection + per-question persistence |
| `tests/test_batched_eval.py` | Create | batch selection + persistence + verdict mapping |
| `app/routers/eval_loop.py` | Modify | `/eval/run?batch=` + `quality_data` cumulative aggregate |
| `app/services/eval_cron.py` | Modify | 3-hourly `batch_eval` job |
| `app/templates/quality.html` | Modify | cumulative live pass-rate headline |

---

### Task 1: `EvalGoldAnswer` live-verdict columns + migration

**Files:** Modify `app/models.py`, `CLAUDE.md`

- [ ] **Step 1: Add columns**

In `class EvalGoldAnswer` (after `created_at`):
```python
    last_live_verdict = Column(String(10), nullable=True)   # PASS | FAIL
    last_live_score   = Column(Float, nullable=True)
    last_live_at      = Column(DateTime, nullable=True)
```
Confirm `Float` is imported in models.py (grep `from sqlalchemy import`); if not, add it.

- [ ] **Step 2: Migrate both DBs**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;"
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -c "ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;"
```
Expected: ALTER TABLE ×2.

- [ ] **Step 3: CLAUDE.md guardrail** — after the `failed_questions` guardrail line in section 4:
```markdown
- **eval_gold_answers live cols:** After rebuild/Railway deploy:
  `ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;` (local + Railway)
```

- [ ] **Step 4: Verify**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT count(*) FROM information_schema.columns WHERE table_name='eval_gold_answers' AND column_name LIKE 'last_live%';"
```
Expected: `3`. (Run the same on Railway URL too.)

- [ ] **Step 5: Commit**

```bash
git add app/models.py CLAUDE.md
git commit -m "feat(eval): EvalGoldAnswer live-verdict columns"
```

---

### Task 2: `run_cycle(batch=)` selection + per-question persistence

**Files:** Modify `app/services/per_question_loop_service.py`; Test `tests/test_batched_eval.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_batched_eval.py
"""run_cycle batch mode: selects oldest-checked N, writes last_live_* per question."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services import per_question_loop_service as pq
from app.models import EvalGoldAnswer
from app.services.per_question_loop_service import QuestionResult


async def _seed(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.commit()
    base = datetime(2026, 6, 1)
    db_session.add_all([
        EvalGoldAnswer(question_hash="h1", question="q1", gold_answer="g", source="manual", last_live_at=base),           # oldest
        EvalGoldAnswer(question_hash="h2", question="q2", gold_answer="g", source="manual", last_live_at=base + timedelta(days=2)),
        EvalGoldAnswer(question_hash="h3", question="q3", gold_answer="g", source="manual", last_live_at=None),            # never → first
    ])
    await db_session.commit()


@pytest.mark.asyncio
async def test_batch_selects_nulls_then_oldest(db_session):
    await _seed(db_session)
    seen = []

    async def fake_one(session, g, *a, **k):
        seen.append(g.question)
        return QuestionResult(question=g.question, question_hash=g.question_hash,
                              gold_answer=g.gold_answer, status="passed_first_try",
                              score_initial=1.0, score_final=1.0)

    with patch.object(pq, "run_one_question", new=fake_one):
        await pq.run_cycle(db_session, user_id=None, repair=False, batch=2)

    assert set(seen) == {"q3", "q1"}          # NULL first, then oldest timestamp


@pytest.mark.asyncio
async def test_batch_persists_verdict(db_session):
    await _seed(db_session)

    async def fake_one(session, g, *a, **k):
        bad = g.question == "q1"
        return QuestionResult(question=g.question, question_hash=g.question_hash,
                              gold_answer=g.gold_answer,
                              status=("unfixable" if bad else "passed_first_try"),
                              score_initial=(0.0 if bad else 1.0),
                              score_final=(0.0 if bad else 1.0))

    with patch.object(pq, "run_one_question", new=fake_one):
        await pq.run_cycle(db_session, user_id=None, repair=False, batch=2)

    from sqlalchemy import select
    rows = {r.question_hash: r for r in (await db_session.execute(select(EvalGoldAnswer))).scalars()}
    assert rows["h3"].last_live_verdict == "PASS"     # q3 judged
    assert rows["h1"].last_live_verdict == "FAIL"     # q1 judged (unfixable)
    assert rows["h1"].last_live_at is not None
    assert rows["h2"].last_live_verdict is None       # q2 not in batch → untouched (was None)
```

Run `docker exec shan-ai-api pytest tests/test_batched_eval.py -v` → FAIL (batch param/persistence absent).

- [ ] **Step 2: Implement batch selection**

In `run_cycle` signature add `batch: int = 0`. Replace the gold-rows select (line 746):
```python
    if batch and batch > 0:
        gold_rows = (await session.execute(
            select(EvalGoldAnswer).order_by(
                EvalGoldAnswer.last_live_at.asc().nulls_first(), EvalGoldAnswer.id
            ).limit(batch)
        )).scalars().all()
    else:
        gold_rows = (await session.execute(
            select(EvalGoldAnswer).order_by(EvalGoldAnswer.id)
        )).scalars().all()
    gold_rows = list(gold_rows)
```
(`nulls_first` is a SQLAlchemy column-expression method; if the installed version lacks `.nulls_first()`, use `from sqlalchemy import nulls_first` and `nulls_first(EvalGoldAnswer.last_live_at.asc())` — check and use whichever imports cleanly.)

- [ ] **Step 3: Implement persistence**

In the `for g in gold_rows:` loop, after `results.append(r)` and counts bookkeeping (and before the `EVAL_PACE_SECONDS` sleep), persist onto the gold row `g` (it IS the ORM row being iterated):
```python
            g.last_live_verdict = "PASS" if r.status in ("passed_first_try", "fixed") else "FAIL"
            g.last_live_score = r.score_final
            g.last_live_at = datetime.utcnow()
            await session.commit()
```
(`datetime` already imported in the file.)

- [ ] **Step 4: Run tests → 2 pass.**
- [ ] **Step 5: Regression**

```bash
docker exec shan-ai-api python -c "import app.services.per_question_loop_service; print('OK')"
docker exec shan-ai-api pytest tests/test_eval_weekly_summary.py tests/test_failed_questions_capture.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_batched_eval.py
git commit -m "feat(eval): run_cycle batch mode + per-question live verdict persistence"
```

---

### Task 3: `/eval/run?batch=` + dashboard cumulative aggregate

**Files:** Modify `app/routers/eval_loop.py`

- [ ] **Step 1: Add batch param to eval_run**

In `eval_run` (it already has `repair: bool = True` from phase G), add `batch: int = 0` and pass through:
```python
async def eval_run(repair: bool = True, batch: int = 0,
                   session: AsyncSession = Depends(get_db_session),
                   current_user: User = Depends(get_current_user)):
    ...
    async def _runner():
        async with async_session_maker() as own:
            try:
                await run_cycle(own, user_id=user_id, emit=_emit_log, repair=repair, batch=batch)
            except Exception as e:
                logger.exception("eval cycle failed")
                _emit_log({"type": "cycle_error", "error": str(e)})
    ...
```
(Keep the rest of the endpoint as-is.)

- [ ] **Step 2: Add cumulative aggregate to quality_data**

Before `quality_data`'s final return, add:
```python
    from app.models import EvalGoldAnswer
    from datetime import timedelta
    gold = (await session.execute(select(EvalGoldAnswer))).scalars().all()
    checked = [g for g in gold if g.last_live_verdict]
    cum_pass = sum(1 for g in checked if g.last_live_verdict == "PASS")
    cum_fail = sum(1 for g in checked if g.last_live_verdict == "FAIL")
    cutoff = datetime.utcnow() - timedelta(hours=48)
    stale = sum(1 for g in checked if g.last_live_at and g.last_live_at < cutoff)
    live_cumulative = {
        "checked": len(checked), "total": len(gold),
        "pass": cum_pass, "fail": cum_fail,
        "pass_rate": round(cum_pass / len(checked) * 100) if checked else 0,
        "stale": stale,
    }
```
Add `"live_cumulative": live_cumulative,` to the returned dict. (`datetime` and `select` already imported in eval_loop.py; confirm.)

- [ ] **Step 3: Verify**

```bash
docker-compose up -d 2>/dev/null; sleep 8
U_ID=$(docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT id FROM users ORDER BY id LIMIT 1;")
curl -s -c /tmp/jar.txt -o /dev/null -X POST http://localhost:8000/login -d "user_id=$U_ID&password=1234"
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality/data | python -c "import sys,json; print(json.load(sys.stdin)['live_cumulative'])"
```
Expected: a dict with checked/total/pass_rate (likely zeros locally — fine). Do NOT POST /eval/run locally.
Regression: `docker exec shan-ai-api pytest tests/test_batched_eval.py tests/test_eval_uses_ask_router.py -q`.

- [ ] **Step 4: Commit**

```bash
git add app/routers/eval_loop.py
git commit -m "feat(eval): /eval/run batch param + cumulative live pass-rate in quality data"
```

---

### Task 4: 3-hourly batch cron

**Files:** Modify `app/services/eval_cron.py`

- [ ] **Step 1: Add the job + runner**

In `start_scheduler`, after the existing `add_job` calls:
```python
    sch.add_job(_batch_eval_run, "interval", hours=3, id="batch_eval", replace_existing=True)
```
Add the runner function (module level), mirroring `_nightly_run`:
```python
async def _batch_eval_run() -> None:
    """Judge-only batch of gold questions (spaced to avoid Groq rate-limit bursts)."""
    from app.database import async_session_maker
    from app.services.per_question_loop_service import run_cycle
    async with async_session_maker() as s:
        try:
            await run_cycle(s, user_id=None, repair=False, batch=8)
        except Exception as e:
            logger.exception(f"batch_eval run failed: {e}")
```
Extend the startup `logger.info` lines to mention `batch_eval (every 3h)`.

- [ ] **Step 2: Verify registration**

```bash
docker-compose restart fastapi && sleep 8
docker logs shan-ai-api --tail 40 2>&1 | grep -iE "batch_eval|scheduler"
docker exec shan-ai-api python -c "import app.services.eval_cron; print('OK')"
```
Expected: `Added job "_batch_eval_run"` / the batch_eval log line.

- [ ] **Step 3: Commit**

```bash
git add app/services/eval_cron.py
git commit -m "feat(eval): 3-hourly judge-only batch eval cron"
```

---

### Task 5: Dashboard cumulative headline

**Files:** Modify `app/templates/quality.html`

- [ ] **Step 1: Add headline element** (near the existing distinct-headline, above charts):
```html
  <div id="live-cum" style="font-size:1.05rem;color:var(--green);margin-bottom:.5rem;"></div>
```

- [ ] **Step 2: Render in load()** — inside the existing `load()` after `const d = await res.json();`:
```javascript
  if (d.live_cumulative) {
    const c = d.live_cumulative;
    document.getElementById("live-cum").textContent =
      `מדידה חיה מצטברת: ${c.pass}/${c.checked} (${c.pass_rate}%) · כיסוי ${c.checked}/${c.total}` +
      (c.stale ? ` · ${c.stale} ישנים` : "");
  }
```

- [ ] **Step 3: Verify**

```bash
docker-compose up -d 2>/dev/null; sleep 8
curl -s -b /tmp/jar.txt -o /dev/null -w "%{http_code}\n" http://localhost:8000/dashboard/quality   # 200
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality | grep -c "live-cum"   # >=1
```

- [ ] **Step 4: Commit**

```bash
git add app/templates/quality.html
git commit -m "feat(eval): cumulative live pass-rate headline on dashboard"
```

---

### Task 6: Ship to Railway + prove stability

- [ ] **Step 1: Full suite** — `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3` → green except the known pre-existing failures (~14-15 in test_weekly_report/test_project_report_service/test_project_learning/test_viewer_role; confirm no NEW file fails).

- [ ] **Step 2: Merge + push**
```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: batched spaced eval (stable pass-rate)"
git push origin master
```

- [ ] **Step 3: Migrate Railway (already done in Task 1 Step 2) + deploy**
```bash
docker-compose stop fastapi
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Wait for build (~2-3 min), confirm up**
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 10 -o /dev/null -w "%{http_code}\n" "$URL/dashboard/quality/distinct"   # 303
```

- [ ] **Step 5: Run a few spaced batches manually to seed coverage**
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
# batch 1:
curl -s -m 20 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/run?repair=false&batch=8"
```
Poll `/dashboard/eval/runs` (Monitor) until that run completes (small, fast). Then read cumulative + repeat batch a few times spaced a few minutes apart:
```bash
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/data" | python -c "import sys,json;print(json.load(sys.stdin)['live_cumulative'])"
```
Each batch: confirm 8 rows gained a verdict, no mass-0.0 (the small batch shouldn't hit quota), and known-good questions (חולה/עתלית) score PASS.

- [ ] **Step 6: Report**
Report the cumulative live pass-rate as it fills in (checked/total climbing), that batches don't mass-fail, and that the number is stable across batches (no 2→27→15 swing). Note the cron will keep it fresh every 3h. Local stays stopped.
```
