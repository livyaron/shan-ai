# Distinct-Question Eval — Phase C of AI-Quality Track

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Report eval pass-rate over **distinct questions**, not raw log rows, so duplicate-heavy traffic stops skewing the picture — and let a manager re-judge the deduped representative set after gold changes.

## Problem

Railway `query_logs` has 363 rows but only **85 distinct questions**. Traffic is brutally skewed:
- "מי המנהל של בת ים?" = 161 rows (44% of all logs)
- "אשלים" = 76 rows

Two questions are 65% of every metric. The per-row pass-rate (55% overall, 71% gold-backed) really measures those two repeats, not the system's spread across problems. The earlier "38 HALLUCINATION" finding was entirely one question ("אשלים") repeated — a stale broken-alias state, mislabeled by the cause classifier. Per-row counting hides this.

We need the honest denominator: **one verdict per distinct question.**

## Design

### Decision recap (approved)
- **A — reporting aggregation** is the metric (free, instant; verdicts already exist from backfill): group by normalized question, latest row per `question_hash` wins → one verdict per distinct question.
- Plus **a scoped re-judge** of the representative set for after gold curation. Not run on every dashboard load (Groq cost).
- No physical dedup of `query_logs` — raw history is preserved.

### 1. `distinct_question_eval(session)` — aggregation service
New file `app/services/distinct_eval_service.py`.

- Select `query_logs` rows with non-null `ai_response`, ordered newest-first.
- Group by `question_hash(question)` (reuse `gold_truth_service.question_hash` — same normalization gold uses, so a question's verdict and its gold align). Keep the **latest** row per hash as the representative.
- Return a list of dicts: `{question, question_hash, verdict, failure_type, judged_against_gold, count}` where `count` = how many raw rows share that hash.
- Provide a summary helper returning `{distinct_total, distinct_pass, distinct_fail, distinct_unjudged, gold_backed, pass_rate}` computed over distinct questions (a question counts once regardless of repeat volume).

### 2. `GET /dashboard/quality/distinct` — data endpoint
In `app/routers/eval_loop.py`, auth-protected like siblings. Returns:
```json
{
  "summary": {"distinct_total": 85, "distinct_pass": .., "distinct_fail": .., "distinct_unjudged": .., "gold_backed": .., "pass_rate": ..},
  "failures": [{"type": "WRONG_PROJECT", "count": ..}, ...],   // by DISTINCT question
  "most_asked": [{"question": "..", "count": 161, "verdict": "..", "failure_type": ".."}, ...]  // top 15 by row count
}
```
Failure ranking counts each distinct failing question once.

### 3. `POST /dashboard/eval/rejudge-distinct` — refresh representatives
In `app/routers/eval_loop.py` + a function in `judge_backfill_service.py` (`rejudge_distinct(session)`):
- Determine the representative (latest) row id per distinct `question_hash`.
- Re-run `judge_one` on each representative, OVERWRITING `judge_verdict` / `failure_type` / `judged_against_gold` (gold may have changed since the row was first judged).
- Reuse the existing `_rejudge_progress` dict + `get_rejudge_progress()` + the create_task/`done()` concurrency guard already used by `rejudge_gold_covered` (one in-flight rejudge of either kind at a time — share the guard).
- Status read via the existing `GET /dashboard/eval/rejudge/status`.

### 4. Quality dashboard — distinct panel
`app/templates/quality.html`:
- New panel at the top: **distinct-question pass-rate** as the headline number, with `distinct_total` and `gold_backed` shown; the existing per-row verdict chart relabeled "נפח (לפי שורות)" / secondary.
- Failure ranking switched to the distinct counts (or shown both, distinct primary).
- "Most-asked" table: top 15 questions by row count with their single verdict — makes the skew visible.
- "♻️ שיפוט מחדש (שאלות ייחודיות)" button → `POST /dashboard/eval/rejudge-distinct`, polling the existing rejudge status.

## Data flow
```
query_logs (363 rows, 85 distinct)
   └─ distinct_question_eval ── group by question_hash, latest wins ──► 85 representatives
                                                                          ├─► /quality/distinct (metric, free)
                                                                          └─► rejudge-distinct ──► judge_one (overwrite) ──► fresh verdicts
```

## Error handling
- Aggregation: pure read; empty table → zeros, no crash.
- rejudge-distinct: per-row try/except + rollback + the shared concurrency guard, same as `rejudge_gold_covered`.

## Testing
- `distinct_question_eval`: 3 rows of same question (2 verdicts) + 1 other → 2 distinct entries; the repeated question's entry uses the LATEST row's verdict and `count==3`.
- summary: pass_rate computed over distinct questions, a 161-dup question counts once.
- `rejudge_distinct`: judges exactly one representative per distinct question (await_count == distinct_total), overwrites verdict.
- endpoint returns the documented shape; auth-protected.

## Success criteria
1. Dashboard headline is distinct-question pass-rate over 85 questions, not 363 rows.
2. "Most-asked" table exposes the בת-ים/אשלים skew.
3. Failure ranking reflects distinct questions (the "38 hallucination" collapses to 1 distinct question).
4. Re-judge-distinct refreshes representatives against current gold.

## Out of scope
- Fixing the `failure_type` classifier mislabel (separate phase — noted: it calls "not found" HALLUCINATION).
- Curating gold to 50 (separate; the `/gold` flow already exists).
- Semantic dedup (paraphrases counted separately — exact normalized hash only, consistent with gold matching).
