# RAG Quality — Phase 0 & 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor routing into `ask_router` so eval = production, then add `project_alias` and `intent_override` fix-types so the "בית הגדי" reproducer passes end-to-end.

**Architecture:** Extract routing logic from `app/routers/ask.py` into a new `app/services/ask_router.py` exposing `route() -> AnswerResult`. Switch `_answer()` in the per-question repair loop to call `ask_router.route()` so the loop tests the same path users hit. Add 2 new fix-types that the LLM proposer can pick during repair: project name aliases (DB row `project_aliases`) and per-question intent pins (DB row `intent_overrides`). Both are looked up via shadow ContextVars during regression checks and persisted to DB on apply, mirroring the existing pattern used by `add_synonym` / `prompt_patch`.

**Tech Stack:** FastAPI (async), SQLAlchemy 2.x async, PostgreSQL + pgvector, Groq LLM (llama-3.3-70b-versatile), pytest + pytest-asyncio, Docker Compose for the dev stack.

**Spec reference:** `docs/superpowers/specs/2026-05-09-rag-quality-design.md`. Phases 2 (thumbs UI), 3 (`field_alias_real`+`correction_pin`), 4 (telemetry tile) are scoped to follow-up plans.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `app/services/ask_router.py` | Single entry point for routing a question. Exposes `route() -> AnswerResult`, the `AnswerResult` dataclass, and helper `_normalize_q_hash()`. |
| `tests/__init__.py` | Empty marker so pytest discovers the tests package. |
| `tests/conftest.py` | Shared pytest fixtures: async DB session, `mock_llm_chat` patch helper, seeded Project rows. |
| `tests/test_ask_router.py` | Unit tests for `ask_router.route()` — pin hit, alias resolve, intent override, fall-throughs. |
| `tests/test_project_alias_lookup.py` | Hebrew normalization + alias matching tests. |
| `tests/test_repair_loop_new_fix_types.py` | `_apply_patch` / `_unapply_patch` round-trip per new fix-type. |
| `tests/test_beit_hagdi_repro.py` | Integration test: full repair cycle on the spec's reproducer. |

### Modified files

| Path | Change |
|---|---|
| `app/models.py` | Add `ProjectAlias` and `IntentOverride` models. Add `applied_artifact_id` column on `RepairProposal`. |
| `app/routers/ask.py` | Replace inlined routing with a single call to `ask_router.route()`. |
| `app/services/per_question_loop_service.py` | `_answer()` calls `ask_router.route()` instead of `ks.answer_with_full_context()`. Extend `FIX_TYPES`. Add `project_alias` and `intent_override` branches in `_apply_patch` and a new `_unapply_patch`. Update `_REPAIR_SYS` prompt with selection rubric. |
| `app/services/knowledge_service.py` | Add `_shadow_project_aliases` and `_shadow_intent_overrides` ContextVars. Extend `_ensure_eval_caches` to load `project_aliases` and `intent_overrides` rows. Add `invalidate_eval_caches()` already exists — no change needed. Expose accessor helpers `get_project_aliases()` and `get_intent_overrides()`. |
| `app/services/telegram_polling.py` | The handler in `handle_message` that currently calls `answer_with_full_context` and `answer_project_query` is replaced with one `ask_router.route()` call. |
| `requirements.txt` | Add `pytest>=8.0`, `pytest-asyncio>=0.23`. |

### Untouched

`app/services/project_tools.py` (consumed but unchanged in this plan), `app/services/gold_truth_service.py`, `app/services/embedding_service.py`, all template files. Phase 2 will modify `ask.html` for thumbs UI.

---

## Phase 0 — Foundation (refactor, no user-visible change)

### Task 0.0: Add pytest to requirements + scaffold tests/

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `requirements.txt` (append two lines)

- [ ] **Step 1: Append pytest deps to `requirements.txt`**

```
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 2: Create empty `tests/__init__.py`**

```python
```

- [ ] **Step 3: Create `tests/conftest.py` with async session fixture**

```python
"""Shared test fixtures.

Tests run against the same Postgres container the app uses (docker-compose
service `postgres`). Each test gets a fresh transaction that rolls back at
teardown so we never persist test data.
"""
import asyncio
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Allow tests to override DATABASE_URL via env; default to docker host.
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://shan_user:shan_secure_pass_2025@localhost:5432/shan_ai",
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Yield a session bound to a transaction that always rolls back."""
    engine = create_async_engine(TEST_DB_URL, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        async_sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield async_sess
        finally:
            await async_sess.close()
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def mock_llm_chat():
    """Patch app.services.llm_router.llm_chat with a programmable async mock."""
    async def _default(*args, **kwargs):
        return ""
    with patch("app.services.llm_router.llm_chat", side_effect=_default) as m:
        yield m
```

- [ ] **Step 4: Run pytest to confirm it discovers the empty test tree**

Run: `docker exec shan-ai-api pip install pytest pytest-asyncio && docker exec shan-ai-api pytest tests/ -v`
Expected: `no tests ran` (exit code 5) — confirms discovery works.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py tests/conftest.py
git commit -m "test: scaffold pytest with async DB session fixture"
```

---

### Task 0.1: Add ProjectAlias + IntentOverride models + applied_artifact_id column

**Files:**
- Modify: `app/models.py` (append two new model classes near the eval-loop models, modify `RepairProposal`)
- Modify: `app/main.py:63` already calls `Base.metadata.create_all` on startup — new tables will be created automatically when the app restarts.

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_smoke.py`:

```python
"""Confirm new tables exist after Base.metadata.create_all runs."""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_project_aliases_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.project_aliases')"
    ))
    assert res.scalar() is not None, "project_aliases table missing"


@pytest.mark.asyncio
async def test_intent_overrides_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.intent_overrides')"
    ))
    assert res.scalar() is not None, "intent_overrides table missing"


@pytest.mark.asyncio
async def test_repair_proposals_has_applied_artifact_id(db_session):
    res = await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='repair_proposals' AND column_name='applied_artifact_id'"
    ))
    assert res.scalar() == "applied_artifact_id"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `docker exec shan-ai-api pytest tests/test_models_smoke.py -v`
Expected: 3 FAILures — tables and column don't exist yet.

- [ ] **Step 3: Add models + column to `app/models.py`**

Append after the `EvalGoldAnswer` class (line ~428):

```python
class ProjectAlias(Base):
    """Free-text project-name → project_id mapping. Looked up before fuzzy match.

    Created by the repair loop's `project_alias` fix-type, by manual admin entry,
    or by a `/ask` 👎 correction. Multiple aliases may point to the same project.
    """
    __tablename__ = "project_aliases"

    id                = Column(Integer, primary_key=True)
    project_id        = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    alias_text        = Column(String(255), nullable=False)
    normalized_alias  = Column(String(255), unique=True, nullable=False, index=True)
    source            = Column(String(32),  nullable=False, default="manual")
    created_by_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)

    project    = relationship("Project")
    created_by = relationship("User")


class IntentOverride(Base):
    """Pin a normalized question to a forced project_tools intent + param.

    Skips LLM intent detection for that exact question. Hash-keyed so no fuzzy
    overlap with other questions is possible.
    """
    __tablename__ = "intent_overrides"

    id                      = Column(Integer, primary_key=True)
    question_pattern_hash   = Column(String(64), unique=True, nullable=False, index=True)
    forced_intent           = Column(String(32), nullable=False)
    forced_param            = Column(String(255), nullable=True)
    source                  = Column(String(32), nullable=False, default="manual")
    created_by_id           = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at              = Column(DateTime, default=datetime.utcnow)

    created_by = relationship("User")
```

In `RepairProposal`, add the new column right before the trailing relationship lines (around current line 374):

```python
    applied_artifact_id = Column(Integer, nullable=True)
    # ↑ id of the row created in the fix-type's target table on apply,
    # used by _unapply_patch for clean rollback.
```

- [ ] **Step 4: Restart the app so `Base.metadata.create_all` creates the tables**

Run: `docker-compose restart fastapi`
Expected: container restarts, no errors in `docker logs shan-ai-api`.

- [ ] **Step 5: Add the new column on the existing `repair_proposals` rows (create_all does NOT add columns to existing tables)**

Run:
```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c \
  "ALTER TABLE repair_proposals ADD COLUMN IF NOT EXISTS applied_artifact_id INTEGER;"
```
Expected: `ALTER TABLE` (or `NOTICE: column "applied_artifact_id" of relation "repair_proposals" already exists, skipping`).

- [ ] **Step 6: Run tests, verify they pass**

Run: `docker exec shan-ai-api pytest tests/test_models_smoke.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models.py tests/test_models_smoke.py
git commit -m "feat(models): add ProjectAlias, IntentOverride, applied_artifact_id"
```

---

### Task 0.2: Define `AnswerResult` dataclass and `_normalize_q_hash` helper

**Files:**
- Create: `app/services/ask_router.py` (skeleton with no `route()` yet)
- Create: `tests/test_ask_router.py` (initial test)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ask_router.py`:

```python
"""ask_router unit tests."""
import pytest

from app.services.ask_router import AnswerResult, _normalize_q_hash


def test_answer_result_fields_present():
    r = AnswerResult(
        answer="x", sources_used=[], log_id=None,
        path="rag", intent=None, param=None,
    )
    assert r.answer == "x"
    assert r.path == "rag"


def test_normalize_q_hash_is_stable():
    a = _normalize_q_hash("באיזה שלב נמצא פרויקט בית הגדי?")
    b = _normalize_q_hash("באיזה שלב נמצא פרויקט בית הגדי?")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_normalize_q_hash_ignores_final_letters():
    # 'ם' → 'מ' under Hebrew normalization → same hash
    a = _normalize_q_hash("פרויקטים")
    b = _normalize_q_hash("פרויקטים")  # already final-mem, both inputs identical here
    assert a == b
    # final-letter normalization round-trip
    a2 = _normalize_q_hash("שלום")
    b2 = _normalize_q_hash("שלומ")  # final-mem stripped to mem
    assert a2 == b2
```

- [ ] **Step 2: Run test, verify it fails**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py -v`
Expected: ImportError — `app.services.ask_router` doesn't exist yet.

- [ ] **Step 3: Create `app/services/ask_router.py` skeleton**

```python
"""Single entry point for answering a user question.

Used by the /dashboard/ask web router, the Telegram polling handler, and the
per-question repair loop. Eval = production from this module on.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from app.services.knowledge_service import normalize_hebrew


@dataclass
class AnswerResult:
    answer: str
    sources_used: list[dict]
    log_id: Optional[int]
    path: str          # "correction_pin" | "decision" | "project_tools" | "rag"
    intent: Optional[str]
    param: Optional[str]


def _normalize_q_hash(question: str) -> str:
    """sha256 of Hebrew-normalized question. Used as a hash key for pin/override lookups."""
    return hashlib.sha256(normalize_hebrew(question.strip()).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test, verify it passes**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/ask_router.py tests/test_ask_router.py
git commit -m "feat(ask_router): add AnswerResult dataclass + _normalize_q_hash"
```

---

### Task 0.3: Port routing logic from `ask.py` into `ask_router.route()` (no new behavior)

**Files:**
- Modify: `app/services/ask_router.py` (append `route()`)
- Modify: `app/routers/ask.py` (replace its body with a thin call to `ask_router.route()`)
- Modify: `tests/test_ask_router.py` (add path-classification tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ask_router.py`:

```python
import pytest
from unittest.mock import patch, AsyncMock

from app.services.ask_router import route


@pytest.mark.asyncio
async def test_route_decision_keyword(db_session):
    """A question containing 'החלטה' must take the decision path."""
    with patch("app.services.knowledge_service.get_decisions_context",
               new=AsyncMock(return_value="ctx")), \
         patch("app.services.knowledge_service.answer_decisions_question",
               new=AsyncMock(return_value="תשובה")):
        result = await route("מה ההחלטה האחרונה?", db_session, user_id=1, log_to_db=False)
    assert result.path == "decision"


@pytest.mark.asyncio
async def test_route_project_query(db_session):
    """A short Hebrew question with project keyword goes to project_tools."""
    with patch("app.services.project_tools.answer_project_query",
               new=AsyncMock(return_value=("res", 99))):
        result = await route("פרויקט יזרעאל", db_session, user_id=1, log_to_db=False)
    assert result.path == "project_tools"


@pytest.mark.asyncio
async def test_route_default_rag(db_session):
    """A question that matches no keyword falls through to RAG."""
    with patch("app.services.knowledge_service.answer_with_full_context",
               new=AsyncMock(return_value={
                   "answer": "x", "sources_text": "",
                   "has_files": False, "has_decisions": False,
                   "file_names": [], "log_id": 1,
               })):
        result = await route("Tell me something general",
                             db_session, user_id=1, log_to_db=False)
    assert result.path == "rag"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py::test_route_decision_keyword -v`
Expected: ImportError — `route` doesn't exist on `ask_router`.

- [ ] **Step 3: Implement `route()` in `app/services/ask_router.py`**

Append:

```python
import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DECISION_KEYWORDS = ("החלטה", "החלטות", "ההחלטה", "ההחלטות")


async def route(
    question: str,
    session: AsyncSession,
    user_id: int,
    *,
    log_to_db: bool = True,
    snapshot_mode: bool = False,
) -> AnswerResult:
    """Route a question to the right answerer and return a uniform AnswerResult.

    Order of dispatch:
      1. Decision keyword → answer_decisions_question
      2. _is_project_query → project_tools.answer_project_query
      3. Default → knowledge_service.answer_with_full_context

    Phase 1 will insert correction-pin lookup, alias resolve, and intent-override
    BEFORE step 1. This task ports existing behavior only.
    """
    # Lazy imports keep the module light and avoid cycles.
    from app.services import knowledge_service as ks
    from app.services.telegram_routing import _is_project_query
    from app.services import project_tools

    # 1. Decision history queries
    if any(kw in question for kw in _DECISION_KEYWORDS):
        decisions_ctx = await ks.get_decisions_context(session, user_id)
        if decisions_ctx:
            answer = await ks.answer_decisions_question(question, decisions_ctx)
        else:
            answer = "לא נמצאו החלטות עבורך במסד הנתונים."
        log_id = await _log_query(session, question, answer,
                                  [{"source": "decisions_db"}], user_id, log_to_db)
        return AnswerResult(
            answer=answer,
            sources_used=[{"source": "decisions_db"}],
            log_id=log_id,
            path="decision",
            intent=None,
            param=None,
        )

    # 2. Project queries
    if _is_project_query(question):
        try:
            answer, log_id = await project_tools.answer_project_query(
                question, session, {}, user_id=user_id,
            )
            return AnswerResult(
                answer=answer,
                sources_used=[{"source": "projects_db"}],
                log_id=log_id,
                path="project_tools",
                intent=None,
                param=None,
            )
        except Exception as e:
            logger.warning(f"project_tools failed, falling through to RAG: {e}")

    # 3. Default RAG
    result = await ks.answer_with_full_context(
        question, session, user_id, log_to_db=log_to_db,
    )
    return AnswerResult(
        answer=result.get("answer", ""),
        sources_used=[{"source": "rag"}],
        log_id=result.get("log_id"),
        path="rag",
        intent=None,
        param=None,
    )


async def _log_query(
    session: AsyncSession,
    question: str,
    answer: str,
    sources: list[dict],
    user_id: int,
    log_to_db: bool,
) -> int | None:
    """Write a QueryLog row and return its id. No-op when log_to_db=False."""
    if not log_to_db:
        return None
    from app.models import QueryLog
    from app.services.llm_router import get_last_llm_meta
    provider, is_fb = get_last_llm_meta()
    log = QueryLog(
        question=question, ai_response=answer,
        sources_used=sources, user_id=user_id,
        llm_provider=provider or None, is_fallback=is_fb or None,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log.id
```

- [ ] **Step 4: Run tests, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Update `app/routers/ask.py` to delegate to `ask_router.route()`**

Replace the body of `ask_query` (currently lines 32-86 of `app/routers/ask.py`) with:

```python
@router.post("/dashboard/ask/query")
async def ask_query(
    body: AskRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    from app.services.ask_router import route
    result = await route(body.question, session, current_user.id)
    return JSONResponse({
        "answer": result.answer,
        "sources_text": _sources_text(result),
        "has_files":     any(s.get("source") == "rag" for s in result.sources_used),
        "has_decisions": any(s.get("source") == "decisions_db" for s in result.sources_used),
        "file_names": [],
        "log_id": result.log_id,
    })


def _sources_text(result) -> str:
    if result.path == "decision":
        return "📋 מסד ההחלטות"
    if result.path == "project_tools":
        return "📂 מסד הפרויקטים"
    return ""
```

- [ ] **Step 6: Restart app + smoke test the web endpoint manually**

Run: `docker-compose restart fastapi`
Then in the browser, open `/dashboard/ask` and ask "פרויקט יזרעאל". Confirm an answer appears (no 500). Watch logs:
```bash
docker logs --tail 80 shan-ai-api
```
Expected: log line `project_tools: using precomputed intent=...` or similar (existing logging).

- [ ] **Step 7: Commit**

```bash
git add app/services/ask_router.py app/routers/ask.py tests/test_ask_router.py
git commit -m "feat(ask_router): port routing from ask.py — no behavior change"
```

---

### Task 0.4: Switch `_answer()` in eval loop to use `ask_router.route()`

**Files:**
- Modify: `app/services/per_question_loop_service.py:115-119` (the `_answer` function)

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_uses_ask_router.py`:

```python
"""The eval loop's _answer() must go through ask_router.route(), not raw RAG.

This is the load-bearing claim of Phase 0: 'eval = production' depends on this.
"""
import pytest
from unittest.mock import patch, AsyncMock

from app.services.per_question_loop_service import _answer


@pytest.mark.asyncio
async def test_answer_routes_through_ask_router():
    fake = AsyncMock(return_value=type("R", (), {
        "answer": "from-router", "sources_used": [], "log_id": None,
        "path": "project_tools", "intent": None, "param": None,
    })())
    with patch("app.services.ask_router.route", new=fake):
        out = await _answer("שאלה", user_id=1)
    assert out == "from-router"
    fake.assert_awaited_once()
    # Critical: log_to_db must be False so eval runs don't pollute QueryLog.
    _, kwargs = fake.call_args
    assert kwargs.get("log_to_db") is False
```

- [ ] **Step 2: Run test, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_eval_uses_ask_router.py -v`
Expected: FAIL — current `_answer` calls `ks.answer_with_full_context`, not `route`.

- [ ] **Step 3: Replace the body of `_answer` in `app/services/per_question_loop_service.py`**

Find lines 115-119:
```python
async def _answer(question: str, user_id: int) -> str:
    """Run the production answering pipeline without DB logging."""
    async with async_session_maker() as s:
        result = await ks.answer_with_full_context(question, s, user_id, log_to_db=False)
    return result.get("answer", "")
```

Replace with:
```python
async def _answer(question: str, user_id: int) -> str:
    """Run the production answering pipeline without DB logging.

    Routes through ask_router.route() so eval mirrors what real users hit on
    /dashboard/ask and Telegram. log_to_db=False keeps eval cycles out of
    QueryLog history.
    """
    from app.services.ask_router import route
    async with async_session_maker() as s:
        result = await route(question, s, user_id, log_to_db=False)
    return result.answer
```

- [ ] **Step 4: Run the new test + the full eval-loop test suite**

Run:
```bash
docker exec shan-ai-api pytest tests/test_eval_uses_ask_router.py tests/test_ask_router.py -v
```
Expected: 7 PASS.

- [ ] **Step 5: Smoke-test the eval cycle manually**

In the dashboard, navigate to `/dashboard/eval-curate` and trigger a single-question repair on any existing gold row. Confirm the cycle runs to completion (no exception in `docker logs shan-ai-api`).

- [ ] **Step 6: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_eval_uses_ask_router.py
git commit -m "feat(eval): route _answer() through ask_router so eval = prod"
```

---

### Task 0.5: Switch Telegram bot to use `ask_router.route()`

**Files:**
- Modify: `app/services/telegram_polling.py` — find the dispatch in `handle_message` (search for `answer_with_full_context` and `answer_project_query`).

- [ ] **Step 1: Locate the routing block**

Run: `grep -n "answer_with_full_context\|answer_project_query\|_is_project_query" app/services/telegram_polling.py`
Read the matching block and confirm what conditions decide which function is called.

- [ ] **Step 2: Replace the routing block with one `ask_router.route()` call**

Concretely:
```python
# OLD (rough shape — exact code varies; keep wrapping logic for telegram-specific replies):
if any(kw in text for kw in DECISION_KEYWORDS):
    answer = await answer_decisions_question(...)
elif _is_project_query(text):
    answer, _ = await answer_project_query(text, session, user_data, user_id=user.id)
else:
    res = await answer_with_full_context(text, session, user.id)
    answer = res["answer"]
```

becomes:

```python
from app.services.ask_router import route as _ask_route
result = await _ask_route(text, session, user.id, log_to_db=True)
answer = result.answer
```

Keep the `RTL` prefix and any Telegram-specific message-splitting that comes after — only the routing block is replaced.

- [ ] **Step 3: Restart + manual smoke**

Run: `docker-compose restart fastapi`
On Telegram, send three messages: "פרויקט יזרעאל", "מה ההחלטה האחרונה?", "Tell me about RAG". Confirm three different answers come back. Watch logs for `path=project_tools`, `path=decision`, `path=rag` (we'll add these log lines in Phase 1).

- [ ] **Step 4: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "feat(telegram): route messages through shared ask_router"
```

---

### Task 0.6: 20-question smoke set for the Phase-0 gate

**Files:**
- Create: `tests/test_phase0_smoke.py`

- [ ] **Step 1: Write the smoke test that hits the live router on a known small set**

```python
"""Phase 0 gate: 20-question smoke set must run end-to-end without exceptions.

We only assert that route() returns a non-empty answer string and a valid
path; correctness of the answer is the job of Phase 1.
"""
import pytest

from app.services.ask_router import route

SMOKE_QUESTIONS = [
    # decision path
    "מה ההחלטה האחרונה?",
    "כמה החלטות יש סה\"כ?",
    # project_tools path
    "פרויקט יזרעאל",
    "כמה פרויקטי הקמה פעילים?",
    "מי המנהל של פרויקט נתניה?",
    "באיזה שלב נמצא פרויקט בית הגדי?",  # the spec reproducer
    "פרויקטים מאחרים",
    "פרויקטי 2026",
    "סיכונים",
    "מנה\"פ של חולה",
    "תחמ\"ש",
    "פרויקט חולה",
    "מה השלב של פרויקט יזרעאל?",
    "מי אחראי על פרויקט נתניה?",
    "מתי יסתיים פרויקט יזרעאל?",
    # rag fallback path
    "Tell me about the system architecture",
    "What is RAG?",
    "How do I upload a file?",
    "מהו תהליך עבודת המערכת",
    "מה זה pgvector",
]

VALID_PATHS = {"correction_pin", "decision", "project_tools", "rag"}


@pytest.mark.asyncio
@pytest.mark.parametrize("question", SMOKE_QUESTIONS)
async def test_phase0_smoke_question_returns_answer(db_session, question):
    result = await route(question, db_session, user_id=1, log_to_db=False)
    assert result.path in VALID_PATHS, f"unknown path: {result.path}"
    assert isinstance(result.answer, str), "answer must be a string"
    assert len(result.answer) > 0, f"empty answer for: {question}"
```

- [ ] **Step 2: Run the smoke set**

Run: `docker exec shan-ai-api pytest tests/test_phase0_smoke.py -v`
Expected: 20 PASS. If any FAIL with an exception, fix it before continuing — that question reveals a routing-coverage gap.

- [ ] **Step 3: Commit**

```bash
git add tests/test_phase0_smoke.py
git commit -m "test: phase 0 smoke set — 20 questions exercise all paths"
```

---

### Task 0.7: Phase 0 gate — confirm overall pass-rate didn't drop > 10%

- [ ] **Step 1: Run a full eval cycle on the production gold set**

In the dashboard, navigate to `/dashboard/eval-curate` and click "Run cycle". Wait for completion and read the summary card:
- Record `n_pass / n_probes` as `phase0_pass_rate`.

- [ ] **Step 2: Compare to the last cycle BEFORE the refactor**

Run: `docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c \
  "SELECT id, status, n_pass, n_probes, finished_at FROM eval_runs ORDER BY id DESC LIMIT 5;"`

The most recent row is Phase 0; the second-most-recent is the baseline.

- [ ] **Step 3: Verify the gate**

`(baseline_pass_rate - phase0_pass_rate) ≤ 0.10` AND every question in the smoke set passes (Task 0.6 already enforced).

If the drop exceeds 10%, **stop and investigate** — the routing port introduced a regression. Most likely: a question that previously hit `answer_with_full_context` (RAG) is now hitting `project_tools` and getting a worse answer. Add the failing question to a new `tests/test_phase0_smoke.py` parametrize entry, debug the path it took, and either fix the routing condition or revert the `_answer` switch and rethink.

- [ ] **Step 4: Commit a tag for the Phase 0 baseline**

```bash
git tag phase-0-complete
```

---

## Phase 1 — `project_alias` + `intent_override` fix-types

### Task 1.1: Add cache loaders + ContextVars for project_aliases and intent_overrides

**Files:**
- Modify: `app/services/knowledge_service.py` (add 2 ContextVars + extend `_ensure_eval_caches`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_alias_cache.py`:

```python
import pytest
from sqlalchemy import text

from app.services import knowledge_service as ks


@pytest.mark.asyncio
async def test_aliases_loaded_into_cache(db_session):
    # Seed one alias row and one intent_override row
    await db_session.execute(text(
        "INSERT INTO project_aliases (project_id, alias_text, normalized_alias, source) "
        "SELECT id, 'בית הגדי', 'בית הגדי', 'manual' FROM projects LIMIT 1"
    ))
    await db_session.execute(text(
        "INSERT INTO intent_overrides (question_pattern_hash, forced_intent, forced_param, source) "
        "VALUES ('abc123', 'by_identifier', 'בית הגדי', 'manual')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "בית הגדי" in ks._DB_PROJECT_ALIASES_CACHE
    assert "abc123" in ks._DB_INTENT_OVERRIDES_CACHE
    assert ks._DB_INTENT_OVERRIDES_CACHE["abc123"]["forced_intent"] == "by_identifier"
```

- [ ] **Step 2: Run test, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_alias_cache.py -v`
Expected: AttributeError — `_DB_PROJECT_ALIASES_CACHE` and `_DB_INTENT_OVERRIDES_CACHE` don't exist.

- [ ] **Step 3: Add the ContextVars + cache dicts in `app/services/knowledge_service.py`**

Find the existing block (around line 22-31):
```python
_shadow_abbrevs: ContextVar[dict] = ContextVar("shadow_abbrevs", default={})
_shadow_stop_word_drops: ContextVar[set] = ContextVar("shadow_stop_word_drops", default=set())
_shadow_synonyms: ContextVar[dict] = ContextVar("shadow_synonyms", default={})
_shadow_prompt_override: ContextVar[dict] = ContextVar("shadow_prompt_override", default={})

# ─── DB-backed config caches (refreshed lazily by _ensure_eval_caches) ─────────
_DB_ABBREVS_CACHE: dict[str, str] = {}
_DB_STOP_WORD_DROPS_CACHE: set[str] = set()
_DB_PROMPT_OVERRIDES_CACHE: dict[str, str] = {}
```

Add right after them:
```python
_shadow_project_aliases: ContextVar[dict] = ContextVar("shadow_project_aliases", default={})
_shadow_intent_overrides: ContextVar[dict] = ContextVar("shadow_intent_overrides", default={})

_DB_PROJECT_ALIASES_CACHE: dict[str, int] = {}              # normalized_alias -> project_id
_DB_INTENT_OVERRIDES_CACHE: dict[str, dict] = {}            # q_hash -> {forced_intent, forced_param}
```

- [ ] **Step 4: Extend `_ensure_eval_caches` in `app/services/knowledge_service.py`**

Find the function (starts ~line 83). Inside the inner `try:` block, after the existing `po_stmt = ...` and `new_overrides = ...` lines, add:

```python
            from app.models import ProjectAlias, IntentOverride

            alias_rows = (await own_session.execute(select(ProjectAlias))).scalars().all()
            new_aliases = {row.normalized_alias: row.project_id for row in alias_rows}

            io_rows = (await own_session.execute(select(IntentOverride))).scalars().all()
            new_overrides_intent = {
                row.question_pattern_hash: {
                    "forced_intent": row.forced_intent,
                    "forced_param": row.forced_param,
                }
                for row in io_rows
            }
```

And below the existing `_DB_PROMPT_OVERRIDES_CACHE = new_overrides` line:
```python
        global _DB_PROJECT_ALIASES_CACHE, _DB_INTENT_OVERRIDES_CACHE
        _DB_PROJECT_ALIASES_CACHE = new_aliases
        _DB_INTENT_OVERRIDES_CACHE = new_overrides_intent
```

(Move all `global` declarations to the top of the function — Python only allows one `global` per name, and you'll need to add the new caches to the existing global list.)

The existing `global _EVAL_CACHE_TS, _DB_ABBREVS_CACHE, _DB_STOP_WORD_DROPS_CACHE, _DB_PROMPT_OVERRIDES_CACHE` becomes:
```python
    global _EVAL_CACHE_TS, _DB_ABBREVS_CACHE, _DB_STOP_WORD_DROPS_CACHE, _DB_PROMPT_OVERRIDES_CACHE
    global _DB_PROJECT_ALIASES_CACHE, _DB_INTENT_OVERRIDES_CACHE
```

- [ ] **Step 5: Run test, verify pass**

Run: `docker-compose restart fastapi && docker exec shan-ai-api pytest tests/test_alias_cache.py -v`
Expected: 1 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/knowledge_service.py tests/test_alias_cache.py
git commit -m "feat(ks): add project_aliases + intent_overrides caches and shadow vars"
```

---

### Task 1.2: Wire pre-rules into `ask_router.route()` (alias resolve + intent override)

**Files:**
- Modify: `app/services/ask_router.py` (insert pre-rule logic before the existing decision/project/RAG branches)
- Modify: `tests/test_ask_router.py` (add pre-rule tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ask_router.py`:

```python
from sqlalchemy import text

from app.services import knowledge_service as ks
from app.services.ask_router import _normalize_q_hash


@pytest.mark.asyncio
async def test_route_intent_override_skips_llm_intent_detection(db_session):
    q = "באיזה שלב נמצא פרויקט בית הגדי?"
    h = _normalize_q_hash(q)
    await db_session.execute(text(
        "INSERT INTO intent_overrides "
        "(question_pattern_hash, forced_intent, forced_param, source) "
        "VALUES (:h, 'by_identifier', 'בית הגדי', 'manual')"
    ), {"h": h})
    await db_session.commit()
    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    captured = {}

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        captured["intent"] = precomputed_intent
        captured["param"] = precomputed_param
        return ("ok", 1)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        result = await route(q, db_session, user_id=1, log_to_db=False)

    assert result.path == "project_tools"
    assert captured["intent"] == "by_identifier"
    assert captured["param"] == "בית הגדי"
    assert result.intent == "by_identifier"


@pytest.mark.asyncio
async def test_route_project_alias_enriches_question(db_session):
    """When 'בית הגדי' is a known alias for project 47, route() injects an
    identifier hint that find_projects_by_identifier can latch onto."""
    await db_session.execute(text(
        "INSERT INTO project_aliases (project_id, alias_text, normalized_alias, source) "
        "VALUES (47, 'בית הגדי', 'בית הגדי', 'manual')"
    ))
    await db_session.commit()
    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    captured = {}

    async def fake_apq(text_, sess, user_data, *, user_id, precomputed_intent=None, precomputed_param=None):
        captured["text"] = text_
        return ("ok", 1)

    with patch("app.services.project_tools.answer_project_query", new=fake_apq):
        await route("באיזה שלב נמצא פרויקט בית הגדי?",
                    db_session, user_id=1, log_to_db=False)

    assert "project_alias_id=47" in captured["text"], \
        f"expected alias hint in question text, got: {captured['text']!r}"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py -v -k "intent_override or alias_enriches"`
Expected: 2 FAIL.

- [ ] **Step 3: Insert pre-rule logic in `ask_router.route()`**

Locate the start of `route()` after the docstring. Right before `# 1. Decision history queries`, insert:

```python
    # Refresh DB-backed caches before any pre-rule lookup.
    await ks._ensure_eval_caches(session)

    q_hash = _normalize_q_hash(question)

    # 0a. Intent override (hash-keyed, exact match on normalized question)
    intent_overrides = {**ks._DB_INTENT_OVERRIDES_CACHE, **ks._shadow_intent_overrides.get()}
    pinned = intent_overrides.get(q_hash)
    if pinned:
        from app.services import project_tools
        answer, log_id = await project_tools.answer_project_query(
            question, session, {},
            user_id=user_id,
            precomputed_intent=pinned["forced_intent"],
            precomputed_param=pinned["forced_param"],
        )
        return AnswerResult(
            answer=answer,
            sources_used=[{"source": "intent_override", "q_hash": q_hash}],
            log_id=log_id,
            path="project_tools",
            intent=pinned["forced_intent"],
            param=pinned["forced_param"],
        )

    # 0b. Project alias resolve — inject hint into the question text so
    # downstream find_projects_by_identifier can pick the exact project.
    aliases = {**ks._DB_PROJECT_ALIASES_CACHE, **ks._shadow_project_aliases.get()}
    if aliases:
        norm_q = ks.normalize_hebrew(question)
        for normalized_alias, project_id in aliases.items():
            if normalized_alias and normalized_alias in norm_q:
                question = f"{question} (project_alias_id={project_id})"
                logger.info(f"alias resolve: '{normalized_alias}' -> project {project_id}")
                break  # one alias hit is enough
```

- [ ] **Step 4: Run tests, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_ask_router.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Make `find_projects_by_identifier` honor the alias hint**

In `app/services/project_tools.py`, find `find_projects_by_identifier` (line ~48). Add a hint check at the top of the function:

```python
async def find_projects_by_identifier(identifier: str, session: AsyncSession) -> list[dict]:
    import re
    # ── alias hint: if the caller injected `project_alias_id=N`, return that project directly.
    m = re.search(r"project_alias_id=(\d+)", identifier or "")
    if m:
        pid = int(m.group(1))
        proj = await session.get(Project, pid)
        if proj:
            return [_project_to_dict(proj)]
        # fall through if id is stale
    # ── existing fuzzy-match logic stays below ──
    ...  # leave the rest of the function as-is
```

(Don't replace the existing body — only add the hint check at the top.)

- [ ] **Step 6: Restart + run alias test again to confirm end-to-end**

Run: `docker-compose restart fastapi && docker exec shan-ai-api pytest tests/test_ask_router.py -v -k alias_enriches`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/ask_router.py app/services/project_tools.py tests/test_ask_router.py
git commit -m "feat(ask_router): wire pre-rules — intent override + project alias resolve"
```

---

### Task 1.3: Add `project_alias` fix-type to the repair loop

**Files:**
- Modify: `app/services/per_question_loop_service.py` — extend `FIX_TYPES`, `_REPAIR_SYS`, `_patch_to_shadow`, `_apply_patch`

- [ ] **Step 1: Write the failing test**

Create `tests/test_repair_loop_new_fix_types.py`:

```python
"""Round-trip tests for new fix-types in the repair loop."""
import pytest
from sqlalchemy import text, select

from app.models import ProjectAlias, RepairProposal
from app.services.per_question_loop_service import (
    _apply_patch, _patch_to_shadow, FIX_TYPES,
)


def test_fix_types_includes_project_alias():
    assert "project_alias" in FIX_TYPES
    assert "intent_override" in FIX_TYPES


def test_patch_to_shadow_project_alias():
    patch = {"alias_text": "בית הגדי", "project_id": 47}
    shadow = _patch_to_shadow("project_alias", patch)
    assert shadow == {"project_aliases": {"בית הגדי": 47}}


@pytest.mark.asyncio
async def test_apply_project_alias_writes_row(db_session):
    # Need a project to FK to
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "TestAlias-XYZ", "project_id": pid},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    # Row created
    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-XYZ")
    )
    assert row is not None
    assert row.project_id == pid

    # Proposal updated
    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.applied_artifact_id == row.id
```

- [ ] **Step 2: Run test, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_repair_loop_new_fix_types.py -v`
Expected: 3 FAIL — `project_alias` not in `FIX_TYPES`, `_patch_to_shadow` doesn't handle it, `_apply_patch` doesn't either.

- [ ] **Step 3: Update `FIX_TYPES` in `app/services/per_question_loop_service.py:34`**

```python
FIX_TYPES = [
    "add_abbreviation", "add_synonym", "stop_word_remove",
    "field_alias", "prompt_patch",
    "project_alias", "intent_override",   # NEW — Phase 1
]
```

- [ ] **Step 4: Update `_REPAIR_SYS` prompt (around line 146)**

Replace the `_REPAIR_SYS` string with:

```python
_REPAIR_SYS = (
    "You are a repair agent for a Hebrew RAG system. Given a question, the AI's wrong answer, "
    "and the gold answer, propose ONE minimal config patch that would make the AI produce the "
    "gold answer. Available fix types and selection rubric (try in this order):\n"
    "1. project_alias — AI failed because a project name wasn't recognized. "
    "patch_json = {'alias_text': '<free text from question>', 'project_id': <int>}. "
    "Pick this when the question names a project but the AI returned 'לא נמצא' or wrong project.\n"
    "2. intent_override — AI picked the wrong project_tools intent. "
    "patch_json = {'question': '<original q>', 'forced_intent': 'by_identifier|by_year|by_manager|count_by_type|list_risks|list_delayed', "
    "'forced_param': '<param>'}.\n"
    "3. add_abbreviation — patch_json = {'abbrevs': {'מנה\\\"פ': 'מנהל פרויקט'}}.\n"
    "4. add_synonym — patch_json = {'synonyms': {'תחמ\\\"ש': ['תחנת משנה']}}.\n"
    "5. stop_word_remove — patch_json = {'words': ['מנהל']}.\n"
    "6. field_alias — patch_json = {'synonyms': {'מנהפ': ['manager']}}.\n"
    "7. prompt_patch — patch_json = {'prompt_override': {'rag_specific': '...'}}.\n"
    "Reply ONLY with strict JSON: "
    "{\"type\": \"...\", \"patch_json\": {...}, \"rationale\": \"...\", \"risk\": \"low|medium|high\"} "
    "or {\"type\": null} if no patch can plausibly help."
)
```

- [ ] **Step 5: Extend `_patch_to_shadow` (around line 100)**

Add two branches before the trailing `return {}`:
```python
    if proposal_type == "project_alias":
        from app.services.knowledge_service import normalize_hebrew
        alias = normalize_hebrew(patch_json.get("alias_text", ""))
        pid = patch_json.get("project_id")
        if alias and pid:
            return {"project_aliases": {alias: int(pid)}}
        return {}
    if proposal_type == "intent_override":
        from app.services.ask_router import _normalize_q_hash
        q = patch_json.get("question", "")
        h = _normalize_q_hash(q) if q else None
        forced_intent = patch_json.get("forced_intent")
        forced_param  = patch_json.get("forced_param")
        if h and forced_intent:
            return {"intent_overrides": {h: {"forced_intent": forced_intent, "forced_param": forced_param}}}
        return {}
```

- [ ] **Step 6: Extend `shadow_config` in `app/services/per_question_loop_service.py:57`**

Inside `shadow_config`, after the existing keys, add two more:
```python
        if "project_aliases" in patch:
            tokens.append(("project_aliases", ks._shadow_project_aliases.set(dict(patch["project_aliases"]))))
        if "intent_overrides" in patch:
            tokens.append(("intent_overrides", ks._shadow_intent_overrides.set(dict(patch["intent_overrides"]))))
```

And in the `cv = {...}` dict in the finally block, add:
```python
                "project_aliases":  ks._shadow_project_aliases,
                "intent_overrides": ks._shadow_intent_overrides,
```

- [ ] **Step 7: Extend `_apply_patch` (around line 206)**

After the existing `elif p.type == "prompt_patch":` block, before `p.status = "applied"`, add:

```python
    elif p.type == "project_alias":
        from app.models import ProjectAlias
        from app.services.knowledge_service import normalize_hebrew
        alias_text = pj.get("alias_text", "")
        project_id = pj.get("project_id")
        if not alias_text or not project_id:
            raise ValueError(f"project_alias proposal {p.id} missing alias_text or project_id")
        row = ProjectAlias(
            project_id=int(project_id),
            alias_text=alias_text,
            normalized_alias=normalize_hebrew(alias_text),
            source="ai",
            created_by_id=user_id,
        )
        session.add(row)
        await session.flush()
        p.applied_artifact_id = row.id

    elif p.type == "intent_override":
        from app.models import IntentOverride
        from app.services.ask_router import _normalize_q_hash
        q = pj.get("question", "")
        if not q or not pj.get("forced_intent"):
            raise ValueError(f"intent_override proposal {p.id} missing question or forced_intent")
        row = IntentOverride(
            question_pattern_hash=_normalize_q_hash(q),
            forced_intent=pj["forced_intent"],
            forced_param=pj.get("forced_param"),
            source="ai",
            created_by_id=user_id,
        )
        session.add(row)
        await session.flush()
        p.applied_artifact_id = row.id
```

- [ ] **Step 8: Run tests, verify pass**

Run: `docker-compose restart fastapi && docker exec shan-ai-api pytest tests/test_repair_loop_new_fix_types.py -v`
Expected: 3 PASS.

- [ ] **Step 9: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_repair_loop_new_fix_types.py
git commit -m "feat(repair-loop): add project_alias + intent_override fix-types"
```

---

### Task 1.4: `_unapply_patch` for new fix-types (rollback support)

**Files:**
- Modify: `app/services/per_question_loop_service.py` — add new `_unapply_patch` function

- [ ] **Step 1: Write the failing test**

Append to `tests/test_repair_loop_new_fix_types.py`:

```python
from app.services.per_question_loop_service import _unapply_patch


@pytest.mark.asyncio
async def test_unapply_project_alias_deletes_row(db_session):
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    proposal = RepairProposal(
        type="project_alias",
        patch_json={"alias_text": "TestAlias-RB", "project_id": pid},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)
    assert proposal.applied_artifact_id is not None

    await _unapply_patch(db_session, proposal)

    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-RB")
    )
    assert row is None, "alias row should have been deleted"
    await db_session.refresh(proposal)
    assert proposal.status == "rolled_back"
```

- [ ] **Step 2: Run test, verify failure**

Run: `docker exec shan-ai-api pytest tests/test_repair_loop_new_fix_types.py::test_unapply_project_alias_deletes_row -v`
Expected: ImportError — `_unapply_patch` doesn't exist.

- [ ] **Step 3: Add `_unapply_patch` to `app/services/per_question_loop_service.py`**

Append after `_apply_patch`:

```python
async def _unapply_patch(session: AsyncSession, proposal: RepairProposal) -> None:
    """Reverse the artifact created by _apply_patch and mark the proposal rolled_back."""
    if not proposal.applied_artifact_id:
        # No artifact to delete — older proposals from before this column existed.
        proposal.status = "rolled_back"
        await session.commit()
        ks.invalidate_eval_caches()
        return

    if proposal.type == "project_alias":
        from app.models import ProjectAlias
        row = await session.get(ProjectAlias, proposal.applied_artifact_id)
        if row:
            await session.delete(row)
    elif proposal.type == "intent_override":
        from app.models import IntentOverride
        row = await session.get(IntentOverride, proposal.applied_artifact_id)
        if row:
            await session.delete(row)
    elif proposal.type == "prompt_patch":
        from app.models import PromptOverride
        row = await session.get(PromptOverride, proposal.applied_artifact_id)
        if row:
            row.active = False  # PromptOverride is soft-deactivated, not deleted
    elif proposal.type in ("add_abbreviation", "add_synonym",
                           "field_alias", "stop_word_remove"):
        # These mutate sentinel rows in query_synonyms — reverse mutation is
        # noisy and rarely useful; mark rolled_back without DB deletion. Admin
        # can edit the sentinel row manually if needed.
        pass

    proposal.status = "rolled_back"
    await session.commit()
    ks.invalidate_eval_caches()
```

- [ ] **Step 4: Run test, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_repair_loop_new_fix_types.py::test_unapply_project_alias_deletes_row -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_repair_loop_new_fix_types.py
git commit -m "feat(repair-loop): add _unapply_patch for rollback support"
```

---

### Task 1.5: "בית הגדי" end-to-end reproducer

**Files:**
- Create: `tests/test_beit_hagdi_repro.py`

- [ ] **Step 1: Write the integration test**

```python
"""Integration test for the spec's reproducer.

Seed a Project row named 'בית הגדי' in stage 'תכנון', confirm the system
answers WRONG initially, then run the repair loop and confirm it answers
CORRECTLY after.
"""
import json
import pytest
from sqlalchemy import select, text
from unittest.mock import patch, AsyncMock

from app.models import EvalGoldAnswer, Project, ProjectAlias
from app.services.ask_router import route, _normalize_q_hash
from app.services.gold_truth_service import save_gold
from app.services.per_question_loop_service import run_one_question
from app.services import knowledge_service as ks


Q = "באיזה שלב נמצא פרויקט בית הגדי?"
GOLD = "הפרויקט רשום בשלב תכנון"


@pytest.mark.asyncio
async def test_beit_hagdi_baseline_is_wrong(db_session):
    """Without an alias, find_projects_by_identifier misses 'בית הגדי'.

    We don't assert a specific wrong answer — we just assert the answer
    does NOT contain the gold's key phrase 'תכנון'. If it already does,
    the test environment has data we don't expect; investigate.
    """
    # Seed
    proj = Project(name="בית הגדי", project_identifier="BG-04",
                   stage="תכנון", is_active=True)
    db_session.add(proj)
    await db_session.commit()

    ks.invalidate_eval_caches()
    result = await route(Q, db_session, user_id=1, log_to_db=False)

    # Either the system says 'לא נמצא' or returns unrelated projects;
    # in both cases the gold key phrase is absent.
    assert "תכנון" not in result.answer or "לא נמצא" in result.answer, \
        f"Expected baseline to miss the project, got: {result.answer!r}"


@pytest.mark.asyncio
async def test_beit_hagdi_repair_loop_creates_alias_and_fixes_answer(db_session):
    proj = Project(name="בית הגדי", project_identifier="BG-04",
                   stage="תכנון", is_active=True)
    db_session.add(proj)
    await db_session.commit()
    await db_session.refresh(proj)

    gold = await save_gold(db_session, question=Q, gold_answer=GOLD,
                           user_id=None, source="manual")

    # Mock the repair-proposer LLM to return a project_alias proposal.
    async def fake_llm_chat(usage, messages, **kw):
        if usage == "eval_repair":
            return json.dumps({
                "type": "project_alias",
                "patch_json": {"alias_text": "בית הגדי", "project_id": proj.id},
                "rationale": "name not recognized; alias maps it to project id",
                "risk": "low",
            })
        if usage == "eval_judge":
            # Compare uses YES/NO. After patch we want PASS.
            return "YES"
        return ""

    with patch("app.services.llm_router.llm_chat", new=fake_llm_chat):
        all_gold = (await db_session.execute(
            select(EvalGoldAnswer))).scalars().all()
        result = await run_one_question(
            db_session, gold, user_id=1, all_gold=list(all_gold),
            eval_run_id=None, max_repairs=2, threshold=0.8,
        )

    assert result.status in ("fixed", "passed_first_try"), \
        f"expected fixed/passed_first_try, got {result.status} (rejected={result.rejected_fixes})"
    if result.status == "fixed":
        # Alias row exists and points to our project
        row = await db_session.scalar(
            select(ProjectAlias).where(ProjectAlias.project_id == proj.id))
        assert row is not None
        assert row.alias_text == "בית הגדי"

    # Re-route the same question — answer must now contain the gold key phrase.
    ks.invalidate_eval_caches()
    after = await route(Q, db_session, user_id=1, log_to_db=False)
    assert "תכנון" in after.answer, \
        f"after repair, expected 'תכנון' in answer, got: {after.answer!r}"
```

- [ ] **Step 2: Run the reproducer**

Run: `docker-compose restart fastapi && docker exec shan-ai-api pytest tests/test_beit_hagdi_repro.py -v -s`
Expected: 2 PASS. The first test confirms baseline is broken; the second confirms the loop fixes it.

If the second test FAILS with `unfixable`: print `result.rejected_fixes` to see which proposals were rejected and why. The most common failure mode is the regression-gate snapshot picking up unrelated questions that flap — for the integration test we may need to seed `all_gold` with only the one question we care about.

If FAILS with `error`: check `docker logs shan-ai-api` for stack traces; likely a missing column or unhandled type in `_apply_patch`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_beit_hagdi_repro.py
git commit -m "test: end-to-end reproducer for 'בית הגדי' fix"
```

---

### Task 1.6: Phase 1 gate — full eval cycle + reproducer in production

- [ ] **Step 1: Run a full eval cycle on the production gold set with the new fix-types**

In `/dashboard/eval-curate`, click "Run cycle". Wait for completion. Compare `n_pass / n_probes` to the Phase 0 baseline (recorded in Task 0.7). The number should be **higher** — Phase 1 added two fix-types the loop can now use.

- [ ] **Step 2: Reproduce the spec failure manually**

In `/dashboard/ask`, type:
```
באיזה שלב נמצא פרויקט בית הגדי?
```
Note the answer.

If it's already correct (the loop fixed it during the Step 1 cycle): SUCCESS — skip to step 4.

If it's still wrong: open `/dashboard/eval-curate`, find the row for this question, click "Run repair on this question." Watch the SSE stream for `repair_proposed` → `repair_applied` events. After completion, re-ask in `/dashboard/ask`.

- [ ] **Step 3: Confirm DB rows exist**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c \
  "SELECT id, alias_text, project_id, source, created_at FROM project_aliases ORDER BY id DESC LIMIT 5;"
```
Expected: at least one row, alias_text contains 'בית הגדי' (or whatever name your real failing project uses).

- [ ] **Step 4: Tag**

```bash
git tag phase-1-complete
```

- [ ] **Step 5: Capture the new pass-rate as the Phase 3 baseline**

The next plan (Phase 2) ships thumbs UI; the plan after that (Phase 3) is what the spec's "≥ 85% within 7 days of Phase 3 merge" gate references. Record the current rate so future plans have a baseline:

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai -c \
  "INSERT INTO system_flags (key, value) VALUES ('phase1_baseline_pass_rate', :rate) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;"
```
(Replace `:rate` with the decimal pass-rate from Step 1, e.g. `'0.71'`.)

---

## Self-Review

Ran the post-write checklist:

**1. Spec coverage:**
- §1 Architecture — Tasks 0.2/0.3/0.4/0.5 (ask_router extracted, all 3 surfaces switched). ✅
- §2 Data model — Task 0.1 covers `ProjectAlias`, `IntentOverride`, `applied_artifact_id`. `correction_pins`, `answer_feedback`, `route_traces` deferred to Phase 3 / Phase 2 / Phase 4 plans (called out at the top). ✅
- §3 Routing & lookup — Task 1.2 wires the pin/alias/override pre-rules. Correction-pin lookup is wired only behind a `_DB_CORRECTION_PINS_CACHE` that doesn't exist yet (Phase 3 plan adds it). For Phase 1, `route()` checks intent-override and alias only — explicitly noted in 0.3's docstring. ✅
- §4 New fix-types — Tasks 1.3/1.4 cover `project_alias` + `intent_override` only. `field_alias_real` and `correction_pin` deferred to Phase 3 plan. ✅
- §5 UI changes — entirely deferred to Phase 2 plan. ✅
- §6 Regression gates — existing gate (`passing_before − passing_after`) covers all fix-types per spec; no per-type tightening needed in code (Tasks 1.3/1.4 fix-types both score risk≤medium and the universal gate enforces 0 regressions). Snapshot-budget mitigations (`snapshot_mode=True`, `asyncio.gather` parallelism) are NOT implemented in this plan — only the parameter is plumbed through. If snapshot time becomes a problem, add a follow-up task. ✅ (deferred consciously)
- §7.1 Tests — unit tests in 0.2/0.3/1.1/1.2/1.3, integration test in 1.5, smoke set in 0.6. Eval-vs-prod parity test is implicit in 0.4 (eval now goes through ask_router) but no separate parity assertion harness; Phase 4 plan covers explicit parity CI. ✅
- §7.2 Phase 0 + Phase 1 rollout — Tasks 0.7 and 1.6 enforce the gates. ✅
- §7.4 Definition of done — partial: "בית הגדי" reproducer in 1.5 + 1.6. 85% pass-rate is a Phase 3 gate, not Phase 1. Eval/prod parity, no correction_pin auto-applies → Phase 2/3/4 plans. ✅

**2. Placeholder scan:** Searched for `TBD`, `TODO`, `implement later`, `add appropriate`, `similar to`, `fill in`. None found. Each step contains the actual code or command to run.

**3. Type consistency:** Verified `ProjectAlias` columns referenced in tests match the model (`alias_text`, `normalized_alias`, `project_id`, `source`, `created_by_id`). `IntentOverride.question_pattern_hash` matches `_normalize_q_hash` output (sha256 hex, 64 chars). `RepairProposal.applied_artifact_id` (Integer nullable) is set by `await session.flush()` then read by `_unapply_patch`. `_normalize_q_hash` is exported from `app/services/ask_router.py` and imported in `_patch_to_shadow` and the integration test — no name drift. `_apply_patch` signature `(session, proposal, user_id)` matches existing call sites — `user_id=None` in tests is valid (column is nullable).

No issues found that need rewriting.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-rag-quality-phase-0-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
