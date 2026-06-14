# Fact-Based compare_to_gold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Judge id-bearing gold by project-identifier presence (deterministic, no LLM), fixing format false-negatives and cutting Groq load.

**Architecture:** Add `_fact_based_check` to `gold_truth_service.py`; call it first in `compare_to_gold` — return its score when gold has project ids, else fall through to existing rule-check + LLM.

**Tech Stack:** Python, pytest. **Spec:** `docs/superpowers/specs/2026-06-14-fact-based-compare-design.md`

**Conventions:** Railway deploy target; never `docker-compose down -v`. Grounded: `compare_to_gold` at `gold_truth_service.py:311`; `normalize_hebrew` + `llm_chat` imported there; `_rule_check` + entity-guard precede the LLM call.

---

### Task 1: `_fact_based_check` + integrate into compare_to_gold

**Files:** Modify `app/services/gold_truth_service.py`; Test `tests/test_fact_based_compare.py`

- [ ] **Step 1: failing tests**

```python
# tests/test_fact_based_compare.py
"""compare_to_gold uses deterministic project-id matching for id-bearing gold."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import gold_truth_service as gts


@pytest.mark.asyncio
async def test_right_id_passes_no_llm():
    gold = '‏WBE-252 | חולה - החלפת שנאי | מנה"פ: יעקבי, ניר | שלב: עבודה אזרחית'
    ans = '📁 מזהה: WBE-252\nשם: חולה\nמנה"פ: יעקבי, ניר'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM for id-bearing gold"))):
        score = await gts.compare_to_gold("חולה", ans, gold)
    assert score == 1.0


@pytest.mark.asyncio
async def test_wrong_id_fails_no_llm():
    gold = '‏WBE-252 | חולה | מנה"פ: יעקבי'
    ans = '📁 מזהה: WBE-999\nשם: משהו אחר'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        score = await gts.compare_to_gold("חולה", ans, gold)
    assert score == 0.0


@pytest.mark.asyncio
async def test_raw_json_with_right_id_passes():
    gold = '‏WBE-195 | עתלית | מנה"פ: כהן | שלב: תכנון'
    ans = '{"id": 394, "project_identifier": "WBE-195", "name": "עתלית- התקנת שנאי"}'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        score = await gts.compare_to_gold("עתלית", ans, gold)
    assert score == 1.0


@pytest.mark.asyncio
async def test_multi_id_all_present_passes():
    gold = '‏WBE-204 | אשלים א\n‏WBE-180 | אשלים ב'
    ans = 'WBE-204 ... WBE-180 ...'
    with patch.object(gts, "llm_chat", new=AsyncMock(side_effect=AssertionError("no LLM"))):
        score = await gts.compare_to_gold("אשלים", ans, gold)
    assert score == 1.0


@pytest.mark.asyncio
async def test_no_id_gold_defers_to_existing():
    # field-answer gold (no project id) → fact-based returns None → existing path runs
    gold = '‏מנהל הפרויקט: יעקבי, ניר'
    ans = '‏מנהל הפרויקט: יעקבי, ניר'
    # existing rule_check substring → 1.0 without LLM; llm patched but should be unused here too
    with patch.object(gts, "llm_chat", new=AsyncMock(return_value="YES")):
        score = await gts.compare_to_gold("מי המנהל", ans, gold)
    assert score == 1.0
```

Run `docker exec shan-ai-api pytest tests/test_fact_based_compare.py -v` → the id tests FAIL (LLM currently invoked / wrong scoring).

- [ ] **Step 2: implement `_fact_based_check`**

Add near `_rule_check` in gold_truth_service.py:
```python
_PROJECT_ID_RE = re.compile(r"WB[A-Z]-?\d+", re.IGNORECASE)


def _project_ids(text: str) -> set[str]:
    return {m.group(0).upper().replace(" ", "") for m in _PROJECT_ID_RE.finditer(text or "")}


def _fact_based_check(ai_answer: str, gold_answer: str) -> float | None:
    """Deterministic judge for gold that names project identifier(s).
    1.0 if every gold id is in the answer; 0.0 if none is; None (defer) if partial
    or gold has no ids."""
    gold_ids = _project_ids(gold_answer)
    if not gold_ids:
        return None
    ans_ids = _project_ids(ai_answer)
    if gold_ids <= ans_ids:
        return 1.0
    if not (gold_ids & ans_ids):
        return 0.0
    return None
```
(`re` is already imported in the file — confirm.)

- [ ] **Step 3: integrate into compare_to_gold**

At the very top of `compare_to_gold` (line 311 body), before any existing logic:
```python
    fb = _fact_based_check(ai_answer, gold_answer)
    if fb is not None:
        return fb
```
Leave the rest (entity-guard, `_rule_check`, LLM) unchanged.

- [ ] **Step 4: run tests → 5 pass.**
- [ ] **Step 5: regression**

```bash
docker exec shan-ai-api python -c "import app.services.gold_truth_service; print('OK')"
docker exec shan-ai-api pytest tests/test_judge_gold_backed.py tests/test_gold_name_seed.py tests/test_batched_eval.py -q
```
(If any existing compare_to_gold test now behaves differently because its gold contains a WBE id, inspect: the fact-based result should be correct; only adjust a test if its expectation was format-dependent and the new deterministic result is right. Note any such change.)

- [ ] **Step 6: commit**

```bash
git add app/services/gold_truth_service.py tests/test_fact_based_compare.py
git commit -m "feat(eval): fact-based compare_to_gold via project-id presence (deterministic, no LLM)"
```

---

### Task 2: Ship to Railway + re-batch + report

- [ ] **Step 1: Full suite** — `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3` → no NEW failures outside the known set (`grep -E "^FAILED" <output> | grep -viE "test_weekly_report|test_project_report_service|test_project_learning|test_viewer_role"` → none).

- [ ] **Step 2: Merge + push**
```bash
git checkout master
git merge --no-ff feat/fact-based-compare -m "merge: fact-based compare_to_gold"
git push origin master
```

- [ ] **Step 3: Stop local, deploy Railway**
```bash
docker-compose stop fastapi
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Wait for build (~2-3 min), confirm up**
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 10 -o /dev/null -w "%{http_code}\n" "$URL/dashboard/quality/distinct"   # 303
```

- [ ] **Step 5: Re-batch to refresh verdicts under the new judge**

The existing `last_live_*` reflect the OLD LLM judge. Re-run batches so questions get re-judged by the fact-based path. Login + fire several batches (oldest-checked rotate, so repeated batches cover the set):
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
# fire a batch, wait completion, read cumulative; repeat ~8 times for full 61 coverage
curl -s -m 20 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/run?repair=false&batch=8"
```
Poll `/dashboard/eval/runs` to completion between batches (Monitor). After full coverage:
```bash
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/data" | python -c "import sys,json;print(json.load(sys.stdin)['live_cumulative'])"
```
Spot-check the previously-false-negative questions:
```bash
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -c "SELECT question, last_live_verdict FROM eval_gold_answers WHERE question IN ('עתלית','בת ים','WBE-178','חולה');"
```
Expected: עתלית/בת ים/WBE-178 now PASS.

- [ ] **Step 6: Report**
Report the new cumulative pass-rate vs ~31-38%, confirm the false-negatives flipped to PASS, and list the genuine remaining failures (non-project noise / true misses). Note Groq load dropped (no LLM judge for id-bearing gold). Local stays stopped.
```
