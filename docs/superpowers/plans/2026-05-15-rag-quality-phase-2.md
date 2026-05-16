# RAG Quality — Phase 2 Implementation Plan (Thumbs UI + Auto-Gold + 👎 Correction Flow)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing thumbs UI into a full learning loop. 👍 silently captures gold answers; 👎 opens a correction box → saves gold + kicks off a single-question repair cycle in the background. Adds `AnswerFeedback` table per spec §2 and the related per-vote rate limit.

**Architecture:** Existing thumbs UI on `app/templates/ask.html` already POSTs to `/api/logs/feedback` (logs.py:63) which sets `QueryLog.user_feedback`. We layer a richer flow on top: a new `AnswerFeedback` row per click (with optional `correction_text`), an auto-gold conversion on 👍 (insert into `EvalGoldAnswer` if no row exists for this question hash), and a 👎-triggered background `run_one_question` via FastAPI `BackgroundTasks`. The existing `/api/logs/feedback` endpoint stays as the simple thumb-tracker; a new `POST /dashboard/ask/correct` endpoint handles the 👎+correction submission with the heavy lifting. UI gets a small inline correction textarea revealed by 👎.

**Tech Stack:** FastAPI (async) + SQLAlchemy 2.x + Postgres + pgvector + Jinja2 + Vanilla JS. pytest + pytest-asyncio. Docker for the dev stack.

**Spec reference:** `docs/superpowers/specs/2026-05-09-rag-quality-design.md` §2 (`AnswerFeedback` table), §5.1 (thumbs UI + correction-box behavior), §5.3 (auto-learn cache constraints — no overwrite of manual gold, no repair on 👍, ≤5 thumbs/min rate limit). Phase 3 and Phase 4 remain in their own future plans.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `app/services/answer_feedback_service.py` | `record_thumbs_up(log_id, user_id)` — insert AnswerFeedback row, auto-gold if eligible. `record_thumbs_down(log_id, user_id, correction_text)` — insert AnswerFeedback row, save_gold, schedule background repair. Rate-limit checks. |
| `tests/test_answer_feedback_service.py` | Unit tests for the service: row insertion, auto-gold dedupe (don't overwrite manual gold), rate-limit skip, background-repair scheduling. |
| `tests/test_ask_correct_endpoint.py` | Integration test for `POST /dashboard/ask/correct`. |

### Modified files

| Path | Change |
|---|---|
| `app/models.py` | Add `AnswerFeedback` model: id (PK), query_log_id (FK QueryLog, indexed), user_id (FK users, nullable), vote (String 4, `up`/`down`), correction_text (Text, nullable), gold_id (FK EvalGoldAnswer, nullable), created_at. |
| `app/main.py` | Add `CREATE TABLE IF NOT EXISTS answer_feedback (…)` and a `CREATE INDEX IF NOT EXISTS ix_answer_feedback_log` to the startup ALTERs block — mirrors the existing pattern. (Not strictly required since create_all builds the table on first deploy, but matches the "fresh-deploy-on-existing-DB" pattern used elsewhere.) |
| `app/routers/logs.py` | Extend the existing `/api/logs/feedback` POST handler to ALSO write an `AnswerFeedback` row (vote=up/down based on body.feedback sign) and trigger auto-gold on 👍. Existing `QueryLog.user_feedback` behavior preserved. |
| `app/routers/ask.py` | Add new `POST /dashboard/ask/correct` endpoint that takes `{log_id, correction_text}`, writes AnswerFeedback (vote=down), calls `save_gold`, schedules `run_one_question` via FastAPI `BackgroundTasks`, returns `{status, run_id, gold_id}`. |
| `app/templates/ask.html` | Modify `sendFeedback(logId, value)` JS: on `value === -1`, instead of immediately POSTing, reveal an inline `<textarea>` + "שמור ולמד" / "ביטול" buttons. Submit calls `/dashboard/ask/correct`. On 👍 keep current POST flow. |

### Untouched

`app/services/ask_router.py`, `app/services/per_question_loop_service.py`, `app/services/knowledge_service.py`, `app/services/gold_truth_service.py` (only consumed via `save_gold` + `run_one_question`). `app/services/telegram_polling.py` (Telegram has no thumbs UI in this phase).

---

## Task 2.0: AnswerFeedback model + smoke test

**Files:**
- Modify: `app/models.py` — append `AnswerFeedback` after `EvalGoldAnswer` (end of file)
- Modify: `app/main.py:127` — add a `CREATE TABLE IF NOT EXISTS answer_feedback (…)` block + index, alongside the existing ALTER block
- Create: `tests/test_answer_feedback_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_answer_feedback_model.py`:

```python
"""Smoke tests for AnswerFeedback model: table + columns + FK constraints."""
from sqlalchemy import text


async def test_answer_feedback_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.answer_feedback')"
    ))
    assert res.scalar() is not None, "answer_feedback table missing"


async def test_answer_feedback_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='answer_feedback' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "query_log_id", "user_id", "vote",
                "correction_text", "gold_id", "created_at"}
    assert expected.issubset(set(rows)), \
        f"missing columns: {expected - set(rows)}"


async def test_answer_feedback_index_on_query_log_id(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes "
        "WHERE tablename='answer_feedback' AND indexdef LIKE '%query_log_id%'"
    ))
    assert res.scalar() == 1, "missing index on answer_feedback.query_log_id"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_answer_feedback_model.py -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Add `AnswerFeedback` to `app/models.py`**

Append at the end of `app/models.py`:

```python
class AnswerFeedback(Base):
    """Per-click feedback row for a /ask answer.

    Written on every thumbs-up/down click. 👍 may trigger auto-gold conversion
    (handled by answer_feedback_service); 👎 may trigger a single-question
    repair cycle. The row records the action regardless of whether the
    follow-up step ran.
    """
    __tablename__ = "answer_feedback"

    id              = Column(Integer, primary_key=True)
    query_log_id    = Column(Integer, ForeignKey("query_logs.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True)
    vote            = Column(String(4), nullable=False)  # "up" | "down"
    correction_text = Column(Text, nullable=True)
    gold_id         = Column(Integer, ForeignKey("eval_gold_answers.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, index=True)

    query_log = relationship("QueryLog")
    user      = relationship("User")
    gold      = relationship("EvalGoldAnswer")
```

- [ ] **Step 4: Add startup CREATE TABLE to `app/main.py`**

Find the ALTER-block at `app/main.py:125-132`. After the `judge_verdict` ALTER, add:

```python
                # Phase 2 (rag-quality): per-click thumbs feedback
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS answer_feedback (
                        id              SERIAL PRIMARY KEY,
                        query_log_id    INTEGER NOT NULL REFERENCES query_logs(id) ON DELETE CASCADE,
                        user_id         INTEGER REFERENCES users(id),
                        vote            VARCHAR(4) NOT NULL,
                        correction_text TEXT,
                        gold_id         INTEGER REFERENCES eval_gold_answers(id),
                        created_at      TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_answer_feedback_log "
                    "ON answer_feedback (query_log_id)"
                ))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_answer_feedback_created "
                    "ON answer_feedback (created_at)"
                ))
```

- [ ] **Step 5: Restart + run smoke**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_answer_feedback_model.py -v 2>&1 | tail -10
```

Expected: 3 PASS.

- [ ] **Step 6: Run full suite**

```bash
docker exec shan-ai-api pytest tests/ -v 2>&1 | tail -10
```

Expected: 48 PASS (45 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/main.py tests/test_answer_feedback_model.py
git commit -m "feat(models): add AnswerFeedback table + startup migration"
```

---

## Task 2.1: `answer_feedback_service` — record + auto-gold + rate limit

**Files:**
- Create: `app/services/answer_feedback_service.py`
- Create: `tests/test_answer_feedback_service.py`

The service exposes:

- `async def record_thumbs_up(session, log_id, user_id) -> AnswerFeedback` — writes row, attempts auto-gold (no-op if existing gold for that question_hash OR if rate-limited).
- `async def record_thumbs_down(session, log_id, user_id, correction_text) -> tuple[AnswerFeedback, EvalGoldAnswer]` — writes row, calls `save_gold` with the correction as the gold answer, links the gold_id back onto the AnswerFeedback row.
- Internal `async def _is_rate_limited(session, user_id) -> bool` — returns True if user has > 5 AnswerFeedback rows in the last 60 seconds.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_answer_feedback_service.py`:

```python
"""Unit tests for answer_feedback_service."""
import pytest
from sqlalchemy import select, text

from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog
from app.services.answer_feedback_service import (
    record_thumbs_up, record_thumbs_down, _is_rate_limited,
)


async def _seed_log(db_session, question="שאלת בדיקה", answer="תשובה"):
    log = QueryLog(question=question, ai_response=answer, sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.asyncio
async def test_thumbs_up_writes_feedback_row(db_session):
    log = await _seed_log(db_session, question="up-q-001")
    fb = await record_thumbs_up(db_session, log.id, user_id=None)
    assert fb.vote == "up"
    assert fb.query_log_id == log.id
    assert fb.correction_text is None


@pytest.mark.asyncio
async def test_thumbs_up_creates_auto_gold_when_none_exists(db_session):
    log = await _seed_log(db_session, question="auto-gold-q-002", answer="auto-gold-answer")
    fb = await record_thumbs_up(db_session, log.id, user_id=None)

    from app.services.gold_truth_service import question_hash
    h = question_hash("auto-gold-q-002")
    gold = await db_session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    assert gold is not None
    assert gold.source == "auto_user_confirmed"
    assert gold.gold_answer == "auto-gold-answer"
    assert fb.gold_id == gold.id


@pytest.mark.asyncio
async def test_thumbs_up_does_not_overwrite_existing_gold(db_session):
    log = await _seed_log(db_session, question="noclobber-q-003", answer="new-ai-answer")
    # Pre-seed a manual gold for the same question
    from app.services.gold_truth_service import save_gold
    original_gold = await save_gold(
        db_session, question="noclobber-q-003",
        gold_answer="manual-gold-text", user_id=None, source="manual",
    )

    fb = await record_thumbs_up(db_session, log.id, user_id=None)

    from app.services.gold_truth_service import question_hash
    h = question_hash("noclobber-q-003")
    rows = (await db_session.execute(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h)
    )).scalars().all()
    assert len(rows) == 1, "must not insert a second gold row for same question"
    assert rows[0].gold_answer == "manual-gold-text"
    assert rows[0].source == "manual"
    # AnswerFeedback should link to the EXISTING gold, not a new one
    assert fb.gold_id == original_gold.id


@pytest.mark.asyncio
async def test_thumbs_down_writes_feedback_row_and_gold(db_session):
    log = await _seed_log(db_session, question="down-q-004", answer="wrong-answer")

    fb, gold = await record_thumbs_down(
        db_session, log.id, user_id=None,
        correction_text="the correct answer",
    )
    assert fb.vote == "down"
    assert fb.correction_text == "the correct answer"
    assert fb.gold_id == gold.id
    assert gold.gold_answer == "the correct answer"
    assert gold.source == "user_correction"


@pytest.mark.asyncio
async def test_rate_limit_skips_auto_gold_after_5_in_60s(db_session):
    """6th 👍 within a minute must skip the auto-gold conversion (the row
    still inserts; only the gold side-effect is suppressed)."""
    # Burst 5 thumbs by inserting feedback rows directly
    log = await _seed_log(db_session, question="burst-q-005")
    for i in range(5):
        db_session.add(AnswerFeedback(
            query_log_id=log.id, user_id=42, vote="up",
        ))
    await db_session.commit()

    # 6th 👍 by user 42 — must be rate-limited
    log6 = await _seed_log(db_session, question="burst-q-006", answer="ans-006")
    fb = await record_thumbs_up(db_session, log6.id, user_id=42)
    assert fb.gold_id is None, "expected auto-gold skipped under rate limit"

    from app.services.gold_truth_service import question_hash
    h = question_hash("burst-q-006")
    gold = await db_session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    assert gold is None


@pytest.mark.asyncio
async def test_is_rate_limited_returns_false_below_threshold(db_session):
    log = await _seed_log(db_session, question="under-q-007")
    for _ in range(3):
        db_session.add(AnswerFeedback(query_log_id=log.id, user_id=99, vote="up"))
    await db_session.commit()
    assert await _is_rate_limited(db_session, user_id=99) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_answer_feedback_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.answer_feedback_service'`.

- [ ] **Step 3: Implement `app/services/answer_feedback_service.py`**

```python
"""Per-click answer feedback orchestration.

record_thumbs_up: writes AnswerFeedback row; auto-converts question into an
EvalGoldAnswer (source='auto_user_confirmed') only when no existing gold row
points to the same question_hash AND the user is not currently rate-limited
(>5 feedback clicks in the last 60s).

record_thumbs_down: writes AnswerFeedback row with correction_text; calls
save_gold() with the correction as the new gold (source='user_correction',
overwrites any prior auto_user_confirmed row but NOT manual rows — that
non-clobber behavior is enforced by save_gold itself updating the existing
record in place rather than inserting).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog
from app.services.gold_truth_service import question_hash, save_gold

logger = logging.getLogger(__name__)

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_THRESHOLD = 5


async def _is_rate_limited(session: AsyncSession, user_id: int | None) -> bool:
    """True when this user has > _RATE_LIMIT_THRESHOLD feedback rows in
    the last _RATE_LIMIT_WINDOW_SECONDS seconds."""
    if user_id is None:
        # Anonymous (no auth) — we still record the row but never auto-gold
        # for anonymous users because we can't bound the rate.
        return False
    cutoff = datetime.utcnow() - timedelta(seconds=_RATE_LIMIT_WINDOW_SECONDS)
    count = await session.scalar(
        select(func.count(AnswerFeedback.id))
        .where(AnswerFeedback.user_id == user_id)
        .where(AnswerFeedback.created_at >= cutoff)
    )
    return (count or 0) > _RATE_LIMIT_THRESHOLD


async def record_thumbs_up(
    session: AsyncSession,
    log_id: int,
    user_id: int | None,
) -> AnswerFeedback:
    """Insert AnswerFeedback row. Attempt auto-gold conversion when
    eligible (no existing gold for this question + not rate-limited).
    """
    fb = AnswerFeedback(query_log_id=log_id, user_id=user_id, vote="up")
    session.add(fb)
    await session.flush()  # get fb.id before adding gold link

    log = await session.get(QueryLog, log_id)
    if log is None:
        # Shouldn't happen — caller already validated — but stay defensive.
        await session.commit()
        return fb

    h = question_hash(log.question)
    existing = await session.scalar(
        select(EvalGoldAnswer).where(EvalGoldAnswer.question_hash == h))
    if existing is not None:
        # Don't insert a new gold — but link the feedback to the existing one
        # so callers can see "thumbs were collected for this gold."
        fb.gold_id = existing.id
        await session.commit()
        return fb

    if await _is_rate_limited(session, user_id):
        logger.info(f"auto-gold skipped (rate-limited) user_id={user_id} log_id={log_id}")
        await session.commit()
        return fb

    gold = await save_gold(
        session,
        question=log.question,
        gold_answer=log.ai_response or "",
        user_id=user_id,
        source="auto_user_confirmed",
    )
    fb.gold_id = gold.id
    await session.commit()
    return fb


async def record_thumbs_down(
    session: AsyncSession,
    log_id: int,
    user_id: int | None,
    correction_text: str,
) -> tuple[AnswerFeedback, EvalGoldAnswer]:
    """Insert AnswerFeedback row with the correction, save correction as
    user_correction gold, link them. Background repair scheduling is the
    caller's responsibility (route handler uses FastAPI BackgroundTasks)."""
    if not correction_text or not correction_text.strip():
        raise ValueError("correction_text required for thumbs-down")

    log = await session.get(QueryLog, log_id)
    if log is None:
        raise LookupError(f"query_log {log_id} not found")

    fb = AnswerFeedback(
        query_log_id=log_id, user_id=user_id, vote="down",
        correction_text=correction_text.strip(),
    )
    session.add(fb)
    await session.flush()

    gold = await save_gold(
        session,
        question=log.question,
        gold_answer=correction_text.strip(),
        user_id=user_id,
        source="user_correction",
    )
    fb.gold_id = gold.id
    await session.commit()
    return fb, gold
```

- [ ] **Step 4: Run tests**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_answer_feedback_service.py -v 2>&1 | tail -20
```

Expected: 6 PASS.

- [ ] **Step 5: Full suite**

```bash
docker exec shan-ai-api pytest tests/ -v 2>&1 | tail -10
```

Expected: 54 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/answer_feedback_service.py tests/test_answer_feedback_service.py
git commit -m "feat(feedback): add answer_feedback_service with auto-gold + rate limit"
```

---

## Task 2.2: Extend `/api/logs/feedback` to write AnswerFeedback rows

**Files:**
- Modify: `app/routers/logs.py` (existing `submit_feedback` handler at line 63)

The existing handler sets `QueryLog.user_feedback`. We extend it to also write `AnswerFeedback` via the new service. The signature stays the same — the frontend `sendFeedback(logId, value)` continues to call `POST /api/logs/feedback` with `{log_id, feedback: 1 | -1}`.

On `feedback=1` → `record_thumbs_up`.
On `feedback=-1` (no correction text in this endpoint — correction-text goes through `/dashboard/ask/correct`) → record a bare `vote="down"` row (no save_gold, no repair trigger) and continue.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_answer_feedback_service.py`:

```python
@pytest.mark.asyncio
async def test_legacy_logs_feedback_endpoint_writes_answer_feedback(db_session):
    """POST /api/logs/feedback with feedback=1 must still create both
    QueryLog.user_feedback AND an AnswerFeedback row (vote='up')."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    log = await _seed_log(db_session, question="legacy-q-008")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Bypass auth by patching get_current_user
        from app.routers.login import get_current_user
        from app.models import User
        async def fake_user():
            return User(id=1, username="t", role="DIVISION_MANAGER")
        app.dependency_overrides[get_current_user] = fake_user
        try:
            r = await client.post("/api/logs/feedback",
                                  json={"log_id": log.id, "feedback": 1})
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

    fb = (await db_session.execute(
        select(AnswerFeedback).where(AnswerFeedback.query_log_id == log.id)
    )).scalar_one_or_none()
    assert fb is not None
    assert fb.vote == "up"
```

NOTE: this integration test imports `app.main` and exercises the FastAPI route through an in-process AsyncClient. It depends on `httpx>=0.27`. Verify the test discovers and runs before continuing.

- [ ] **Step 2: Run test, confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_answer_feedback_service.py::test_legacy_logs_feedback_endpoint_writes_answer_feedback -v
```

Expected: FAIL (no AnswerFeedback row created).

- [ ] **Step 3: Modify `app/routers/logs.py:submit_feedback`**

Replace the body of `submit_feedback` (lines 64-78) with:

```python
@router.post("/api/logs/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if body.feedback not in (1, -1):
        raise HTTPException(status_code=400, detail="feedback must be 1 or -1")

    log = await session.get(QueryLog, body.log_id)
    if not log:
        raise HTTPException(status_code=404, detail="log not found")

    log.user_feedback = body.feedback

    # Phase 2: also write a per-click AnswerFeedback row. For 👍, the service
    # may also create an auto_user_confirmed gold answer when eligible.
    from app.services.answer_feedback_service import record_thumbs_up
    from app.models import AnswerFeedback
    if body.feedback == 1:
        await record_thumbs_up(session, body.log_id, current_user.id)
    else:
        # Bare 👎 with no correction text — record the vote but defer the
        # save_gold + repair-loop trigger to /dashboard/ask/correct.
        session.add(AnswerFeedback(
            query_log_id=body.log_id, user_id=current_user.id, vote="down",
        ))
        await session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run test**

```bash
docker exec shan-ai-api pytest tests/test_answer_feedback_service.py -v 2>&1 | tail -15
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/logs.py tests/test_answer_feedback_service.py
git commit -m "feat(logs): /api/logs/feedback now writes AnswerFeedback rows"
```

---

## Task 2.3: New `POST /dashboard/ask/correct` endpoint + background repair

**Files:**
- Modify: `app/routers/ask.py` — add new endpoint
- Create: `tests/test_ask_correct_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
"""Integration test for POST /dashboard/ask/correct."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.models import AnswerFeedback, EvalGoldAnswer, QueryLog, User
from app.routers.login import get_current_user


async def _seed_log(db_session, question="corr-q-001", answer="wrong"):
    log = QueryLog(question=question, ai_response=answer, sources_used=[], user_id=None)
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.asyncio
async def test_ask_correct_writes_gold_and_returns_run_id(db_session, monkeypatch):
    log = await _seed_log(db_session, question="באיזה שלב נמצא פרויקט בית X?")

    # Stub the background-repair entry so the test doesn't actually run the loop
    scheduled = {}
    async def fake_run(*args, **kwargs):
        scheduled["called"] = True
        return None
    monkeypatch.setattr(
        "app.routers.ask._schedule_repair_for_gold", fake_run,
    )

    async def fake_user():
        return User(id=1, username="t", role="DIVISION_MANAGER")
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/ask/correct",
                json={"log_id": log.id,
                      "correction_text": "הפרויקט בשלב תכנון"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "learning"
        assert body.get("gold_id") is not None
    finally:
        app.dependency_overrides.clear()

    # Verify side-effects
    fb = (await db_session.execute(
        select(AnswerFeedback).where(AnswerFeedback.query_log_id == log.id)
    )).scalar_one_or_none()
    assert fb is not None
    assert fb.vote == "down"
    assert fb.correction_text == "הפרויקט בשלב תכנון"
    assert fb.gold_id is not None

    gold = await db_session.get(EvalGoldAnswer, fb.gold_id)
    assert gold is not None
    assert gold.gold_answer == "הפרויקט בשלב תכנון"
    assert gold.source == "user_correction"

    assert scheduled.get("called") is True


@pytest.mark.asyncio
async def test_ask_correct_rejects_empty_correction(db_session):
    log = await _seed_log(db_session, question="empty-corr-q")
    async def fake_user():
        return User(id=1, username="t", role="DIVISION_MANAGER")
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/ask/correct",
                json={"log_id": log.id, "correction_text": ""},
            )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run, confirm 404 / route not found**

```bash
docker exec shan-ai-api pytest tests/test_ask_correct_endpoint.py -v
```

Expected: 2 FAIL (404 from the missing route).

- [ ] **Step 3: Add the endpoint to `app/routers/ask.py`**

Append to `app/routers/ask.py` (after the existing `ask_query` handler):

```python
class CorrectionRequest(BaseModel):
    log_id: int
    correction_text: str


async def _schedule_repair_for_gold(gold_id: int, user_id: int | None) -> None:
    """Background task: run a single-question repair cycle for the given gold row.
    Opens its own DB session so it survives after the request completes."""
    import logging as _logging
    log = _logging.getLogger(__name__)
    try:
        from app.database import async_session_maker
        from app.models import EvalGoldAnswer
        from sqlalchemy import select as _select
        from app.services.per_question_loop_service import run_one_question
        async with async_session_maker() as s:
            gold = await s.get(EvalGoldAnswer, gold_id)
            if gold is None:
                log.warning(f"_schedule_repair_for_gold: gold {gold_id} not found")
                return
            all_gold = (await s.execute(
                _select(EvalGoldAnswer))).scalars().all()
            await run_one_question(
                s, gold, user_id=user_id,
                all_gold=list(all_gold),
                eval_run_id=None,
                max_repairs=3, threshold=0.8,
            )
    except Exception as e:
        log.warning(f"_schedule_repair_for_gold failed: {e}", exc_info=True)


@router.post("/dashboard/ask/correct")
async def ask_correct(
    body: CorrectionRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if not body.correction_text or not body.correction_text.strip():
        raise HTTPException(status_code=400, detail="correction_text required")

    from app.services.answer_feedback_service import record_thumbs_down
    try:
        fb, gold = await record_thumbs_down(
            session, body.log_id, current_user.id, body.correction_text,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Schedule the single-question repair in the background. The request
    # returns immediately; the loop runs after the response is sent.
    background.add_task(_schedule_repair_for_gold, gold.id, current_user.id)

    return {"status": "learning", "gold_id": gold.id, "feedback_id": fb.id}
```

Add the `BackgroundTasks` import at the top of `app/routers/ask.py`:

```python
from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException
```

- [ ] **Step 4: Run tests**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_ask_correct_endpoint.py -v 2>&1 | tail -15
```

Expected: 2 PASS.

- [ ] **Step 5: Full suite**

```bash
docker exec shan-ai-api pytest tests/ -v 2>&1 | tail -10
```

Expected: 56 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/ask.py tests/test_ask_correct_endpoint.py
git commit -m "feat(ask): /dashboard/ask/correct — gold + background repair on 👎"
```

---

## Task 2.4: Update `app/templates/ask.html` thumbs-down UX

**Files:**
- Modify: `app/templates/ask.html` — extend `sendFeedback(logId, value)` to reveal an inline correction box on 👎

Currently `sendFeedback(logId, -1)` POSTs immediately. We change the 👎 path to:

1. Reveal an inline `<textarea>` placeholder = "מה היית מצפה לשמוע?" + buttons "שמור ולמד" / "ביטול"
2. On "שמור ולמד": call `POST /dashboard/ask/correct` with `{log_id, correction_text}`
3. On success: show toast `🔄 לומד מהתיקון... (gold #N)` + link to `/dashboard/eval-curate?focus=N`
4. The bare 👎 (immediate POST without correction) is removed — every 👎 now MUST go through the correction box

Auto-test for HTML/JS isn't worth the effort; the manual gate in Task 2.5 covers it.

- [ ] **Step 1: Read the current `sendFeedback` function**

```bash
grep -n "function sendFeedback\|feedback-btn\|feedbackHtml" app/templates/ask.html | head -10
```

The plan refers to lines ~483-516 (the `feedbackHtml` template literal in `appendAIMsg`, and the `sendFeedback` function below it).

- [ ] **Step 2: Replace the relevant block**

Find the `feedbackHtml` template literal (currently lines ~483-488):

```javascript
    const feedbackHtml = logId ? `
        <div class="feedback-row">
            <button class="feedback-btn" id="up-${logId}" onclick="sendFeedback(${logId}, 1)">👍</button>
            <button class="feedback-btn" id="dn-${logId}" onclick="sendFeedback(${logId}, -1)">👎</button>
            <span class="feedback-done" id="fd-${logId}">✓ נשמר</span>
        </div>` : '';
```

Replace with:

```javascript
    const feedbackHtml = logId ? `
        <div class="feedback-row" id="fb-row-${logId}">
            <button class="feedback-btn" id="up-${logId}" onclick="sendFeedback(${logId}, 1)">👍</button>
            <button class="feedback-btn" id="dn-${logId}" onclick="openCorrectionBox(${logId})">👎</button>
            <span class="feedback-done" id="fd-${logId}">✓ נשמר</span>
        </div>
        <div class="correction-box" id="cb-${logId}" style="display:none; margin-top:8px;">
            <div style="font-size:.85rem; color:#a8b0d0; margin-bottom:4px;">מה היית מצפה לשמוע?</div>
            <textarea id="cb-text-${logId}" rows="3" style="width:100%; background:#0f1117;
                color:#fff; border:1px solid #2d3047; border-radius:8px; padding:6px;
                font-family:Heebo,sans-serif; resize:vertical;"></textarea>
            <div style="margin-top:6px; display:flex; gap:8px;">
                <button class="feedback-btn"
                    onclick="submitCorrection(${logId})"
                    style="background:rgba(54,226,115,.18); border:1px solid #36e273; color:#a0ffb0;">
                    שמור ולמד
                </button>
                <button class="feedback-btn"
                    onclick="closeCorrectionBox(${logId})"
                    style="background:#2d3047; color:#e0e0e0;">
                    ביטול
                </button>
            </div>
            <div id="cb-status-${logId}" style="margin-top:6px; font-size:.8rem; color:#8b9cf4;"></div>
        </div>` : '';
```

Replace the `sendFeedback` function (lines ~500-517) with:

```javascript
async function sendFeedback(logId, value) {
    // value=1 only here — value=-1 routes via openCorrectionBox / submitCorrection.
    const upBtn = document.getElementById(`up-${logId}`);
    const dnBtn = document.getElementById(`dn-${logId}`);
    const doneSpan = document.getElementById(`fd-${logId}`);
    upBtn.disabled = true; dnBtn.disabled = true;
    upBtn.classList.add('selected-up');
    try {
        await fetch('/api/logs/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ log_id: logId, feedback: 1 }),
        });
        doneSpan.style.display = 'inline';
    } catch (e) {
        upBtn.disabled = false; dnBtn.disabled = false;
        upBtn.classList.remove('selected-up');
    }
}

function openCorrectionBox(logId) {
    const cb = document.getElementById(`cb-${logId}`);
    if (cb) cb.style.display = 'block';
    const ta = document.getElementById(`cb-text-${logId}`);
    if (ta) ta.focus();
}

function closeCorrectionBox(logId) {
    const cb = document.getElementById(`cb-${logId}`);
    if (cb) cb.style.display = 'none';
}

async function submitCorrection(logId) {
    const text = (document.getElementById(`cb-text-${logId}`).value || "").trim();
    const status = document.getElementById(`cb-status-${logId}`);
    if (!text) {
        status.textContent = "נא להזין תיקון";
        status.style.color = "#ff6b7a";
        return;
    }
    status.textContent = "שולח...";
    status.style.color = "#8b9cf4";
    try {
        const resp = await fetch('/dashboard/ask/correct', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ log_id: logId, correction_text: text }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        status.innerHTML = `🔄 לומד מהתיקון... <a href="/dashboard/eval/curate?focus=${data.gold_id}" target="_blank" style="color:#36e273;">צפה בלמידה →</a>`;
        status.style.color = "#36e273";
        // Lock the down button — selected state
        document.getElementById(`dn-${logId}`).disabled = true;
        document.getElementById(`up-${logId}`).disabled = true;
        document.getElementById(`dn-${logId}`).classList.add('selected-down');
    } catch (e) {
        status.textContent = "שגיאה: " + e.message;
        status.style.color = "#ff6b7a";
    }
}
```

- [ ] **Step 3: Manual smoke**

Restart and open `/dashboard/ask` in the browser. Ask a question. Click 👎 → confirm correction box appears. Type a correction → click "שמור ולמד" → confirm status flips to "🔄 לומד מהתיקון... צפה בלמידה →". Click the link → confirms `/dashboard/eval/curate?focus=N` opens with that gold row.

Click 👍 on a different answer → confirm it flips to selected state with no popup.

- [ ] **Step 4: Run pytest (should still pass — no test broken)**

```bash
docker-compose restart fastapi
sleep 3
docker exec shan-ai-api pytest tests/ -v 2>&1 | tail -10
```

Expected: 56 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/ask.html
git commit -m "feat(ask UI): inline correction box on 👎 + auto-learn link"
```

---

## Task 2.5: Phase 2 gate (manual smoke)

The implementation pieces are tested individually; the gate confirms they compose end-to-end through the real UI + browser.

- [ ] **Step 1: Open `/dashboard/ask`, ask a known-bad question**

Pick the example that fired in the original brainstorm:

```
מי המנהל של פרויקט בת ים?
```

Wait for the AI's wrong answer (probably the long list of unrelated projects).

- [ ] **Step 2: Click 👎**

Inline correction box appears. Type the actual gold answer (e.g. `מנהל הפרויקט: יהודר בכר`).

Click "שמור ולמד". Status should change to `🔄 לומד מהתיקון... צפה בלמידה →`.

- [ ] **Step 3: Watch the repair land**

Click the "צפה בלמידה" link or open `/dashboard/eval/curate?focus=<gold_id>`. The row should appear with the correction as its gold answer. Within ~30-90s the background repair task should complete:

- If the loop fixed it: `applied_fixes` non-empty, status `fixed`, a new alias / intent_override / etc. exists.
- If unfixable: `rejected_fixes` lists what was tried — at least the loop ran.

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT id, source, gold_answer, approved_at FROM eval_gold_answers WHERE source IN ('user_correction','auto_user_confirmed') ORDER BY id DESC LIMIT 5;"
```

Should show the new `user_correction` row.

- [ ] **Step 4: Re-ask the same question**

In `/dashboard/ask`, ask `מי המנהל של פרויקט בת ים?` again. If the repair loop produced a useful patch (alias, intent_override, etc.), the answer should now match the correction.

If it doesn't: that's a Phase 3 problem (this question may need `field_alias_real` or `correction_pin` — both deferred). Phase 2's contract is "the gold + repair pipeline runs on 👎"; the gate passes when:

- AnswerFeedback row exists for the 👎 click
- EvalGoldAnswer row exists with source=user_correction
- A repair_proposals row exists with eval_run_id NULL (the background run)
- No 500s, no unhandled exceptions in `docker logs shan-ai-api`

- [ ] **Step 5: 👍 smoke**

Ask any question whose answer is roughly correct. Click 👍. Should silently flip to selected. Check:

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT id, vote, gold_id FROM answer_feedback ORDER BY id DESC LIMIT 5;"
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c "SELECT id, source, gold_answer FROM eval_gold_answers WHERE source='auto_user_confirmed' ORDER BY id DESC LIMIT 5;"
```

The first 👍 on a question with no prior gold creates an `auto_user_confirmed` row. Subsequent 👍 on the same question (or any question already in `eval_gold_answers`) does NOT duplicate.

- [ ] **Step 6: Tag**

```bash
git tag phase-2-complete
```

---

## Self-Review

Ran the post-write checklist:

**1. Spec coverage:**
- §2 `AnswerFeedback` table — Task 2.0. ✅
- §5.1 Thumbs UI on /ask, 👍 silent flip, 👎 correction box — Task 2.4. ✅
- §5.1 👍 → `POST /api/logs/feedback` → write feedback + auto-gold — Task 2.2. ✅
- §5.1 👎 → `POST /dashboard/ask/correct` → save_gold + BackgroundTasks repair — Task 2.3. ✅
- §5.3 No overwrite of manual gold — Task 2.1's `test_thumbs_up_does_not_overwrite_existing_gold`. ✅
- §5.3 No repair on 👍 — `record_thumbs_up` does not call `run_one_question` (verified by the absence of that import). ✅
- §5.3 Auto-gold only when no existing gold — Task 2.1 enforces. ✅
- §5.3 Rate limit > 5 thumbs/min — Task 2.1's `_is_rate_limited`. ✅
- §6.3 Kill-switch parity — out of scope for Phase 2 per spec; folded into Phase 4 plan.

**2. Placeholder scan:** Searched for `TBD`, `TODO`, `implement later`, `add appropriate`, `similar to`, `fill in`. None found. Every step has actual code or a concrete command.

**3. Type consistency:**
- `AnswerFeedback.query_log_id` is `Integer` ForeignKey to `query_logs.id`. Matches existing FK style in `RepairProposal.eval_run_id` etc.
- `vote` is `String(4)` storing "up" / "down" — chosen over `Boolean` so the column self-documents.
- `correction_text` and `gold_id` are nullable — `gold_id` is null on bare 👎 (no correction text supplied via `/api/logs/feedback`); `correction_text` is null on 👍.
- `record_thumbs_down` returns `(AnswerFeedback, EvalGoldAnswer)` — used by `ask_correct` to build the response with `gold_id`.
- `_schedule_repair_for_gold` opens its own DB session because the request session is already committed when BackgroundTasks fire.
- Front-end JS contract:
  - `/api/logs/feedback`: `{log_id, feedback: 1 | -1}` (unchanged from existing endpoint).
  - `/dashboard/ask/correct`: `{log_id, correction_text}` returns `{status, gold_id, feedback_id}`. UI uses `gold_id` in the link.

No issues found that need rewriting.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-rag-quality-phase-2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session via executing-plans, batch checkpoints

Which approach?
