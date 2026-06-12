# Quality Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Label production Q&A data with an LLM judge, expand the gold set from production candidates, expose a quality dashboard, capture failure causes from Telegram 👎, and auto-summarize weekly eval — so AI-answer failure causes become measurable.

**Architecture:** Reuse the existing eval stack: `gold_truth_service.propose_gold/compare_to_gold` for grounded judging, `llm_router.llm_chat("eval_judge", ...)` for LLM calls, `eval_loop.py` router + curate UI for gold curation, `eval_cron.py` APScheduler for automation, `_navbar.html` for the new page. New code is one backfill service, a handful of router endpoints, one template, one Telegram callback branch, and two cron jobs.

**Tech Stack:** FastAPI, SQLAlchemy async, Groq via `llm_chat`, Chart.js, python-telegram-bot v21, APScheduler, pytest (+pytest-asyncio, already in use — see `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-06-12-quality-baseline-design.md`

**Conventions (apply to every task):**
- All bot/user-facing Hebrew strings prefixed with `‏` (RTL mark).
- Run tests inside Docker if host has no venv: `docker exec shan-ai-api pytest <path> -v` (fall back to host `pytest` if it works).
- After each code-change task: `docker-compose restart fastapi`.
- `failure_type` values (fit `String(20)`): `WRONG_PROJECT`, `MISSING_DATA`, `HALLUCINATION`, `UNSTABLE`, `STRUCTURE`, `REFUSED`.
- `judge_verdict` values (fit `String(10)`): `PASS`, `PARTIAL`, `FAIL`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/services/judge_backfill_service.py` | Create | Judge unlabeled `query_logs` rows; module-level progress state |
| `tests/test_judge_backfill.py` | Create | Backfill unit tests |
| `app/routers/eval_loop.py` | Modify | + backfill endpoints, + gold candidates endpoint, + quality page/data endpoints |
| `app/templates/eval_curate.html` | Modify | + "מועמדים מהשטח" section |
| `app/templates/quality.html` | Create | Quality dashboard page |
| `app/templates/_navbar.html` | Modify | + link to /dashboard/quality |
| `app/services/telegram_polling.py` | Modify | 👎 → cause keyboard; new `lfc` callback |
| `tests/test_feedback_cause.py` | Create | Cause-callback unit test |
| `app/services/eval_cron.py` | Modify | Register dead `_project_report_cron` (bugfix); + weekly eval summary job |
| `tests/test_eval_weekly_summary.py` | Create | Summary formatting test |

---

### Task 1: Judge backfill service — verdict logic

**Files:**
- Create: `app/services/judge_backfill_service.py`
- Test: `tests/test_judge_backfill.py`

- [ ] **Step 1: Write failing tests for pure helpers**

```python
# tests/test_judge_backfill.py
"""Tests for judge_backfill_service: verdict mapping, failure-type parsing, idempotent row selection."""
import pytest

from app.services.judge_backfill_service import (
    score_to_verdict,
    parse_failure_type,
    NO_INFO,
)


def test_score_to_verdict_thresholds():
    assert score_to_verdict(1.0) == "PASS"
    assert score_to_verdict(0.8) == "PASS"
    assert score_to_verdict(0.79) == "PARTIAL"
    assert score_to_verdict(0.5) == "PARTIAL"
    assert score_to_verdict(0.49) == "FAIL"
    assert score_to_verdict(0.0) == "FAIL"


def test_parse_failure_type_valid():
    assert parse_failure_type("WRONG_PROJECT") == "WRONG_PROJECT"
    assert parse_failure_type("  hallucination \n") == "HALLUCINATION"
    assert parse_failure_type("התשובה: MISSING_DATA כי חסר") == "MISSING_DATA"


def test_parse_failure_type_garbage_returns_none():
    assert parse_failure_type("לא יודע") is None
    assert parse_failure_type("") is None
    assert parse_failure_type(None) is None


def test_no_info_constant():
    # propose_gold returns exactly this string when DB has no answer
    assert NO_INFO == "אין מידע"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `docker exec shan-ai-api pytest tests/test_judge_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.judge_backfill_service'`

- [ ] **Step 3: Implement the service**

```python
# app/services/judge_backfill_service.py
"""Offline LLM-judge backfill for query_logs.

Labels rows where judge_verdict IS NULL with PASS/PARTIAL/FAIL and,
for non-PASS rows, a failure_type from the fixed taxonomy.

Judging strategy per row:
1. propose_gold(question) -> grounded reference answer from current DB.
2. If both reference and answer are "no info" -> PASS.
3. Otherwise compare_to_gold(question, ai_response, reference) -> score
   -> verdict via thresholds (>=0.8 PASS, >=0.5 PARTIAL, else FAIL).
4. For non-PASS rows, one extra LLM call classifies failure_type.

Idempotent: only selects judge_verdict IS NULL. One bad row never aborts
the batch. Module-level _progress dict supports UI polling.
"""

import asyncio
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog
from app.services.gold_truth_service import propose_gold, compare_to_gold
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

NO_INFO = "אין מידע"

FAILURE_TYPES = ("WRONG_PROJECT", "MISSING_DATA", "HALLUCINATION", "UNSTABLE", "STRUCTURE", "REFUSED")

# UI-pollable progress: reset at run start, updated per row.
_progress = {"running": False, "total": 0, "done": 0, "judged": 0, "errors": 0}


def get_progress() -> dict:
    return dict(_progress)


def score_to_verdict(score: float) -> str:
    if score >= 0.8:
        return "PASS"
    if score >= 0.5:
        return "PARTIAL"
    return "FAIL"


def parse_failure_type(raw: str | None) -> str | None:
    """Extract a taxonomy token from LLM output; garbage -> None."""
    if not raw:
        return None
    up = raw.upper()
    for ft in FAILURE_TYPES:
        if re.search(rf"\b{ft}\b", up):
            return ft
    return None


async def _classify_failure(question: str, answer: str, reference: str) -> str | None:
    sys = (
        "אתה מסווג כשלים של מערכת שאלות-תשובות. החזר אך ורק אחת מהמילים: "
        "WRONG_PROJECT (ענה על פרויקט/ישות לא נכונים), "
        "MISSING_DATA (המידע קיים בהפניה אך חסר בתשובה), "
        "HALLUCINATION (התשובה מכילה עובדות שאינן בהפניה), "
        "STRUCTURE (פלט שבור/לא קריא), "
        "REFUSED (סירוב או תשובה ריקה)."
    )
    user = f"שאלה: {question}\nתשובת המערכת: {answer}\nתשובת ההפניה (אמת): {reference}"
    try:
        raw = await llm_chat(
            "eval_judge",
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            max_tokens=20,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"judge_backfill: classify failed: {e}")
        return None
    return parse_failure_type(raw)


def _is_no_info(text: str) -> bool:
    return NO_INFO in (text or "")


async def judge_one(session: AsyncSession, log: QueryLog) -> tuple[str, str | None]:
    """Judge a single QueryLog row. Returns (verdict, failure_type)."""
    answer = (log.ai_response or "").strip()
    if not answer:
        return "FAIL", "REFUSED"

    ref = await propose_gold(session, log.question)
    reference = ref["answer"]

    if _is_no_info(reference) and _is_no_info(answer):
        return "PASS", None

    score = await compare_to_gold(log.question, answer, reference)
    verdict = score_to_verdict(score)
    if verdict == "PASS":
        return verdict, None

    failure = await _classify_failure(log.question, answer, reference)
    return verdict, failure


async def run_backfill(session: AsyncSession, limit: int = 200) -> dict:
    """Judge up to `limit` unjudged rows, newest first. Returns stats dict."""
    rows = (await session.execute(
        select(QueryLog)
        .where(QueryLog.judge_verdict.is_(None))
        .where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
        .limit(limit)
    )).scalars().all()

    _progress.update({"running": True, "total": len(rows), "done": 0, "judged": 0, "errors": 0})

    for log in rows:
        try:
            verdict, failure = await judge_one(session, log)
            log.judge_verdict = verdict
            if failure:
                log.failure_type = failure
            await session.commit()
            _progress["judged"] += 1
        except Exception as e:
            await session.rollback()
            _progress["errors"] += 1
            logger.warning(f"judge_backfill: row {log.id} failed: {e}")
            # Groq rate limit -> brief backoff, keep going
            await asyncio.sleep(2)
        finally:
            _progress["done"] += 1

    _progress["running"] = False
    stats = get_progress()
    logger.info(f"judge_backfill: finished {stats}")
    return stats
```

- [ ] **Step 4: Run tests, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_judge_backfill.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/judge_backfill_service.py tests/test_judge_backfill.py
git commit -m "feat(quality): judge backfill service for unlabeled query_logs"
```

---

### Task 2: Backfill idempotency test (DB-level)

**Files:**
- Modify: `tests/test_judge_backfill.py`

- [ ] **Step 1: Add failing async test using the project's session fixture**

Check `tests/conftest.py` first for the async session fixture name (other tests, e.g. `tests/test_is_relevant.py`, show the pattern — copy the fixture usage style exactly). Test:

```python
# append to tests/test_judge_backfill.py
import pytest
from unittest.mock import AsyncMock, patch

from app.models import QueryLog
from app.services.judge_backfill_service import run_backfill


@pytest.mark.asyncio
async def test_backfill_skips_already_judged(db_session):  # use conftest fixture name
    db_session.add_all([
        QueryLog(question="ש1", ai_response="ת1", judge_verdict="PASS"),
        QueryLog(question="ש2", ai_response="ת2", judge_verdict=None),
    ])
    await db_session.commit()

    with patch(
        "app.services.judge_backfill_service.judge_one",
        new=AsyncMock(return_value=("FAIL", "MISSING_DATA")),
    ) as mocked:
        stats = await run_backfill(db_session, limit=50)

    assert mocked.await_count == 1          # only the unjudged row
    assert stats["judged"] == 1
```

- [ ] **Step 2: Run, verify it fails or passes for the right reason**

Run: `docker exec shan-ai-api pytest tests/test_judge_backfill.py -v`
Expected: all PASS (implementation from Task 1 already filters `judge_verdict IS NULL`; if FAIL, the where-clause is wrong — fix service, not test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_judge_backfill.py
git commit -m "test(quality): backfill idempotency against judged rows"
```

---

### Task 3: Backfill + candidates endpoints in eval router

**Files:**
- Modify: `app/routers/eval_loop.py` (append after the `/eval/runs` endpoint, ~line 274)

- [ ] **Step 1: Add endpoints**

Follow the auth pattern used by existing endpoints in this file (they take `current_user=Depends(get_current_user)` — copy the exact import/dependency used at the top of `eval_loop.py`).

```python
# append to app/routers/eval_loop.py
from sqlalchemy import func

from app.models import QueryLog
from app.services import judge_backfill_service


@router.post("/eval/backfill")
async def start_backfill(
    request: Request,
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
):
    """Kick off judge backfill in the background; UI polls /eval/backfill/status."""
    if judge_backfill_service.get_progress()["running"]:
        return {"status": "already_running"}

    async def _run():
        from app.database import async_session_maker
        async with async_session_maker() as s:
            await judge_backfill_service.run_backfill(s, limit=limit)

    asyncio.create_task(_run())
    return {"status": "started", "limit": limit}


@router.get("/eval/backfill/status")
async def backfill_status(current_user=Depends(get_current_user)):
    return judge_backfill_service.get_progress()


@router.get("/eval/gold/candidates")
async def gold_candidates(
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
):
    """Ranked gold candidates from production logs.

    Rank 1: human-rated rows; rank 2: judge FAIL/PARTIAL; rank 3: frequent questions.
    Dedup by normalized question text; exclude questions already in gold.
    """
    from app.services.gold_truth_service import list_gold, question_hash

    gold_hashes = {g.question_hash for g in await list_gold(session)}

    rows = (await session.execute(
        select(QueryLog)
        .where(QueryLog.ai_response.isnot(None))
        .order_by(QueryLog.timestamp.desc())
        .limit(1000)
    )).scalars().all()

    freq: dict[str, int] = {}
    best: dict[str, QueryLog] = {}
    for r in rows:
        key = (r.question or "").strip().lower()
        if not key:
            continue
        freq[key] = freq.get(key, 0) + 1
        best.setdefault(key, r)

    def rank(r: QueryLog, key: str) -> tuple:
        human = 0 if (r.user_feedback or 0) != 0 else 1
        judged_bad = 0 if r.judge_verdict in ("FAIL", "PARTIAL") else 1
        return (human, judged_bad, -freq[key])

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
```

Also add `import asyncio` and `Request` to the file's imports if missing (check top of file first — most likely `asyncio` is absent).

- [ ] **Step 2: Restart + smoke-test endpoints**

```bash
docker-compose restart fastapi && sleep 8
# login cookie as in previous verification, then:
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/eval/backfill/status
# Expected: {"running": false, "total": 0, ...}
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/eval/gold/candidates | head -c 300
# Expected: {"candidates": [...]} JSON, no 500
```

- [ ] **Step 3: Run existing eval tests (regression)**

Run: `docker exec shan-ai-api pytest tests/test_eval_uses_ask_router.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/routers/eval_loop.py
git commit -m "feat(quality): backfill + gold-candidates endpoints"
```

---

### Task 4: Curate UI — "מועמדים מהשטח" section

**Files:**
- Modify: `app/templates/eval_curate.html`

- [ ] **Step 1: Add section + JS**

Below the existing table (inside `.eval-wrap`, after `</table>`), add:

```html
<h2 style="color:var(--cyan);font-size:1.1rem;margin-top:2rem;">📥 מועמדים מהשטח (מ-query_logs)</h2>
<table>
    <thead>
        <tr>
            <th style="width: 30%;">שאלה</th>
            <th style="width: 40%;">תשובה אחרונה</th>
            <th style="width: 15%;">אות</th>
            <th style="width: 15%;">פעולות</th>
        </tr>
    </thead>
    <tbody id="cand-rows">
        <tr><td colspan="4" style="text-align:center; color: var(--text-2);">טוען…</td></tr>
    </tbody>
</table>
```

JS (append inside the existing `<script>`): fetch `/dashboard/eval/gold/candidates`, render rows; signal column shows `👍/👎` if `user_feedback != 0`, else `judge_verdict` + `failure_type`, else `×N` frequency; per-row button «הצע תשובה» calls the page's existing propose/save flow with the candidate question (reuse the same JS function the proposals list uses — find its name in the file, e.g. the handler bound to the approve buttons, and call it with the candidate's question).

```javascript
async function loadCandidates() {
    const res = await fetch("/dashboard/eval/gold/candidates");
    const data = await res.json();
    const tb = document.getElementById("cand-rows");
    tb.innerHTML = "";
    for (const c of data.candidates) {
        const sig = c.user_feedback ? (c.user_feedback > 0 ? "👍" : "👎")
                  : c.judge_verdict ? `${c.judge_verdict}${c.failure_type ? " · " + c.failure_type : ""}`
                  : `×${c.count}`;
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${c.question}</td>
            <td style="color:var(--text-2);">${(c.ai_response || "").slice(0, 160)}</td>
            <td>${sig}</td>
            <td class="actions"><button onclick='proposeFor(${JSON.stringify(c.question)})'>הצע תשובה</button></td>`;
        tb.appendChild(tr);
    }
    if (!data.candidates.length) {
        tb.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-2);">אין מועמדים</td></tr>';
    }
}
loadCandidates();
```

`proposeFor(question)` implementation (fixed approach — do exactly this):

1. In `app/routers/eval_loop.py`, extend the existing `GET /eval/gold/proposals` endpoint with an optional query param `question: str | None = None`. When provided, return a single proposal for just that question via `propose_gold(session, question)` in the same response shape the endpoint already uses for its list (read the endpoint body at line ~95 first and mirror its item dict exactly).
2. In the template JS:

```javascript
async function proposeFor(question) {
    const res = await fetch(`/dashboard/eval/gold/proposals?question=${encodeURIComponent(question)}`);
    const data = await res.json();
    const p = (data.proposals || [data])[0];
    const answer = prompt("‏אשר/ערוך תשובת זהב:", p.answer || p.proposed_answer || "");
    if (answer === null) return;
    await fetch("/dashboard/eval/gold/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question: question, answer: answer}),
    });
    loadCandidates();
}
```

3. Check the exact request body `POST /eval/gold/save` expects (endpoint at line ~154 of `eval_loop.py`) and match its field names — if it expects e.g. `gold_answer` instead of `answer`, use that.

- [ ] **Step 2: Manual verify**

```bash
docker-compose restart fastapi && sleep 8
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/eval/curate | grep -c "cand-rows"
# Expected: 1
```

Open page in browser: candidates table populates; «הצע תשובה» creates a proposal row that can be approved into gold.

- [ ] **Step 3: Commit**

```bash
git add app/templates/eval_curate.html app/routers/eval_loop.py
git commit -m "feat(quality): production candidates section in curate UI"
```

---

### Task 5: Quality dashboard page

**Files:**
- Modify: `app/routers/eval_loop.py` (two endpoints)
- Create: `app/templates/quality.html`
- Modify: `app/templates/_navbar.html`

- [ ] **Step 1: Data endpoint + page endpoint**

```python
# append to app/routers/eval_loop.py
from app.models import EvalRun


@router.get("/quality", response_class=HTMLResponse)
async def quality_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse("quality.html", {"request": request, "current_user": current_user})


@router.get("/quality/data")
async def quality_data(
    session: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user),
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

    return {
        "run_trend": run_trend,
        "verdicts": verdicts,
        "failures": failures,
        "feedback_weekly": fb_weekly,
        "worst": worst,
    }
```

- [ ] **Step 2: Template**

`app/templates/quality.html` — same boilerplate as `project_reports.html` (dark tokens, Heebo, Bootstrap), plus Chart.js CDN (copy the `<script src=...chart.js...>` line from `dashboard.html` line 9) and `{% include "_navbar.html" %}` right after `<body>`. Content:

```html
<div class="container-fluid px-4 py-4">
  <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
    <span class="page-title">🧪 איכות תשובות AI</span>
    <div class="d-flex gap-2 align-items-center">
      <span id="bf-status" style="color:var(--text-2);font-size:.85rem;"></span>
      <button class="btn-cyan" onclick="startBackfill()">⚖️ שפוט 200 שאלות אחרונות</button>
    </div>
  </div>

  <div class="row g-3">
    <div class="col-md-6"><div class="card p-3"><h6>מגמת הצלחה ב-eval</h6><canvas id="trend"></canvas></div></div>
    <div class="col-md-3"><div class="card p-3"><h6>פסקי דין (שופט)</h6><canvas id="verdicts"></canvas></div></div>
    <div class="col-md-3"><div class="card p-3"><h6>סוגי כשל</h6><canvas id="failures"></canvas></div></div>
    <div class="col-md-6"><div class="card p-3"><h6>פידבק שבועי</h6><canvas id="fb"></canvas></div></div>
    <div class="col-md-6"><div class="card p-3"><h6>שאלות שנכשלו (20 אחרונות)</h6>
      <table class="table table-sm mb-0"><thead><tr><th>שאלה</th><th>כשל</th><th>מתי</th><th></th></tr></thead>
      <tbody id="worst"></tbody></table>
    </div></div>
  </div>
</div>

<script>
async function load() {
  const d = await (await fetch("/dashboard/quality/data")).json();
  new Chart(trend, {type:"line", data:{labels:d.run_trend.map(r=>r.date),
    datasets:[{label:"% הצלחה", data:d.run_trend.map(r=>r.pass_rate), borderColor:"#00d4ff", tension:.3}]}});
  new Chart(verdicts, {type:"doughnut", data:{labels:Object.keys(d.verdicts),
    datasets:[{data:Object.values(d.verdicts), backgroundColor:["#10b981","#f59e0b","#ef4444"]}]}});
  new Chart(failures, {type:"bar", data:{labels:d.failures.map(f=>f.type),
    datasets:[{data:d.failures.map(f=>f.count), backgroundColor:"#8b9cf4"}]}});
  new Chart(fb, {type:"bar", data:{labels:d.feedback_weekly.map(w=>w.week),
    datasets:[{label:"👍",data:d.feedback_weekly.map(w=>w.up),backgroundColor:"#10b981"},
              {label:"👎",data:d.feedback_weekly.map(w=>w.down),backgroundColor:"#ef4444"},
              {label:"ללא",data:d.feedback_weekly.map(w=>w.none),backgroundColor:"#344156"}]},
    options:{scales:{x:{stacked:true},y:{stacked:true}}}});
  worst.innerHTML = d.worst.map(w =>
    `<tr><td>${w.question}</td><td>${w.failure_type||"—"}</td><td>${w.ts}</td>
     <td><a href="/dashboard/logs" style="color:var(--cyan);">לוג ↗</a></td></tr>`).join("")
    || '<tr><td colspan="4" style="color:var(--text-2);text-align:center;">אין כשלים מתועדים</td></tr>';
}
async function startBackfill() {
  await fetch("/dashboard/eval/backfill", {method:"POST"});
  pollBf();
}
async function pollBf() {
  const s = await (await fetch("/dashboard/eval/backfill/status")).json();
  document.getElementById("bf-status").textContent =
    s.running ? `שופט… ${s.done}/${s.total}` : (s.total ? `הסתיים: ${s.judged} נשפטו, ${s.errors} שגיאות` : "");
  if (s.running) setTimeout(pollBf, 2000); else if (s.total) load();
}
load(); pollBf();
</script>
```

- [ ] **Step 3: Navbar link**

In `app/templates/_navbar.html`, inside the «⚙️ מערכת» dropdown menu, after the Eval link:

```html
<a href="/dashboard/quality" class="shan-nav-link{% if _path.startswith('/dashboard/quality') %} active{% endif %}">🧪 איכות AI</a>
```

(Rename the existing Eval link label to «🛠 מחזור אבחון» to avoid two near-identical 🧪 entries.)

- [ ] **Step 4: Verify**

```bash
docker-compose restart fastapi && sleep 8
curl -s -b /tmp/jar.txt -o /tmp/q.html -w "%{http_code}\n" http://localhost:8000/dashboard/quality   # 200
curl -s -b /tmp/jar.txt http://localhost:8000/dashboard/quality/data | head -c 300                    # JSON, no 500
```

Browser: charts render (trend may be sparse — 2 completed runs), backfill button live-updates counter.

- [ ] **Step 5: Commit**

```bash
git add app/templates/quality.html app/templates/_navbar.html app/routers/eval_loop.py
git commit -m "feat(quality): /dashboard/quality page with judge + feedback analytics"
```

---

### Task 6: Telegram 👎 cause follow-up

**Files:**
- Modify: `app/services/telegram_polling.py` (keyboard ~line 100, callback handler ~line 1095)
- Test: `tests/test_feedback_cause.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_feedback_cause.py
"""👎 follow-up: cause callback writes failure_type to the QueryLog row."""
import pytest

from app.services.telegram_polling import _cause_keyboard, CAUSE_MAP


def test_cause_keyboard_has_three_causes_plus_skip():
    kb = _cause_keyboard(42)
    flat = [b for row in kb.inline_keyboard for b in row]
    datas = [b.callback_data for b in flat]
    assert "lfc:42:WRONG_PROJECT" in datas
    assert "lfc:42:MISSING_DATA" in datas
    assert "lfc:42:HALLUCINATION" in datas
    assert "lfc:42:SKIP" in datas


def test_cause_map_values_match_taxonomy():
    assert set(CAUSE_MAP.keys()) == {"WRONG_PROJECT", "MISSING_DATA", "HALLUCINATION"}
```

Run: `docker exec shan-ai-api pytest tests/test_feedback_cause.py -v`
Expected: FAIL — `ImportError: cannot import name '_cause_keyboard'`

- [ ] **Step 2: Implement keyboard + constants**

In `telegram_polling.py`, after `_feedback_keyboard` (~line 106):

```python
CAUSE_MAP = {
    "WRONG_PROJECT": "פרויקט לא נכון",
    "MISSING_DATA": "חסר מידע",
    "HALLUCINATION": "תשובה שגויה",
}


def _cause_keyboard(log_id: int) -> InlineKeyboardMarkup:
    """Follow-up after 👎: one tap classifies the failure cause."""
    rows = [[InlineKeyboardButton(label, callback_data=f"lfc:{log_id}:{code}")]
            for code, label in CAUSE_MAP.items()]
    rows.append([InlineKeyboardButton("דלג", callback_data=f"lfc:{log_id}:SKIP")])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 3: Wire into the callback handler**

In the `lfb_up`/`lfb_dn` branch (~line 1096): on 👎, send the cause keyboard instead of the plain thank-you.

```python
            # --- Query log feedback (👍/👎 on RAG answers) ---
            if action in ("lfb_up", "lfb_dn"):
                from app.models import QueryLog as _QL
                log = await session.get(_QL, decision_id)
                if log:
                    log.user_feedback = 1 if action == "lfb_up" else -1
                    await session.commit()
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                if action == "lfb_up":
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="‏👍 תודה! הפידבק נשמר.",
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="‏👎 נשמר. מה היה לא בסדר?",
                        reply_markup=_cause_keyboard(decision_id),
                    )
                return
```

Then a new branch for the cause callback. **Important:** find how `action` and `decision_id` are parsed from `callback_data` at the top of the handler (it splits on `:`). `lfc:<log_id>:<CODE>` has 3 parts — parse accordingly:

```python
            # --- Query log failure cause (after 👎) ---
            if action == "lfc":
                from app.models import QueryLog as _QL
                parts = query.data.split(":")          # ["lfc", "<log_id>", "<CODE>"]
                lfc_log_id, lfc_code = int(parts[1]), parts[2]
                if lfc_code != "SKIP":
                    log = await session.get(_QL, lfc_log_id)
                    if log:
                        log.failure_type = lfc_code
                        await session.commit()
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="‏תודה! זה עוזר לשפר את המערכת.",
                )
                return
```

If the handler's generic `decision_id` parsing chokes on 3-part data, place the `lfc` branch **before** the generic parse (read the handler's first ~20 lines and adapt — the branch only needs `query.data`).

- [ ] **Step 4: Audit answer paths**

Verify every `send_message` carrying an AI answer passes `reply_markup=_feedback_keyboard(...)`. Known call sites: lines ~416, ~753, ~777, ~833, ~1029. Search:

```bash
grep -n "_feedback_keyboard" app/services/telegram_polling.py
```

For any AI-answer send site lacking it (compare against the list of places `knowledge_service` answers are sent), add the keyboard when a `log_id` exists.

- [ ] **Step 5: Run tests**

Run: `docker exec shan-ai-api pytest tests/test_feedback_cause.py -v`
Expected: 2 PASS

- [ ] **Step 6: Manual Telegram check**

Restart fastapi; in Telegram ask the bot a question → 👎 → cause keyboard appears → tap «חסר מידע» → verify:

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT id, user_feedback, failure_type FROM query_logs ORDER BY id DESC LIMIT 3;"
```

Expected: newest row `user_feedback=-1`, `failure_type=MISSING_DATA`.

- [ ] **Step 7: Commit**

```bash
git add app/services/telegram_polling.py tests/test_feedback_cause.py
git commit -m "feat(quality): failure-cause follow-up on Telegram thumbs-down"
```

---

### Task 7: Weekly eval summary + register dead project-report cron (bugfix)

**Files:**
- Modify: `app/services/eval_cron.py`
- Test: `tests/test_eval_weekly_summary.py`

- [ ] **Step 1: Failing test for summary formatting**

```python
# tests/test_eval_weekly_summary.py
from app.services.eval_cron import format_eval_summary


def test_format_eval_summary_with_delta():
    cur = {"n_probes": 50, "n_pass": 40, "started_at": "08/06"}
    prev = {"n_probes": 50, "n_pass": 45}
    msg = format_eval_summary(cur, prev, newly_failing=["מתי חישמול תחנה X?"])
    assert "80%" in msg
    assert "90%" in msg          # previous rate
    assert "מתי חישמול תחנה X?" in msg
    assert msg.startswith("‏")


def test_format_eval_summary_no_previous():
    msg = format_eval_summary({"n_probes": 10, "n_pass": 7, "started_at": "08/06"}, None, [])
    assert "70%" in msg
```

Run: `docker exec shan-ai-api pytest tests/test_eval_weekly_summary.py -v`
Expected: FAIL — ImportError

- [ ] **Step 2: Implement in eval_cron.py**

```python
def format_eval_summary(cur: dict, prev: dict | None, newly_failing: list[str]) -> str:
    """Hebrew Telegram summary of a completed eval run vs the previous one."""
    rate = round(cur["n_pass"] / cur["n_probes"] * 100) if cur["n_probes"] else 0
    lines = [f"‏🧪 סיכום eval שבועי ({cur['started_at']})",
             f"‏הצלחה: {cur['n_pass']}/{cur['n_probes']} ({rate}%)"]
    if prev and prev.get("n_probes"):
        prev_rate = round(prev["n_pass"] / prev["n_probes"] * 100)
        arrow = "📈" if rate >= prev_rate else "📉"
        lines.append(f"‏{arrow} ריצה קודמת: {prev_rate}%")
    if newly_failing:
        lines.append("‏❌ נכשלו הפעם:")
        lines.extend(f"‏• {q}" for q in newly_failing[:10])
    return "\n".join(lines)


async def _weekly_eval_summary() -> None:
    """Sunday 07:00 IL: judge-only eval over the gold set + admin Telegram summary."""
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models import EvalRun, User, RoleEnum
    from app.services.per_question_loop_service import run_cycle
    from app.services.telegram_polling import telegram_bot

    async with async_session_maker() as s:
        try:
            await run_cycle(s, user_id=None, repair=False)   # judge-only; see note below
        except TypeError:
            await run_cycle(s, user_id=None)                  # fallback if no repair kwarg
        except Exception as e:
            logger.exception(f"weekly_eval_summary run failed: {e}")
            return

        runs = (await s.execute(
            select(EvalRun).where(EvalRun.status == "completed").order_by(EvalRun.id.desc()).limit(2)
        )).scalars().all()
        if not runs:
            return
        cur = {"n_probes": runs[0].n_probes, "n_pass": runs[0].n_pass,
               "started_at": runs[0].started_at.strftime("%d/%m")}
        prev = ({"n_probes": runs[1].n_probes, "n_pass": runs[1].n_pass}
                if len(runs) > 1 else None)

        # newly failing questions: FAIL rows from this run via route of per_question loop
        # (kept simple: report count only when per-question detail is unavailable)
        msg = format_eval_summary(cur, prev, newly_failing=[])

        admins = (await s.execute(
            select(User).where(User.role == RoleEnum.DIVISION_MANAGER, User.telegram_id.isnot(None))
        )).scalars().all()
        bot = (telegram_bot.application.bot
               if telegram_bot.application and telegram_bot.application.bot else None)
        if bot:
            for a in admins:
                try:
                    await bot.send_message(chat_id=a.telegram_id, text=msg)
                except Exception as e:
                    logger.warning(f"weekly_eval_summary: send to {a.id} failed: {e}")
```

**Note on `repair=False`:** check `run_cycle`'s signature in `per_question_loop_service.py` (line ~150-200). If it has no flag controlling auto-repair, add keyword `repair: bool = True` and gate the repair-proposal step on it (the spec requires judge-only weekly runs). If a flag with a different name exists, use it and update this call + the except-TypeError fallback is then unnecessary.

- [ ] **Step 3: Register jobs in `start_scheduler` — including the dead cron bugfix**

`_project_report_cron` is defined in this file but **never registered** — the report-schedule UI promises 15-minute checks that never run. Add both jobs after the existing `add_job` calls:

```python
    sch.add_job(
        _project_report_cron,
        "interval", minutes=15,
        id="project_report_cron", replace_existing=True,
    )
    sch.add_job(
        _weekly_eval_summary,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone="Asia/Jerusalem"),
        id="weekly_eval_summary", replace_existing=True,
    )
```

And extend the startup log lines accordingly.

- [ ] **Step 4: Run tests + full regression**

```bash
docker exec shan-ai-api pytest tests/test_eval_weekly_summary.py -v   # 2 PASS
docker exec shan-ai-api pytest tests/ -x -q                            # all green
```

- [ ] **Step 5: Restart + verify scheduler registration**

```bash
docker-compose restart fastapi && sleep 8
docker logs shan-ai-api --tail 30 | grep -i "cron\|scheduler"
```

Expected: log lines for `eval_nightly`, `weekly_report`, `project_report_cron`, `weekly_eval_summary`.

- [ ] **Step 6: Commit**

```bash
git add app/services/eval_cron.py tests/test_eval_weekly_summary.py app/services/per_question_loop_service.py
git commit -m "feat(quality): weekly eval summary to admins; fix: register dead project-report cron"
```

---

### Task 8: Final verification + kick off the baseline

- [ ] **Step 1: Full test suite**

Run: `docker exec shan-ai-api pytest tests/ -q`
Expected: all pass (same count green as before this plan + new tests).

- [ ] **Step 2: All dashboard routes still render**

Re-run the 16-route curl loop from the HMI work (login → curl each `/dashboard/*` path + `/dashboard/quality`); expect all 200.

- [ ] **Step 3: Run the actual backfill (production data)**

From `/dashboard/quality` click «שפוט 200 שאלות אחרונות» (or `curl -X POST .../dashboard/eval/backfill`). Wait for completion (~200 Groq calls; rate-limit backoff may stretch this to 15-30 min). Then:

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT judge_verdict, count(*) FROM query_logs WHERE judge_verdict IS NOT NULL GROUP BY 1;"
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT failure_type, count(*) FROM query_logs WHERE failure_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;"
```

Expected: ~200 judged rows; failure ranking visible on `/dashboard/quality`.

- [ ] **Step 4: Commit any leftovers + report**

Summarize to user: pass rate on production data, top failure types, gold-set size, next-step recommendation for phase 2/3.
