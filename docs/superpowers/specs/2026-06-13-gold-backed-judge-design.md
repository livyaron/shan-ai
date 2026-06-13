# Gold-Backed Judge — Phase B of AI-Quality Track

**Date:** 2026-06-13
**Status:** Approved
**Goal:** make the eval judge trustworthy by comparing production answers against human-approved gold instead of an LLM-guessed reference, then re-judge so the failure numbers reflect reality.

## Problem (from calibration)

Hand-verified 15 FAIL rows against the DB. The judge is unreliable:
- **Non-deterministic:** identical (question, answer) pairs `id=2784` and `id=2793` got MISSING_DATA vs WRONG_PROJECT.
- **High false-negative rate:** "מה השלב של חולה?" answered perfectly (DB confirms חולה=WBE-252, stage=עבודה אזרחית) but judged WRONG_PROJECT. "מי המנהל של קריית גת?" → "לא נמצאו תוצאות" is correct (no such project exists) but judged MISSING_DATA. ≥33% of sampled FAILs were wrong.
- **Root cause:** `judge_one` compares the answer against `propose_gold()`'s output, which falls back to an LLM **guess** when no gold exists. The guess varies per call and is often wrong, so the comparison is against a bad reference. `compare_to_gold` itself is already deterministic (temp=0, rule-check first, entity guard) — the instability is entirely in the guessed reference.

So 37% PASS / 63% FAIL is not credible; real pass-rate is higher and WRONG_PROJECT is overcounted.

## Design

### Decision recap
- **Matching:** exact normalized-hash now (`question_hash` already normalizes Hebrew via `normalize_hebrew`). Semantic/pgvector matching deferred to a later phase. Production logs contain many exact-repeat questions, so exact match covers a large fraction.
- **Seeding:** auto-seed gold from DB-derivable questions (deterministic `propose_gold(use_llm=False)` → `source="db_lookup"`); LLM-needed questions require manual human approval via the existing curate UI.

### 1. Judge prefers real gold — `judge_backfill_service.judge_one`
- At the top of `judge_one`, call `gts.get_gold(session, log.question)` (exact normalized-hash lookup, already implemented).
- **Gold hit:** `score = compare_to_gold(question, answer, gold.gold_answer)` → verdict via existing thresholds. This path is fully deterministic and trustworthy.
- **Gold miss:** keep the current `propose_gold` fallback, but the verdict is "low-confidence". Persist the distinction (see §4) so the dashboard can separate trustworthy from guessed verdicts.
- Empty answer → `("FAIL","REFUSED")` unchanged.
- The failure-type classifier (`_classify_failure`) is unchanged but now receives a real gold reference on gold-hit rows, so `WRONG_PROJECT`/`MISSING_DATA` labels become reliable too.

### 2. Persist gold-backed flag — `query_logs`
- Add column `judged_against_gold BOOLEAN` (nullable, default NULL) to `QueryLog`. NULL = not judged; True = compared against real gold; False = compared against an LLM guess.
- Migration: `ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS judged_against_gold BOOLEAN;` (run local + documented for Railway, like the other guardrails in CLAUDE.md).
- `judge_one` returns this flag; `run_backfill` writes it alongside verdict.

### 3. Auto-seed gold from production — `POST /dashboard/eval/gold/seed-from-production`
- New endpoint + small service function. Steps:
  1. Pull distinct frequent questions from `query_logs` (reuse the candidates dedup/rank logic — frequency desc).
  2. For each not already in gold: `propose_gold(session, q, use_llm=False)`.
  3. If the proposal `source == "db_lookup"` and answer non-empty → `save_gold(..., source="db_lookup", user_id=current_user.id)`.
  4. Skip (leave for manual) when no DB answer (would need LLM).
- Returns `{seeded: N, needs_manual: M, total_candidates: K}`.
- Mirrors the existing `bulk_approve` pattern but sources questions from production logs instead of the static `SEED_QUESTIONS` list.
- Button on `/dashboard/quality`: "🌱 זריעת gold מ-DB".

### 4. Re-judge gold-covered rows — `POST /dashboard/eval/rejudge`
- `run_backfill` skips rows with non-NULL `judge_verdict`, so it will not re-judge already-labeled rows after gold grows. This endpoint targets exactly those.
- New service `rejudge_gold_covered(session, limit)`:
  1. Select `query_logs` rows whose `question_hash` matches an existing gold row (compute hash per row or join on a hash set of gold hashes) — regardless of current `judge_verdict`.
  2. Re-run `judge_one` on each, **overwriting** `judge_verdict`, `failure_type`, `judged_against_gold`.
  3. Same per-row error isolation, progress dict, and concurrency guard as `run_backfill` (reuse the `_progress` pattern; separate task ref).
- Button on `/dashboard/quality`: "♻️ שיפוט מחדש (gold)". Polls the same status shape.

### 5. Dashboard: gold-coverage trust indicator — `/dashboard/quality`
- `quality_data` adds: `gold_coverage = {gold_backed: count(judged_against_gold=True), guessed: count(judged_against_gold=False)}` and total gold-set size.
- Template shows a line: "X מתוך Y פסקי דין מגובי-gold (Z תשובות זהב בסט)" so the viewer knows how much of the pass-rate is trustworthy.

### Human step (out of code scope)
After §3 auto-seeds the DB-derivable questions, the user curates the LLM-needed questions ("needs_manual") via the existing curate UI to reach ≥50 gold answers, then triggers §4 re-judge.

## Data flow

```
query_logs (200 judged, guessed references)
   │
   ├─ §3 seed-from-production ──► propose_gold(use_llm=False) ──► eval_gold_answers (db_lookup, auto)
   │                                              └─ needs_manual ──► curate UI (human) ──► eval_gold_answers (manual)
   │
   └─ §4 rejudge gold-covered ──► judge_one (gold hit) ──► overwrite judge_verdict + judged_against_gold=True
                                                                  └─► §5 dashboard shows trustworthy vs guessed split
```

## Error handling
- Seed + rejudge: per-row try/except, rollback, continue; `_progress` finally-guarded `running=False` (same as `run_backfill`).
- `get_gold` miss is normal control flow, not an error.
- Migration is idempotent (`IF NOT EXISTS`).

## Testing
- `judge_one` gold-hit: with a seeded gold row, returns a gold-backed verdict and `judged_against_gold=True`; gold-miss returns `False`. (mock `compare_to_gold`/`propose_gold`.)
- `rejudge_gold_covered`: only touches rows whose question has gold; overwrites an existing verdict; leaves non-covered rows untouched.
- seed-from-production: DB-lookup proposals get saved with `source="db_lookup"`; LLM-needed questions are counted as `needs_manual`, not saved.
- All existing eval/backfill tests stay green.

## Success criteria
1. ≥50 gold answers (auto-seeded + human-curated).
2. Re-judge produces a gold-backed pass-rate; `judged_against_gold=True` on all gold-covered rows.
3. Spot-check: the 2 known false-negatives (חולה stage, קריית גת not-found) now judge PASS.
4. Dashboard shows the gold-backed vs guessed split.

## Out of scope (later)
- Semantic/pgvector gold matching for paraphrased questions.
- Fixing the underlying retrieval failures (the actual MISSING_DATA / WRONG_PROJECT causes) — that is the next phase, now that the judge is trustworthy.
- Auto-curation of LLM-needed gold (stays human-approved by design).
