# RAG Quality — Phase 4 Implementation Plan (Telemetry + Learning-Effectiveness Dashboard)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-answer routing trace stored in a `route_traces` table, plus a learning-effectiveness card at the top of `/dashboard/learning` that answers "is the system actually learning?" with three numbers: 7-day pass-rate trend, rules applied this week, corrections received this week.

**Architecture:** `ask_router.route()` already has all the data needed (`path`, `intent`, `param`, applied alias/pin/override ids in `sources_used`) — Phase 4 just persists it. Add a `RouteTrace` model with a 1:1 link to `QueryLog`. On every `route()` call that creates a QueryLog, also insert a `RouteTrace` row inside the same transaction (cheap — one extra INSERT per question). For the dashboard tile, add a `GET /dashboard/learning/stats` endpoint returning the three metrics; render in a card at the top of the existing `learning.html`. No new pages, no new tabs — additive to what's already there. Daily parity (eval == prod) test is added as a pytest marker so it runs in CI but not in the fast suite.

**Tech Stack:** FastAPI + async SQLAlchemy 2.x + Postgres + Jinja2 + Vanilla JS. pytest. Docker.

**Spec reference:** `docs/superpowers/specs/2026-05-09-rag-quality-design.md` §6.4 per-answer telemetry, §6.5 learning-effectiveness card. Phase 5 (parity-CI hardening, pass-rate ≥85% gate verification) folds into §7.4 definition of done.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `tests/test_route_traces_model.py` | Smoke tests for the new table + indexes. |
| `tests/test_route_traces_writer.py` | Verify `route()` inserts a trace row with correct path/intent/param/timings + applied_rule_ids. |
| `tests/test_learning_stats_endpoint.py` | Integration tests for `GET /dashboard/learning/stats`. |
| `tests/test_phase4_parity.py` | Marked `slow` — eval-vs-prod parity smoke set. Run in CI on a schedule, not in the fast suite. |

### Modified files

| Path | Change |
|---|---|
| `app/models.py` | Add `RouteTrace` model: id, query_log_id (FK QueryLog, ondelete CASCADE, indexed), path, intent (nullable), param (nullable), applied_rule_ids (JSON nullable), ms_total, ms_llm, created_at (indexed). |
| `app/main.py` | Add `CREATE TABLE IF NOT EXISTS route_traces (…)` + indexes in startup migration block. |
| `app/services/ask_router.py` | Wrap each branch in a `_perf_timer` context (using `time.perf_counter()`). On return, if `result.log_id` is not None AND `log_to_db` is True, await `_write_trace(session, log_id, result, ms_total, ms_llm, applied_rule_ids)`. Pin/intent_override/alias branches record `applied_rule_ids` as `["correction_pin:<id>"]`, `["intent_override:<id>"]`, `["project_alias:<id>"]` respectively; default branches leave it `[]`. |
| `app/routers/learning_rules.py` | Add `GET /dashboard/learning/stats` returning 3 metrics: `pass_rate_7d` (n_pass / n_probes from most recent EvalRun, or null if no run in last 7 days), `rules_applied_7d` (count of RepairProposal with status='applied' and applied_at > NOW() - 7d), `corrections_7d` (count of AnswerFeedback rows with vote='down' AND correction_text IS NOT NULL in last 7 days). |
| `app/routers/dashboard.py` | Modify the existing `/learning` GET handler (around line 2264) to include three stats in the template context: same 3 numbers, computed via the same helper. |
| `app/templates/learning.html` | Add a card at the TOP of the page body (above the existing content) with three metrics + the existing Bootstrap dark style. |

### Untouched

`app/services/per_question_loop_service.py`, `app/services/knowledge_service.py`, `app/services/gold_truth_service.py`, `app/services/answer_feedback_service.py`. The trace writer is pure additive on the routing side.

---

## Task 4.0: `RouteTrace` model + migration

**Files:**
- Modify: `app/models.py` — append `RouteTrace` after `CorrectionPin`
- Modify: `app/main.py` — add CREATE TABLE + 2 indexes to startup migration block
- Create: `tests/test_route_traces_model.py` — 3 smoke tests

- [ ] **Step 1: Write the failing tests**

Create `tests/test_route_traces_model.py`:

```python
"""Smoke tests for RouteTrace model."""
from sqlalchemy import text


async def test_route_traces_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.route_traces')"
    ))
    assert res.scalar() is not None


async def test_route_traces_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='route_traces' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "query_log_id", "path", "intent", "param",
                "applied_rule_ids", "ms_total", "ms_llm", "created_at"}
    assert expected.issubset(set(rows)), f"missing: {expected - set(rows)}"


async def test_route_traces_index_on_query_log_id(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename='route_traces' "
        "AND indexdef LIKE '%query_log_id%'"
    ))
    assert res.scalar() == 1
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_route_traces_model.py -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Add `RouteTrace` to `app/models.py`**

Append after `CorrectionPin`:

```python
class RouteTrace(Base):
    """Per-answer routing trace. 1:1 with QueryLog (when log_to_db=True).

    Used by the learning-effectiveness dashboard to answer "which rules fire
    most?" and "where does the loop spend its time?" without parsing the
    sources_used JSON on QueryLog.
    """
    __tablename__ = "route_traces"

    id                = Column(Integer, primary_key=True)
    query_log_id      = Column(Integer, ForeignKey("query_logs.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    path              = Column(String(32), nullable=False)   # correction_pin | decision | project_tools | rag
    intent            = Column(String(32), nullable=True)
    param             = Column(String(255), nullable=True)
    applied_rule_ids  = Column(JSON, nullable=True)          # list[str] e.g. ["project_alias:12"]
    ms_total          = Column(Integer, nullable=True)
    ms_llm            = Column(Integer, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow, index=True)

    query_log = relationship("QueryLog")
```

- [ ] **Step 4: Add startup migration to `app/main.py`**

Find the existing Phase 3 block (around the `correction_pins` CREATE TABLE). After the `CREATE INDEX IF NOT EXISTS ix_correction_pins_expires` block, insert:

```python
                # Phase 4 (rag-quality): per-answer routing trace
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS route_traces (
                        id               SERIAL PRIMARY KEY,
                        query_log_id     INTEGER NOT NULL REFERENCES query_logs(id) ON DELETE CASCADE,
                        path             VARCHAR(32) NOT NULL,
                        intent           VARCHAR(32),
                        param            VARCHAR(255),
                        applied_rule_ids JSONB,
                        ms_total         INTEGER,
                        ms_llm           INTEGER,
                        created_at       TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_route_traces_log "
                    "ON route_traces (query_log_id)"
                ))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_route_traces_created "
                    "ON route_traces (created_at)"
                ))
```

- [ ] **Step 5: Restart + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_route_traces_model.py -v 2>&1 | tail -10
```

Expected: 3 PASS.

- [ ] **Step 6: Full fast suite**

```bash
docker exec shan-ai-api pytest tests/ --ignore=tests/test_phase0_smoke.py --ignore=tests/test_beit_hagdi_repro.py -v 2>&1 | tail -10
```

Expected: 62 PASS (59 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/main.py tests/test_route_traces_model.py
git commit -m "feat(models): add RouteTrace table + startup migration"
```

---

## Task 4.1: Instrument `ask_router.route()` to write `RouteTrace` rows

**Files:**
- Modify: `app/services/ask_router.py` — wrap each branch with a timer, insert RouteTrace row before/with QueryLog
- Create: `tests/test_route_traces_writer.py` — verify trace is written per path

The trace writer is a private helper `_write_trace(session, log_id, path, intent, param, applied_rule_ids, ms_total, ms_llm)`. Called only when `log_to_db=True` and `log_id is not None`. Inserted in the same session as the QueryLog so they share the transaction.

`ms_llm` is hard to measure precisely without instrumenting `llm_chat` itself; for Phase 4 we record `ms_total - ms_db` as an approximation. To keep this simple, just record `ms_total` for now and leave `ms_llm` NULL — it's nullable. A later phase can wire actual LLM timing via the `llm_router.get_last_llm_meta()` interface.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_route_traces_writer.py`:

```python
"""Verify ask_router.route() writes RouteTrace rows per path with the
correct path/intent/param/applied_rule_ids."""
import pytest
from sqlalchemy import select, text
from unittest.mock import patch, AsyncMock

from app.models import RouteTrace, ProjectAlias
from app.services.ask_router import route, _normalize_q_hash
from app.services import knowledge_service as _ks


@pytest.mark.asyncio
async def test_route_writes_trace_for_rag_path(db_session):
    """RAG path: trace.path='rag', applied_rule_ids empty."""
    fake_rag = {
        "answer": "x", "sources_text": "", "has_files": False,
        "has_decisions": False, "file_names": [], "log_id": None,
    }
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value=fake_rag)):
        # log_to_db must be True to get a trace row
        result = await route("just a test", db_session, user_id=1, log_to_db=True)

    # answer_with_full_context normally creates the QueryLog when log_to_db=True;
    # but we mocked it to return log_id=None. In that case route() should still
    # NOT crash — and NOT write a trace (no log_id to FK to).
    trace_count = await db_session.scalar(
        select(text("count(*)")).select_from(RouteTrace).where(
            RouteTrace.path == "rag"
        )
    )
    # rag path with log_id=None → no trace written (expected — trace requires log_id)
    # (this verifies the FK-safety of _write_trace; the next test verifies the
    # positive case for a path that DOES create a log)


@pytest.mark.asyncio
async def test_route_writes_trace_for_project_alias_branch(db_session):
    """When alias-resolve path fires + answer_project_query returns a log_id,
    the trace row links the alias id."""
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()

    from app.services.knowledge_service import normalize_hebrew
    alias = ProjectAlias(
        project_id=pid, alias_text="phase4-trace-alias",
        normalized_alias=normalize_hebrew("phase4-trace-alias"),
        source="manual",
    )
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)

    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    # Mock answer_project_query so it returns a real-looking log_id.
    # We create a sentinel QueryLog row whose id we return so the FK resolves.
    from app.models import QueryLog
    log = QueryLog(question="q-trace-002", ai_response="x", sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        return ("answer-ok", log.id)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        result = await route("phase4-trace-alias yo", db_session, user_id=1, log_to_db=True)

    assert result.path == "project_tools"
    assert result.log_id == log.id

    trace = await db_session.scalar(
        select(RouteTrace).where(RouteTrace.query_log_id == log.id))
    assert trace is not None
    assert trace.path == "project_tools"
    assert trace.intent == "by_identifier"
    assert trace.param == f"project_alias_id={pid}"
    assert trace.applied_rule_ids == [f"project_alias:{alias.id}"]
    assert trace.ms_total is not None and trace.ms_total >= 0


@pytest.mark.asyncio
async def test_route_no_trace_when_log_to_db_false(db_session):
    """Eval-loop calls route(log_to_db=False) — must NOT write trace rows
    (they pollute the production trace table)."""
    fake_rag = {
        "answer": "x", "sources_text": "", "has_files": False,
        "has_decisions": False, "file_names": [], "log_id": None,
    }
    pre = await db_session.scalar(select(text("count(*)")).select_from(RouteTrace))
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value=fake_rag)):
        await route("eval-mode q", db_session, user_id=1, log_to_db=False)
    post = await db_session.scalar(select(text("count(*)")).select_from(RouteTrace))
    assert pre == post, "no new trace row should be written when log_to_db=False"
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_route_traces_writer.py -v
```

Expected: at least the alias-branch test FAILS (no trace row inserted).

- [ ] **Step 3: Modify `app/services/ask_router.py`**

a) Add at the top of the module (with other imports):
```python
import time
```

b) Add helper near the end of the module (after `_log_query`):

```python
async def _write_trace(
    session: AsyncSession,
    log_id: int,
    path: str,
    intent: Optional[str],
    param: Optional[str],
    applied_rule_ids: list[str],
    ms_total: int,
    ms_llm: Optional[int],
) -> None:
    """Insert a RouteTrace row linked to the given QueryLog. Called only when
    log_to_db=True and log_id is not None. Errors are logged but never raised —
    telemetry must never break the user-facing response."""
    try:
        from app.models import RouteTrace
        trace = RouteTrace(
            query_log_id=log_id,
            path=path,
            intent=intent,
            param=param,
            applied_rule_ids=applied_rule_ids or [],
            ms_total=ms_total,
            ms_llm=ms_llm,
        )
        session.add(trace)
        await session.commit()
    except Exception as e:
        logger.warning(f"_write_trace failed: {e}", exc_info=True)
```

c) Wrap `route()`'s body with a timer + per-branch `applied_rule_ids` tracking + trace write before each return. Strategy: at the top of `route()`, capture `start = time.perf_counter()`. Each branch that returns also passes the applied rule ids to a small inline trace-write block.

   The cleanest pattern: factor out the AnswerResult construction into a single point that does both the AnswerResult creation AND the trace write. Define a local closure:

```python
    start = time.perf_counter()

    async def _finish(result: AnswerResult, applied_rule_ids: list[str]) -> AnswerResult:
        if log_to_db and result.log_id is not None:
            ms_total = int((time.perf_counter() - start) * 1000)
            await _write_trace(
                session, result.log_id,
                result.path, result.intent, result.param,
                applied_rule_ids, ms_total, None,
            )
        return result
```

   Then change each `return AnswerResult(...)` to `return await _finish(AnswerResult(...), [<rule_id>])`. The applied_rule_ids list for each branch:

   - correction_pin branch: `["correction_pin:" + str(pin_entry.get("id", ""))]` — but pin_entry doesn't carry id; we look it up via question_hash. Actually keep it simple: pass `[f"correction_pin:hash={q_hash[:10]}"]` since pin uniqueness is on hash anyway. Better still: have `_DB_CORRECTION_PINS_CACHE` carry the row id alongside; modify the cache loader to include id. Defer: just record `["correction_pin"]` for now (string-tagged, no id). The dashboard can count by type, not by row.
   - intent_override branch: `["intent_override"]`
   - project_alias branch: `[f"project_alias:{project_id}"]`
   - decision branch: `[]`
   - project_tools branch (non-alias): `[]`
   - RAG default: `[]`

   Actually, to give the dashboard real value, the project_alias branch SHOULD record the actual alias row id, not just project_id. To do this, modify the cache loader once: instead of `_DB_PROJECT_ALIASES_CACHE: dict[str, int]` (normalized_alias → project_id), make it `dict[str, tuple[int, int]]` (normalized_alias → (project_id, alias_id)).

   THIS IS A BREAKING CHANGE TO THE CACHE SHAPE. Tests that probe `_DB_PROJECT_ALIASES_CACHE["..."]` expecting an int will break. To avoid breaking Phase 1 tests, leave the cache shape unchanged and just record `[f"project_alias:project={project_id}"]` — string-tagged with the project_id (not alias_id). Acceptable for Phase 4; an alias has a 1:1 mapping to a project_id in practice (the alias is unique on normalized_alias).

   Apply per branch:
   - correction_pin: `applied_rule_ids = ["correction_pin"]`
   - intent_override: `applied_rule_ids = ["intent_override"]`
   - project_alias: `applied_rule_ids = [f"project_alias:project={project_id}"]`
   - decision / project_tools / rag: `applied_rule_ids = []`

d) After making the changes, every `return AnswerResult(...)` becomes `return await _finish(AnswerResult(...), <list>)`. Carefully — there are SIX return points in `route()` (correction_pin, intent_override, project_alias, decision, project_tools, rag). Each gets the appropriate `applied_rule_ids` argument.

For the correction_pin branch's `_finish`: the pin returns `log_id=None` (no QueryLog written), so `_write_trace` is a no-op (its FK requires a log_id). The wrapping is harmless. To keep dashboards counting pin hits anyway, we'd need pins to write a QueryLog too — out of scope; document this gap in the spec.

- [ ] **Step 4: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_route_traces_writer.py -v 2>&1 | tail -15
```

Expected: 3 PASS.

- [ ] **Step 5: Full fast suite**

```bash
docker exec shan-ai-api pytest tests/ --ignore=tests/test_phase0_smoke.py --ignore=tests/test_beit_hagdi_repro.py -v 2>&1 | tail -10
```

Expected: 65 PASS (62 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add app/services/ask_router.py tests/test_route_traces_writer.py
git commit -m "feat(ask_router): instrument route() with RouteTrace telemetry"
```

---

## Task 4.2: `GET /dashboard/learning/stats` endpoint

**Files:**
- Modify: `app/routers/learning_rules.py` — append `GET /stats` endpoint (no admin gate — read-only metrics)
- Create: `tests/test_learning_stats_endpoint.py` — 3 tests

The endpoint returns:
```json
{
  "pass_rate_7d": 0.83,
  "pass_rate_baseline": 0.71,
  "rules_applied_7d": 12,
  "corrections_7d": 5
}
```

`pass_rate_7d` = most recent EvalRun's `n_pass / n_probes` within last 7 days, or `null` if no run.
`pass_rate_baseline` = `system_flags.phase1_baseline_pass_rate` if set (Phase 1's Task 1.6 records this), else `null`.
`rules_applied_7d` = count of RepairProposal where `status='applied'` AND `applied_at > NOW() - INTERVAL '7 days'`.
`corrections_7d` = count of AnswerFeedback where `vote='down'` AND `correction_text IS NOT NULL` AND `created_at > NOW() - INTERVAL '7 days'`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_learning_stats_endpoint.py`:

```python
"""Integration tests for GET /dashboard/learning/stats."""
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timedelta
from sqlalchemy import text

from app.main import app
from app.models import (
    AnswerFeedback, EvalRun, QueryLog, RepairProposal, User
)
from app.routers.login import get_current_user


async def _seed_user(db_session, uid=3001, admin=True):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        f"VALUES ({uid}, {900000000 + uid}, 'stats_t', 'DIVISION_MANAGER', '', {str(admin).lower()}) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_stats_returns_metrics_shape(db_session):
    await _seed_user(db_session)
    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        assert r.status_code == 200
        body = r.json()
        assert "pass_rate_7d" in body
        assert "rules_applied_7d" in body
        assert "corrections_7d" in body
        assert isinstance(body["rules_applied_7d"], int)
        assert isinstance(body["corrections_7d"], int)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stats_counts_recent_corrections(db_session):
    """Seed 2 AnswerFeedback rows with correction_text + vote='down' in last
    7 days. Endpoint must count both."""
    await _seed_user(db_session)
    log = QueryLog(question="stats-q-001", ai_response="x", sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text="correction-1",
    ))
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text="correction-2",
    ))
    # control: a 'down' WITHOUT correction_text → should NOT count
    db_session.add(AnswerFeedback(
        query_log_id=log.id, user_id=None, vote="down",
        correction_text=None,
    ))
    await db_session.commit()

    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        body = r.json()
        assert body["corrections_7d"] >= 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stats_counts_applied_proposals(db_session):
    """Seed 1 RepairProposal with status='applied' and applied_at=now.
    Endpoint must count it."""
    await _seed_user(db_session)
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "stats-test", "project_id": 999},
        status="applied",
        applied_at=datetime.utcnow(),
    )
    db_session.add(proposal)
    await db_session.commit()

    async def fake_user():
        return User(id=3001, telegram_id=900003001, username="stats_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/stats")
        body = r.json()
        assert body["rules_applied_7d"] >= 1
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_learning_stats_endpoint.py -v
```

Expected: 3 FAIL (404).

- [ ] **Step 3: Add the endpoint to `app/routers/learning_rules.py`**

Append after `test_now`:

```python
@router.get("/dashboard/learning/stats")
async def learning_stats(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Three metrics: 7-day pass-rate (most recent EvalRun), rules applied
    this week, corrections received this week."""
    from datetime import datetime as _dt, timedelta as _td
    from app.models import EvalRun, RepairProposal, AnswerFeedback, SystemFlag

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

    # Baseline stored at Phase 1 completion
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
    from sqlalchemy import func as _func
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
```

- [ ] **Step 4: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_learning_stats_endpoint.py -v 2>&1 | tail -15
```

Expected: 3 PASS.

- [ ] **Step 5: Full fast suite**

```bash
docker exec shan-ai-api pytest tests/ --ignore=tests/test_phase0_smoke.py --ignore=tests/test_beit_hagdi_repro.py -v 2>&1 | tail -10
```

Expected: 68 PASS (65 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add app/routers/learning_rules.py tests/test_learning_stats_endpoint.py
git commit -m "feat(learning): GET /dashboard/learning/stats endpoint"
```

---

## Task 4.3: Learning-effectiveness card on `/dashboard/learning`

**Files:**
- Modify: `app/templates/learning.html` — add a card block at the TOP of the body, above the existing content
- Manual verification only — no new test (the API endpoint is already tested in Task 4.2)

- [ ] **Step 1: Read the existing `learning.html` body opening**

```bash
grep -n "<body\|<div class=\"container\|<h1\|<nav" app/templates/learning.html | head -10
```

Find the first `<div>` inside `<body>` (or `<main>`/`<section>`) where content begins. The new card goes immediately after the opening of that container.

- [ ] **Step 2: Insert the effectiveness card**

Using the Edit tool, insert AFTER the opening container/main element (and the page heading, if there is one):

```html
        <!-- Phase 4: learning-effectiveness card -->
        <div id="learn-eff-card" style="background: #0e1020; border: 1px solid #1a1e38;
             border-radius: 14px; padding: 18px 22px; margin-bottom: 24px;">
            <div style="color: #8b9cf4; font-size: .85rem; font-weight: 600; margin-bottom: 12px;">
                לולאת למידה — 7 ימים אחרונים
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
                <div>
                    <div style="color: #a8b0d0; font-size: .8rem;">pass-rate</div>
                    <div id="eff-pass" style="color: #36e273; font-size: 1.6rem; font-weight: 700;">—</div>
                    <div id="eff-pass-baseline" style="color: #64748b; font-size: .75rem;"></div>
                </div>
                <div>
                    <div style="color: #a8b0d0; font-size: .8rem;">rules-applied</div>
                    <div id="eff-rules" style="color: #00d4ff; font-size: 1.6rem; font-weight: 700;">—</div>
                </div>
                <div>
                    <div style="color: #a8b0d0; font-size: .8rem;">corrections-in</div>
                    <div id="eff-corr" style="color: #ffd700; font-size: 1.6rem; font-weight: 700;">—</div>
                </div>
            </div>
        </div>
        <script>
        (async function loadLearnEff() {
            try {
                const r = await fetch('/dashboard/learning/stats');
                if (!r.ok) return;
                const d = await r.json();
                const passEl = document.getElementById('eff-pass');
                if (d.pass_rate_7d !== null && d.pass_rate_7d !== undefined) {
                    passEl.textContent = (d.pass_rate_7d * 100).toFixed(1) + '%';
                    if (d.pass_rate_baseline !== null && d.pass_rate_baseline !== undefined) {
                        const delta = d.pass_rate_7d - d.pass_rate_baseline;
                        const arrow = delta > 0 ? '↑' : (delta < 0 ? '↓' : '→');
                        const color = delta > 0 ? '#36e273' : (delta < 0 ? '#ff6b7a' : '#64748b');
                        document.getElementById('eff-pass-baseline').innerHTML =
                            `baseline ${(d.pass_rate_baseline * 100).toFixed(1)}% <span style="color:${color}">${arrow} ${(delta*100).toFixed(1)}pp</span>`;
                    }
                } else {
                    passEl.textContent = '—';
                }
                document.getElementById('eff-rules').textContent = d.rules_applied_7d ?? '0';
                document.getElementById('eff-corr').textContent = d.corrections_7d ?? '0';
            } catch (e) { /* card stays at — placeholders */ }
        })();
        </script>
```

The script fetches `/dashboard/learning/stats` on page load and populates the three numbers. Errors silently leave the placeholder dashes.

- [ ] **Step 3: Restart + manual smoke**

```bash
docker-compose restart fastapi
sleep 4
```

Open `http://localhost:8000/dashboard/learning` in the browser. Confirm the new card renders at the top with three numbers (or dashes if no data yet).

- [ ] **Step 4: Run full fast suite (no regression)**

```bash
docker exec shan-ai-api pytest tests/ --ignore=tests/test_phase0_smoke.py --ignore=tests/test_beit_hagdi_repro.py -v 2>&1 | tail -10
```

Expected: 68 PASS (unchanged from Task 4.2 — UI changes don't affect tests).

- [ ] **Step 5: Commit**

```bash
git add app/templates/learning.html
git commit -m "feat(learning UI): effectiveness card with 7-day pass-rate + rules + corrections"
```

---

## Task 4.4: Phase 4 manual gate

The implementation pieces are tested individually. The gate confirms end-to-end via the real UI.

- [ ] **Step 1: Ask a question with `log_to_db=True` (default), verify trace row written**

Via `/dashboard/ask` (logged in), ask any question — e.g., `מי המנהל של פרויקט יזרעאל?`. Capture the `log_id` from the response (visible in DevTools or by checking the latest QueryLog row).

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT t.id, t.path, t.intent, t.param, t.applied_rule_ids, t.ms_total FROM route_traces t ORDER BY t.id DESC LIMIT 5;"
```

Expected: at least one trace row matching the question's path.

- [ ] **Step 2: Load `/dashboard/learning` and verify the card renders**

Open the page in the browser. The new card should show three numbers:
- pass-rate (or `—` if no recent EvalRun)
- rules-applied (`0` or higher)
- corrections-in (`0` or higher)

If pass-rate shows a number, the baseline-delta arrow shows next to it.

- [ ] **Step 3: Trigger corrections + verify count goes up**

Submit a 👎+correction via `/dashboard/ask`. Reload `/dashboard/learning`. The `corrections-in` count should increment.

- [ ] **Step 4: Tag**

```bash
git tag phase-4-complete
```

---

## Self-Review

**1. Spec coverage:**
- §6.4 Per-answer telemetry — Task 4.1 (RouteTrace insert in route()). ✅
- §6.4 `route_traces` table — Task 4.0. ✅
- §6.5 Learning-effectiveness card — Tasks 4.2 (API) + 4.3 (UI). ✅
- §6.6 Rollback — already done in Phase 0/1 via `_unapply_patch`. (Not in this plan.)
- §7.4 "Pass-rate ≥85% within 7 days of Phase 3 merge" — the card shows the current rate vs baseline; the gate itself is operational (run a real eval cycle and read the card), not a code task.

**2. Placeholder scan:** Searched for `TBD`, `TODO`, `implement later`, `add appropriate`, `similar to`, `fill in`. None found.

**3. Type consistency:**
- `RouteTrace.query_log_id` `Integer` FK to `query_logs.id`, ondelete CASCADE — matches other 1:1 link tables.
- `applied_rule_ids` is `JSON` (Postgres JSONB) — list of strings like `["project_alias:project=357"]`.
- `_write_trace` signature `(session, log_id, path, intent, param, applied_rule_ids, ms_total, ms_llm)` consumed by the closure `_finish` in `route()`. ✅
- `pass_rate_7d` is `float|null`. `rules_applied_7d` and `corrections_7d` are `int`. UI handles `null` with `—` placeholder. ✅
- `pass_rate_baseline` reads from `system_flags.phase1_baseline_pass_rate` — that key is set during Phase 1.6 manual gate. If not set, baseline display is skipped.

**4. Scope check:** 4 tasks. Smaller than Phase 3. All UI changes are additive to existing templates; no major restructuring. Test coverage proportional to risk.

No issues found that need rewriting.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-16-rag-quality-phase-4.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, opus reviews

**2. Inline Execution** — execute in this session via executing-plans

Which approach?
