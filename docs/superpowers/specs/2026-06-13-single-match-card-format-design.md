# Single-Match Card Formatting — Phase H

**Date:** 2026-06-13
**Status:** Approved
**Goal:** Stop single-match project lookups from returning raw JSON; format them deterministically as a card, removing the LLM dependency (and its rate-limit JSON fallback).

## Problem

In `answer_project_query` (`app/services/project_tools.py`), the `by_identifier` **single-match** branch (line 567-571) sets `context_str = json.dumps(data)` and falls through to an LLM formatting call (line 756, usage `project_query`). When that LLM call fails — e.g. Groq rate limit — the `except` returns `context_str[:1000]` = **raw JSON** (line 771). So exact-code (WBE-178…) and bare single-name (עתלית) queries return raw JSON under load.

Impact: the live eval's dominant failure cluster (~20 of 34) is this — the right project is found but emitted as JSON, which the judge rightly won't equate to the identity-line gold. Verified: עתלית scores 0.0 in isolation because its live answer is the JSON fallback.

The 5+ multi-match branch (line 577-588) already returns deterministic cards directly (no LLM). Single-match should do the same.

## Design

In the `by_identifier` single-match branch, return a deterministic card directly instead of routing through the LLM:

```python
            elif len(matches) == 1:
                data = matches[0]
                user_data["last_project"] = data["project_identifier"]
                answer = _format_project_card(data, 1, 1)
                log_id = await _log_query(text, answer, intent, data["project_identifier"], session, user_id)
                return answer, log_id
```

- Mirrors the existing 5+ multi-card return pattern (same `_format_project_card`, same `_log_query` + early return).
- Removes the JSON `context_str` + LLM round-trip for this path → no raw-JSON fallback, one fewer Groq call per single-match query (also eases rate limits), faster, consistent output.
- `_format_project_card(data, 1, 1)` renders "📁 פרויקט 1 מתוך 1" + fields; the judge equates it to identity-line gold (proven: card-vs-identity-line → 1.0 in isolation).

### Scope guard
Only the `by_identifier` single-match branch changes. The disambiguation (2-4), 5+ cards, and other intents are untouched. The generic `except` JSON fallback for other intents stays (out of scope; single-match was the measured offender).

## Testing
- `answer_project_query` with a query resolving to exactly one project returns a `_format_project_card` string (contains "📁" + the identifier) and does NOT contain raw JSON (`{"id"`), without invoking `llm_chat`. (Patch `find_projects_by_identifier` → one match; assert no `llm_chat` call via patching it to raise — if it's called, the test fails.)
- Multi (5+) and not-found paths unchanged (existing behavior).

## Success criteria
1. Single-match project/code queries return a formatted card, never raw JSON — even when Groq is rate-limited (no LLM call in this path).
2. After deploy + re-run live eval on Railway, the WBE-code / bare-name cluster passes → pass-rate jumps materially above 44%.

## Validation
Re-run `POST /dashboard/eval/run?repair=false` on Railway → report new pass-rate vs 27/61, and the genuine remaining failures (expected: non-project noise + fuzzy-precision misses).

## Out of scope
- Generic `except` fallback for non-single-match intents.
- Disambiguation (2-4) and 5+ behavior.
- Fuzzy-match precision; non-project gold cleanup.
