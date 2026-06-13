# Gold-Backed Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the eval judge trustworthy by comparing production answers against human-approved gold (not an LLM guess), let managers build gold from web or Telegram, then re-judge so failure numbers reflect reality.

**Architecture:** Reuse the existing eval stack. `judge_one` gains a real-gold lookup via `gts.get_gold` (exact normalized-hash, already implemented) before falling back to `propose_gold`. A new `judged_against_gold` column records which verdicts are trustworthy. A seed endpoint auto-creates gold for DB-derivable questions; a re-judge endpoint overwrites verdicts for gold-covered rows. A Telegram `/gold` command lets managers curate the LLM-needed remainder using the bot's existing inline-keyboard + awaiting-text state machine.

**Tech Stack:** FastAPI, async SQLAlchemy, Groq via `llm_chat`, python-telegram-bot v21, Chart.js, pytest (+pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-06-13-gold-backed-judge-design.md`

**Conventions (every task):**
- Bot/user-facing Hebrew strings prefixed `‏` (U+200F).
- Tests in Docker: `docker exec shan-ai-api pytest <path> -v`. After code changes: `docker-compose restart fastapi`.
- `failure_type` ∈ WRONG_PROJECT/MISSING_DATA/HALLUCINATION/UNSTABLE/STRUCTURE/REFUSED; `judge_verdict` ∈ PASS/PARTIAL/FAIL.
- Never `docker-compose down -v`.
- Manager roles = `{RoleEnum.DEPARTMENT_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER, RoleEnum.DIVISION_MANAGER}` (the exact set already used at `telegram_polling.py:34`).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/models.py` | Modify | + `QueryLog.judged_against_gold` column |
| `CLAUDE.md` | Modify | + migration guardrail line |
| `app/services/judge_backfill_service.py` | Modify | `judge_one` returns gold-backed flag; `run_backfill` persists it; new `rejudge_gold_covered` |
| `tests/test_judge_gold_backed.py` | Create | judge_one gold-hit/miss + rejudge tests |
| `app/services/gold_seed_service.py` | Create | auto-seed gold from production candidates |
| `tests/test_gold_seed.py` | Create | seed selection tests |
| `app/routers/eval_loop.py` | Modify | + seed-from-production, rejudge endpoints; quality_data gold-coverage |
| `app/templates/quality.html` | Modify | seed + rejudge buttons; gold-coverage line |
| `app/services/gold_telegram_service.py` | Create | candidate queue + keyboard for `/gold` |
| `tests/test_gold_telegram.py` | Create | keyboard + role-gate + queue tests |
| `app/services/telegram_state.py` | Modify | + `_awaiting_gold_text` dict |
| `app/services/telegram_polling.py` | Modify | `/gold` command, `gold:` callback, awaiting-text branch, handler registration |

---

### Task 1: Add `judged_against_gold` column + migration

**Files:**
- Modify: `app/models.py` (QueryLog, after `judge_verdict` ~line 240)
- Modify: `CLAUDE.md` (guardrails section)

- [ ] **Step 1: Add the column**

In `app/models.py`, in `class QueryLog`, immediately after the `judge_verdict` column line, add:

```python
    judged_against_gold = Column(Boolean, nullable=True)  # True=compared to real gold, False=LLM-guessed reference, None=not judged
```

(`Boolean` is already imported in models.py — it is used by other models.)

- [ ] **Step 2: Apply migration to the live DB**

Run:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;"
```
Expected: `ALTER TABLE`.

- [ ] **Step 3: Document the guardrail**

In `CLAUDE.md` section 4 (Critical Operational Guardrails), after the `roleenum VIEWER` line, add:

```markdown
- **judged_against_gold:** After rebuild/fresh DB or Railway deploy, run on both:
  `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;"`
```

- [ ] **Step 4: Verify column exists**

Run:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT column_name FROM information_schema.columns WHERE table_name='query_logs' AND column_name='judged_against_gold';"
```
Expected: `judged_against_gold`

- [ ] **Step 5: Commit**

```bash
git add app/models.py CLAUDE.md
git commit -m "feat(quality): add judged_against_gold column to query_logs"
```

---

### Task 2: `judge_one` prefers real gold + returns gold-backed flag

**Files:**
- Modify: `app/services/judge_backfill_service.py`
- Test: `tests/test_judge_gold_backed.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_judge_gold_backed.py
"""judge_one prefers real gold and reports whether it was gold-backed."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog
from app.services import judge_backfill_service as jbs


@pytest.mark.asyncio
async def test_judge_one_uses_gold_when_present():
    log = QueryLog(question="מי המנהל של חולה?", ai_response="יעקבי, ניר")

    class _Gold:
        gold_answer = "המנהל: יעקבי, ניר"

    with patch.object(jbs, "get_gold", new=AsyncMock(return_value=_Gold())) as g, \
         patch.object(jbs, "propose_gold", new=AsyncMock()) as p, \
         patch.object(jbs, "compare_to_gold", new=AsyncMock(return_value=1.0)):
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)

    assert verdict == "PASS"
    assert failure is None
    assert gold_backed is True
    g.assert_awaited_once()
    p.assert_not_awaited()          # gold present → never guesses


@pytest.mark.asyncio
async def test_judge_one_falls_back_to_propose_when_no_gold():
    log = QueryLog(question="שאלה נדירה", ai_response="תשובה")

    with patch.object(jbs, "get_gold", new=AsyncMock(return_value=None)), \
         patch.object(jbs, "propose_gold", new=AsyncMock(return_value={"answer": "ref"})), \
         patch.object(jbs, "compare_to_gold", new=AsyncMock(return_value=0.0)), \
         patch.object(jbs, "_classify_failure", new=AsyncMock(return_value="MISSING_DATA")):
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)

    assert verdict == "FAIL"
    assert failure == "MISSING_DATA"
    assert gold_backed is False


@pytest.mark.asyncio
async def test_judge_one_empty_answer_is_refused_and_not_gold_backed():
    log = QueryLog(question="ש", ai_response="")
    with patch.object(jbs, "get_gold", new=AsyncMock()) as g:
        verdict, failure, gold_backed = await jbs.judge_one(session=AsyncMock(), log=log)
    assert (verdict, failure) == ("FAIL", "REFUSED")
    assert gold_backed is False
    g.assert_not_awaited()          # short-circuits before any lookup
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py -v`
Expected: FAIL — `judge_one` currently returns a 2-tuple and has no `get_gold` symbol imported.

- [ ] **Step 3: Implement**

In `app/services/judge_backfill_service.py`, update the import line that pulls from gold_truth_service to include `get_gold`:

```python
from app.services.gold_truth_service import propose_gold, compare_to_gold, get_gold
```

Replace `judge_one` (currently ending at the `return verdict, failure` around line 107) with:

```python
async def judge_one(session: AsyncSession, log: QueryLog) -> tuple[str, str | None, bool]:
    """Judge a single QueryLog row.

    Returns (verdict, failure_type, gold_backed). gold_backed is True when the
    comparison used a real human-approved gold answer (trustworthy), False when
    it fell back to an LLM-guessed reference.
    """
    answer = (log.ai_response or "").strip()
    if not answer:
        return "FAIL", "REFUSED", False

    gold = await get_gold(session, log.question)
    if gold is not None:
        reference = gold.gold_answer
        gold_backed = True
    else:
        ref = await propose_gold(session, log.question)
        reference = ref["answer"]
        gold_backed = False

    if _is_no_info(reference) and _is_no_info(answer):
        return "PASS", None, gold_backed

    score = await compare_to_gold(log.question, answer, reference)
    verdict = score_to_verdict(score)
    if verdict == "PASS":
        return verdict, None, gold_backed

    failure = await _classify_failure(log.question, answer, reference)
    return verdict, failure, gold_backed
```

- [ ] **Step 4: Update `run_backfill` to persist the flag**

In `run_backfill`, change the per-row body from:

```python
                verdict, failure = await judge_one(session, log)
                log.judge_verdict = verdict
                if failure:
                    log.failure_type = failure
```
to:
```python
                verdict, failure, gold_backed = await judge_one(session, log)
                log.judge_verdict = verdict
                log.failure_type = failure          # may be None (PASS) — clearing is correct
                log.judged_against_gold = gold_backed
```

- [ ] **Step 5: Run tests + the existing backfill idempotency test**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py tests/test_judge_backfill.py -v`
Expected: new 3 PASS. NOTE: `tests/test_judge_backfill.py::test_backfill_skips_already_judged` mocks `judge_one` with `return_value=("FAIL","MISSING_DATA")` — a 2-tuple — which will now break unpacking. Update that mock in `tests/test_judge_backfill.py` to `return_value=("FAIL", "MISSING_DATA", False)` and add `await db_session.refresh(unjudged); assert unjudged.judged_against_gold is False` after the existing assertions.

- [ ] **Step 6: Re-run both files**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py tests/test_judge_backfill.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/judge_backfill_service.py tests/test_judge_gold_backed.py tests/test_judge_backfill.py
git commit -m "feat(quality): judge prefers real gold, records gold-backed flag"
```

---

### Task 3: `rejudge_gold_covered` — overwrite verdicts for gold-covered rows

**Files:**
- Modify: `app/services/judge_backfill_service.py`
- Test: `tests/test_judge_gold_backed.py` (append)

- [ ] **Step 1: Write failing test**

```python
# append to tests/test_judge_gold_backed.py
from app.services.gold_truth_service import save_gold


@pytest.mark.asyncio
async def test_rejudge_only_touches_gold_covered(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(QueryLog))
    await db_session.commit()

    covered = QueryLog(question="מי המנהל של חולה?", ai_response="יעקבי, ניר", judge_verdict="FAIL")
    uncovered = QueryLog(question="שאלה ללא זהב", ai_response="משהו", judge_verdict="FAIL")
    db_session.add_all([covered, uncovered])
    await db_session.commit()

    await save_gold(db_session, question="מי המנהל של חולה?", gold_answer="יעקבי, ניר",
                    user_id=None, source="db_lookup")

    with patch.object(jbs, "judge_one",
                      new=AsyncMock(return_value=("PASS", None, True))) as j:
        stats = await jbs.rejudge_gold_covered(db_session, limit=100)

    assert j.await_count == 1                       # only the covered row
    await db_session.refresh(covered)
    await db_session.refresh(uncovered)
    assert covered.judge_verdict == "PASS"          # overwritten
    assert covered.judged_against_gold is True
    assert uncovered.judge_verdict == "FAIL"        # untouched
    assert stats["judged"] == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py::test_rejudge_only_touches_gold_covered -v`
Expected: FAIL — `rejudge_gold_covered` not defined.

- [ ] **Step 3: Implement**

Add a second progress dict and the function to `judge_backfill_service.py`. Near the top, beside `_progress`:

```python
_rejudge_progress = {"running": False, "total": 0, "done": 0, "judged": 0, "errors": 0}


def get_rejudge_progress() -> dict:
    return dict(_rejudge_progress)
```

Add the function after `run_backfill`:

```python
async def rejudge_gold_covered(session: AsyncSession, limit: int = 500) -> dict:
    """Re-judge query_logs rows whose question now has a gold answer, OVERWRITING
    the existing verdict. Unlike run_backfill this ignores judge_verdict (gold may
    have arrived after the first judgement)."""
    from app.models import EvalGoldAnswer
    from app.services.gold_truth_service import question_hash

    gold_hashes = set((await session.execute(
        select(EvalGoldAnswer.question_hash)
    )).scalars().all())

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc()).limit(limit)
    )).scalars().all()
    covered = [r for r in rows if question_hash(r.question) in gold_hashes]

    _rejudge_progress.update({"running": True, "total": len(covered), "done": 0, "judged": 0, "errors": 0})
    try:
        for log in covered:
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
                logger.warning(f"rejudge: row {log.id} failed: {e}")
                await asyncio.sleep(2)
                if not session.is_active:
                    logger.error("rejudge: session no longer active, aborting")
                    break
            finally:
                _rejudge_progress["done"] += 1
    finally:
        _rejudge_progress["running"] = False

    stats = get_rejudge_progress()
    logger.info(f"rejudge: finished {stats}")
    return stats
```

- [ ] **Step 4: Run test**

Run: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/judge_backfill_service.py tests/test_judge_gold_backed.py
git commit -m "feat(quality): rejudge_gold_covered overwrites verdicts for gold rows"
```

---

### Task 4: `gold_seed_service` — auto-seed gold from production

**Files:**
- Create: `app/services/gold_seed_service.py`
- Test: `tests/test_gold_seed.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_gold_seed.py
"""seed_from_production saves DB-derivable gold, leaves LLM-needed questions for humans."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog
from app.services import gold_seed_service as gss


@pytest.mark.asyncio
async def test_seed_saves_db_lookup_skips_llm_needed(db_session):
    from sqlalchemy import delete
    from app.models import EvalGoldAnswer
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()
    db_session.add_all([
        QueryLog(question="מי המנהל של חולה?", ai_response="x"),
        QueryLog(question="שאלה עמומה", ai_response="y"),
    ])
    await db_session.commit()

    async def fake_propose(session, q, *, use_llm=True):
        if "חולה" in q:
            return {"answer": "המנהל: יעקבי, ניר", "source": "db_lookup",
                    "target_project": "WBE-252", "target_field": "manager"}
        return {"answer": "", "source": "manual", "target_project": None, "target_field": None}

    with patch.object(gss, "propose_gold", new=fake_propose):
        result = await gss.seed_from_production(db_session, user_id=None)

    assert result["seeded"] == 1
    assert result["needs_manual"] == 1
    from app.services.gold_truth_service import get_gold
    g = await get_gold(db_session, "מי המנהל של חולה?")
    assert g is not None and g.source == "db_lookup"
    assert await get_gold(db_session, "שאלה עמומה") is None
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_gold_seed.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/gold_seed_service.py
"""Auto-seed gold answers from production query_logs.

For each distinct frequent question without gold, ask propose_gold for a
DB-only (deterministic) answer. If one exists, save it as gold automatically
(source="db_lookup"). Questions needing an LLM answer are left for human
curation (web curate UI or Telegram /gold) and counted as needs_manual.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import propose_gold, save_gold, list_gold, question_hash

logger = logging.getLogger(__name__)


async def seed_from_production(session: AsyncSession, user_id: int | None, scan: int = 1000) -> dict:
    """Returns {seeded, needs_manual, total_candidates}."""
    gold_hashes = {g.question_hash for g in await list_gold(session)}

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc()).limit(scan)
    )).scalars().all()

    # dedup by normalized key, keep first (newest) occurrence
    seen: set[str] = set()
    questions: list[str] = []
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if question_hash(r.question) in gold_hashes:
            continue
        questions.append(r.question)

    seeded = 0
    needs_manual = 0
    for q in questions:
        try:
            proposal = await propose_gold(session, q, use_llm=False)
        except Exception as e:
            logger.warning(f"seed: propose_gold failed for {q!r}: {e}")
            needs_manual += 1
            continue
        if proposal.get("source") == "db_lookup" and (proposal.get("answer") or "").strip():
            await save_gold(
                session, question=q, gold_answer=proposal["answer"], user_id=user_id,
                target_project=proposal.get("target_project"),
                target_field=proposal.get("target_field"), source="db_lookup",
            )
            seeded += 1
        else:
            needs_manual += 1

    return {"seeded": seeded, "needs_manual": needs_manual, "total_candidates": len(questions)}
```

- [ ] **Step 4: Run test**

Run: `docker exec shan-ai-api pytest tests/test_gold_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gold_seed_service.py tests/test_gold_seed.py
git commit -m "feat(quality): auto-seed gold from DB-derivable production questions"
```

---

### Task 5: Endpoints — seed, rejudge, gold-coverage data

**Files:**
- Modify: `app/routers/eval_loop.py`

- [ ] **Step 1: Add the endpoints**

Append after the existing backfill endpoints (copy their auth dependency exactly — `current_user: User = Depends(get_current_user)`):

```python
from app.services import gold_seed_service


@router.post("/eval/gold/seed-from-production")
async def seed_gold(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    return await gold_seed_service.seed_from_production(session, user_id=current_user.id)


@router.post("/eval/rejudge")
async def start_rejudge(current_user: User = Depends(get_current_user)):
    if judge_backfill_service.get_rejudge_progress()["running"]:
        return {"status": "already_running"}

    global _rejudge_task
    if _rejudge_task is not None and not _rejudge_task.done():
        return {"status": "already_running"}

    async def _run():
        from app.database import async_session_maker
        async with async_session_maker() as s:
            await judge_backfill_service.rejudge_gold_covered(s)

    _rejudge_task = asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/eval/rejudge/status")
async def rejudge_status(current_user: User = Depends(get_current_user)):
    return judge_backfill_service.get_rejudge_progress()
```

Add the module global near the existing `_backfill_task` declaration:
```python
_rejudge_task: asyncio.Task | None = None
```

- [ ] **Step 2: Extend `quality_data` with gold coverage**

In the `quality_data` endpoint, before the final `return {...}`, add:

```python
    from app.models import EvalGoldAnswer
    gold_total = (await session.execute(select(func.count()).select_from(EvalGoldAnswer))).scalar() or 0
    gold_backed = (await session.execute(
        select(func.count()).select_from(QueryLog).where(QueryLog.judged_against_gold.is_(True))
    )).scalar() or 0
    guessed = (await session.execute(
        select(func.count()).select_from(QueryLog).where(QueryLog.judged_against_gold.is_(False))
    )).scalar() or 0
```

and add to the returned dict:
```python
        "gold_coverage": {"gold_backed": gold_backed, "guessed": guessed, "gold_total": gold_total},
```

- [ ] **Step 3: Verify**

```bash
docker-compose restart fastapi && sleep 8
U_ID=$(docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT id FROM users ORDER BY id LIMIT 1;")
curl -s -c /tmp/jar.txt -o /dev/null -X POST http://localhost:8000/login -d "user_id=$U_ID&password=1234"
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/eval/rejudge/status      # {"running":false,...}
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality/data | python -c "import sys,json; print(json.load(sys.stdin)['gold_coverage'])"
```
Expected: rejudge status JSON; `gold_coverage` dict prints. Do NOT POST seed/rejudge yet.

Regression: `docker exec shan-ai-api pytest tests/test_judge_gold_backed.py tests/test_gold_seed.py tests/test_eval_uses_ask_router.py -q`

- [ ] **Step 4: Commit**

```bash
git add app/routers/eval_loop.py
git commit -m "feat(quality): seed/rejudge endpoints + gold-coverage in quality data"
```

---

### Task 6: Quality dashboard — seed/rejudge buttons + coverage line

**Files:**
- Modify: `app/templates/quality.html`

- [ ] **Step 1: Add controls + coverage display**

In the header `<div class="d-flex gap-2 align-items-center">` (next to the existing backfill button), add two buttons:

```html
      <button class="btn-outline-dim" onclick="seedGold()">🌱 זריעת gold מ-DB</button>
      <button class="btn-outline-dim" onclick="startRejudge()">♻️ שיפוט מחדש (gold)</button>
```

Under the `page-title` row, add a coverage line element:

```html
  <div id="gold-cov" style="color:var(--text-2);font-size:.85rem;margin-bottom:1rem;"></div>
```

- [ ] **Step 2: Add JS**

Inside the `<script>`, in the existing `load()` after `const d = await res.json();`, add:

```javascript
  if (d.gold_coverage) {
    const c = d.gold_coverage;
    document.getElementById("gold-cov").textContent =
      `מגובי-gold: ${c.gold_backed} | מנוחשים: ${c.guessed} | תשובות זהב בסט: ${c.gold_total}`;
  }
```

Add these functions:

```javascript
async function seedGold() {
  const btn = event.target; btn.disabled = true; btn.textContent = "🌱 זורע…";
  const r = await fetch("/dashboard/eval/gold/seed-from-production", {method:"POST"});
  const d = await r.json();
  alert(`נזרעו ${d.seeded} תשובות זהב מ-DB. ${d.needs_manual} דורשות אישור ידני (curate / טלגרם).`);
  btn.disabled = false; btn.textContent = "🌱 זריעת gold מ-DB";
  load();
}
async function startRejudge() {
  await fetch("/dashboard/eval/rejudge", {method:"POST"});
  pollRejudge();
}
async function pollRejudge() {
  const res = await fetch("/dashboard/eval/rejudge/status");
  if (!res.ok) return;
  const s = await res.json();
  document.getElementById("bf-status").textContent =
    s.running ? `שיפוט מחדש… ${s.done}/${s.total}` : (s.total ? `הסתיים: ${s.judged} עודכנו` : "");
  if (s.running) setTimeout(pollRejudge, 2000); else if (s.total) load();
}
```

- [ ] **Step 3: Verify**

```bash
docker-compose restart fastapi && sleep 8
curl -s -b /tmp/jar.txt -o /dev/null -w "%{http_code}\n" http://localhost:8000/dashboard/quality   # 200
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality | grep -c "gold-cov"                # 1
```
Browser: page renders, coverage line shows, both buttons present.

- [ ] **Step 4: Commit**

```bash
git add app/templates/quality.html
git commit -m "feat(quality): seed/rejudge controls + gold-coverage line on dashboard"
```

---

### Task 7: `gold_telegram_service` — candidate queue + keyboard

**Files:**
- Create: `app/services/gold_telegram_service.py`
- Test: `tests/test_gold_telegram.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gold_telegram.py
"""Telegram /gold: role gate, candidate queue, keyboard structure."""
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog, RoleEnum
from app.services import gold_telegram_service as gts_tg


def test_is_manager():
    class U:  # noqa
        def __init__(self, role): self.role = role
    assert gts_tg.is_manager(U(RoleEnum.DEPARTMENT_MANAGER)) is True
    assert gts_tg.is_manager(U(RoleEnum.DIVISION_MANAGER)) is True
    assert gts_tg.is_manager(U(RoleEnum.PROJECT_MANAGER)) is False
    assert gts_tg.is_manager(U(RoleEnum.VIEWER)) is False
    assert gts_tg.is_manager(None) is False


def test_cause_keyboard_callbacks():
    kb = gts_tg.gold_keyboard(candidate_id=7)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "gold:approve:7" in datas
    assert "gold:edit:7" in datas
    assert "gold:skip:7" in datas
    assert "gold:stop:7" in datas


@pytest.mark.asyncio
async def test_next_candidate_skips_questions_with_gold(db_session):
    from sqlalchemy import delete
    from app.models import EvalGoldAnswer
    from app.services.gold_truth_service import save_gold
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.execute(delete(QueryLog))
    await db_session.commit()
    db_session.add_all([
        QueryLog(question="שאלה עם זהב", ai_response="a"),
        QueryLog(question="שאלה בלי זהב", ai_response="b"),
    ])
    await db_session.commit()
    await save_gold(db_session, question="שאלה עם זהב", gold_answer="g", user_id=None, source="manual")

    cand = await gts_tg.next_candidate(db_session, exclude_questions=set())
    assert cand is not None
    assert cand["question"] == "שאלה בלי זהב"
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_gold_telegram.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/services/gold_telegram_service.py
"""Telegram /gold manager curation: pick the next ungolded production question
and build the approve/edit/skip/stop keyboard.

Candidate "id" is the QueryLog row id of a representative occurrence — used only
to carry the question through the callback; gold is keyed by question_hash."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog, RoleEnum, EvalGoldAnswer
from app.services.gold_truth_service import question_hash

_MANAGER_ROLES = {RoleEnum.DEPARTMENT_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER, RoleEnum.DIVISION_MANAGER}


def is_manager(user) -> bool:
    return bool(user and getattr(user, "role", None) in _MANAGER_ROLES)


def gold_keyboard(candidate_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ אשר", callback_data=f"gold:approve:{candidate_id}"),
         InlineKeyboardButton("✏️ תקן", callback_data=f"gold:edit:{candidate_id}")],
        [InlineKeyboardButton("⏭ דלג", callback_data=f"gold:skip:{candidate_id}"),
         InlineKeyboardButton("⏹ סיום", callback_data=f"gold:stop:{candidate_id}")],
    ])


async def next_candidate(session: AsyncSession, exclude_questions: set[str]) -> dict | None:
    """Return {id, question} for the next frequent production question that has
    no gold and is not in exclude_questions (normalized keys already shown this
    session). None when the queue is empty."""
    gold_hashes = set((await session.execute(select(EvalGoldAnswer.question_hash))).scalars().all())

    rows = (await session.execute(
        select(QueryLog).where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc()).limit(1000)
    )).scalars().all()

    seen: set[str] = set()
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if key in exclude_questions:
            continue
        if question_hash(r.question) in gold_hashes:
            continue
        return {"id": r.id, "question": r.question}
    return None
```

- [ ] **Step 4: Run tests**

Run: `docker exec shan-ai-api pytest tests/test_gold_telegram.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gold_telegram_service.py tests/test_gold_telegram.py
git commit -m "feat(quality): gold_telegram_service — manager curation queue + keyboard"
```

---

### Task 8: Telegram `/gold` command, callback, awaiting-text wiring

**Files:**
- Modify: `app/services/telegram_state.py`
- Modify: `app/services/telegram_polling.py`

- [ ] **Step 1: Add awaiting-text state**

In `app/services/telegram_state.py`, after the existing awaiting dicts, add:

```python
# { telegram_id (int): {"question": str, "shown": set[str]} } — manager mid /gold edit
_awaiting_gold_text: dict[int, dict] = {}
```

- [ ] **Step 2: Register the command handler**

In `telegram_polling.py` `__init__`/setup, beside the other `CommandHandler` registrations (~line 147), add:

```python
        self.application.add_handler(CommandHandler("gold", self.handle_gold))
```

- [ ] **Step 3: Implement `handle_gold`**

Add the method (mirror the user-lookup pattern other handlers use — `session.scalar(select(User).where(User.telegram_id == telegram_id))`):

```python
    async def handle_gold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/gold — manager-only gold curation of ungolded production questions."""
        from app.services import gold_telegram_service as gtg
        from app.services.gold_truth_service import propose_gold
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not gtg.is_manager(user):
                await update.message.reply_text("‏🔒 פקודה זו זמינה למנהלים בלבד.")
                return
            cand = await gtg.next_candidate(session, exclude_questions=set())
            if not cand:
                await update.message.reply_text("‏✅ אין שאלות הממתינות לתשובת זהב.")
                return
            proposal = await propose_gold(session, cand["question"], use_llm=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(f"‏🥇 <b>בניית תשובת זהב</b>\n\n"
                  f"‏<b>שאלה:</b> {cand['question']}\n\n"
                  f"‏<b>הצעה:</b> {proposal['answer']}"),
            parse_mode="HTML",
            reply_markup=gtg.gold_keyboard(cand["id"]),
        )
```

- [ ] **Step 4: Implement the `gold:` callback branch**

In `handle_callback`, place this branch alongside the `lfc:` branch (before the generic `parts = data.split(":")` parse), guarded the same way:

```python
        # --- Manager gold curation (/gold) ---
        if query.data.startswith("gold:"):
            from app.services import gold_telegram_service as gtg
            from app.services.gold_truth_service import save_gold, propose_gold
            from app.services.telegram_state import _awaiting_gold_text
            parts = query.data.split(":", 2)
            if len(parts) != 3:
                return
            _, g_action, g_id = parts
            telegram_id = update.effective_user.id
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            if g_action == "stop":
                _awaiting_gold_text.pop(telegram_id, None)
                await context.bot.send_message(chat_id=update.effective_chat.id, text="‏⏹ הסתיים. תודה!")
                return

            async with async_session_maker() as session:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if not gtg.is_manager(user):
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏🔒 פקודה זו זמינה למנהלים בלבד.")
                    return
                try:
                    qlog = await session.get(QueryLog, int(g_id))
                except ValueError:
                    return
                question = qlog.question if qlog else None

                if g_action == "approve" and question:
                    proposal = await propose_gold(session, question, use_llm=True)
                    await save_gold(session, question=question, gold_answer=proposal["answer"],
                                    user_id=user.id, source="telegram",
                                    target_project=proposal.get("target_project"),
                                    target_field=proposal.get("target_field"))
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="‏✅ נשמר כתשובת זהב.")
                elif g_action == "edit" and question:
                    _awaiting_gold_text[telegram_id] = {"question": question}
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏✏️ שלח/י את תשובת הזהב הנכונה כהודעה.")
                    return    # wait for the text; no next card yet
                # skip falls through to next card

                cand = await gtg.next_candidate(session, exclude_questions=set())
                if not cand:
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏✅ אין עוד שאלות. תודה!")
                    return
                nxt = await propose_gold(session, cand["question"], use_llm=True)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(f"‏🥇 <b>בניית תשובת זהב</b>\n\n‏<b>שאלה:</b> {cand['question']}\n\n"
                      f"‏<b>הצעה:</b> {nxt['answer']}"),
                parse_mode="HTML",
                reply_markup=gtg.gold_keyboard(cand["id"]),
            )
            return
```

- [ ] **Step 5: Handle the edit-text message**

In `handle_message`, beside the other awaiting-text checks (~line 537, after resolving `user` and `text`), add:

```python
            from app.services.telegram_state import _awaiting_gold_text
            if telegram_id in _awaiting_gold_text:
                gold_state = _awaiting_gold_text.pop(telegram_id)
                from app.services.gold_truth_service import save_gold
                async with async_session_maker() as gs:
                    await save_gold(gs, question=gold_state["question"], gold_answer=text.strip(),
                                    user_id=user.id, source="telegram")
                await update.message.reply_text("‏✅ תשובת הזהב נשמרה. שלח /gold להמשך.")
                return
```

- [ ] **Step 6: Verify import + syntax**

Run: `docker exec shan-ai-api python -c "import app.services.telegram_polling, app.services.gold_telegram_service; print('OK')"`
Expected: OK

- [ ] **Step 7: Run the telegram tests + full suite delta**

```bash
docker exec shan-ai-api pytest tests/test_gold_telegram.py -v
docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3
```
Expected: gold-telegram tests pass; full suite = previous green count + new tests, the 14 known pre-existing failures unchanged (test_weekly_report ×9, test_project_report_service ×3, test_project_learning ×1, test_viewer_role ×1).

- [ ] **Step 8: Commit**

```bash
git add app/services/telegram_state.py app/services/telegram_polling.py
git commit -m "feat(quality): /gold Telegram manager curation flow"
```

---

### Task 9: End-to-end — seed, curate, re-judge on real data

- [ ] **Step 1: Restart + full suite**

```bash
docker-compose restart fastapi && sleep 10
docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3
```
Expected: green except the 14 known pre-existing failures.

- [ ] **Step 2: All dashboard routes**

Login, then curl `/dashboard/quality` + `/dashboard/eval/curate` → both 200.

- [ ] **Step 3: Seed gold from DB**

```bash
curl -s -b /tmp/jar.txt -X POST http://localhost:8000/dashboard/eval/gold/seed-from-production
```
Record `seeded` / `needs_manual`. Then:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT source, count(*) FROM eval_gold_answers GROUP BY 1;"
```

- [ ] **Step 4: Re-judge gold-covered rows**

```bash
curl -s -b /tmp/jar.txt -X POST http://localhost:8000/dashboard/eval/rejudge
# poll until running:false
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/eval/rejudge/status
```
Then compare verdicts on the two known false-negatives:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT question, judge_verdict, judged_against_gold FROM query_logs WHERE question LIKE '%חולה%' OR question LIKE '%קריית גת%' ORDER BY id DESC LIMIT 6;"
```
Expected: gold-covered rows now `judged_against_gold=t`; the חולה stage row should flip toward PASS if gold seeded it.

- [ ] **Step 5: Report**

Summarize: gold-set size, seeded vs manual, gold-backed pass-rate vs the original guessed 37%, whether the known false-negatives corrected. Recommend whether to expand gold further (Telegram `/gold`) before the retrieval-fix phase.
