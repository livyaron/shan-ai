# Gold Auto-Seed for Project-Name Lookups — Phase D

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Auto-seed trustworthy gold answers for bare project-name questions (the bulk of the 77 "needs_manual"), so the gold set passes 50 and the distinct-question pass-rate becomes trustworthy — without humans hand-writing answers or saving LLM guesses as gold.

## Problem

After Phase C, Railway has 8 gold answers over 85 distinct questions; the distinct pass-rate (27%) mostly rests on the unreliable LLM-guessed reference. 77 distinct questions are "needs_manual". Most are **bare project-name lookups** ("אשלים", "חולה", "יאסיף", "תימורים", "בר לב"…) whose answer is fully derivable from the DB — but the current `propose_gold(use_llm=False)` only produces deterministic gold for *field-specific* questions (manager/stage/date), so bare names fall through to `needs_manual`.

## Design

### Decision recap (approved)
- **Gold = full project card(s)** — the same render the bot produces (kept as-is; "working fine"). Accepted risk: for 2–4-match names the live bot returns a disambiguation prompt while gold is multi-card; the semantic judge may still pass but it is not guaranteed (documented).
- **Too-many matches → narrowing-selection gold, NOT skip.** When a name is too broad to show as cards, gold = a selection/narrowing prompt listing the candidate projects (the disambiguation the bot itself would give), so the "correct" answer is to narrow down.

### Extend `propose_gold(session, q, use_llm=False)` — `app/services/gold_truth_service.py`
Add a branch, taken when **no field is detected** AND the query resolves to project match(es). Match using the **same project search the bot uses** for name/identifier lookup (reuse the `project_tools` matching, not the looser `_detect_project`), so gold reflects what the bot actually finds.

Behaviour by match count `N`:

| N | gold answer | source |
|---|---|---|
| 0 | (unchanged) empty/manual → stays `needs_manual` (non-project chatter: שלום, "מה זה R ב RACI", statements) | manual |
| 1 | `build_project_card(project)` (single-card render) | `db_lookup` |
| 2 ≤ N ≤ `SHOWABLE_CARDS_MAX` (=5) | combined multi-card: each project via the bot's multi-card formatter (`📁 פרויקט i מתוך N` + divider) | `db_lookup` |
| N > `SHOWABLE_CARDS_MAX` | **narrowing-selection prompt**: a short Hebrew message listing the candidate projects (id + name) inviting the user to narrow — the same shape the bot's disambiguation produces | `db_lookup` |

- `SHOWABLE_CARDS_MAX = 5` (named constant). Rationale: keeps card gold "showable"; beyond it, narrowing is the correct answer.
- The card/multi-card renderers already exist (`projects_menu_service.build_project_card`, `project_tools._format_project_card`). Reuse them — do not re-implement formatting (DRY, and keeps gold aligned with bot output). If reuse causes an import cycle, extract the formatter into a small shared helper module rather than duplicating.
- The narrowing-selection text is a new small helper (e.g. `_format_narrowing(matches)`): `‏נמצאו N פרויקטים. צמצם/י: <id — name> · <id — name> …` (cap the listed candidates, e.g. first 10).

### Seed flow — `app/services/gold_seed_service.py`
**No change.** `seed_from_production` already loops distinct production questions, calls `propose_gold(use_llm=False)`, and saves any `source=="db_lookup"` with non-empty answer. Extending `propose_gold` automatically makes name-lookup questions seedable. Re-running seed on Railway after the change jumps gold past 50.

### Judge alignment
`judge_one` uses real gold when present (Phase B). Once these cards are gold, name-lookup questions become **gold-backed** (trustworthy verdicts) on the next `rejudge`/`rejudge-distinct`. No judge change needed.

## Data flow
```
distinct production question "אשלים"
   └─ propose_gold(use_llm=False)
        ├─ field? no
        ├─ project search (bot's matcher) → 2 matches (WBE-204, WBE-180)
        └─ 2 ≤ N ≤ 5 → combined multi-card → save_gold(source=db_lookup)
"בית"  → N=7 > 5 → narrowing-selection prompt → save_gold(source=db_lookup)
"שלום" → N=0 → needs_manual (untouched)
```

## Error handling
- Project search failure for one question → log + treat as `needs_manual`, never abort the seed batch (seed already isolates per-question).
- Card formatter raising on a malformed project row → caught per-question → `needs_manual`.

## Testing
Unit (mock the project matcher to return controlled match lists):
- field detected → unchanged (no card branch taken).
- no field, 0 matches → empty/manual (needs_manual).
- no field, 1 match → gold == `build_project_card(p)`, source `db_lookup`.
- no field, 2 matches → combined multi-card contains both ids, `📁 פרויקט 1 מתוך 2`, source `db_lookup`.
- no field, 6 matches (> SHOWABLE_CARDS_MAX) → narrowing-selection gold lists candidates, source `db_lookup`, NOT skipped.
- non-project text ("שלום") → 0 matches → needs_manual.
Integration: `gold_seed_service.seed_from_production` now seeds a name-lookup question end-to-end (already covered by extending propose_gold; add one case asserting a name question becomes gold).

## Success criteria
1. Re-running seed on Railway brings gold to **≥50**.
2. Bare project-name questions are gold-backed after `rejudge-distinct`.
3. Over-broad names produce a narrowing-selection gold (not skipped).
4. Non-project chatter still `needs_manual`.
5. Distinct pass-rate after re-judge reflects mostly gold-backed verdicts (trustworthy).

## Out of scope
- Changing the card format (kept as-is).
- The `failure_type` classifier mislabel (separate).
- Human curation of the genuinely non-project remainder (stays `/gold`).
- Semantic matching of paraphrases (exact normalized hash only).
