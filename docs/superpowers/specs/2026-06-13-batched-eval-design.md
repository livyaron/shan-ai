# Batched Spaced Eval — Phase I

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Produce a stable, trustworthy current pass-rate by evaluating the gold set in small spaced batches that aggregate per-question over time — instead of one big burst that Groq rate-limits into noise.

## Problem

The bulk live eval fires ~120-180 Groq calls per run; on the free tier each run randomly exhausts quota → mass `0.0` failures → the score swings (2/56, 18/61, 27/61, 15/61) and measures throttling, not retrieval. The underlying answer/retrieval fixes are verified individually, but we have no stable number.

## Design

Evaluate a few questions per run, persist each question's latest verdict, and aggregate the latest-per-question across all gold = an always-current pass-rate. A cron fires small batches on a schedule so coverage rotates without bursting quota.

### 1. Per-question live verdict — `EvalGoldAnswer` columns
Add to `app/models.py` `EvalGoldAnswer`:
```python
    last_live_verdict = Column(String(10), nullable=True)   # PASS | FAIL
    last_live_score   = Column(Float, nullable=True)
    last_live_at      = Column(DateTime, nullable=True)
```
Migration (local + Railway, documented in CLAUDE.md):
`ALTER TABLE eval_gold_answers ADD COLUMN IF NOT EXISTS last_live_verdict VARCHAR(10), ADD COLUMN IF NOT EXISTS last_live_score DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS last_live_at TIMESTAMP;`

### 2. Batch mode + persistence — `run_cycle` (`per_question_loop_service.py`)
- Add `batch: int = 0` param. When `batch > 0`, select only the `batch` gold rows ordered by `last_live_at` **NULLS FIRST** (never-checked first, then oldest) — so successive batches rotate through the whole set. When `batch == 0`, behavior unchanged (all gold).
  - Current select (line 746) `ORDER BY EvalGoldAnswer.id` → for batch mode use `ORDER BY last_live_at NULLS FIRST, id` + `.limit(batch)`.
- After each `run_one_question` result `r`, persist onto its gold row:
  - `last_live_verdict` = `"PASS"` if `r.status in ("passed_first_try","fixed")` else `"FAIL"`
  - `last_live_score` = `r.score_final`
  - `last_live_at` = `datetime.utcnow()`
  - (map `r.question_hash` → the gold row already in `gold_rows`).
- Keep existing EvalRun bookkeeping + `EVAL_PACE_SECONDS` pacing.

### 3. Endpoint — `/eval/run` gains `batch`
`eval_run(repair: bool = True, batch: int = 0, ...)` → `run_cycle(..., repair=repair, batch=batch)`. Existing callers unaffected (`batch=0`).

### 4. Cron — spaced batches (`eval_cron.py`)
Register a job: every 3 hours, run a judge-only batch:
```python
sch.add_job(_batch_eval_run, "interval", hours=3, id="batch_eval", replace_existing=True)
```
`_batch_eval_run`: open a session, `run_cycle(s, user_id=None, repair=False, batch=8)`. 8 questions × ~2 calls (answer + judge; single-match now skips the format LLM) ≈ 16 calls/run — safe under quota. ~61 gold ÷ 8 × 3h ≈ full refresh < a day.

### 5. Dashboard — cumulative live pass-rate (`quality.html` + `quality_data`)
`quality_data` aggregates `EvalGoldAnswer` rows:
```python
checked = rows with last_live_verdict not null
live_pass_rate = round(PASS / checked * 100)
stale = checked rows with last_live_at older than 48h
```
Return `"live_cumulative": {"checked": n, "total": gold_total, "pass": p, "fail": f, "pass_rate": r, "stale": s}`. Dashboard shows it as the headline trustworthy metric ("מדידה חיה מצטברת: P/checked (rate%) · כיסוי checked/total"). The old bulk live-failed panel stays for drill-down.

## Why this is stable
Each batch is small enough to (almost) never hit quota; a transient failure on one batch only affects ~8 questions' freshness, not the whole number, and self-heals next rotation. The aggregate is the latest verdict per question, so it converges to the true current pass-rate and stays fresh.

## Testing
- `run_cycle(batch=2)` selects exactly 2 gold rows (oldest `last_live_at` first / NULLS first) and writes `last_live_verdict`/`score`/`at` on them; rows not in the batch keep their old values. (patch `run_one_question` to controlled results.)
- verdict mapping: `passed_first_try`/`fixed` → PASS; `unfixable`/`error` → FAIL.
- batch ordering: a row with NULL `last_live_at` is picked before a row with a recent timestamp.
- `quality_data` `live_cumulative` math: 3 PASS + 1 FAIL among checked → pass_rate 75, checked 4.
- `batch=0` unchanged (selects all).

## Success criteria
1. A batch run judges only N questions and updates their `last_live_*` — no quota burst.
2. Over a day of cron batches, every gold question has a recent `last_live_verdict`.
3. Dashboard shows a stable cumulative live pass-rate that doesn't swing run-to-run.

## Validation
On Railway: run 2-3 manual `POST /eval/run?repair=false&batch=8` spaced a few minutes apart; confirm each updates 8 rows, the cumulative pass-rate accumulates, and verdicts are non-0.0 for known-good questions (חולה/עתלית). Confirm the `batch_eval` cron is registered in logs.

## Out of scope
- Removing the LLM judge (option 1, not chosen).
- Changing retrieval/answer logic (done in prior phases).
- Historical per-run trend of batches (only latest-per-question is stored).
