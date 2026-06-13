# Identity-Line Gold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Project-name gold = concise identity line (facts), not full card, so the judge passes format-divergent-but-correct live answers.

**Architecture:** Replace the card output in `propose_gold`'s project-name branch with an `_identity_line` helper. Re-seed (overwrite stale card gold) + re-judge on Railway.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest.

**Spec:** `docs/superpowers/specs/2026-06-13-identity-line-gold-design.md`

**Conventions:** Railway deploy target; Hebrew `‏` prefix; never `docker-compose down -v`.
`_project_to_dict` keys (confirmed): `project_identifier`, `name`, `manager`, `stage`, `id`.

---

### Task 1: identity-line gold in propose_gold

**Files:**
- Modify: `app/services/gold_truth_service.py`
- Modify: `tests/test_gold_name_seed.py`

- [ ] **Step 1: Update tests to expect identity lines**

In `tests/test_gold_name_seed.py`, change `test_single_match_returns_rich_card` and `test_two_matches_returns_combined_multicard` expectations from card text to identity-line text. Replace those two tests' bodies' assertions:

For single match — after the `propose_gold(... "טסטוביל" ...)` call, assert:
```python
    assert res["source"] == "db_lookup"
    assert "WBE-999" in res["answer"]
    assert "טסטוביל" in res["answer"]
    assert "מנה" in res["answer"]            # manager segment present
    assert "📁" not in res["answer"]          # NOT a card
    assert res["target_project"] == "WBE-999"
```
(The single-match test seeds a real Project `WBE-999 טסטוביל manager="כהן, דנה" stage="הרכבה חשמלית"`, and patches `_detect_field`→None + `find_projects_by_identifier`→`[{"id":proj.id,"project_identifier":"WBE-999","name":"טסטוביל","manager":"כהן, דנה","stage":"הרכבה חשמלית"}]`. Add manager/stage to the patched dict so the identity line can include them.)

For two matches — patch `find_projects_by_identifier` to return two dicts each with `project_identifier`/`name`/`manager`/`stage`, drop the `_format_project_card` patch, and assert:
```python
    assert res["source"] == "db_lookup"
    assert "WBE-204" in res["answer"] and "WBE-180" in res["answer"]
    assert "\n" in res["answer"]              # two lines
    assert "📁" not in res["answer"]
    assert res["target_project"] is None
```
Keep `test_too_many_matches_returns_narrowing` and `test_no_project_match_stays_manual` unchanged.

- [ ] **Step 2: Run, verify the two updated tests fail**

Run: `docker exec shan-ai-api pytest tests/test_gold_name_seed.py -v`
Expected: the single + two-match tests FAIL (still emitting cards).

- [ ] **Step 3: Implement**

In `gold_truth_service.py`, add the helper near `_format_narrowing`:
```python
def _identity_line(p: dict) -> str:
    """Concise fact line for a project (judge-friendly gold)."""
    parts = []
    if p.get("project_identifier"):
        parts.append(p["project_identifier"])
    if p.get("name"):
        parts.append(p["name"])
    if p.get("manager"):
        parts.append(f'מנה"פ: {p["manager"]}')
    if p.get("stage"):
        parts.append(f'שלב: {p["stage"]}')
    return f"{RTL}" + " | ".join(parts)
```

Replace the card-producing block in `propose_gold`'s project-name branch:
```python
            if n == 1:
                answer = _identity_line(matches[0])
                target = matches[0].get("project_identifier")
            elif n <= SHOWABLE_CARDS_MAX:
                answer = "\n".join(_identity_line(p) for p in matches)
                target = None
            else:
                answer = _format_narrowing(matches)
                target = None
```
Remove the now-unused inline `from app.services.projects_menu_service import build_project_card` and the `session.get(Project, ...)` fetch in the n==1 path. Remove the `_format_project_card` import if it is no longer used anywhere else in the file (grep first — keep if used elsewhere). Keep `_CARD_DIVIDER` only if still referenced (likely now unused → remove). Keep `SHOWABLE_CARDS_MAX`.

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_gold_name_seed.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Regression + import**

```bash
docker exec shan-ai-api python -c "import app.services.gold_truth_service; print('OK')"
docker exec shan-ai-api pytest tests/test_gold_seed.py tests/test_judge_gold_backed.py -q
```
Expected: OK + pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/gold_truth_service.py tests/test_gold_name_seed.py
git commit -m "feat(quality): identity-line gold for project-name lookups (was full card)"
```

---

### Task 2: Ship to Railway + re-seed + re-judge + report

- [ ] **Step 1: Full suite**

Run: `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3`
Expected: green except the 14 known pre-existing failures.

- [ ] **Step 2: Merge + push**

```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: identity-line gold"
git push origin master
```

- [ ] **Step 3: Stop local, deploy Railway**

```bash
docker-compose stop fastapi
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Delete stale card gold on Railway, re-seed**

After the new build is live (~2-3 min; confirm app up):
```bash
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -c "DELETE FROM eval_gold_answers WHERE source IN ('db_lookup','auto_user_confirmed');"
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -X POST "$URL/login" -d "user_id=3&password=1234"
curl -s -m 150 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/gold/seed-from-production"
```
Expected: re-seed `seeded` count similar to before (~47); gold now identity-line. Spot-check one:
```bash
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -tAc "SELECT left(gold_answer,80) FROM eval_gold_answers WHERE question='חולה';"
```
Expected: an identity line (id | name | מנה"פ | שלב), no `📁` card header.

- [ ] **Step 5: Judge-only live measurement**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 20 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/run?repair=false"
```
Poll `/dashboard/eval/runs` (Monitor loop) until newest run `status=="completed"`, then:
```bash
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/eval/runs" | python -c "import sys,json;r=json.load(sys.stdin)['runs'][0];print('pass',r['n_pass'],'/',r['n_probes'])"
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/data" | python -c "import sys,json;d=json.load(sys.stdin);[print('FAIL:',f['question']) for f in d.get('live_failed',[])]"
```

- [ ] **Step 6: Report**

Report the new live pass-rate (vs the artifact 2/56 and the stale 41%), confirm חולה/עתלית/בת ים now PASS, and list the genuine remaining failures. Note local stays stopped.
```
