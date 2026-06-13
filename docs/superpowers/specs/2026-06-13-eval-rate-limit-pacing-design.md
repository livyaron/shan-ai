# Eval Rate-Limit Pacing — Phase G

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Stop the live eval from spurious-failing under Groq rate limits, so the measured pass-rate reflects retrieval quality, not throttling.

## Problem

The judge-only live eval re-asks N gold questions + judges each = ~3 LLM calls/question, fired in a burst. `groq_chat` tries 3 models (1s sleep between) then **raises** when all buckets are exhausted; `llm_chat` then falls to Gemma, and if that also fails the exception propagates → the eval records an empty/failed answer → score 0.0. Proven: the judge scores a facts-present answer **1.0 in isolation**, but the same question scored 0.0 inside a 61-question burst ("Rate limit on llama-3.3…" observed). So 18/61 (30%) is a rate-limit artifact, not retrieval quality.

## Design

Two small, complementary changes.

### 1. Backoff-retry when all Groq models are exhausted — `app/services/groq_client.py`
Today `groq_chat` raises `last_error` after one pass through `MODELS` (1s between). Add a bounded outer retry: if a full pass exhausts all models on `RateLimitError`, sleep with exponential backoff and retry the whole list, up to `MAX_ROUNDS` rounds.

- `MAX_ROUNDS = 3` (named constant). Backoff between rounds: `2 ** round` seconds (2s, 4s) — i.e. sleep after a fully-exhausted pass, then retry.
- Only `RateLimitError` triggers a retry round; any other exception still raises immediately (unchanged).
- After the final round still exhausted → raise `last_error` (unchanged terminal behavior).
- Low risk: extra latency only occurs when already fully rate-limited (better than failing).

### 2. Pace the eval between questions — `app/services/per_question_loop_service.py`
In `run_cycle`, after each `run_one_question`, `await asyncio.sleep(EVAL_PACE_SECONDS)` so the burst doesn't drain all buckets at once.

- `EVAL_PACE_SECONDS = 1.5` (named constant). For ~60 questions that adds ~90s — acceptable for a measurement run.
- Pace applies to the per-question loop only (not user-facing answers). Skip the sleep after the last question (minor).

### Why both
Pacing reduces the burst pressure; the backoff-retry recovers the calls that still hit a limit. Together the eval completes without spurious 0.0s, giving a trustworthy number.

## Testing
- `groq_chat`: with a stub client raising `RateLimitError` for the first full pass then succeeding, `groq_chat` returns the success (proves it retried the list, not raised). Patch `asyncio.sleep` to no-op so the test is fast. With a client that always raises `RateLimitError`, it raises after `MAX_ROUNDS` (no infinite loop).
- `run_cycle`: pacing constant exists and `asyncio.sleep` is called between questions (patch `run_one_question` + assert sleep called N-1 times, or simply assert `EVAL_PACE_SECONDS` is referenced and a 2-question run calls sleep at least once). Keep it light — the real proof is the Railway run.

## Success criteria
1. A judge-only live eval over the full gold set on Railway completes with **far fewer 0.0 rate-limit failures** — the pass-rate jumps toward the spot-check reality (correct answers like חולה/עתלית/בת ים PASS).
2. No infinite retry; non-rate-limit errors still surface.

## Validation
Re-run `POST /dashboard/eval/run?repair=false` on Railway after deploy → report the new pass-rate vs the 18/61 artifact, and the genuine remaining failures.

## Out of scope
- compare_to_gold internals (proven working).
- Identity-line gold (done).
- Switching providers / raising Groq quota.
