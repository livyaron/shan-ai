# RAG Quality — Teaching the System to Answer Correctly

**Date:** 2026-05-09
**Author:** Yaron + Claude (brainstorming)
**Status:** Approved design — ready for implementation plan

## Problem

The RAG / `/ask` pipeline gives bad answers. The user has been trying to teach it via gold-truth pairs and a per-question repair loop, but answers do not improve. Project data is correctly populated in the `Project` table — yet the system still answers wrongly or claims it has no information.

**Concrete failing case:**

```
Q:    באיזה שלב נמצא פרויקט בית הגדי?
Gold: הפרויקט רשום בשלב תכנון
AI:   לא נמצא בנתונים פרויקט בשם 'בית הגדי'. להלן רשימת הפרויקטים שנמצאו: 🏗️ רעות …
```

The question contains a real project name; the AI dumps unrelated projects.

## Root Causes

1. **Eval/prod path mismatch.** `per_question_loop_service._answer()` calls `knowledge_service.answer_with_full_context()` directly. Production routes most short Hebrew questions to `project_tools.answer_project_query()` via `_is_project_query()` in `app/routers/ask.py`. The repair loop tests and patches a path the user does not actually hit. "Fixed" in eval ≠ fixed in production.
2. **Identifier matching is fragile.** `find_projects_by_identifier()` uses token overlap on `project_identifier` and `name`. A free-text reference like "בית הגדי" misses if the stored identifier or name uses a different form (slash, abbreviation, English).
3. **Fix-types are too narrow.** The 5 existing fix-types — `add_abbreviation`, `add_synonym`, `stop_word_remove`, `field_alias`, `prompt_patch` — are all string-level retrieval tweaks. None can teach "this name = this project," override a wrong intent, alias a real `Project` column, or pin a verbatim answer.
4. **No user-feedback channel.** Gold pairs must be authored manually; a wrong answer in `/ask` has no thumbs-up/down or correction box. Successful answers are never captured as gold either.

## Goals

- Eval pass-rate ≥ 85% on the gold set frozen at the start of Phase 3, measured within 7 days of Phase 3 merge
- "בית הגדי" reproducer: ask → wrong → 👎+correct → ~60 seconds later, ask again → correct
- Telegram + `/ask` + eval loop produce the same `path`/`intent`/`param` for the same question and the same gold-comparable answer (HTML/Markdown rendering differences allowed; canonical text content equivalent)
- All four learning channels live: gold-truth loop · inline thumbs+correction · manual admin rules · auto-cache from 👍

## Non-Goals

- Replacing the LLM provider or model
- Re-architecting `Project` / `KnowledgeChunk` schemas
- Unifying `project_tools` and `knowledge_service` into one pipeline (Approach B — rejected as too large for this sprint)
- Switching identifier matching to embeddings-only (Approach C — rejected; doesn't address learning loop)

---

## §1 Architecture

```
┌──────────────────────────┐    ┌──────────────────────────┐
│ /dashboard/ask  (web)    │    │ Telegram bot              │
└─────────────┬────────────┘    └─────────────┬────────────┘
              │                                │
              ▼                                ▼
        ┌────────────────────────────────────────┐
        │   ask_router.route(q)  ← NEW shared    │
        │   - pre-rules pin?  - alias lookup?    │
        │   - project intent?  - RAG fallback?   │
        └────────────────────┬───────────────────┘
                             │
        ┌────────────────────┴───────────────────┐
        │ project_tools  │ knowledge_service     │
        └────────────────────┬───────────────────┘
                             │
                       answer + log_id
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │ /ask UI: 👍 / 👎 buttons per answer    │
        │  👎 → correction box → save_gold       │
        │       → trigger single-question repair │
        │  👍 → save EvalGoldAnswer (auto_user)  │
        └────────────────────────────────────────┘

Eval loop _answer()  →  ask_router.route()   (NOT raw RAG anymore)
```

Routing logic moves out of `app/routers/ask.py` into a new module `app/services/ask_router.py`. The web router, Telegram polling handler, and per-question eval loop all call `ask_router.route(question, session, user_id, log_to_db=True) → AnswerResult`. Eval = production from this point on.

---

## §2 Data Model

### New tables

| Table | Columns | Purpose |
|---|---|---|
| `project_aliases` | id, project_id (FK Project, cascade delete), alias_text, normalized_alias (unique index), source (manual/ai/user_correction), created_by_id, created_at | Free-text name → project_id. Looked up before fuzzy match. |
| `intent_overrides` | id, question_pattern_hash (unique index, sha256 of normalized question), forced_intent, forced_param (nullable), source, created_by_id, created_at | Skip LLM intent detection when normalized question hash hits. |
| `correction_pins` | id, question_hash (unique index), pinned_answer, scope_project_id (FK Project, nullable), expires_at (nullable), source, created_by_id, created_at | Verbatim answer return when normalized question matches. Highest priority. Bypasses all LLM calls. |
| `answer_feedback` | id, query_log_id (FK QueryLog), user_id, vote (`up`/`down`), correction_text (nullable, only on `down`), gold_id (FK EvalGoldAnswer, nullable — set when converted to gold), created_at | One row per click. 👍 enqueues auto-gold conversion; 👎 opens correction box. |
| `route_traces` | id, query_log_id (FK QueryLog), path (`correction_pin`/`decision`/`project_tools`/`rag`), intent, applied_rule_ids (JSON array), ms_total, ms_llm, created_at | Light-weight per-answer trace for "which rule fired most?" admin views. |

### Extended values (no schema change)

- `EvalGoldAnswer.source` adds: `auto_user_confirmed` (👍 cache), `user_correction` (👎 + correction)
- `RepairProposal.type` extends FIX_TYPES list with: `project_alias`, `intent_override`, `field_alias_real`, `correction_pin` (each gets its own `_apply_patch()` branch)
- `RepairProposal` adds new column `applied_artifact_id` (nullable int) — points to the row created by `_apply_patch` in the fix-type's target table, used for clean rollback

### Untouched

`EvalGoldAnswer`, `RepairProposal` (column add only), `QuerySynonym`, `PromptOverride`, `Project`, `KnowledgeChunk`, `KnowledgeFile` — all keep working unchanged.

---

## §3 Routing & Lookup Order

`ask_router.route(question, session, user_id, log_to_db=True, snapshot_mode=False) → AnswerResult`.

Pipeline (top → bottom; first hit wins):

```
1. normalize_q  = normalize_hebrew(question.strip())
   q_hash       = sha256(normalize_q)

2. CORRECTION-PIN HIT?
   correction_pins WHERE question_hash = q_hash AND (expires_at IS NULL OR expires_at > now)
   → return pinned_answer  [zero LLM calls]
   → log with sources_used = [{"source": "correction_pin", "pin_id": ...}]

3. ALIAS RESOLVE
   For each project_alias WHERE normalized_alias IN tokens(normalize_q):
       inject ` (project_identifier=<X>)` hint into question text
   → continue pipeline with enriched question

4. INTENT OVERRIDE HIT?
   intent_overrides WHERE question_pattern_hash = q_hash
   → skip LLM intent detection
   → return project_tools.answer_project_query(..., precomputed_intent=forced_intent, precomputed_param=forced_param)

5. DECISION KEYWORDS  → answer_decisions_question  (existing)

6. _is_project_query(q)  → project_tools.answer_project_query  (existing — now sees alias-enriched question)

7. DEFAULT  → knowledge_service.answer_with_full_context  (existing RAG)
```

`_answer()` in `per_question_loop_service.py` swaps from `ks.answer_with_full_context(...)` to `ask_router.route(..., log_to_db=False, snapshot_mode=True)`. Telegram bot's message handler also routes through `ask_router.route()`.

### AnswerResult dataclass

```python
@dataclass
class AnswerResult:
    answer: str
    sources_used: list[dict]
    log_id: int | None
    path: str          # "correction_pin" | "decision" | "project_tools" | "rag"
    intent: str | None
    param: str | None
```

`path` is logged to `route_traces.path` and enables admin tooling to see which route handled each question.

---

## §4 New Fix-Types in Repair Loop

Each fix-type defines: when LLM proposer should pick it, the `patch_json` shape, the shadow-config ContextVar that simulates it during regression checks, and how `_apply_patch` persists it.

### 4.1 `project_alias`

**When:** AI returned "לא נמצא" or returned the wrong project, but the gold names a real project. The proposer is given the question + a slice of `Project.name`/`project_identifier` rows and must pick the right project_id.

**patch_json:** `{"alias_text": "בית הגדי", "project_id": 47}`

**Shadow apply:** `_shadow_project_aliases: ContextVar[dict]` keyed by normalized alias → project_id. `ask_router` step 3 reads `db_cache | shadow`.

**Persist:** insert row into `project_aliases` (source="ai"). Increments alias-cache TTL invalidation.

### 4.2 `intent_override`

**When:** Question routes to `project_tools` but `_ai_detect_intent` picks wrong intent (e.g., classifies "באיזה שלב …" as `general` instead of `by_identifier`).

**patch_json:** `{"question": "...", "forced_intent": "by_identifier", "forced_param": "בית הגדי"}`

**Shadow apply:** `_shadow_intent_overrides: ContextVar[dict]` keyed by normalized-q hash. `ask_router` step 4 reads `db_cache | shadow`.

**Persist:** insert row into `intent_overrides`.

### 4.3 `field_alias_real`

**When:** A Hebrew abbreviation or synonym for a `Project` column isn't recognized by `_FIELD_KEYWORDS` in `gold_truth_service` or by the column-keyword routing in `project_tools._detect_intent`.

**patch_json:** `{"alias": "מנה\"פ", "field": "manager"}`

**Shadow apply:** `_shadow_field_aliases: ContextVar[dict]`.

**Persist:** new sentinel row in `query_synonyms`: `original="__field_aliases__"`, `synonyms=["alias=field", ...]`. Reuses the sentinel pattern already in code; no new table.

### 4.4 `correction_pin`

**When:** Last resort. AI is consistently wrong on a question, no alias/intent/synonym tweak helps, and the gold answer is short and stable.

**patch_json:** `{"question": "...", "pinned_answer": "...", "scope_project_id": 47, "ttl_days": 30}`

**Shadow apply:** `_shadow_correction_pins: ContextVar[dict]` keyed by normalized-q hash → answer.

**Persist:** insert row into `correction_pins` with `expires_at = now + ttl_days`.

**Risk gate:** `correction_pin` patches always set `risk="high"` and require human approval (admin clicks "approve" in the rules page) before `_apply_patch` writes the row. Other fix-types apply automatically when the regression-gate passes.

### 4.5 Updated FIX_TYPES list and proposer rubric

```python
FIX_TYPES = [
    "add_abbreviation", "add_synonym", "stop_word_remove",
    "field_alias", "prompt_patch",          # existing
    "project_alias", "intent_override",     # new — auto-apply
    "field_alias_real",                     # new — auto-apply
    "correction_pin",                       # new — human-approve only
]
```

`_REPAIR_SYS` prompt extension — short descriptions + selection rubric:

```
1. AI failed because a name wasn't recognized                → project_alias
2. AI picked wrong intent (by_identifier / by_year / etc.)   → intent_override
3. AI couldn't map a Hebrew term to a Project column         → field_alias_real
4. Retrieval and intent are correct but answer wording is off → prompt_patch
5. Last resort, after others fail (high risk, human approves) → correction_pin
```

The proposer picks ONE fix-type per attempt; max 3 attempts per question (existing). Each rejected proposal moves down the rubric.

---

## §5 UI Changes

### 5.1 Thumbs UI on `/dashboard/ask`

Each answer card gets two buttons under the answer text:

```
👍 תשובה נכונה        👎 תקן אותי
```

**👍 click** → `POST /dashboard/ask/feedback` `{log_id, vote: "up"}`

- Insert `answer_feedback(vote="up")`
- Async job: copy `(question, ai_response)` into `EvalGoldAnswer` with `source="auto_user_confirmed"` only if no existing gold for that `question_hash`
- UI: button flips to `✓ נשמר כדוגמה`

**👎 click** → opens inline correction box pre-filled with the AI's wrong answer:

```
מה היית מצפה לשמוע?
[textarea]
[שמור ולמד]   [ביטול]
```

`POST /dashboard/ask/correct` `{log_id, vote: "down", correction_text}`

- Insert `answer_feedback(vote="down", correction_text=...)`
- Call `save_gold(question, gold_answer=correction_text, source="user_correction")`
- Trigger `run_one_question` for THIS gold row via FastAPI `BackgroundTasks` (single-process, fire-and-forget — no Celery/queue dependency added). Existing `EvalRun.status='running'` partial-unique index already prevents concurrent cycles.
- Return `{status: "learning", run_id}` so UI shows toast `🔄 לומד מהתיקון... (run #123)` linked to `/dashboard/eval-curate?focus=<gold_id>`

### 5.2 Admin rules page — `/dashboard/learning/rules`

New tab block on the existing `/dashboard/learning` page:

```
[ Lessons ]  [ Aliases ]  [ Intent Overrides ]  [ Correction Pins ]  [ Synonyms ]
```

Each tab is a CRUD table over one DB table:

| Tab | Columns | Inline actions |
|---|---|---|
| Aliases | alias_text, project (name+id), source, created_by | edit · delete · merge-duplicates |
| Intent Overrides | question (truncated), forced_intent, forced_param, source | edit · delete · test-now |
| Correction Pins | question, pinned_answer (truncated), expires_at, scope, source | edit · delete · extend-ttl · approve (if pending) |
| Synonyms | original, synonyms (chips), source | edit · delete |

**Test-now** button on each row → modal that runs `ask_router.route()` with the question and shows `path` + `answer`. No DB write.

**Add new rule** at top of each tab → form for that table. Server-side validation. Manual rows get `source="manual"`, `created_by_id=current_user`.

### 5.3 Auto-learn cache constraints

- Only write `EvalGoldAnswer` if no row exists for that `question_hash` (don't overwrite manual/user_correction gold)
- Do NOT trigger a repair loop on 👍 — auto-gold is retention only; loop already passed
- `auto_user_confirmed` rows are eligible for the next batch eval cycle, so drift is caught
- Rate-limit: skip auto-gold if user has > 5 thumbs in the last minute (anti-spam)

---

## §6 Regression Gates, Safety & Telemetry

### 6.1 Regression-gate per fix-type

Existing gate: `passing_before − passing_after` over all gold rows; reject if non-empty. Keep as the universal floor. Tighten per-type:

| Fix-type | Max regressions | Rationale |
|---|---|---|
| `correction_pin` | 0 (and human approval) | Verbatim bypass; high-risk if wrong |
| `intent_override` | 0 | Hash-keyed; should never affect other questions |
| `project_alias` | 0 | Token-keyed; should never affect other questions |
| `field_alias_real` | 0 | New mapping — must not break field detection elsewhere |
| `prompt_patch` | 0 | Existing |
| `add_synonym` / `add_abbreviation` / `stop_word_remove` | 0 | Existing |

If `intent_override` or `project_alias` causes a regression, log loudly and reject — it means the hash/token assumption is broken and the patch design is wrong.

### 6.2 Snapshot budget

`_snapshot_passing` now invokes `ask_router.route()` per gold row. For 100 gold rows × 3 attempts × 2 snapshots = 600 routes per cycle. Mitigations:

- `ask_router.route(..., snapshot_mode=True)` → skips `log_to_db`, skips `learned_instructions` reload, caches alias/intent dicts for the call's lifetime
- Parallelize snapshot with `asyncio.gather` capped at 8 concurrent (currently sequential)
- `EvalRun.config_json.snapshot_workers` configurable

### 6.3 Kill-switch parity

`SystemFlag.eval_kill="1"` already aborts `run_cycle`. Extend:

- `/dashboard/learning/rules` shows a banner + button: `🛑 השבת לולאת למידה` flips the flag
- While set: 👎-correction still saves gold but does NOT trigger `run_one_question` — corrections queue for manual replay later

### 6.4 Per-answer telemetry

`QueryLog.sources_used` JSON gets a routing trace entry:

```json
[
  {"source": "ask_router", "path": "project_tools", "intent": "by_identifier", "param": "בית הגדי",
   "applied_rules": [{"type": "project_alias", "id": 12, "alias": "בית הגדי"}],
   "ms": {"router": 4, "intent": 180, "data": 22, "llm": 1430}}
]
```

Also write a `route_traces` row (lighter, structured). Powers admin "which rule fired most?" without parsing JSON.

### 6.5 Learning-effectiveness card

Top of `/dashboard/learning`:

```
לולאת למידה — 7 ימים אחרונים
┌─────────────────┬─────────────────┬─────────────────┐
│ pass-rate       │ rules-applied   │ corrections-in  │
│  72% → 89%      │  +47            │  31             │
└─────────────────┴─────────────────┴─────────────────┘
[ פירוט per fix-type ↓ ]
```

Source: `EvalRun`, `RepairProposal`, `AnswerFeedback`. If pass-rate doesn't trend up over a week, the design is wrong — revisit, don't pile on more fix-types.

### 6.6 Rollback

- Every applied `RepairProposal` records `applied_artifact_id` at apply time
- Admin rules page → "undo" on each row → calls `_unapply_patch(proposal_id)`, deletes the artifact row, marks proposal `status="rolled_back"`
- Cache invalidation triggered on rollback so production picks up the removal immediately

---

## §7 Testing & Rollout

### 7.1 Test layers

**Unit:**

- `ask_router.route()` — pin hit, alias resolve, intent override, fall-through; assert `path` field correct in each
- `project_aliases` lookup — Hebrew normalization (final-letters, prefixes), token overlap, multiple aliases per project
- New `_apply_patch` branches — each fix-type writes the right row, sets `applied_artifact_id`, increments cache invalidation
- `_unapply_patch` — round-trip per fix-type (apply → unapply → DB clean)

**Integration (real DB, fastembed, mocked LLM):**

- "בית הגדי" reproducer:
  1. Seed Project row id=47, name="בית הגדי", stage="תכנון"
  2. `route("באיזה שלב נמצא פרויקט בית הגדי?")` — assert wrong answer (current behavior)
  3. Run `run_one_question` with gold="הפרויקט רשום בשלב תכנון"
  4. Assert: proposal created with `type="project_alias"`, applied, alias row in DB; second route returns gold answer; `path="project_tools"`, `intent="by_identifier"`
- Eval-vs-prod parity: same 10-question set through `route()` and through a Telegram-handler test harness; answers must be string-equal

**End-to-end (Playwright on `/dashboard/ask`):**

- 👎 correction → toast → poll `/dashboard/eval-curate?focus=…` until `status="fixed"` → re-ask → green answer
- 👍 → assert `EvalGoldAnswer` row exists with `source="auto_user_confirmed"`

**Regression suite:**

- Take the current `EvalGoldAnswer` table as a baseline
- Pre-merge CI: `run_cycle` against this set, fail if pass-rate drops vs. baseline. Catches "fix that breaks N other questions."

### 7.2 Rollout phases

| Phase | Ship | Gate before next |
|---|---|---|
| **0 — Prep** | New tables (migration), `ask_router.route()` extracted, `_answer()` switched to use it. No user-visible change. | A 20-question curated smoke set passes; on the full gold set, no individual question drops from PASS to FAIL with > 10% pass-rate decrease overall. If overall drop > 10%, halt rollout and investigate (likely a routing-coverage gap in `ask_router`). |
| **1 — Aliases + intent_override** | Add 2 fix-types + admin tabs. No UI thumbs yet. | "בית הגדי" reproducer passes; 7-day eval cycle shows pass-rate up |
| **2 — Thumbs UI + auto-gold** | 👍/👎 buttons on `/ask`. Background single-question repair on 👎. | At least 10 user thumbs collected; 0 unhandled exceptions |
| **3 — `field_alias_real` + `correction_pin`** | Last fix-types. correction_pin requires admin approval before apply. | Manual smoke: pin a known-bad question, confirm verbatim return, expire after TTL |
| **4 — Telemetry tile + rule-effectiveness dashboard** | Pass-rate trend card, fix-type breakdown. | Visible green trend over 14 days, or revisit design |

Each phase is a separate PR. Phase 0 must merge and soak ~24h before Phase 1.

### 7.3 Migration safety

- All new tables nullable-FK-friendly + `IF NOT EXISTS` create
- App startup must NOT 500 if a table is missing — `_ensure_eval_caches` already swallows missing tables; new caches use the same pattern
- `RepairProposal.applied_artifact_id` is a new nullable column; no backfill — old rows simply aren't unapplyable

### 7.4 Definition of done

- "בית הגדי" example: ask → wrong → 👎+correct → ~60 seconds later, ask again → correct (end-to-end on real prod)
- Eval pass-rate ≥ 85% on the gold set frozen at start of Phase 3, measured within 7 days of Phase 3 merge
- Telegram + `/ask` + eval produce equivalent answers (same `path`/`intent`/`param`; canonical text content matches under rendering normalization) for the same question (parity test passes daily in CI)
- Zero `correction_pin` auto-applies — guardrail check; must be 100% human-approved

---

## Open Questions

None at design time. Re-open during implementation if encountered.

## Appendix — Files Touched

- **New:** `app/services/ask_router.py`, migrations for 5 new tables
- **Modified:** `app/routers/ask.py` (delegates to ask_router), `app/services/per_question_loop_service.py` (`_answer` uses ask_router; new fix-type branches in `_apply_patch`/`_unapply_patch`/`_REPAIR_SYS`), `app/services/knowledge_service.py` (new shadow ContextVars + cache loaders for aliases/intent_overrides/correction_pins/field_aliases), `app/services/project_tools.py` (consume `_DB_FIELD_ALIASES_CACHE`), `app/services/telegram_polling.py` (route via ask_router)
- **Templates:** `app/templates/ask.html` (thumbs + correction box), `app/templates/learning.html` (new Rules tabs), new `app/templates/learning_rules.html`
- **Models:** `app/models.py` add `ProjectAlias`, `IntentOverride`, `CorrectionPin`, `AnswerFeedback`, `RouteTrace`; add `applied_artifact_id` column on `RepairProposal`
