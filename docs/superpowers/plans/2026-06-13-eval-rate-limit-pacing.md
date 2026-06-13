# Eval Rate-Limit Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the live eval resilient to Groq rate limits (backoff-retry when all models exhaust + pacing between questions) so the measured pass-rate is real.

**Architecture:** `groq_chat` gains a bounded outer retry with exponential backoff when a full model pass is rate-limited; `run_cycle` sleeps `EVAL_PACE_SECONDS` between questions.

**Tech Stack:** FastAPI, async, Groq SDK (`RateLimitError`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-13-eval-rate-limit-pacing-design.md`

**Conventions:** Railway deploy target; never `docker-compose down -v`.

---

### Task 1: groq_chat backoff-retry + run_cycle pacing

**Files:**
- Modify: `app/services/groq_client.py` (`groq_chat`, ~line 23-59)
- Modify: `app/services/per_question_loop_service.py` (`run_cycle` loop)
- Test: `tests/test_groq_retry.py`

**Context:** `groq_chat(messages, ...)` loops `MODELS` (3 models), `await asyncio.sleep(1)` between on `RateLimitError`, raises `last_error` after one pass. `asyncio` already imported there. `run_cycle` loops `for g in gold_rows: r = await run_one_question(...)`; `asyncio` imported.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_groq_retry.py
"""groq_chat retries the whole model list with backoff when all are rate-limited."""
import pytest
from unittest.mock import AsyncMock, patch
from groq import RateLimitError

from app.services import groq_client as gc


def _rate_limit_error():
    # RateLimitError needs response/body; build a minimal instance
    import httpx
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return RateLimitError("rate", response=resp, body=None)


@pytest.mark.asyncio
async def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    calls["n"] += 1
                    # fail the first full pass (3 models), succeed on the 4th call
                    if calls["n"] <= 3:
                        raise _rate_limit_error()
                    class M:  # noqa
                        class choices:  # noqa
                            pass
                    r = type("R", (), {})()
                    r.choices = [type("C", (), {"message": type("Msg", (), {"content": " ok "})()})()]
                    return r

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        out = await gc.groq_chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert calls["n"] >= 4          # retried beyond the first 3-model pass


@pytest.mark.asyncio
async def test_raises_after_max_rounds(monkeypatch):
    class _Stub:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    import httpx
                    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
                    raise RateLimitError("rate", response=resp, body=None)

    with patch.object(gc, "get_client", return_value=_Stub()), \
         patch("app.services.groq_client.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RateLimitError):
            await gc.groq_chat([{"role": "user", "content": "hi"}])
```

Run `docker exec shan-ai-api pytest tests/test_groq_retry.py -v` → `test_retries_then_succeeds` FAILS (current code raises after one pass).

NOTE: confirm `RateLimitError`'s constructor signature for the installed groq version; if `RateLimitError("rate", response=resp, body=None)` doesn't construct, adapt the helper to whatever the SDK requires (check `python -c "from groq import RateLimitError; help(RateLimitError.__init__)"` in the container). The test's intent: simulate 429s.

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_groq_retry.py -v`
Expected: retry test FAILS.

- [ ] **Step 3: Implement groq_chat backoff-retry**

Rewrite the loop body of `groq_chat` (keep signature + kwargs setup) to wrap the model-list pass in bounded rounds:
```python
    MAX_ROUNDS = 3
    last_error = None
    for rnd in range(MAX_ROUNDS):
        for i, model in enumerate(model_list):
            try:
                resp = await _client.chat.completions.create(model=model, **kwargs)
                if i > 0 or rnd > 0:
                    logger.warning(f"Used fallback model [round {rnd}, {i}] {model}")
                return resp.choices[0].message.content.strip()
            except RateLimitError as e:
                last_error = e
                logger.warning(f"Rate limit on {model} (round {rnd})")
                if i < len(model_list) - 1:
                    await asyncio.sleep(1)
            except Exception:
                raise
        # full pass exhausted on rate limits — back off before retrying the list
        if rnd < MAX_ROUNDS - 1:
            await asyncio.sleep(2 ** (rnd + 1))   # 2s, 4s
    raise last_error
```

- [ ] **Step 4: Implement run_cycle pacing**

In `per_question_loop_service.py`, add a module constant near the top (after imports):
```python
EVAL_PACE_SECONDS = 1.5
```
In `run_cycle`'s loop, after `results.append(r)` and the counts/applied-fixes bookkeeping, before the loop continues, add pacing (skip after the last item):
```python
            await asyncio.sleep(EVAL_PACE_SECONDS)
```
Place it as the last statement inside the `for g in gold_rows:` body (an extra 1.5s after the final question is harmless; keep it simple). Confirm `asyncio` is imported in the file (it is).

- [ ] **Step 5: Run tests**

Run: `docker exec shan-ai-api pytest tests/test_groq_retry.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Regression + import**

```bash
docker exec shan-ai-api python -c "import app.services.groq_client, app.services.per_question_loop_service; print('OK')"
docker exec shan-ai-api pytest tests/test_eval_weekly_summary.py tests/test_judge_gold_backed.py -q
```

- [ ] **Step 7: Commit**

```bash
git add app/services/groq_client.py app/services/per_question_loop_service.py tests/test_groq_retry.py
git commit -m "feat(eval): groq backoff-retry on full rate-limit + pace eval between questions"
```

---

### Task 2: Ship to Railway + final live measurement

- [ ] **Step 1: Full suite**

Run: `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3`
Expected: green except the 14 known pre-existing failures.

- [ ] **Step 2: Merge + push**

```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: eval rate-limit pacing"
git push origin master
```

- [ ] **Step 3: Stop local, deploy Railway**

```bash
docker-compose stop fastapi
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Wait for new build (~2-3 min), confirm app up**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 10 -o /dev/null -w "%{http_code}\n" "$URL/dashboard/quality/distinct"   # 303 = up
```

- [ ] **Step 5: Run the paced judge-only live measurement**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
curl -s -m 20 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/run?repair=false"
```
Poll `/dashboard/eval/runs` (Monitor) until newest run `status=="completed"` (will take longer now due to pacing — that's expected), then:
```bash
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/eval/runs" | python -c "import sys,json;r=json.load(sys.stdin)['runs'][0];print('pass',r['n_pass'],'/',r['n_probes'])"
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/data" | python -c "import sys,json;d=json.load(sys.stdin);[print('FAIL:',f['question']) for f in d.get('live_failed',[])]"
```

- [ ] **Step 6: Report**

Report the new pass-rate vs the 18/61 rate-limit artifact; spot-check that previously-rate-limited correct answers (חולה/עתלית/בת ים) now PASS; list the GENUINE remaining failures (the real retrieval/quality targets). Note local stays stopped.
```
