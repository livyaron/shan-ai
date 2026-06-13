# Retrieval Fix (Substation Prefix) + Live Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make project matching strip leading substation/station prefixes ("תחמ"ש ניר יצחק" → "ניר יצחק"), and add a judge-only live measurement of the gold set so retrieval is scored on current behavior, not stale logs.

**Architecture:** Part 2 (the fix) adds a one-shot prefix-strip fallback inside `find_projects_by_identifier`. Part 1 (measurement) exposes `run_cycle(repair=False)` via `/eval/run?repair=false`, persists the failing questions in a new `EvalRun.failed_questions` column, and surfaces a "live measure" button + fail list on the quality dashboard.

**Tech Stack:** FastAPI, async SQLAlchemy, Chart.js, pytest (+pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-06-13-retrieval-fix-substation-prefix-design.md`

**Conventions:**
- Deploy target **Railway** (local deprecated); tests run in the local container during dev.
- Hebrew strings prefixed `‏` (U+200F). Never `docker-compose down -v`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/services/project_tools.py` | Modify | prefix-strip fallback in `find_projects_by_identifier` |
| `tests/test_project_prefix.py` | Create | prefix-strip matching tests |
| `app/models.py` | Modify | + `EvalRun.failed_questions` JSON column |
| `CLAUDE.md` | Modify | + migration guardrail |
| `app/services/per_question_loop_service.py` | Modify | populate `failed_questions` in `run_cycle` |
| `app/routers/eval_loop.py` | Modify | `/eval/run` gains `repair` query param |
| `app/templates/quality.html` | Modify | "live measure" button + fail list |

---

### Task 1: Prefix-strip fallback in `find_projects_by_identifier`

**Files:**
- Modify: `app/services/project_tools.py` (`find_projects_by_identifier`, ~line 48)
- Test: `tests/test_project_prefix.py`

**Context:** `find_projects_by_identifier(identifier, session) -> list[dict]` matches exact code or name-substring (ilike), capped 10; it already has a trailing-char-strip fallback when nothing matches. `normalize_hebrew` is available via `from app.services.knowledge_service import normalize_hebrew` (used elsewhere). The bug: "תחמ"ש ניר יצחק" is searched literally; stripping the leading prefix to "ניר יצחק" matches.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_project_prefix.py
"""find_projects_by_identifier strips leading substation/station prefixes as a fallback."""
import pytest

from app.models import Project
from app.services.project_tools import find_projects_by_identifier


async def _seed(db_session, name, ident):
    p = Project(project_identifier=ident, name=name, is_active=True)
    db_session.add(p)
    await db_session.commit()


@pytest.mark.asyncio
async def test_strips_tachmash_prefix(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(Project))
    await db_session.commit()
    await _seed(db_session, "ניר יצחק - הקמת תחנה", "WBE-700")

    direct = await find_projects_by_identifier("תחמ\"ש ניר יצחק", db_session)
    assert any(m["project_identifier"] == "WBE-700" for m in direct)


@pytest.mark.asyncio
async def test_strips_tachanat_prefix(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(Project))
    await db_session.commit()
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    res = await find_projects_by_identifier("תחנת נתניה", db_session)
    assert any(m["project_identifier"] == "WBE-701" for m in res)


@pytest.mark.asyncio
async def test_prefix_only_returns_nothing(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(Project))
    await db_session.commit()
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    # bare prefix → empty remainder → must NOT match everything
    res = await find_projects_by_identifier("תחמ\"ש", db_session)
    assert res == []


@pytest.mark.asyncio
async def test_existing_match_unchanged(db_session):
    from sqlalchemy import delete
    await db_session.execute(delete(Project))
    await db_session.commit()
    await _seed(db_session, "נתניה מרכז", "WBE-701")

    # no prefix → normal name match still works
    res = await find_projects_by_identifier("נתניה", db_session)
    assert any(m["project_identifier"] == "WBE-701" for m in res)
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_project_prefix.py -v`
Expected: `test_strips_tachmash_prefix` and `test_strips_tachanat_prefix` FAIL (no fallback yet); the other two may already pass.

- [ ] **Step 3: Implement**

In `find_projects_by_identifier`, the function currently ends (~line 89) with `return []` after the trailing-char fallback. Replace that final `return []` with a leading-prefix-strip fallback:

```python
    # ── Fallback: strip a leading location prefix and retry once ──
    _LOCATION_PREFIXES = (
        'תחמ"ש', 'תחמ״ש', 'תחמש', 'תחנת מיתוג', 'תחנת', 'תחנה', 'פרויקט', 'פרוייקט',
    )
    stripped = (identifier or "").strip().strip('"').strip()
    for pref in _LOCATION_PREFIXES:
        if stripped.startswith(pref):
            remainder = stripped[len(pref):].strip().strip('"').strip()
            if not remainder:
                return []                       # bare prefix → don't match everything
            stmt3 = select(Project).where(
                Project.name.ilike(f"%{remainder}%")
            ).order_by(Project.name).limit(10)
            rows = (await session.execute(stmt3)).scalars().all()
            return [_project_to_dict(p) for p in rows]

    return []
```
Notes:
- Place this AFTER the existing trailing-char fallback's `return` paths so it only runs when nothing matched.
- Prefix comparison is on the raw `identifier` (the matcher uses ilike, not normalized forms) — matches how the rest of the function works. Strip a leading `"` so `תחמ"ש` forms are handled.
- Strip at most ONE prefix (return immediately after the first hit).

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_project_prefix.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/project_tools.py tests/test_project_prefix.py
git commit -m "fix(retrieval): strip substation/station prefixes in project matching"
```

---

### Task 2: `EvalRun.failed_questions` column + migration

**Files:**
- Modify: `app/models.py` (EvalRun, after `config_json` ~line 404)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add column**

In `class EvalRun`, after `config_json`:
```python
    failed_questions       = Column(JSON, nullable=True)   # [{"question","score"}] for FAIL rows in this run
```
(`JSON` already imported in models.py — confirm.)

- [ ] **Step 2: Apply migration to live DBs**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;"
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -c "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;"
```
Expected: `ALTER TABLE` (×2).

- [ ] **Step 3: Document guardrail**

In CLAUDE.md section 4, after the `judged_against_gold` line, add:
```markdown
- **eval_runs.failed_questions:** After rebuild/fresh DB or Railway deploy:
  `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;` (run local + Railway)
```

- [ ] **Step 4: Commit**

```bash
git add app/models.py CLAUDE.md
git commit -m "feat(quality): EvalRun.failed_questions column"
```

---

### Task 3: Populate `failed_questions` in `run_cycle`

**Files:**
- Modify: `app/services/per_question_loop_service.py` (`run_cycle`, the completion block ~line 774-780)

**Context:** `run_cycle` builds `results: list[QuestionResult]`; each `r` has `.question` (str), `.status` (str; FAIL statuses are `"unfixable"` and `"error"`), `.score_final` (float). The completion block sets `n_pass`/`n_fail` then commits.

- [ ] **Step 1: Write failing test**

```python
# tests/test_failed_questions_capture.py
"""run_cycle records FAIL questions into EvalRun.failed_questions."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import per_question_loop_service as pq
from app.models import EvalGoldAnswer, EvalRun


@pytest.mark.asyncio
async def test_run_cycle_records_failed_questions(db_session):
    from sqlalchemy import delete
    from app.models import AnswerFeedback
    await db_session.execute(delete(AnswerFeedback))
    await db_session.execute(delete(EvalGoldAnswer))
    await db_session.commit()
    db_session.add_all([
        EvalGoldAnswer(question_hash="h1", question="שאלה טובה", gold_answer="ת", source="manual"),
        EvalGoldAnswer(question_hash="h2", question="שאלה רעה", gold_answer="ת", source="manual"),
    ])
    await db_session.commit()

    from app.services.per_question_loop_service import QuestionResult

    async def fake_run_one(session, g, *a, **k):
        ok = g.question == "שאלה טובה"
        return QuestionResult(
            question=g.question, question_hash=g.question_hash, gold_answer=g.gold_answer,
            status=("passed_first_try" if ok else "unfixable"),
            score_initial=(1.0 if ok else 0.0), score_final=(1.0 if ok else 0.0),
        )

    with patch.object(pq, "run_one_question", new=fake_run_one):
        await pq.run_cycle(db_session, user_id=None, repair=False)

    run = (await db_session.execute(
        EvalRun.__table__.select().order_by(EvalRun.id.desc()).limit(1)
    )).first()
    # read back via ORM for the JSON column
    from sqlalchemy import select
    er = (await db_session.execute(select(EvalRun).order_by(EvalRun.id.desc()).limit(1))).scalar_one()
    fq = er.failed_questions or []
    assert any(item["question"] == "שאלה רעה" for item in fq)
    assert all(item["question"] != "שאלה טובה" for item in fq)
```

IMPORTANT: check `QuestionResult`'s actual constructor signature in `per_question_loop_service.py` and adapt the `QuestionResult(...)` kwargs in the test to the real fields (it's a dataclass — match its required fields; omit optionals if they have defaults).

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_failed_questions_capture.py -v`
Expected: FAIL — `failed_questions` not populated (None).

- [ ] **Step 3: Implement**

In `run_cycle`, in the completion block right after `eval_run.n_fail = ...` and before the final `await session.commit()`:
```python
        eval_run.failed_questions = [
            {"question": r.question, "score": r.score_final}
            for r in results if r.status in ("unfixable", "error")
        ]
```

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_failed_questions_capture.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_failed_questions_capture.py
git commit -m "feat(quality): run_cycle records failed_questions"
```

---

### Task 4: `/eval/run?repair=` param + dashboard live-measure button

**Files:**
- Modify: `app/routers/eval_loop.py` (`eval_run`, ~line 286)
- Modify: `app/templates/quality.html`

- [ ] **Step 1: Add repair param to the endpoint**

Change `eval_run` signature + the `run_cycle` call:
```python
@router.post("/eval/run")
async def eval_run(
    repair: bool = True,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    global _cycle_task
    if _cycle_task and not _cycle_task.done():
        raise HTTPException(409, "cycle already running")

    _events.clear()
    _event_wake.set()
    user_id = current_user.id

    async def _runner():
        async with async_session_maker() as own:
            try:
                await run_cycle(own, user_id=user_id, emit=_emit_log, repair=repair)
            except Exception as e:
                logger.exception("eval cycle failed")
                _emit_log({"type": "cycle_error", "error": str(e)})

    _cycle_task = asyncio.create_task(_runner())
    _emit_log({"type": "cycle_triggered", "user_id": user_id, "repair": repair})
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Add a data field for the latest run's failed_questions**

In the `quality_data` endpoint, before its final return, add:
```python
    last_run = (await session.execute(
        select(EvalRun).where(EvalRun.status == "completed").order_by(EvalRun.id.desc()).limit(1)
    )).scalar_one_or_none()
    live_fail = (last_run.failed_questions or []) if last_run else []
```
and add to the returned dict:
```python
        "live_failed": live_fail,
```

- [ ] **Step 3: Dashboard button + fail list**

In `quality.html`, near the other buttons, add:
```html
      <button class="btn-outline-dim" onclick="liveMeasure()">📡 מדידה חיה (gold, judge-only)</button>
```
And a fail-list block under the panels:
```html
  <div class="card p-3 mb-3">
    <h6>שאלות שנכשלו במדידה החיה האחרונה</h6>
    <ul id="live-failed" style="color:var(--text-2);font-size:.9rem;"></ul>
  </div>
```
JS:
```javascript
async function liveMeasure() {
  const r = await fetch("/dashboard/eval/run?repair=false", {method:"POST"});
  if (r.status === 409) { alert("מדידה כבר רצה."); return; }
  alert("מדידה חיה התחילה — עקוב/י בעמוד ה-Eval. רענן/י כאן בסיום.");
}
// in load(), after fetching d:
//   const ul = document.getElementById("live-failed");
//   ul.innerHTML = "";
//   for (const f of (d.live_failed || [])) { const li=document.createElement("li"); li.textContent = `${f.question} (${f.score})`; ul.appendChild(li); }
```
Add that `live_failed` rendering inside the existing `load()` after `const d = await res.json();`.

- [ ] **Step 4: Verify**

```bash
docker-compose up -d 2>/dev/null; sleep 8
U_ID=$(docker exec shan-ai-postgres psql -U shan_user -d shan_ai -tAc "SELECT id FROM users ORDER BY id LIMIT 1;")
curl -s -c /tmp/jar.txt -o /dev/null -X POST http://localhost:8000/login -d "user_id=$U_ID&password=1234"
curl -s -b /tmp/jar.txt "http://localhost:8000/dashboard/quality/data" | python -c "import sys,json; print('live_failed' in json.load(sys.stdin))"   # True
curl -s -b /tmp/jar.txt -o /dev/null -w "%{http_code}\n" http://localhost:8000/dashboard/quality   # 200
```
Do NOT POST /eval/run locally (would re-ask via Groq). Regression: `docker exec shan-ai-api pytest tests/test_project_prefix.py tests/test_failed_questions_capture.py tests/test_eval_uses_ask_router.py -q`.

- [ ] **Step 5: Commit**

```bash
git add app/routers/eval_loop.py app/templates/quality.html
git commit -m "feat(quality): judge-only live-measure button + failed-questions panel"
```

---

### Task 5: Ship to Railway + before/after measurement

- [ ] **Step 1: Capture pre-fix baseline (current prod, before deploy)**

The fix is not deployed yet, so prod still has the bug. Record the broken probes:
```bash
cd /tmp; URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
for f in "תחמ\"ש ניר יצחק" "תחנת נתניה"; do printf '{"question":"%s"}' "$f" > qb.json; echo "BEFORE $f:"; curl -s -m 50 -b /tmp/rw.txt -X POST "$URL/dashboard/ask/query" -H "Content-Type: application/json; charset=utf-8" --data-binary @qb.json | python -c "import sys,json;print(json.load(sys.stdin).get('answer','(none)')[:80])"; done
```
Expected: both "not found"/none (baseline = broken).

- [ ] **Step 2: Full suite**

Run: `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3`
Expected: green except the 14 known pre-existing failures.

- [ ] **Step 3: Merge + push**

```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: retrieval substation-prefix fix + live measurement"
git push origin master
```

- [ ] **Step 4: Stop local, deploy Railway**

```bash
docker-compose stop fastapi
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```
(The `failed_questions` migration was already applied to Railway in Task 2 Step 2.)

- [ ] **Step 5: Wait for new build (~2-3 min), then confirm the fix live**

```bash
cd /tmp; URL="https://easygoing-endurance-production-df54.up.railway.app"
# after the app is back up:
for f in "תחמ\"ש ניר יצחק" "תחנת נתניה"; do printf '{"question":"%s"}' "$f" > qa.json; echo "AFTER $f:"; curl -s -m 50 -b /tmp/rw.txt -X POST "$URL/dashboard/ask/query" -H "Content-Type: application/json; charset=utf-8" --data-binary @qa.json | python -c "import sys,json;print(json.load(sys.stdin).get('answer','(none)')[:90])"; done
```
Expected: both now resolve to a project (no longer "not found"). If still broken, the new build isn't live yet — wait and retry.

- [ ] **Step 6: Run the judge-only live measurement on Railway**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/run?repair=false"
```
Then poll the latest run (Monitor loop) via `/dashboard/eval/runs` until the newest run `status=="completed"`, then read pass-rate + failed_questions:
```bash
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/eval/runs" | python -c "import sys,json; r=json.load(sys.stdin)['runs'][0]; print('pass', r['n_pass'], '/', r['n_probes'])"
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/data" | python -c "import sys,json; d=json.load(sys.stdin); [print('FAIL:', f['question']) for f in d.get('live_failed',[])]"
```

- [ ] **Step 7: Report**

Report: BEFORE (broken probes) vs AFTER (resolve); the **current-behavior** live pass-rate over the gold set (vs the stale-log 41%); and the real remaining failing questions from `live_failed` — the next fix targets. Note local stays stopped.
```
