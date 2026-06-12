# Quality Baseline — Phase 1 of AI-Quality Improvement Track

**Date:** 2026-06-12
**Status:** Approved (approach A: measure → refactor → fix)
**Goal:** within ~1 week of usage, know the real failure rate of AI answers and the top failure causes, with infrastructure to keep measuring automatically.

## Problem

- 544 real questions in `query_logs`, but 98% have `user_feedback = 0` (unrated), `judge_verdict` is NULL on all rows, `failure_type` set on only 4.
- Gold set has 10 questions. Last eval run: 7/10 pass.
- Result: no data-driven picture of where the AI fails. Every improvement so far has been guesswork.

Existing assets to build on: `eval_runs` / `eval_gold_answers` tables, eval loop with LLM judge (`gold_truth_service`, `per_question_loop_service`), curate UI (`/dashboard/eval/curate`), `answer_feedback`, `route_traces`, 15-minute report scheduler.

## Design

### 1. Offline judge backfill

New service `app/services/judge_backfill_service.py`:

- Input: last N (default 200) `query_logs` rows where `judge_verdict IS NULL` and `ai_response` is non-empty.
- For each row, run the existing eval judge prompt (Groq) on question + answer, grounded on current DB data (reuse `gold_truth_service.compare_to_gold`-style judging; where no gold exists, judge against fresh DB lookup of the same question via the ask pipeline's retrieval, answer-vs-data consistency).
- Writes to existing columns: `judge_verdict` (`pass` / `partial` / `fail`) and `failure_type` — taxonomy (uppercase strings, stored as-is):
  - `WRONG_PROJECT` — answered about the wrong project / wrong entity
  - `MISSING_DATA` — data exists in DB but absent from answer
  - `HALLUCINATION` — answer states facts not in DB
  - `UNSTABLE` — answer materially differs across retries of same question (only set by eval loop, not backfill)
  - `STRUCTURE` — malformed output (JSON/format breakage)
  - `REFUSED` — model declined / empty answer
- Idempotent: never re-judges a row with non-NULL `judge_verdict`. Batch size + rate-limit aware (Groq).
- Trigger: button on the quality dashboard ("שפוט N שאלות אחרונות") → background task; progress via simple polling endpoint. Also runnable as `python -m app.services.judge_backfill_service` style entrypoint for one-off runs.

### 2. Gold set expansion (10 → 50+)

- New candidate source for the existing curate page: `GET /dashboard/eval/gold/candidates-from-production` returns ranked candidates from `query_logs`:
  1. all rows with human rating (`user_feedback != 0`)
  2. rows judged `fail`/`partial` by backfill
  3. most-frequent normalized questions (dedup by trimmed/lowered text)
- Curate UI gets a second list section "מועמדים מהשטח" with the same approve/edit flow already used for DB-proposed answers. No new approval mechanics.
- Target: human curates to ≥50 gold rows. (Tool provides candidates; the curation itself is manual by design — gold stays human-approved.)

### 3. Quality dashboard — `/dashboard/quality`

New router endpoints in `app/routers/eval_loop.py` (or small new `quality.py` router) + template `quality.html` using the shared `_navbar.html` (add link under «⚙️ מערכת»).

Read-only aggregates, no new tables:

- **Eval pass-rate trend:** line chart over `eval_runs` (n_pass / n_probes per completed run).
- **Judge verdict breakdown:** pie of `judge_verdict` over `query_logs` (judged rows only).
- **Failure-type ranking:** bar chart of `failure_type` counts.
- **Feedback trend:** 👍/👎/unrated counts per week from `query_logs.user_feedback` + `answer_feedback`.
- **Worst questions table:** judged-`fail` rows, most recent first — question, answer snippet, failure_type, timestamp, link to the row in `/dashboard/logs`.
- Backfill button + status (section 1).

Charts: Chart.js (already used by dashboard.html).

### 4. Telegram feedback capture

- Audit `telegram_polling.py`: every AI answer sent to Telegram must carry 👍/👎 inline buttons (today some paths may skip them). One handler, all answer paths.
- On 👎: bot sends one follow-up with 3 quick-reply causes mapped to taxonomy: «פרויקט לא נכון» → `WRONG_PROJECT`, «חסר מידע» → `MISSING_DATA`, «תשובה שגויה» → `HALLUCINATION`, plus free-text option stored to `admin_note`.
- Writes land in `query_logs.user_feedback` / `failure_type` (and `answer_feedback` where applicable) — same columns the dashboard reads.
- All bot messages prefixed `‏` per project standard.

### 5. Weekly auto-eval

- Extend existing 15-minute scheduler with weekly job (configurable day/hour, default Sunday 07:00 IL): run eval loop over full gold set in judge-only mode (no auto-repair patches), write an `eval_runs` row, send admin Telegram summary: pass rate, delta vs previous run, list of newly-failing questions.
- Admin = users with role `DIVISION_MANAGER` (existing superior-lookup util).

## Data flow

```
query_logs (544 rows, unlabeled)
   └─ judge backfill ──► judge_verdict + failure_type
                             └─► quality dashboard (aggregates)
query_logs + judge fails ──► gold candidates ──► curate UI ──► eval_gold_answers (≥50)
                                                       └─► weekly auto-eval ──► eval_runs ──► trend chart + TG summary
telegram 👍/👎 + cause ──► query_logs.user_feedback / failure_type (live labels going forward)
```

## Error handling

- Backfill: per-row try/except — one bad row never aborts the batch; failures logged, row left NULL for retry. Groq rate-limit → exponential backoff, resume.
- Judge JSON parsing: reuse the key-boundary regex extraction already hardened for Hebrew quotes (commit f63193e).
- Weekly eval: failure → `eval_runs.status='error'` + error text; Telegram summary reports the failure instead of silently skipping.

## Testing

- Unit: backfill idempotency (judged rows skipped), taxonomy parsing (judge output → enum, garbage → NULL + log), candidate ranking (rated > failed > frequent, dedup works).
- Unit: Telegram 👎 follow-up callback writes correct `failure_type`.
- Existing eval tests must stay green.
- Manual: dashboard aggregates spot-checked against direct SQL on the same DB.

## Success criteria (1 week after deploy)

1. ≥200 production questions carry `judge_verdict`.
2. ≥50 approved gold answers.
3. `/dashboard/quality` shows ranked failure causes from real data.
4. Feedback rate on new answers >20% (vs ~2% today).
5. One automatic weekly eval run completed with Telegram summary.

## Out of scope (later phases)

- Fixing the failure causes themselves (phase 3).
- Splitting `knowledge_service.py` (phase 2).
- Auto-repair patches from weekly runs (kept manual via existing eval UI).
- Dashboard/Telegram UX work beyond the feedback buttons.
