# Identity-Line Gold — Phase F

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Replace full-card gold for project-name lookups with a concise **identity line** (key facts), so the semantic judge passes factually-correct live answers regardless of card formatting — making the live pass-rate trustworthy.

## Problem

Phase D set gold = full project card. Live measurement (phase E) then scored **2/56**, a false-negative artifact: the live pipeline returns the right projects but in a *different* card format, and `compare_to_gold` scores card-vs-card format divergence as 0.0. Verified: חולה/עתלית/אשלים/בת ים all answer correctly live yet FAIL. (Documented in memory `gold-format-pitfall`.)

## Design

### Identity-line gold — `gold_truth_service.py`
Replace the **card output** in `propose_gold`'s project-name branch (phase D) with identity lines. Keep the matcher, the no-field gate, and the >5 narrowing branch unchanged.

- New helper `_identity_line(p: dict) -> str`:
  `‏{project_identifier} | {name} | מנה"פ: {manager} | שלב: {stage}` — omit a segment whose value is missing/empty. Use the dict keys returned by `_project_to_dict` (verify: `project_identifier`, `name`, `manager`, `stage`).
- Branch behaviour (replaces the `build_project_card` / `_format_project_card` outputs):
  - **1 match** → `_identity_line(match)`; `target_project` = its identifier.
  - **2 ≤ N ≤ 5** → the N identity lines joined by `"\n"`; `target_project` = None.
  - **N > 5** → narrowing prompt (unchanged).
  - **0 matches** → unchanged (manual / needs_manual).
- Remove the now-unused `build_project_card` import and `_format_project_card` usage from this branch (and the `_CARD_DIVIDER` constant if no longer used). `SHOWABLE_CARDS_MAX` stays (controls the multi vs narrowing cutoff).

### Why this fixes measurement
`compare_to_gold` runs rule-check (substring/entity) then an LLM YES/NO equivalence judge. With concise identity-line gold, a verbose live card that *contains* the same id/name/manager/stage is judged equivalent (the judge compares facts, not formatting). Card-vs-card brittleness is removed.

### Re-seed (overwrite stale card gold) + re-judge — ship step
Existing `db_lookup` gold rows hold the old cards; `save_gold` only inserts when absent, so a plain re-seed won't overwrite them. On Railway:
1. Delete stale auto-seeded gold: `DELETE FROM eval_gold_answers WHERE source IN ('db_lookup','auto_user_confirmed');` (keep `manual`/`telegram` human gold).
2. Re-seed (`POST /dashboard/eval/gold/seed-from-production`) → regenerates identity-line gold.
3. Judge-only live measurement (`POST /dashboard/eval/run?repair=false`) → trustworthy current pass-rate + real failing set.

## Testing
- `_identity_line({id,name,manager,stage})` → `‏WBE-252 | חולה | מנה"פ: יעקבי | שלב: עבודה אזרחית`; omits missing segments.
- propose_gold 1 match → identity line (contains id + manager, NOT the `📁 פרוייקט #` card header), source `db_lookup`.
- 2 matches → two identity lines, both ids present, joined by newline.
- >5 → narrowing (unchanged).
- non-project → manual (unchanged).
- Existing `tests/test_gold_name_seed.py` updates: assertions that expected card text (`📁`/`build_project_card`) change to identity-line expectations.

## Success criteria
1. Project-name gold is identity lines, not cards.
2. After re-seed + re-judge on Railway, the live pass-rate jumps well above 2/56 (correct answers like חולה/עתלית/בת ים now PASS).
3. The remaining `failed_questions` are genuine failures (real targets), not format artifacts.

## Out of scope
- `compare_to_gold` internals (unchanged — relying on the LLM equivalence judge with concise gold).
- The raw-JSON live answer for some bare-identifier queries (separate formatting bug, noted).
- Fuzzy-match precision ("ניר יצחק"→בשור).
