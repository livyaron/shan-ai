# Retrieval Fix: Substation Prefix + Live Measurement — Phase E

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Fix the confirmed current retrieval bug (substation/station name-prefixes break project matching) and measure retrieval against *current* behavior (live re-ask of the gold set), not stale logs.

## Problem

The gold-backed "retrieval failures" (MISSING_DATA 10, WRONG_PROJECT 5) mostly score **stale logged answers**. Verified live on Railway:
- "מי מנהל פרויקט של בת ים", "אשלים", "עתלית" — logged as failures, **pass today**.
- So the 41% distinct pass-rate understates current quality: it judges history (stored `ai_response`), not the live pipeline.

But one class **still fails today** (confirmed live):
- "תחמ"ש ניר יצחק" → *not found*; "ניר יצחק" alone → matches.
- "תחנת נתניה" → *not found*.
- "סטטוס בית שאן" → matches (field word `סטטוס` already stripped).

**Root cause:** location prefixes — `תחמ"ש` / `תחמש` / `תחנת` / `תחנה` — are not stripped before project-name matching, so the matcher searches the literal string (incl. prefix) and misses. "תחמ"ש" is the project's core domain term (users say "substation X"), so this hits real queries.

## Design (one phase: measure + fix, per approval)

### Part 1 — Live measurement (judge-only), stop trusting stale logs
The eval loop already re-asks each gold question through the live pipeline (`per_question_loop_service._answer` → `ask_router.route(..., log_to_db=False)`) and compares to gold. Phase 1 added a `repair` flag to `run_cycle`/`run_one_question`. Expose a **judge-only** live run and surface its result.

- **Endpoint:** `POST /dashboard/eval/run` gains an optional `repair: bool = True` query param; pass `repair=False` for measurement (no auto-mutation of production aliases/gold).
- **Persist the failing set:** add `EvalRun.failed_questions` (nullable JSON) — list of `{question, score}` for questions that scored FAIL in the run. `run_cycle` populates it from its existing per-question results. Migration: `ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS failed_questions JSON;` (local + Railway, documented in CLAUDE.md).
- **Dashboard:** on `/dashboard/quality`, add a button "מדידה חיה (gold, judge-only)" → `POST /eval/run?repair=false`; show live progress via the existing SSE stream link, and after completion show the latest run's pass-rate + `failed_questions` list. This becomes the trustworthy current-behavior metric, distinct from the stale-log distinct view.

### Part 2 — Fix: strip location prefixes in project matching
Add a **prefix-strip fallback** in `find_projects_by_identifier` (`app/services/project_tools.py`), mirroring its existing "strip trailing char" fallback: when the normal match yields nothing, strip a leading location prefix and retry once.

- Prefixes (checked at the start of the identifier, case/quote-insensitive): `תחמ"ש`, `תחמ״ש`, `תחמש`, `תחנת מיתוג`, `תחנת`, `תחנה`, `פרויקט`, `פרוייקט`.
- Implementation: normalize the identifier, if it starts with any prefix token, strip it (and following whitespace), and re-run the existing name/identifier match on the remainder. Only used as a fallback (don't change behavior when the original already matches — preserves exact-code UX).
- Example: `תחמ"ש ניר יצחק` → strip `תחמ"ש` → match `ניר יצחק`.
- Bound: strip at most one leading prefix; if the remainder is empty, return no match (don't match everything).

### Validation
Run Part-1 live measurement on Railway **before** and **after** Part-2 deploy → report the current-behavior pass-rate and the lift attributable to the prefix fix. Confirm "תחמ"ש ניר יצחק" / "תחנת נתניה" resolve after the fix.

## Data flow
```
gold set (56) ──► /eval/run?repair=false ──► _answer (LIVE ask_router) ──► compare_to_gold
                                                   └─► EvalRun{n_pass,n_fail,failed_questions}
                                                          └─► quality dashboard (true current pass-rate + fail list)

"תחמ"ש ניר יצחק" ──► ask_router ──► find_projects_by_identifier
                                        ├─ direct match? no
                                        └─ strip "תחמ"ש" → match "ניר יצחק" ✅
```

## Error handling
- Prefix strip: if remainder empty after stripping → no match (avoid matching all). Wrap in the existing function's flow; never raise.
- Live eval: existing run_cycle error handling (per-question isolation, EvalRun.status=error on failure). `failed_questions` defaults to NULL/[] if the run errors early.

## Testing
**Part 2 (unit, fast):**
- `find_projects_by_identifier("תחמ\"ש ניר יצחק", session)` returns the same match as `"ניר יצחק"` (seed a project whose name contains "ניר יצחק").
- prefix `תחנת`: "תחנת נתניה" matches the נתניה project.
- no false-widening: "תחמ"ש" alone (prefix only, empty remainder) → no match (not all projects).
- original exact-code match unchanged (e.g. "WBE-204" still single exact).
**Part 1:**
- `run_cycle(repair=False)` populates `EvalRun.failed_questions` with the FAIL questions and does NOT create repair proposals.
- endpoint `/eval/run?repair=false` passes the flag through.

## Success criteria
1. "תחמ"ש ניר יצחק" and "תחנת נתניה" resolve to the right project live after deploy.
2. Judge-only live measurement runs on Railway and reports a current-behavior pass-rate (expected well above the stale-log 41%).
3. `failed_questions` visible on the dashboard — the real current failing set to drive the next fix.
4. No regression: exact-code and already-working name lookups unchanged.

## Out of scope
- Loose fuzzy-token precision ("ניר יצחק" → "בארות יצחק" partial-token match) — noted for a later precision pass.
- `failure_type` classifier reliability — separate.
- Re-judging the stale stored logs — the live measurement supersedes them; we don't retro-fix history.
