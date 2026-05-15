# RAG Quality — Phase 3 Implementation Plan (`field_alias_real`, `correction_pin`, Admin Rules UI, Proposer/Judge Quality)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the last two fix-types (`field_alias_real`, `correction_pin`) plus the admin rules CRUD UI per spec §5.2, AND fix the two repair-quality holes Phase 2 revealed: the proposer picks the wrong candidate when several share a field, and the judge accepts substring matches that aren't entity-equivalent.

**Architecture:** `field_alias_real` is a free-form mapping `("מנה\"פ" → "manager")` consulted by `gold_truth_service._detect_field` and by `project_tools._detect_intent`'s column-keyword routing. Storage reuses the existing `query_synonyms` sentinel-row pattern (`original="__field_aliases__"`). `correction_pin` is a verbatim Q→A bypass: lookup in `ask_router.route()` BEFORE the existing intent-override step; matching question returns the pinned answer with zero LLM calls. Pin proposals from the repair loop are marked `awaiting_approval` (not `pending`) and need admin click before they apply. The admin rules page is a new tab block on `/dashboard/learning` exposing CRUD for `project_aliases`, `intent_overrides`, `correction_pins`, and the synonym sentinel rows. Proposer reranking sorts candidates by question-token overlap before sending to the LLM. Judge tightening adds an "entity match" check: when the question mentions a project token, the AI answer must contain that token (or a known alias of it) to count as 1.0.

**Tech Stack:** FastAPI + async SQLAlchemy 2.x + Postgres + Jinja2 + Vanilla JS. pytest + pytest-asyncio. Docker.

**Spec reference:** `docs/superpowers/specs/2026-05-09-rag-quality-design.md` §4.3 `field_alias_real`, §4.4 `correction_pin`, §5.2 admin rules page. Phase 4 (telemetry tile + rule-effectiveness dashboard) remains a separate future plan.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `app/services/field_alias_service.py` | Loader + sentinel-row helpers for `__field_aliases__` (alias→field column). Used by `_detect_field` in gold_truth_service and `_detect_intent` in project_tools. |
| `app/routers/learning_rules.py` | New FastAPI router for `/dashboard/learning/rules` GET + per-table CRUD POST endpoints (`/aliases`, `/intent_overrides`, `/correction_pins`, `/synonyms`). |
| `app/templates/learning_rules.html` | Standalone admin page rendered as a sub-tab from `learning.html`. Lists rows, inline edit/delete, "add new", "test-now" buttons. |
| `tests/test_field_alias_real.py` | Fix-type tests: patch_to_shadow, apply, _detect_field consumption. |
| `tests/test_correction_pin.py` | Fix-type tests: pin-hit short-circuits `route()`, awaiting_approval gate, manual approval workflow. |
| `tests/test_proposer_rerank.py` | Reranker tests: candidate ordering by token overlap. |
| `tests/test_judge_tightening.py` | `_rule_check` no longer scores 1.0 on substring-only when entity tokens diverge. |
| `tests/test_learning_rules_router.py` | Endpoint integration tests for the admin CRUD. |

### Modified files

| Path | Change |
|---|---|
| `app/models.py` | Add `CorrectionPin` model: id, question_hash (unique), pinned_answer, scope_project_id (FK Project, nullable), expires_at (nullable), source, created_by_id, created_at. |
| `app/main.py` | Add `CREATE TABLE IF NOT EXISTS correction_pins (…)` + unique index in startup migration block. |
| `app/services/knowledge_service.py` | Add `_shadow_correction_pins` + `_shadow_field_aliases` ContextVars and corresponding `_DB_CORRECTION_PINS_CACHE` + `_DB_FIELD_ALIASES_CACHE` dicts. Extend `_ensure_eval_caches` to populate from `correction_pins` table + `__field_aliases__` sentinel. |
| `app/services/ask_router.py` | Insert correction-pin lookup as Step 0 (BEFORE intent_override + alias resolve). On hit: return `AnswerResult(path="correction_pin", …)` with the pinned answer verbatim, zero LLM calls. |
| `app/services/per_question_loop_service.py` | Extend `FIX_TYPES` with `field_alias_real`, `correction_pin`. Extend `_patch_to_shadow` for both. Extend `shadow_config` ContextVar mapping. Extend `_apply_patch` for both — `correction_pin` writes row with `status="awaiting_approval"` instead of immediately applying. Extend `_unapply_patch` for both. Extend `_REPAIR_SYS` rubric. Add `approve_pin(proposal_id, user_id)` helper. Reranker added inside `_candidate_projects`. |
| `app/services/gold_truth_service.py` | `_detect_field` consults `_DB_FIELD_ALIASES_CACHE` + shadow override BEFORE `_FIELD_KEYWORDS` static dict. `compare_to_gold._rule_check` adds entity-token check. |
| `app/services/project_tools.py` | `_detect_intent` keyword-routing layer consults field aliases for column-keyword detection. |
| `app/templates/learning.html` | Add nav link / iframe / fragment include for the new rules page (the rules page itself is a separate template — `learning.html` just gets a tab link). |

### Untouched

`app/routers/ask.py` (Phase 2 work already exposes `/dashboard/ask/correct`), `app/services/answer_feedback_service.py` (Phase 2), `app/services/embedding_service.py`, `app/templates/ask.html`.

---

## Task 3.0: CorrectionPin model + migration

**Files:**
- Modify: `app/models.py` — append `CorrectionPin` after `AnswerFeedback`
- Modify: `app/main.py` — add CREATE TABLE in startup migration block
- Create: `tests/test_correction_pin_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_correction_pin_model.py`:

```python
"""Smoke tests for CorrectionPin model."""
from sqlalchemy import text


async def test_correction_pins_table_exists(db_session):
    res = await db_session.execute(text(
        "SELECT to_regclass('public.correction_pins')"
    ))
    assert res.scalar() is not None


async def test_correction_pins_columns(db_session):
    rows = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='correction_pins' ORDER BY ordinal_position"
    ))).scalars().all()
    expected = {"id", "question_hash", "pinned_answer", "scope_project_id",
                "expires_at", "source", "created_by_id", "created_at"}
    assert expected.issubset(set(rows)), f"missing: {expected - set(rows)}"


async def test_correction_pins_unique_question_hash(db_session):
    res = await db_session.execute(text(
        "SELECT 1 FROM pg_indexes WHERE tablename='correction_pins' "
        "AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%question_hash%'"
    ))
    assert res.scalar() == 1, "missing unique index on question_hash"
```

- [ ] **Step 2: Run, confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_correction_pin_model.py -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Add model to `app/models.py`**

Append after `AnswerFeedback`:

```python
class CorrectionPin(Base):
    """Verbatim pinned answer for a normalized question. Highest-priority
    lookup in ask_router — bypasses all LLM calls when hit.
    Pin proposals from the repair loop need human approval before insertion.
    """
    __tablename__ = "correction_pins"

    id                = Column(Integer, primary_key=True)
    question_hash     = Column(String(64), unique=True, nullable=False, index=True)
    pinned_answer     = Column(Text, nullable=False)
    scope_project_id  = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"),
                               nullable=True)
    expires_at        = Column(DateTime, nullable=True)
    source            = Column(String(32), nullable=False, default="manual")
    created_by_id     = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)

    scope_project = relationship("Project")
    created_by    = relationship("User")
```

- [ ] **Step 4: Add startup migration to `app/main.py`**

After the existing `Phase 2 (rag-quality)` block, before the `# LLM config table` block, add:

```python
                # Phase 3 (rag-quality): correction-pin verbatim answers
                await conn.execute(_text("""
                    CREATE TABLE IF NOT EXISTS correction_pins (
                        id               SERIAL PRIMARY KEY,
                        question_hash    VARCHAR(64) NOT NULL UNIQUE,
                        pinned_answer    TEXT NOT NULL,
                        scope_project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        expires_at       TIMESTAMP,
                        source           VARCHAR(32) NOT NULL DEFAULT 'manual',
                        created_by_id    INTEGER REFERENCES users(id),
                        created_at       TIMESTAMP DEFAULT NOW()
                    )
                """))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_correction_pins_hash "
                    "ON correction_pins (question_hash)"
                ))
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_correction_pins_expires "
                    "ON correction_pins (expires_at)"
                ))
```

- [ ] **Step 5: Restart + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_correction_pin_model.py -v 2>&1 | tail -10
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/main.py tests/test_correction_pin_model.py
git commit -m "feat(models): add CorrectionPin table + startup migration"
```

---

## Task 3.1: Caches + shadow ContextVars for correction_pin + field_alias_real

**Files:**
- Modify: `app/services/knowledge_service.py` — add 2 ContextVars + 2 cache dicts + extend `_ensure_eval_caches`
- Create: `tests/test_phase3_caches.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase3_caches.py`:

```python
"""Verify correction_pins + __field_aliases__ rows populate the in-memory caches."""
import pytest
from sqlalchemy import text

from app.services import knowledge_service as ks


@pytest.mark.asyncio
async def test_correction_pins_loaded_into_cache(db_session):
    await db_session.execute(text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source) "
        "VALUES ('hash-cp-001', 'pinned-answer-text', 'manual')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "hash-cp-001" in ks._DB_CORRECTION_PINS_CACHE
    assert ks._DB_CORRECTION_PINS_CACHE["hash-cp-001"]["pinned_answer"] == "pinned-answer-text"


@pytest.mark.asyncio
async def test_field_aliases_loaded_from_sentinel(db_session):
    """__field_aliases__ sentinel row stored as ['alias=field', ...] — mirrors
    the existing __hebrew_abbrevs__ pattern."""
    await db_session.execute(text(
        "INSERT INTO query_synonyms (original, synonyms, source) "
        "VALUES ('__field_aliases__', :syn::jsonb, 'ai')"
    ), {"syn": '["מנה\\"פ=manager", "תכנון=stage"]'})
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert ks._DB_FIELD_ALIASES_CACHE.get('מנה"פ') == "manager"
    assert ks._DB_FIELD_ALIASES_CACHE.get("תכנון") == "stage"


@pytest.mark.asyncio
async def test_expired_correction_pin_not_loaded(db_session):
    """Pins with expires_at < now must NOT load into the cache."""
    await db_session.execute(text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source, expires_at) "
        "VALUES ('hash-expired', 'old-pin', 'manual', NOW() - INTERVAL '1 day')"
    ))
    await db_session.commit()

    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    assert "hash-expired" not in ks._DB_CORRECTION_PINS_CACHE
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_phase3_caches.py -v
```

Expected: AttributeError on `_DB_CORRECTION_PINS_CACHE` and `_DB_FIELD_ALIASES_CACHE`.

- [ ] **Step 3: Extend `app/services/knowledge_service.py`**

Find the existing ContextVar/cache block (around lines 22-34 — same area as Tasks 0.0 and 1.1 added). Append:

```python
_shadow_correction_pins: ContextVar[dict] = ContextVar("shadow_correction_pins", default={})
_shadow_field_aliases: ContextVar[dict] = ContextVar("shadow_field_aliases", default={})

_DB_CORRECTION_PINS_CACHE: dict[str, dict] = {}   # q_hash -> {"pinned_answer", "scope_project_id", "expires_at"}
_DB_FIELD_ALIASES_CACHE: dict[str, str] = {}      # alias_text -> field_name
```

Extend `_ensure_eval_caches`:

a) Add to global declaration:
```python
    global _DB_CORRECTION_PINS_CACHE, _DB_FIELD_ALIASES_CACHE
```

b) Inside the `try` block, after the existing model imports add `CorrectionPin`:
```python
        from app.models import QuerySynonym, PromptOverride, ProjectAlias, IntentOverride, CorrectionPin
```

c) Inside the `try` block, after the existing alias/intent_overrides queries, add:

```python
            # Correction pins — load only those not yet expired
            from datetime import datetime as _dt
            now = _dt.utcnow()
            cp_rows = (await own_session.execute(
                select(CorrectionPin).where(
                    (CorrectionPin.expires_at.is_(None)) | (CorrectionPin.expires_at > now)
                )
            )).scalars().all()
            new_pins = {
                row.question_hash: {
                    "pinned_answer": row.pinned_answer,
                    "scope_project_id": row.scope_project_id,
                    "expires_at": row.expires_at,
                }
                for row in cp_rows
            }

            # Field aliases from __field_aliases__ sentinel — stored as ['alias=field', ...]
            fa_sentinel = await own_session.scalar(
                select(QuerySynonym).where(QuerySynonym.original == "__field_aliases__")
            )
            new_field_aliases: dict[str, str] = {}
            if fa_sentinel and fa_sentinel.synonyms:
                for entry in fa_sentinel.synonyms:
                    if isinstance(entry, str) and "=" in entry:
                        a, f = entry.split("=", 1)
                        new_field_aliases[a.strip()] = f.strip()
```

d) After existing assignments:
```python
        _DB_CORRECTION_PINS_CACHE = new_pins
        _DB_FIELD_ALIASES_CACHE = new_field_aliases
```

- [ ] **Step 4: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_phase3_caches.py -v 2>&1 | tail -10
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/knowledge_service.py tests/test_phase3_caches.py
git commit -m "feat(ks): add correction_pins + field_aliases caches with TTL filter"
```

---

## Task 3.2: `correction_pin` lookup in `ask_router.route()`

**Files:**
- Modify: `app/services/ask_router.py` — insert pin lookup as Step 0 (FIRST step inside route())
- Modify: `tests/test_ask_router.py` — append pin-hit test

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ask_router.py`:

```python
@pytest.mark.asyncio
async def test_route_correction_pin_short_circuits(db_session):
    """A pinned answer must return verbatim with zero LLM calls and
    path='correction_pin'."""
    q = "PIN-Q-001: questionable question for pin"
    h = _normalize_q_hash(q)
    await db_session.execute(_sql_text(
        "INSERT INTO correction_pins (question_hash, pinned_answer, source) "
        "VALUES (:h, :ans, 'manual')"
    ), {"h": h, "ans": "verbatim pinned answer"})
    await db_session.commit()
    _ks.invalidate_eval_caches()
    await _ks._ensure_eval_caches(db_session)

    # Patch every downstream call site that route() could fall into. None
    # should be hit when the pin lookup wins.
    apq = AsyncMock(return_value=("should-not-fire", 0))
    awfc = AsyncMock(return_value={"answer": "rag-should-not-fire"})
    adq = AsyncMock(return_value="decision-should-not-fire")
    with patch("app.services.project_tools.answer_project_query", new=apq), \
         patch("app.services.knowledge_service.answer_with_full_context", new=awfc), \
         patch("app.services.knowledge_service.answer_decisions_question", new=adq):
        result = await route(q, db_session, user_id=1, log_to_db=False)

    assert result.path == "correction_pin"
    assert result.answer == "verbatim pinned answer"
    apq.assert_not_called()
    awfc.assert_not_called()
    adq.assert_not_called()
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_ask_router.py::test_route_correction_pin_short_circuits -v
```

Expected: FAIL (path != "correction_pin"; falls through to existing dispatch).

- [ ] **Step 3: Insert Step 0 in `app/services/ask_router.py`**

Find the existing pre-rules block (the `# ── Pre-rules (Phase 1) ───` comment and the `_ensure_eval_caches` call below it). The current Step 0a is intent_override; the new pin lookup goes BEFORE that.

Replace the `# ── Pre-rules (Phase 1) ───` comment + the `_ensure_eval_caches` call + `q_hash = ...` with:

```python
    # ── Pre-rules ─────────────────────────────────────────────────────────
    # Refresh DB-backed caches before pre-rule lookup.
    await ks._ensure_eval_caches(session)

    q_hash = _normalize_q_hash(question)

    # 0. Correction-pin (highest priority — verbatim answer, zero LLM calls)
    pins = {**ks._DB_CORRECTION_PINS_CACHE, **ks._shadow_correction_pins.get()}
    pin_entry = pins.get(q_hash)
    if pin_entry:
        return AnswerResult(
            answer=pin_entry["pinned_answer"],
            sources_used=[{"source": "correction_pin", "q_hash": q_hash}],
            log_id=None,
            path="correction_pin",
            intent=None,
            param=None,
            has_files=False,
            has_decisions=False,
            file_names=[],
            sources_text="📌 תשובה מאושרת",
        )
```

Keep the existing `# 0a. Intent override` block right after this. Renumber comment from `0a` to `1a` if you want for clarity (cosmetic only).

- [ ] **Step 4: Run, verify pass**

```bash
docker exec shan-ai-api pytest tests/test_ask_router.py -v 2>&1 | tail -15
```

Expected: 12 PASS (11 prior + 1 new).

- [ ] **Step 5: Commit**

```bash
git add app/services/ask_router.py tests/test_ask_router.py
git commit -m "feat(ask_router): correction_pin lookup as Step 0 — verbatim, zero LLM"
```

---

## Task 3.3: `field_alias_real` fix-type — proposer + apply + consume

**Files:**
- Modify: `app/services/per_question_loop_service.py` — extend FIX_TYPES, _patch_to_shadow, shadow_config, _apply_patch, _REPAIR_SYS
- Modify: `app/services/gold_truth_service.py` — `_detect_field` consults field-alias cache
- Create: `tests/test_field_alias_real.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_field_alias_real.py`:

```python
"""Tests for field_alias_real fix-type — write a sentinel-row alias, verify
_detect_field consumes it BEFORE the static _FIELD_KEYWORDS dict."""
import pytest
from sqlalchemy import select, text

from app.models import QuerySynonym, RepairProposal
from app.services.per_question_loop_service import (
    FIX_TYPES, _patch_to_shadow, _apply_patch,
)
from app.services import knowledge_service as ks
from app.services.gold_truth_service import _detect_field


def test_fix_types_includes_field_alias_real():
    assert "field_alias_real" in FIX_TYPES


def test_patch_to_shadow_field_alias_real():
    patch = {"alias": "מנה\"פ", "field": "manager"}
    shadow = _patch_to_shadow("field_alias_real", patch)
    assert shadow == {"field_aliases": {'מנה"פ': "manager"}}


@pytest.mark.asyncio
async def test_apply_field_alias_real_writes_sentinel(db_session):
    proposal = RepairProposal(
        type="field_alias_real",
        patch_json={"alias": "אחראי", "field": "manager"},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    sentinel = await db_session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__field_aliases__"))
    assert sentinel is not None
    assert any(e.startswith("אחראי=manager") for e in sentinel.synonyms)

    await db_session.refresh(proposal)
    assert proposal.status == "applied"


@pytest.mark.asyncio
async def test_detect_field_uses_db_alias_before_static(db_session):
    """Insert an alias that maps 'תיכן' to 'stage' and verify _detect_field
    picks it up via the DB cache."""
    await db_session.execute(text(
        "INSERT INTO query_synonyms (original, synonyms, source) "
        "VALUES ('__field_aliases__', :s::jsonb, 'ai') "
        "ON CONFLICT (original) DO UPDATE SET synonyms = EXCLUDED.synonyms"
    ), {"s": '["תיכן=stage"]'})
    await db_session.commit()
    ks.invalidate_eval_caches()
    await ks._ensure_eval_caches(db_session)

    field = _detect_field("מה ה-תיכן של פרויקט X?")
    assert field == "stage", f"expected stage, got {field!r}"
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_field_alias_real.py -v
```

Expected: 4 FAIL.

- [ ] **Step 3: Modify `app/services/per_question_loop_service.py`**

a) `FIX_TYPES` (around line 34) — add `"field_alias_real"`:
```python
FIX_TYPES = [
    "add_abbreviation", "add_synonym", "stop_word_remove",
    "field_alias", "prompt_patch",
    "project_alias", "intent_override",
    "field_alias_real",   # NEW Phase 3
]
```

b) `_REPAIR_SYS` — add bullet between intent_override and add_abbreviation:
```
"3. field_alias_real — AI couldn't map a Hebrew word/abbreviation to a Project column. "
"patch_json = {'alias': 'מנה\\\"פ', 'field': 'manager|stage|risks|weekly_report|to_handle|estimated_finish_date|dev_plan_date'}. "
"Use when the question contains a Hebrew column-synonym that the static _FIELD_KEYWORDS dict misses.\n"
```

Renumber subsequent bullets.

c) `_patch_to_shadow` — add branch:
```python
    if proposal_type == "field_alias_real":
        a = (patch_json.get("alias") or "").strip()
        f = (patch_json.get("field") or "").strip()
        if a and f:
            return {"field_aliases": {a: f}}
        return {}
```

d) `shadow_config` — add CV mapping:
```python
        if "field_aliases" in patch:
            tokens.append(("field_aliases", ks._shadow_field_aliases.set(dict(patch["field_aliases"]))))
```

And in the finally-block `cv = {...}` dict:
```python
                "field_aliases": ks._shadow_field_aliases,
```

e) `_apply_patch` — add branch right after the existing `intent_override` branch:
```python
    elif p.type == "field_alias_real":
        alias = (pj.get("alias") or "").strip()
        field = (pj.get("field") or "").strip()
        if not alias or not field:
            raise ValueError(f"field_alias_real proposal {p.id} missing alias or field")
        # Reuse the sentinel-row helper used by add_abbreviation
        await _upsert_synonym_sentinel(
            session, "__field_aliases__",
            merge_pairs={alias: field},
        )
        # Find the sentinel row id for rollback
        sentinel = await session.scalar(
            select(QuerySynonym).where(QuerySynonym.original == "__field_aliases__"))
        if sentinel:
            p.applied_artifact_id = sentinel.id
```

(`_upsert_synonym_sentinel` already exists in this file from earlier phases — verify by grepping.)

- [ ] **Step 4: Modify `_detect_field` in `app/services/gold_truth_service.py`**

Read the existing `_detect_field` (around line 91). Replace its body with:

```python
def _detect_field(question: str) -> str | None:
    nq = normalize_hebrew(question)
    # Phase 3: DB-backed + shadow overrides take precedence over the static dict
    from app.services import knowledge_service as ks
    effective = {**ks._DB_FIELD_ALIASES_CACHE, **ks._shadow_field_aliases.get()}
    for alias, field in effective.items():
        if normalize_hebrew(alias) in nq:
            return field
    # Fall through to static keyword map
    for field, keywords in _FIELD_KEYWORDS.items():
        for kw in keywords:
            if normalize_hebrew(kw) in nq:
                return field
    return None
```

- [ ] **Step 5: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_field_alias_real.py -v 2>&1 | tail -15
```

Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/per_question_loop_service.py app/services/gold_truth_service.py tests/test_field_alias_real.py
git commit -m "feat(repair-loop): add field_alias_real fix-type with DB sentinel storage"
```

---

## Task 3.4: `correction_pin` fix-type with human-approval gate

**Files:**
- Modify: `app/services/per_question_loop_service.py` — extend FIX_TYPES + everything else for `correction_pin`. KEY: `_apply_patch` for this type writes the proposal with `status="awaiting_approval"` and creates the CorrectionPin row only on `approve_pin(proposal_id, user_id)` call.
- Create: `tests/test_correction_pin.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_correction_pin.py`:

```python
"""Tests for correction_pin fix-type — awaiting_approval gate, manual approve."""
import pytest
from sqlalchemy import select, text

from app.models import CorrectionPin, RepairProposal
from app.services.per_question_loop_service import (
    FIX_TYPES, _patch_to_shadow, _apply_patch, approve_pin,
)
from app.services.ask_router import _normalize_q_hash


def test_fix_types_includes_correction_pin():
    assert "correction_pin" in FIX_TYPES


def test_patch_to_shadow_correction_pin():
    patch = {
        "question": "test-pin-q-001",
        "pinned_answer": "the answer",
        "scope_project_id": None,
        "ttl_days": 30,
    }
    shadow = _patch_to_shadow("correction_pin", patch)
    h = _normalize_q_hash("test-pin-q-001")
    assert h in shadow["correction_pins"]
    assert shadow["correction_pins"][h]["pinned_answer"] == "the answer"


@pytest.mark.asyncio
async def test_apply_correction_pin_creates_awaiting_approval(db_session):
    """_apply_patch for correction_pin must NOT write the CorrectionPin row.
    It only marks the proposal awaiting_approval — admin must approve."""
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={
            "question": "pin-q-002",
            "pinned_answer": "verbatim text",
            "ttl_days": 30,
        },
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await _apply_patch(db_session, proposal, user_id=None)

    # Pin row NOT yet created — awaiting approval
    h = _normalize_q_hash("pin-q-002")
    pin = await db_session.scalar(
        select(CorrectionPin).where(CorrectionPin.question_hash == h))
    assert pin is None

    await db_session.refresh(proposal)
    assert proposal.status == "awaiting_approval"


@pytest.mark.asyncio
async def test_approve_pin_creates_correction_pin_row(db_session):
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={
            "question": "pin-q-003",
            "pinned_answer": "verbatim text 2",
            "ttl_days": 14,
        },
        status="awaiting_approval",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    await approve_pin(db_session, proposal.id, user_id=None)

    h = _normalize_q_hash("pin-q-003")
    pin = await db_session.scalar(
        select(CorrectionPin).where(CorrectionPin.question_hash == h))
    assert pin is not None
    assert pin.pinned_answer == "verbatim text 2"
    assert pin.expires_at is not None  # ttl_days=14 → not None

    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.applied_artifact_id == pin.id


@pytest.mark.asyncio
async def test_approve_pin_rejects_non_awaiting(db_session):
    proposal = RepairProposal(
        type="correction_pin",
        patch_json={"question": "pin-q-004", "pinned_answer": "x"},
        status="rejected",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)

    with pytest.raises(ValueError, match="not awaiting"):
        await approve_pin(db_session, proposal.id, user_id=None)
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_correction_pin.py -v
```

Expected: 5 FAIL (one ImportError on `approve_pin`, others from missing branches).

- [ ] **Step 3: Modify `app/services/per_question_loop_service.py`**

a) Add to `FIX_TYPES`:
```python
    "correction_pin",   # NEW Phase 3 — needs human approval
```

b) `_REPAIR_SYS` — add bullet, with risk note:
```
"4. correction_pin — LAST RESORT. AI is consistently wrong on a specific question and "
"no alias/synonym tweak fixes it. patch_json = {'question': '<original q>', 'pinned_answer': '<gold>', "
"'scope_project_id': <int|null>, 'ttl_days': 30}. The pin returns the answer VERBATIM with zero LLM "
"calls. ALWAYS set risk='high'. Requires admin approval before activation.\n"
```

c) `_patch_to_shadow` — add branch:
```python
    if proposal_type == "correction_pin":
        from app.services.ask_router import _normalize_q_hash
        q = patch_json.get("question", "")
        ans = patch_json.get("pinned_answer", "")
        if not q or not ans:
            return {}
        h = _normalize_q_hash(q)
        return {"correction_pins": {h: {
            "pinned_answer": ans,
            "scope_project_id": patch_json.get("scope_project_id"),
            "expires_at": None,  # shadow doesn't enforce TTL
        }}}
```

d) `shadow_config` mapping:
```python
        if "correction_pins" in patch:
            tokens.append(("correction_pins", ks._shadow_correction_pins.set(dict(patch["correction_pins"]))))
```

And in finally-block:
```python
                "correction_pins": ks._shadow_correction_pins,
```

e) `_apply_patch` — add branch that DOES NOT write the row, only marks awaiting:
```python
    elif p.type == "correction_pin":
        # Defer DB insert to approve_pin(). Mark proposal awaiting_approval.
        if not pj.get("question") or not pj.get("pinned_answer"):
            raise ValueError(f"correction_pin proposal {p.id} missing question or pinned_answer")
        p.status = "awaiting_approval"
        p.applied_at = None
        await session.commit()
        return  # Do NOT fall through to the `p.status = "applied"` line below
```

f) Add `approve_pin` function after `_apply_patch`:

```python
async def approve_pin(
    session: AsyncSession,
    proposal_id: int,
    user_id: int | None,
) -> "RepairProposal":
    """Admin-triggered approval for an awaiting_approval correction_pin proposal.
    Creates the CorrectionPin row, marks proposal applied."""
    from app.models import CorrectionPin
    from datetime import timedelta
    from app.services.ask_router import _normalize_q_hash

    proposal = await session.get(RepairProposal, proposal_id)
    if proposal is None:
        raise LookupError(f"proposal {proposal_id} not found")
    if proposal.status != "awaiting_approval":
        raise ValueError(
            f"proposal {proposal_id} is not awaiting approval (status={proposal.status})"
        )
    if proposal.type != "correction_pin":
        raise ValueError(f"proposal {proposal_id} is not a correction_pin")

    pj = proposal.patch_json or {}
    question = pj.get("question", "")
    answer = pj.get("pinned_answer", "")
    ttl_days = int(pj.get("ttl_days") or 30)
    scope = pj.get("scope_project_id")

    expires_at = datetime.utcnow() + timedelta(days=ttl_days) if ttl_days > 0 else None
    pin = CorrectionPin(
        question_hash=_normalize_q_hash(question),
        pinned_answer=answer,
        scope_project_id=scope,
        expires_at=expires_at,
        source="ai_approved",
        created_by_id=user_id,
    )
    session.add(pin)
    await session.flush()

    proposal.status = "applied"
    proposal.applied_at = datetime.utcnow()
    proposal.applied_by_id = user_id
    proposal.applied_artifact_id = pin.id
    await session.commit()
    ks.invalidate_eval_caches()
    return proposal
```

g) `_unapply_patch` — add branch:
```python
    elif proposal.type == "correction_pin":
        from app.models import CorrectionPin
        row = await session.get(CorrectionPin, proposal.applied_artifact_id)
        if row:
            await session.delete(row)
    elif proposal.type == "field_alias_real":
        # Sentinel-row mutation — soft rollback (admin can edit via /rules)
        pass
```

- [ ] **Step 4: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_correction_pin.py -v 2>&1 | tail -15
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/per_question_loop_service.py tests/test_correction_pin.py
git commit -m "feat(repair-loop): correction_pin fix-type — awaiting_approval gate + approve_pin"
```

---

## Task 3.5: Reranker on candidate_projects + judge tightening

**Files:**
- Modify: `app/services/per_question_loop_service.py` — `_candidate_projects` reranks by name-overlap
- Modify: `app/services/gold_truth_service.py` — `_rule_check` adds entity-token check
- Create: `tests/test_proposer_rerank.py`
- Create: `tests/test_judge_tightening.py`

This addresses the two Phase 2 lessons: LLM picked wrong candidate when several shared a manager (because `_candidate_projects` returned name-unrelated rows first), and `_rule_check` scored 1.0 on substring overlap even when entities differed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proposer_rerank.py`:

```python
"""Verify _candidate_projects ranks rows by question-name overlap."""
import pytest
from sqlalchemy import text

from app.services.per_question_loop_service import _candidate_projects


@pytest.mark.asyncio
async def test_candidate_projects_ranks_name_matches_first(db_session):
    """Insert 3 projects: 2 with the same manager, 1 with name containing
    the question token. The name-match must come first in the returned list."""
    await db_session.execute(text("""
        INSERT INTO projects (project_identifier, name, manager, is_active)
        VALUES ('RNK-A', 'תל אביב מרכז', 'משה כהן', true),
               ('RNK-B', 'בת ים אזורי', 'משה כהן', true),
               ('RNK-C', 'חיפה צפון', 'משה כהן', true)
    """))
    await db_session.commit()

    rows = await _candidate_projects(
        db_session,
        "מי המנהל של פרויקט בת ים?",
        limit=10,
    )

    assert len(rows) >= 1
    # The name-match ("בת ים אזורי") must be first.
    assert "בת ים" in rows[0]["name"], f"expected name-match first, got {rows[0]['name']!r}"
```

Create `tests/test_judge_tightening.py`:

```python
"""Verify _rule_check no longer scores 1.0 when AI answer mentions a DIFFERENT
project than the question's project token."""
from app.services.gold_truth_service import _rule_check, compare_to_gold


def test_rule_check_rejects_wrong_entity():
    """Question asks about 'בת ים', gold mentions manager 'יהודר בכר'.
    AI returns project 'תל השומר' (wrong entity!) with the same manager.
    Substring containment of 'יהודר בכר' alone must NOT yield score 1.0
    when the question token is missing from the answer."""
    # This test needs the entity-token-check to be implemented in _rule_check.
    question = "מי המנהל של פרויקט בת ים?"
    gold     = "מנהל הפרויקט: יהודר בכר"
    ai       = "📌 שם הפרויקט: תל השומר 📌 מנהל הפרויקט: יהודר בכר, אורית"

    # _rule_check signature is (ai_answer, gold_answer) — entity check needs
    # question context. We pass via the public compare_to_gold which can
    # apply the check.
    import asyncio
    score = asyncio.run(compare_to_gold(question, ai, gold))
    assert score < 1.0, f"expected sub-1.0 (different entity), got {score}"


def test_rule_check_passes_when_entity_matches():
    """Same-manager scenario but AI answer DOES mention 'בת ים' in the project
    name. Should score 1.0."""
    question = "מי המנהל של פרויקט בת ים?"
    gold     = "מנהל הפרויקט: יהודר בכר"
    ai       = "📌 שם הפרויקט: בת ים תחמ\"ש 📌 מנהל הפרויקט: יהודר בכר, אורית"

    import asyncio
    score = asyncio.run(compare_to_gold(question, ai, gold))
    assert score >= 1.0, f"expected full match, got {score}"
```

- [ ] **Step 2: Run, confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_proposer_rerank.py tests/test_judge_tightening.py -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Modify `_candidate_projects` in `per_question_loop_service.py`**

Replace the function body:

```python
async def _candidate_projects(session: AsyncSession, question: str, limit: int = 15) -> list[dict]:
    """Return up to `limit` projects whose name/identifier/manager overlaps the question's
    tokens. Results are reranked: rows whose NAME contains a question token come FIRST,
    then identifier-matches, then manager-matches. This gives the LLM-proposer a strong
    hint to pick the right project when several share a manager.
    """
    from app.models import Project
    from app.services.knowledge_service import normalize_hebrew
    from sqlalchemy import or_

    tokens = [t for t in normalize_hebrew(question).split() if len(t) >= 2]
    if not tokens:
        return []

    clauses = []
    for t in tokens:
        clauses.append(Project.name.ilike(f"%{t}%"))
        clauses.append(Project.project_identifier.ilike(f"%{t}%"))
        clauses.append(Project.manager.ilike(f"%{t}%"))
    stmt = (
        select(Project.id, Project.project_identifier, Project.name, Project.manager)
        .where(or_(*clauses))
        .where(Project.is_active.is_(True))
        .limit(limit * 3)  # over-fetch for reranking
    )
    rows = (await session.execute(stmt)).all()

    # Rerank: score each row by which field matched. Name > identifier > manager.
    def _score(row) -> int:
        nname = normalize_hebrew(row.name or "")
        nid   = normalize_hebrew(row.project_identifier or "")
        nmgr  = normalize_hebrew(row.manager or "")
        if any(t in nname for t in tokens):
            return 3
        if any(t in nid for t in tokens):
            return 2
        if any(t in nmgr for t in tokens):
            return 1
        return 0

    ranked = sorted(rows, key=_score, reverse=True)[:limit]
    return [
        {"id": r.id, "project_identifier": r.project_identifier,
         "name": r.name, "manager": r.manager}
        for r in ranked
    ]
```

- [ ] **Step 4: Modify `_rule_check` + `compare_to_gold` in `app/services/gold_truth_service.py`**

The current `_rule_check(ai_answer, gold_answer)` returns 1.0 on substring containment. We add an entity-token check at the `compare_to_gold` level, where we have the question text.

Read the existing `compare_to_gold` (around line 256). Add an entity-token check BEFORE invoking `_rule_check`:

```python
async def compare_to_gold(question: str, ai_answer: str, gold_answer: str) -> float:
    """Return similarity score 0.0..1.0. Tries cheap rule check first, then LLM judge."""
    # Phase 3: entity-token guard. If the question mentions a non-stop-word
    # token AND that token does NOT appear in ai_answer, treat as wrong entity
    # regardless of substring containment of the gold elsewhere.
    nq = normalize_hebrew(question)
    na = normalize_hebrew(ai_answer)
    _ENTITY_STOPS = {"של", "את", "על", "מה", "מי", "כמה", "איזה", "באיזה",
                     "פרויקט", "הפרויקט", "מנהל", "המנהל", "שלב", "סטטוס"}
    q_tokens = [t for t in nq.split() if len(t) >= 3 and t not in _ENTITY_STOPS]
    if q_tokens:
        # Pick the LAST non-stop token — likely the project name (Hebrew SOV)
        entity = q_tokens[-1]
        if entity not in na:
            # Entity not mentioned in answer → not the same thing
            # Defer to LLM judge anyway, but don't let _rule_check short-circuit to 1.0
            rule = _rule_check(ai_answer, gold_answer)
            if rule == 1.0:
                # Suppress false-positive substring match
                rule = None
            # Continue to LLM judge below
        else:
            rule = _rule_check(ai_answer, gold_answer)
    else:
        rule = _rule_check(ai_answer, gold_answer)

    if rule is not None:
        return rule

    # ... existing LLM-judge fallback below unchanged ...
```

Replace the existing `rule = _rule_check(ai_answer, gold_answer)` line at the top of the function with the block above. Keep the rest unchanged.

- [ ] **Step 5: Run + verify**

```bash
docker exec shan-ai-api pytest tests/test_proposer_rerank.py tests/test_judge_tightening.py -v 2>&1 | tail -15
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/per_question_loop_service.py app/services/gold_truth_service.py tests/test_proposer_rerank.py tests/test_judge_tightening.py
git commit -m "fix(repair-loop): rerank candidates by name overlap + judge entity-token guard"
```

---

## Task 3.6: Admin rules page — `/dashboard/learning/rules`

**Files:**
- Create: `app/routers/learning_rules.py` (GET + per-table CRUD)
- Create: `app/templates/learning_rules.html`
- Modify: `app/templates/learning.html` — add nav link to `/dashboard/learning/rules`
- Modify: `app/main.py` — register new router
- Create: `tests/test_learning_rules_router.py`

This is the largest task. It implements 5 tabs: Aliases, Intent Overrides, Correction Pins, Synonyms, Pending Approvals. Each tab is a CRUD table with inline edit/delete + an "add new rule" form + a "test-now" button that runs `ask_router.route()` with the question and shows path/answer/intent in a modal (no DB write).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_learning_rules_router.py`:

```python
"""Endpoint integration tests for /dashboard/learning/rules CRUD."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from app.main import app
from app.models import ProjectAlias, IntentOverride, CorrectionPin, User
from app.routers.login import get_current_user


async def _seed_admin(db_session, uid=2001):
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        f"VALUES ({uid}, {900000000 + uid}, 'admin_t', 'DIVISION_MANAGER', '', true) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_rules_page_returns_html(db_session):
    await _seed_admin(db_session)
    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/dashboard/learning/rules")
        assert r.status_code == 200
        assert "כללי למידה" in r.text or "אליאסים" in r.text  # Hebrew tab labels
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_project_alias(db_session):
    await _seed_admin(db_session)
    # Need an existing project to FK to
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()

    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/learning/rules/aliases",
                json={"alias_text": "TestAlias-Z9", "project_id": pid},
            )
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()

    row = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-Z9"))
    assert row is not None
    assert row.source == "manual"


@pytest.mark.asyncio
async def test_delete_project_alias(db_session):
    await _seed_admin(db_session)
    pid = (await db_session.execute(text(
        "SELECT id FROM projects LIMIT 1"
    ))).scalar()
    from app.services.knowledge_service import normalize_hebrew
    db_session.add(ProjectAlias(
        project_id=pid, alias_text="TestAlias-Del",
        normalized_alias=normalize_hebrew("TestAlias-Del"),
        source="manual",
    ))
    await db_session.commit()
    alias = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.alias_text == "TestAlias-Del"))

    async def fake_user():
        return User(id=2001, telegram_id=900002001, username="admin_t",
                    role="DIVISION_MANAGER", password_hash="", is_admin=True)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.delete(f"/dashboard/learning/rules/aliases/{alias.id}")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()

    still = await db_session.scalar(
        select(ProjectAlias).where(ProjectAlias.id == alias.id))
    assert still is None


@pytest.mark.asyncio
async def test_admin_only(db_session):
    """Non-admin user gets 403 on all CRUD endpoints."""
    await db_session.execute(text(
        "INSERT INTO users (id, telegram_id, username, role, password_hash, is_admin) "
        "VALUES (2099, 902002099, 'non_admin', 'PROJECT_MANAGER', '', false) "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db_session.commit()

    async def fake_user():
        return User(id=2099, telegram_id=902002099, username="non_admin",
                    role="PROJECT_MANAGER", password_hash="", is_admin=False)
    app.dependency_overrides[get_current_user] = fake_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post(
                "/dashboard/learning/rules/aliases",
                json={"alias_text": "X", "project_id": 1},
            )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Confirm failure**

```bash
docker exec shan-ai-api pytest tests/test_learning_rules_router.py -v
```

Expected: 4 FAIL (404 from missing routes).

- [ ] **Step 3: Implement `app/routers/learning_rules.py`**

```python
"""Admin CRUD endpoints for the learning-rules page.

All endpoints require is_admin=True. Tabs:
- /aliases — project_aliases CRUD
- /intent_overrides — CRUD
- /correction_pins — list + delete + approve (manual create not exposed; pins are usually AI-proposed)
- /synonyms — query_synonyms CRUD (including sentinel rows like __field_aliases__)
- /pending_approvals — list awaiting_approval RepairProposals + POST /approve
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import (
    ProjectAlias, IntentOverride, CorrectionPin, QuerySynonym, RepairProposal, User
)
from app.routers.login import get_current_user
from app.services.knowledge_service import normalize_hebrew, invalidate_eval_caches
from app.services.ask_router import _normalize_q_hash

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_admin(user: User) -> None:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/dashboard/learning/rules", response_class=HTMLResponse)
async def rules_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)

    aliases = (await session.execute(select(ProjectAlias))).scalars().all()
    intents = (await session.execute(select(IntentOverride))).scalars().all()
    pins    = (await session.execute(select(CorrectionPin))).scalars().all()
    synonyms = (await session.execute(select(QuerySynonym))).scalars().all()
    pending = (await session.execute(
        select(RepairProposal).where(RepairProposal.status == "awaiting_approval")
    )).scalars().all()

    return templates.TemplateResponse("learning_rules.html", {
        "request": request,
        "current_user": current_user,
        "aliases": aliases,
        "intents": intents,
        "pins": pins,
        "synonyms": synonyms,
        "pending": pending,
    })


# ── Aliases ─────────────────────────────────────────────────────────
class AliasCreate(BaseModel):
    alias_text: str
    project_id: int


@router.post("/dashboard/learning/rules/aliases")
async def create_alias(
    body: AliasCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = ProjectAlias(
        project_id=body.project_id,
        alias_text=body.alias_text,
        normalized_alias=normalize_hebrew(body.alias_text),
        source="manual",
        created_by_id=current_user.id,
    )
    session.add(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True, "id": row.id}


@router.delete("/dashboard/learning/rules/aliases/{alias_id}")
async def delete_alias(
    alias_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(ProjectAlias, alias_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Intent overrides ────────────────────────────────────────────────
class IntentOverrideCreate(BaseModel):
    question: str
    forced_intent: str
    forced_param: str | None = None


@router.post("/dashboard/learning/rules/intent_overrides")
async def create_intent_override(
    body: IntentOverrideCreate,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = IntentOverride(
        question_pattern_hash=_normalize_q_hash(body.question),
        forced_intent=body.forced_intent,
        forced_param=body.forced_param,
        source="manual",
        created_by_id=current_user.id,
    )
    session.add(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True, "id": row.id}


@router.delete("/dashboard/learning/rules/intent_overrides/{override_id}")
async def delete_intent_override(
    override_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(IntentOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Correction pins ─────────────────────────────────────────────────
@router.delete("/dashboard/learning/rules/correction_pins/{pin_id}")
async def delete_correction_pin(
    pin_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = await session.get(CorrectionPin, pin_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(row)
    await session.commit()
    invalidate_eval_caches()
    return {"ok": True}


# ── Approve pending proposals (correction_pin only) ────────────────
@router.post("/dashboard/learning/rules/pending/{proposal_id}/approve")
async def approve_pending(
    proposal_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    from app.services.per_question_loop_service import approve_pin
    try:
        await approve_pin(session, proposal_id, current_user.id)
    except (LookupError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ── Test-now (no DB write) ─────────────────────────────────────────
class TestNowRequest(BaseModel):
    question: str


@router.post("/dashboard/learning/rules/test_now")
async def test_now(
    body: TestNowRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    from app.services.ask_router import route
    result = await route(body.question, session, current_user.id, log_to_db=False)
    return {
        "path": result.path,
        "intent": result.intent,
        "param": result.param,
        "answer": result.answer[:1000],
        "sources_text": result.sources_text,
    }
```

- [ ] **Step 4: Implement `app/templates/learning_rules.html`**

This is a long template. Key structure:

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>Shan-AI — כללי למידה</title>
    <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #080a12; color: #e0e0e0; font-family: 'Heebo', sans-serif; }
        .tab-btn { background: #0e1020; border: 1px solid #1a1e38; color: #8b9cf4;
                   padding: 10px 18px; border-radius: 10px 10px 0 0; cursor: pointer;
                   margin: 0 4px; font-weight: 600; }
        .tab-btn.active { background: #5865f2; color: #fff; border-color: #5865f2; }
        .tab-panel { display: none; padding: 24px; background: #0e1020; border: 1px solid #1a1e38;
                     border-radius: 0 14px 14px 14px; min-height: 400px; }
        .tab-panel.active { display: block; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; text-align: right; border-bottom: 1px solid #1a1e38; }
        th { color: #8b9cf4; }
        .btn-mini { padding: 4px 10px; font-size: .85rem; border-radius: 6px; border: none; cursor: pointer; }
        .btn-del { background: rgba(239,68,68,.2); color: #fca5a5; }
        .btn-test { background: rgba(0,212,255,.2); color: #00d4ff; }
        .btn-approve { background: rgba(54,226,115,.2); color: #36e273; }
        .add-form { background: #0a0c18; padding: 16px; border-radius: 10px; margin-bottom: 18px;
                    border: 1px solid #1a1e38; }
        .add-form input, .add-form select, .add-form textarea {
            background: #0f1117; color: #fff; border: 1px solid #2d3047;
            border-radius: 6px; padding: 6px 10px; margin-right: 8px;
        }
    </style>
</head>
<body>
<div class="container-fluid p-4">
    <h2 style="color:#d8e0fa;">🎯 כללי למידה</h2>
    <p style="color:#8b9cf4;">CRUD על הכללים שהמערכת לומדת — אליאסים, אינטנט-אוברייד, פינים, סינונימים, ואישורי המתנה.</p>

    <div style="margin-top: 20px;">
        <button class="tab-btn active" onclick="showTab('aliases')">אליאסים ({{ aliases|length }})</button>
        <button class="tab-btn" onclick="showTab('intents')">אינטנט אוברייד ({{ intents|length }})</button>
        <button class="tab-btn" onclick="showTab('pins')">פינים ({{ pins|length }})</button>
        <button class="tab-btn" onclick="showTab('synonyms')">סינונימים ({{ synonyms|length }})</button>
        <button class="tab-btn" onclick="showTab('pending')">ממתינים לאישור ({{ pending|length }})</button>
    </div>

    <div id="tab-aliases" class="tab-panel active">
        <div class="add-form">
            <input id="new-alias-text" placeholder="alias_text" />
            <input id="new-alias-pid" type="number" placeholder="project_id" />
            <button class="btn-mini btn-test" onclick="createAlias()">הוסף אליאס</button>
        </div>
        <table>
            <thead><tr><th>ID</th><th>Alias</th><th>Project ID</th><th>Source</th><th></th></tr></thead>
            <tbody>
            {% for a in aliases %}
            <tr><td>{{ a.id }}</td><td>{{ a.alias_text }}</td><td>{{ a.project_id }}</td>
                <td>{{ a.source }}</td>
                <td><button class="btn-mini btn-del" onclick="del('aliases', {{ a.id }})">מחק</button></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="tab-intents" class="tab-panel">
        <table>
            <thead><tr><th>ID</th><th>Hash</th><th>Forced Intent</th><th>Param</th><th></th></tr></thead>
            <tbody>
            {% for i in intents %}
            <tr><td>{{ i.id }}</td><td><code>{{ i.question_pattern_hash[:10] }}…</code></td>
                <td>{{ i.forced_intent }}</td><td>{{ i.forced_param or '—' }}</td>
                <td><button class="btn-mini btn-del" onclick="del('intent_overrides', {{ i.id }})">מחק</button></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="tab-pins" class="tab-panel">
        <table>
            <thead><tr><th>ID</th><th>Hash</th><th>Pinned Answer</th><th>Expires</th><th></th></tr></thead>
            <tbody>
            {% for p in pins %}
            <tr><td>{{ p.id }}</td><td><code>{{ p.question_hash[:10] }}…</code></td>
                <td style="max-width:300px; overflow:hidden; text-overflow:ellipsis;">{{ p.pinned_answer }}</td>
                <td>{{ p.expires_at or '—' }}</td>
                <td><button class="btn-mini btn-del" onclick="del('correction_pins', {{ p.id }})">מחק</button></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="tab-synonyms" class="tab-panel">
        <table>
            <thead><tr><th>Original</th><th>Synonyms</th><th>Source</th></tr></thead>
            <tbody>
            {% for s in synonyms %}
            <tr><td>{{ s.original }}</td><td>{{ s.synonyms|tojson }}</td><td>{{ s.source }}</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <div id="tab-pending" class="tab-panel">
        <table>
            <thead><tr><th>ID</th><th>Type</th><th>Patch</th><th>Rationale</th><th></th></tr></thead>
            <tbody>
            {% for r in pending %}
            <tr><td>{{ r.id }}</td><td>{{ r.type }}</td>
                <td><code style="font-size:.8rem;">{{ r.patch_json|tojson }}</code></td>
                <td>{{ r.rationale or '—' }}</td>
                <td><button class="btn-mini btn-approve" onclick="approve({{ r.id }})">אשר</button></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</div>
<script>
function showTab(name) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById(`tab-${name}`).classList.add('active');
}

async function createAlias() {
    const text = document.getElementById('new-alias-text').value.trim();
    const pid = parseInt(document.getElementById('new-alias-pid').value);
    if (!text || !pid) { alert('alias_text + project_id required'); return; }
    const r = await fetch('/dashboard/learning/rules/aliases', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({alias_text: text, project_id: pid}),
    });
    if (r.ok) location.reload();
    else alert('שגיאה: ' + r.status);
}

async function del(table, id) {
    if (!confirm('למחוק?')) return;
    const r = await fetch(`/dashboard/learning/rules/${table}/${id}`, {method: 'DELETE'});
    if (r.ok) location.reload();
    else alert('שגיאה: ' + r.status);
}

async function approve(id) {
    if (!confirm('לאשר את ההצעה?')) return;
    const r = await fetch(`/dashboard/learning/rules/pending/${id}/approve`, {method: 'POST'});
    if (r.ok) location.reload();
    else { const e = await r.json(); alert('שגיאה: ' + (e.detail || r.status)); }
}
</script>
</body>
</html>
```

- [ ] **Step 5: Register router in `app/main.py`**

In the router-import block:
```python
from app.routers import learning_rules as learning_rules_router  # noqa: E402
```

And in the `include_router` block:
```python
app.include_router(learning_rules_router.router)
```

- [ ] **Step 6: Add nav link in `app/templates/learning.html`**

Find the navbar in `learning.html` and add:
```html
<a href="/dashboard/learning/rules" class="nav-link">⚙ כללי למידה</a>
```

(Place wherever other nav links live — match the existing pattern in the file.)

- [ ] **Step 7: Run + verify**

```bash
docker-compose restart fastapi
sleep 4
docker exec shan-ai-api pytest tests/test_learning_rules_router.py -v 2>&1 | tail -15
```

Expected: 4 PASS.

- [ ] **Step 8: Commit**

```bash
git add app/routers/learning_rules.py app/templates/learning_rules.html app/templates/learning.html app/main.py tests/test_learning_rules_router.py
git commit -m "feat(learning): admin CRUD page for aliases / overrides / pins / synonyms"
```

---

## Task 3.7: Phase 3 manual gate

The implementation pieces all have unit/integration tests. The gate confirms they compose end-to-end via the live UI.

- [ ] **Step 1: Open `/dashboard/learning/rules` in the browser**

Confirm:
- 5 tabs render (Aliases / Intent Overrides / Pins / Synonyms / Pending)
- Counts in tab labels match the DB
- Each tab shows its rows in a table

- [ ] **Step 2: Add an alias manually**

In the Aliases tab:
- Type `alias_text="בת ים"`, `project_id=357` (the real Bat Yam project from Phase 2 testing)
- Click "הוסף אליאס"
- Page reloads; new row appears

- [ ] **Step 3: Re-ask the failing question**

On `/dashboard/ask`:
```
מי המנהל של פרויקט בת ים?
```

Now the alias resolves to project 357 (the real one). Answer should be the Bat Yam project card with manager "יהודר בכר, אורית".

- [ ] **Step 4: Trigger an awaiting-approval pin**

Trigger a 👎 correction on a question. The repair loop may propose `correction_pin` as the last-resort fix-type (high risk → awaiting_approval). Check `/dashboard/learning/rules` → "ממתינים לאישור" tab. If a proposal appears:
- Click "אשר"
- Page reloads; the pin moves to the "פינים" tab
- Re-ask the question; AI now returns the verbatim pinned answer (no LLM call)

- [ ] **Step 5: Delete the alias from Step 2**

In the Aliases tab, click "מחק" on the row. Confirm. The page reloads and the row is gone.

Re-ask the same question — confirm the system reverts to baseline behavior (wrong answer without alias).

- [ ] **Step 6: Tag**

```bash
git tag phase-3-complete
```

---

## Self-Review

**1. Spec coverage:**
- §4.3 `field_alias_real` — Task 3.3 (proposer + apply + consume + tests). ✅
- §4.4 `correction_pin` with human-approval gate — Task 3.4 (separate `approve_pin` function, awaiting_approval status). ✅
- §2 `CorrectionPin` table — Task 3.0. ✅
- §3 correction-pin lookup BEFORE other pre-rules — Task 3.2 (Step 0 in `route()`). ✅
- §5.2 admin rules page — Task 3.6. ✅
- §6.1 regression-gate per fix-type — Tasks 3.3/3.4 reuse the existing universal gate. `correction_pin` adds human-approval gate ON TOP (covered). ✅
- Phase 2 lessons (proposer reranking + judge tightening) — Task 3.5. ✅ (not in original spec — added as essential follow-up)

**2. Placeholder scan:**
Searched for `TBD`, `TODO`, `implement later`, `add appropriate`, `similar to`, `fill in`. None found.

**3. Type consistency:**
- `CorrectionPin.question_hash`: `String(64)`, unique. Matches `EvalGoldAnswer.question_hash` and `IntentOverride.question_pattern_hash`. ✅
- `approve_pin(session, proposal_id, user_id)`: matches the call signature used by the new `/dashboard/learning/rules/pending/{id}/approve` endpoint. ✅
- `_DB_FIELD_ALIASES_CACHE: dict[str, str]` — alias→field. Consumed by `_detect_field` in `gold_truth_service`. ✅
- `_DB_CORRECTION_PINS_CACHE: dict[str, dict]` — keyed by `q_hash`, value carries `pinned_answer`, `scope_project_id`, `expires_at`. Consumed by `ask_router.route()`. ✅
- `_candidate_projects` signature unchanged (same kwargs); only the internal ordering changed. ✅
- `_rule_check` signature unchanged. Entity-token check lives in the caller `compare_to_gold`. ✅

**4. Scope check:**
8 tasks, mostly mechanical TDD plumbing. Task 3.6 (admin page) is the largest; templates can grow but the test-coverage scope is bounded (4 endpoints). The plan is one phase, not over-scoped.

No issues found that need rewriting.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-rag-quality-phase-3.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, opus reviews between tasks

**2. Inline Execution** — execute tasks in this session via executing-plans

Which approach?
