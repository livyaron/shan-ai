# RACI Learning Loop — Design Spec

**Date:** 2026-06-14
**Status:** Approved (brainstorming) → ready for plan
**Author:** brainstorming session

## Problem

The AI's RACI suggestions are identical across recent decisions and show no
evidence of learning from manager corrections. Investigation found the learning
machinery exists by design but is starved of input, and the user-entered
`responsibilities` (תחומי אחריות) field — meant to drive assignment — is passed
to the model but never used as a primary signal.

## Root Causes (verified in code)

1. **Web edits don't close the loop.** `save_raci` ([dashboard.py:1824](../../../app/routers/dashboard.py))
   rewrites `DecisionRaciRole` rows but never touches `RACISuggestion` — no
   `EDITED` outcome, no `final_assignments`, no `edit_reason`. Web corrections
   vanish from learning.
2. **Edit reason never captured at edit time.** `mark_raci_edited`
   ([raci_service.py:832](../../../app/services/raci_service.py)) stores
   `final_assignments` only. Per user decision: reason is entered *later* on the
   web RACI-בינה page, so this is acceptable — but only if an `EDITED` row exists
   to attach it to (see #1).
3. **Patterns gated on `feedback_score >= 4`.** `get_raci_patterns`
   ([lessons_service.py:151](../../../app/services/lessons_service.py)) returns
   nothing unless decisions are feedback-scored ≥4. Usually empty.
4. **Determinism hides the rest.** `temperature=0.1` + identical roster → identical
   output even when a signal is injected.
5. **תחום אחריות is decorative.** `responsibilities` is included in the roster line
   (`, תחום: ...`) but the prompt guidelines instruct selection by hierarchy and
   "direct executors", never by responsibility-domain match. The only תחום-driven
   instruction lives inside `get_raci_patterns` and is therefore pattern-gated (#3).

## Scope

Full scope chosen: **wire + visibility + strengthen + make תחום primary**.

## Architecture

Two shared helpers become the single source of truth, replacing scattered logic
across two assignment paths and three edit surfaces.

### `build_raci_context(decision, session) -> (context_text, context_meta)`
Builds the learned-signal block injected into the RACI prompt, used by BOTH
`generate_raci_for_decision` and `assign_raci_from_ai`. Returns:
- `context_text`: patterns + few-shots + active rules, formatted for the prompt.
- `context_meta`: structured counts, e.g. `{"rules": 3, "past_edits": 4, "patterns": 2}`,
  for visibility surfaces (Section C).

### `record_raci_outcome(decision_id, final_items, session)`
Upserts the `RACISuggestion` for a decision. Compares `final_items` against
`suggested_assignments`: identical → `ACCEPTED`, differs → `EDITED`. Stores
`final_assignments`, resets `reason_analyzed=False`. Creates the row if missing.
Called by every edit surface. Replaces `mark_raci_edited` / `mark_raci_accepted`
bodies.

## Sections

### A — Close the loop
- `save_raci` (web): after writing `DecisionRaciRole`, call `record_raci_outcome`.
- Telegram inline edit: route through `record_raci_outcome` (handles missing row +
  accepted-vs-edited detection).
- Telegram approve-as-is: route `mark_raci_accepted` through `record_raci_outcome`.

**Outcome:** every correction on every surface becomes an `ACCEPTED`/`EDITED` row →
feeds few-shots immediately and appears on the RACI-בינה page for later reasoning.

### B — Reason captured later (no new build)
Existing `save_raci_edit_reason` → `analyze-reasons` → `RACIRule` pipeline already
works. Section A feeds it. Verify the raci-intelligence list query surfaces
web-originated `EDITED` rows.

### C — Visibility
1. Surface the per-user `reason` (already in `suggested_assignments`) on the decision
   page and Telegram proposal — shows *why* each person was picked.
2. Append a learning-footprint line from `context_meta` to the Telegram proposal
   (e.g. `📚 התבסס על: 3 כללים, 4 תיקוני עבר`) and show it on the dashboard.

### D — Strengthen
1. Derive RACI patterns from `RACISuggestion` `ACCEPTED`/`EDITED` final assignments
   (actual corrections), not only feedback-scored decisions. Drop / lower the
   `feedback_score >= 4` gate.
2. Few-shots: raise limit, sort `EDITED` (real corrections) ahead of `ACCEPTED`.
3. Unify `assign_raci_from_ai` to use `build_raci_context` (today it injects only
   patterns).
4. Keep `temperature=0.1`; add explicit prompt instruction:
   `"כללים ותיקוני עבר גוברים על ברירת המחדל"`.

### E — Make תחום אחריות a primary matching key
1. Rewrite guidelines so responsibility-domain is the first-class signal:
   - R: `"בחר Responsible לפי התאמה בין הבעיה/הפעולה לבין תחום האחריות של המשתמש — זהו השיקול העיקרי, לא רק ההיררכיה."`
   - C: `"בחר Consulted לפי תחומי אחריות משיקים לבעיה."`
2. Restructure roster so תחום אחריות is prominent, not a trailing fragment.
3. Require per-user `reason` to reference the matched תחום (ties into Section C).
4. Always present — independent of feedback/patterns. Directly breaks the
   "all identical" symptom even before learning accumulates.

## No schema changes required
`RACISuggestion` already has `final_assignments`, `outcome`, `edit_reason`,
`reason_analyzed`, `accepted_at` ([models.py:275](../../../app/models.py)).
`context_meta` is computed live (not persisted) unless visibility on historical
suggestions is later desired.

## Success Criteria
1. Editing RACI on web OR Telegram produces an `EDITED` `RACISuggestion` row visible
   on the RACI-בינה page.
2. After a few corrections, new proposals visibly differ and the footprint line shows
   non-zero counts.
3. R/C selection changes when a user's תחום אחריות is edited, on the very next
   decision, with no other change.
4. Both assignment paths inject identical learned context.

## Out of Scope
- Changing the RACI editor UX (keyboards/forms stay).
- Persisting `context_meta` history.
- Auto-prompting for edit reason at edit time (explicitly declined — reason entered
  later on web).
