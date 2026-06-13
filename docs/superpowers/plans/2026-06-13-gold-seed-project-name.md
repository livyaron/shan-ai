# Gold Auto-Seed for Project-Name Lookups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `propose_gold(use_llm=False)` produce deterministic card gold for bare project-name questions, so the existing seed auto-creates gold for them and the gold set passes 50.

**Architecture:** Add one branch to `propose_gold` in `gold_truth_service.py`: when no field is detected, run the bot's own project matcher (`find_projects_by_identifier`); 1 match → rich single card (`build_project_card`), 2–5 → combined multi-card (`_format_project_card`), >5 → a narrowing-selection prompt. The seed service is unchanged — it already saves `source=="db_lookup"` proposals. Then ship to Railway, re-seed, re-judge.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (+pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-06-13-gold-seed-project-name-design.md`

**Conventions:**
- Deploy target is **Railway** (local Docker deprecated); tests run in the local container during dev, final verify + data run on Railway.
- Hebrew strings prefixed `‏` (U+200F).
- Never `docker-compose down -v`.
- `SHOWABLE_CARDS_MAX = 5` (named constant in the module).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/services/gold_truth_service.py` | Modify | + project-name branch in `propose_gold`; + `_format_narrowing` helper; + `SHOWABLE_CARDS_MAX` |
| `tests/test_gold_name_seed.py` | Create | Branch behaviour by match count |

---

### Task 1: Project-name card branch in `propose_gold`

**Files:**
- Modify: `app/services/gold_truth_service.py`
- Test: `tests/test_gold_name_seed.py`

**Context for the implementer (read these in the codebase first):**
- `propose_gold(session, question, *, use_llm=True) -> dict` in `gold_truth_service.py` (~line 141). Current flow: detect project + field; if both and a formatted field answer exists → `db_lookup`; else (when `use_llm=False`) it returns an empty `manual` proposal; else LLM fallback. You are inserting the new branch in the **`use_llm=False` / no-field** path, BEFORE the empty-manual return.
- `_detect_field(question) -> str | None` (~line 91) — already used; reuse to decide "no field".
- `find_projects_by_identifier(identifier: str, session) -> list[dict]` in `app/services/project_tools.py` (~line 48): exact-code-or-name-substring match, capped at 10, returns project **dicts** (via `_project_to_dict`). Confirm each dict has keys `id`, `project_identifier`, `name` (read `_project_to_dict`).
- `_format_project_card(p: dict, index: int, total: int) -> str` in `project_tools.py` (~line 807) — dict-based multi-card renderer (`📁 פרויקט i מתוך N`).
- `build_project_card(p: Project) -> str` in `app/services/projects_menu_service.py` (~line 261) — rich single-card; takes a **Project object** (fetch via `session.get(Project, id)`).
- `RTL` constant already defined in `gold_truth_service.py` (used by `_format_field_answer`).
- The multi-card divider the bot uses is the string `"\n━━━━━━━━━━━━━━━━━━\n"` (see `project_tools.py` ~line 564).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gold_name_seed.py
"""propose_gold project-name branch: 1 card / multi-card / narrowing / non-project."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import gold_truth_service as gts


@pytest.mark.asyncio
async def test_single_match_returns_rich_card(db_session):
    from app.models import Project
    proj = Project(project_identifier="WBE-999", name="טסטוביל", manager="כהן, דנה",
                   stage="הרכבה חשמלית", is_active=True)
    db_session.add(proj)
    await db_session.commit()
    await db_session.refresh(proj)

    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier",
                      new=AsyncMock(return_value=[{"id": proj.id, "project_identifier": "WBE-999", "name": "טסטוביל"}])):
        res = await gts.propose_gold(db_session, "טסטוביל", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-999" in res["answer"]
    assert "טסטוביל" in res["answer"]
    assert res["target_project"] == "WBE-999"


@pytest.mark.asyncio
async def test_two_matches_returns_combined_multicard(db_session):
    matches = [
        {"id": 1, "project_identifier": "WBE-204", "name": "אשלים-התקנת 2 שנאים"},
        {"id": 2, "project_identifier": "WBE-180", "name": "אשלים-PV3"},
    ]
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=matches)), \
         patch.object(gts, "_format_project_card", side_effect=lambda p, i, n: f"CARD[{p['project_identifier']} {i}/{n}]"):
        res = await gts.propose_gold(db_session, "אשלים", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-204" in res["answer"] and "WBE-180" in res["answer"]
    assert "1/2" in res["answer"] and "2/2" in res["answer"]
    assert res["target_project"] is None          # ambiguous → no single target


@pytest.mark.asyncio
async def test_too_many_matches_returns_narrowing(db_session):
    matches = [{"id": i, "project_identifier": f"WBE-{i}", "name": f"בית {i}"} for i in range(1, 8)]  # 7 > 5
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=matches)):
        res = await gts.propose_gold(db_session, "בית", use_llm=False)

    assert res["source"] == "db_lookup"
    assert "WBE-1" in res["answer"]               # lists candidates
    assert res["answer"]                          # non-empty narrowing prompt
    # narrowing, not skipped:
    assert res["source"] != "manual"


@pytest.mark.asyncio
async def test_no_project_match_stays_manual(db_session):
    with patch.object(gts, "_detect_field", return_value=None), \
         patch.object(gts, "find_projects_by_identifier", new=AsyncMock(return_value=[])):
        res = await gts.propose_gold(db_session, "שלום", use_llm=False)

    assert res["source"] == "manual"
    assert (res["answer"] or "") == ""            # empty → seed leaves it needs_manual
```

- [ ] **Step 2: Run, verify fail**

Run: `docker exec shan-ai-api pytest tests/test_gold_name_seed.py -v`
Expected: FAIL — `find_projects_by_identifier` not in `gts` namespace yet / branch absent.

- [ ] **Step 3: Implement**

In `gold_truth_service.py`:

a) Add import near the top (with the other `from app.services...` imports):
```python
from app.services.project_tools import find_projects_by_identifier, _format_project_card
```

b) Add the constant + divider + narrowing helper (module level, near other constants):
```python
SHOWABLE_CARDS_MAX = 5
_CARD_DIVIDER = "\n━━━━━━━━━━━━━━━━━━\n"


def _format_narrowing(matches: list[dict]) -> str:
    """Selection prompt for over-broad name matches — invite the user to narrow."""
    listed = " · ".join(
        f"{m.get('project_identifier', '')} — {m.get('name', '')}".strip(" —")
        for m in matches[:10]
    )
    return f"{RTL}נמצאו {len(matches)} פרויקטים. צמצם/י את החיפוש: {listed}"
```

c) In `propose_gold`, locate the point where `use_llm=False` currently returns the empty manual proposal (the no-DB-answer path). BEFORE that return, add the no-field project-name branch:
```python
    # No specific field asked — treat as a bare project-name lookup.
    if not field:
        matches = await find_projects_by_identifier((question or "").strip(), session)
        if matches:
            n = len(matches)
            if n == 1:
                proj = await session.get(Project, matches[0]["id"])
                from app.services.projects_menu_service import build_project_card
                answer = build_project_card(proj) if proj else _format_project_card(matches[0], 1, 1)
                target = matches[0].get("project_identifier")
            elif n <= SHOWABLE_CARDS_MAX:
                answer = _CARD_DIVIDER.join(
                    _format_project_card(p, i + 1, n) for i, p in enumerate(matches)
                )
                target = None
            else:
                answer = _format_narrowing(matches)
                target = None
            return {
                "answer": answer,
                "source": "db_lookup",
                "target_project": target,
                "target_field": None,
            }
```
Notes:
- `field` is the variable already set earlier by `_detect_field` in `propose_gold` — reuse it; do not call `_detect_field` again.
- `Project` is already imported in `gold_truth_service.py` (used by `_detect_project`); confirm, else add it.
- `build_project_card` is imported locally inside the branch to avoid any import cycle with `projects_menu_service`.
- This branch runs for BOTH `use_llm=False` and `use_llm=True` (a bare-name question gets a card either way — correct; the LLM fallback below is only reached when there is no field AND no project match, i.e. non-project chatter). Confirm by reading the surrounding code that placing it before the LLM-fallback block yields: field→field-answer; no-field+project→card; no-field+no-project→(use_llm? LLM : empty manual).

- [ ] **Step 4: Run, verify pass**

Run: `docker exec shan-ai-api pytest tests/test_gold_name_seed.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Regression**

Run: `docker exec shan-ai-api pytest tests/test_gold_seed.py tests/test_judge_gold_backed.py tests/test_distinct_eval.py -q`
Expected: all pass (the seed service consumes the new `db_lookup` proposals unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/services/gold_truth_service.py tests/test_gold_name_seed.py
git commit -m "feat(quality): propose_gold seeds card gold for project-name lookups"
```

---

### Task 2: Ship to Railway + re-seed + re-judge

- [ ] **Step 1: Full suite**

Run: `docker exec shan-ai-api pytest tests/ -q 2>&1 | tail -3`
Expected: green except the 14 known pre-existing failures (test_weekly_report ×9, test_project_report_service ×3, test_project_learning ×1, test_viewer_role ×1).

- [ ] **Step 2: Merge + push**

```bash
git checkout master
git merge --no-ff <feature-branch> -m "merge: gold auto-seed for project-name lookups"
git push origin master
```

- [ ] **Step 3: Stop local, deploy Railway**

```bash
docker-compose stop fastapi      # avoid Telegram polling conflict; keep postgres for psql
TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"; SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"; ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "mutation { serviceInstanceDeploy(serviceId: \"'$SVC_ID'\", environmentId: \"'$ENV_ID'\") }"}'
```

- [ ] **Step 4: Wait for new build live**

The change is internal (no new route), so probe behaviour: after deploy, re-seed should now seed many more. First confirm the app is back up:
```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 10 -o /dev/null -w "%{http_code}\n" "$URL/dashboard/quality/distinct"   # 303 (up, auth redirect)
```
To be sure the NEW code is running (not the old container), check the Railway deploy status via the API or wait ~2–3 min after the mutation, then proceed — the re-seed result in Step 6 (seeded count jumping well above the previous 6) confirms the new code.

- [ ] **Step 5: Login**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -c /tmp/rw.txt -o /dev/null -w "login:%{http_code}\n" -X POST "$URL/login" -d "user_id=3&password=1234"
```

- [ ] **Step 6: Re-seed gold from production**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 120 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/gold/seed-from-production"
docker exec shan-ai-postgres psql "postgresql://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai" -c "SELECT source, count(*) FROM eval_gold_answers GROUP BY 1;"
```
Expected: `seeded` now far higher than 6 (project-name questions); total gold ≥ 50. (If the Railway psql DNS hiccups, read counts via the dashboard `/quality/distinct` `gold_total` instead.)

- [ ] **Step 7: Re-judge distinct (gold-backed refresh)**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 15 -b /tmp/rw.txt -X POST "$URL/dashboard/eval/rejudge-distinct"
# poll until done:
curl -s -m 15 -b /tmp/rw.txt "$URL/dashboard/eval/rejudge/status"
```
Wait until `running:false` (use a Monitor poll loop; ~85 representatives, several minutes with Groq rate limits).

- [ ] **Step 8: Report**

```bash
URL="https://easygoing-endurance-production-df54.up.railway.app"
curl -s -m 20 -b /tmp/rw.txt "$URL/dashboard/quality/distinct" | python -c "import sys,json; d=json.load(sys.stdin); print('summary:', d['summary']); print('failures:', d['failures'])"
```
Report: new gold count, gold-backed share, and the **trustworthy distinct pass-rate** vs the earlier 27% (mostly-guessed). Note whether ≥50 gold reached and which questions still need human `/gold` curation.
```
