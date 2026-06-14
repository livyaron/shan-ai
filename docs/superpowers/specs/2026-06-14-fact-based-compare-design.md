# Fact-Based compare_to_gold — Phase J

**Date:** 2026-06-14
**Status:** Approved
**Goal:** Judge answers by whether they contain the gold's key facts (project identifier) rather than asking the LLM "equivalent?", removing per-question false-negatives and a Groq call per judgement.

## Problem

With identity-line gold, the LLM equivalence judge still false-negatives certain single-match questions: עתלית / בת ים / WBE-178 answer correctly live (the right project card) but score 0.0, while חולה / אשלים pass — inconsistent LLM behavior, not retrieval. Each judgement also costs a Groq call (the eval's main quota load).

The gold identity line starts with the project identifier (e.g. `WBE-252 | חולה … | מנה"פ: … | שלב: …`). A correct answer (card / JSON / phrased) always contains that identifier. So identifier presence is a deterministic, format-independent correctness signal.

## Design

Add a deterministic **fact-based pre-check** at the top of `compare_to_gold` (`app/services/gold_truth_service.py`), before the existing rule-check + LLM. It only fires when the gold contains project identifier(s); otherwise behavior is unchanged.

### `_fact_based_check(ai_answer, gold_answer) -> float | None`
1. Extract project identifiers from gold via regex `r"WB[A-Z]-?\d+"` (matches WBE-252, WBC-083, WBM-40275, WBD-088, WBJ-002, WBK-021, etc.), uppercased, normalized (strip spaces). Call this `gold_ids`.
2. If `gold_ids` is empty → return `None` (defer to existing rule-check + LLM; covers field-answer gold, narrowing prompts, non-project gold).
3. Extract the same-pattern identifiers from `ai_answer` → `ans_ids`.
4. Decision:
   - If **every** id in `gold_ids` appears in `ans_ids` → `1.0` (the answer names exactly the right project(s); facts of the right project follow in any format).
   - Else if **no** gold id appears in the answer → `0.0` (wrong/missing project — the WRONG_PROJECT / not-found case).
   - Else (some but not all — partial multi-match) → `None` (defer to LLM for the nuanced case).

### Integrate into `compare_to_gold`
At the very start (after computing nothing else), call `_fact_based_check`; if it returns non-None, return that score immediately. Otherwise continue to the existing entity-guard + `_rule_check` + LLM path unchanged.

```python
async def compare_to_gold(question, ai_answer, gold_answer) -> float:
    fb = _fact_based_check(ai_answer, gold_answer)
    if fb is not None:
        return fb
    ... existing logic unchanged ...
```

### Why this is correct + cheap
- Identifier match is format-independent: a card (`מזהה: WBE-252`), raw JSON (`"project_identifier": "WBE-252"`), or phrased text all contain `WBE-252`. The judge no longer rejects correct answers on formatting.
- It's the precise WRONG_PROJECT detector: wrong id → 0.0.
- No Groq call for any id-bearing gold (the bulk of the gold set) → eval quota load drops sharply, complementing batched pacing.
- Multi-match identity-line gold (e.g. אשלים = two WBE lines): PASS only if the answer names both ids; disambiguation answers that list both pass, partial → LLM.

## Testing
- gold `"WBE-252 | חולה | מנה\"פ: יעקבי"`, answer card containing `WBE-252` → 1.0, no LLM call (patch `llm_chat` to raise; must not be invoked).
- same gold, answer about `WBE-999` (wrong id) → 0.0, no LLM call.
- multi-id gold `"WBE-204 | … \n WBE-180 | …"`, answer containing both → 1.0; answer containing only WBE-204 → None → falls to existing path (assert LLM path reached).
- gold with NO identifier (`"מנהל הפרויקט: יעקבי, ניר"`) → `_fact_based_check` returns None; existing rule-check still works (regression: existing compare_to_gold tests pass).
- regex matches the real id formats present in gold (WBE-/WBC-/WBM-/WBD-/WBJ-/WBK-).

## Success criteria
1. עתלית / בת ים / WBE-178 (right project returned) now score PASS in a live batch.
2. compare_to_gold makes no Groq call for id-bearing gold.
3. Existing compare_to_gold / eval tests stay green.
4. After deploy + re-batch on Railway, the cumulative pass-rate rises and remains stable; the remaining fails are genuine (non-project noise, true retrieval misses).

## Validation
Re-run several `POST /eval/run?repair=false&batch=8` on Railway; confirm previously-false-negative questions flip to PASS and the cumulative pass-rate climbs vs the ~31-38% baseline. Report the new stable rate + genuine remaining failures.

## Out of scope
- Field-answer / narrowing / non-project gold judging (still rule-check + LLM).
- Retrieval changes; gold curation.
