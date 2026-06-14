# RACI Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RACI AI learn from manager corrections on every edit surface, use the user-entered `responsibilities` (תחום אחריות) as a primary matching signal, and show what it learned.

**Architecture:** Introduce two shared helpers in `raci_service.py` — `record_raci_outcome` (every edit surface records ACCEPTED/EDITED) and `build_raci_context` (single learned-signal block for both assignment paths). Rewrite the RACI prompt so תחום אחריות drives R/C selection unconditionally. Surface a learning footprint.

**Tech Stack:** FastAPI, SQLAlchemy async, Groq via `llm_router.llm_chat`, pytest + `unittest.mock`. Source spec: `docs/superpowers/specs/2026-06-14-raci-learning-loop-design.md`.

---

## File Structure

- Modify `app/services/raci_service.py` — add `_diff_outcome`, `record_raci_outcome`, `build_raci_context`, `_build_raci_prompt`; refactor `assign_raci_from_ai`, `generate_raci_for_decision`, `mark_raci_accepted`, `mark_raci_edited`, `_get_raci_few_shots`.
- Modify `app/services/lessons_service.py` — `get_raci_patterns` derives from `RACISuggestion` corrections, drops `feedback_score >= 4` gate.
- Modify `app/routers/dashboard.py` — `save_raci` calls `record_raci_outcome`.
- Modify `app/services/raci_service.py` `propose_raci_to_submitter` — append footprint line.
- Create `tests/test_raci_learning.py` — unit tests for the pure helpers and prompt content.

No schema changes — `RACISuggestion` already has `final_assignments`, `outcome`, `edit_reason`, `reason_analyzed`, `accepted_at`.

---

## Task 1: Pure outcome differ `_diff_outcome`

**Files:**
- Modify: `app/services/raci_service.py`
- Test: `tests/test_raci_learning.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_raci_learning.py
"""Tests for RACI learning loop helpers."""
import pytest
from app.models import RACISuggestionStatusEnum


def test_diff_outcome_identical_is_accepted():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "R"}]
    final = [{"user_id": 2, "role": "R"}, {"user_id": 1, "role": "A"}]  # order-independent
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.ACCEPTED


def test_diff_outcome_changed_role_is_edited():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "R"}]
    final = [{"user_id": 1, "role": "A"}, {"user_id": 2, "role": "C"}]
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.EDITED


def test_diff_outcome_added_user_is_edited():
    from app.services.raci_service import _diff_outcome
    suggested = [{"user_id": 1, "role": "A"}]
    final = [{"user_id": 1, "role": "A"}, {"user_id": 3, "role": "I"}]
    assert _diff_outcome(suggested, final) == RACISuggestionStatusEnum.EDITED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: FAIL — `ImportError: cannot import name '_diff_outcome'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/raci_service.py` (near the top, after `ROLE_HE`):

```python
def _diff_outcome(suggested: list[dict], final: list[dict]) -> RACISuggestionStatusEnum:
    """Compare suggested vs final RACI (order-independent). ACCEPTED if identical, else EDITED."""
    def norm(items: list[dict]) -> set[tuple[int, str]]:
        return {(int(i["user_id"]), str(i["role"]).upper()) for i in items}
    return (
        RACISuggestionStatusEnum.ACCEPTED
        if norm(suggested or []) == norm(final or [])
        else RACISuggestionStatusEnum.EDITED
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): pure _diff_outcome helper (accepted vs edited)"
```

---

## Task 2: `record_raci_outcome` upsert + delegate existing markers

**Files:**
- Modify: `app/services/raci_service.py` — add `record_raci_outcome`; rewrite `mark_raci_accepted`, `mark_raci_edited` bodies to delegate.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
@pytest.mark.asyncio
async def test_record_raci_outcome_creates_edited_row(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.services.raci_service as rs

    suggestion = MagicMock()
    suggestion.suggested_assignments = [{"user_id": 1, "role": "A"}]
    suggestion.outcome = None
    suggestion.final_assignments = None
    suggestion.reason_analyzed = True

    session = AsyncMock()
    session.scalar.return_value = suggestion

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=session)
    sess_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(rs, "async_session_maker", lambda: sess_cm, raising=False)
    # async_session_maker is imported inside the function; patch the source module too
    import app.database as dbmod
    monkeypatch.setattr(dbmod, "async_session_maker", lambda: sess_cm, raising=False)

    await rs.record_raci_outcome(99, [{"user_id": 1, "role": "C"}])

    from app.models import RACISuggestionStatusEnum
    assert suggestion.outcome == RACISuggestionStatusEnum.EDITED
    assert suggestion.final_assignments == [{"user_id": 1, "role": "C"}]
    assert suggestion.reason_analyzed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_record_raci_outcome_creates_edited_row -v`
Expected: FAIL — `AttributeError: module 'app.services.raci_service' has no attribute 'record_raci_outcome'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/raci_service.py`:

```python
async def record_raci_outcome(decision_id: int, final_items: list[dict]) -> None:
    """Upsert the RACISuggestion outcome for a decision from the final assignments.
    Sets ACCEPTED/EDITED via _diff_outcome, stores final_assignments, resets reason_analyzed.
    Creates the row if missing. Never raises."""
    from app.database import async_session_maker
    from datetime import datetime as _dt
    norm_final = [{"user_id": int(i["user_id"]), "role": str(i["role"]).upper()} for i in final_items]
    try:
        async with async_session_maker() as session:
            suggestion = await session.scalar(
                select(RACISuggestion).where(RACISuggestion.decision_id == decision_id)
            )
            if not suggestion:
                suggestion = RACISuggestion(
                    decision_id=decision_id,
                    suggested_assignments=norm_final,  # no prior proposal → treat final as suggestion
                )
                session.add(suggestion)
            outcome = _diff_outcome(suggestion.suggested_assignments or norm_final, norm_final)
            suggestion.outcome = outcome
            suggestion.final_assignments = norm_final
            suggestion.reason_analyzed = False
            suggestion.accepted_at = _dt.utcnow()
            await session.commit()
            logger.info(f"record_raci_outcome: decision {decision_id} → {outcome.value} ({len(norm_final)} items)")
    except Exception as e:
        logger.warning(f"record_raci_outcome: failed for decision {decision_id}: {e}")
```

Rewrite the existing `mark_raci_accepted` body to delegate:

```python
async def mark_raci_accepted(decision_id: int) -> None:
    """Mark a pending RACI suggestion as accepted (user approved as-is)."""
    from app.database import async_session_maker
    try:
        async with async_session_maker() as session:
            suggestion = await session.scalar(
                select(RACISuggestion).where(RACISuggestion.decision_id == decision_id)
            )
            final = list(suggestion.suggested_assignments or []) if suggestion else []
        await record_raci_outcome(decision_id, final)
    except Exception as e:
        logger.warning(f"mark_raci_accepted: failed for decision {decision_id}: {e}")
```

Rewrite `mark_raci_edited` body to delegate:

```python
async def mark_raci_edited(decision_id: int, final_items: list[dict]) -> None:
    """Mark a RACI suggestion as edited, storing the final assignments."""
    await record_raci_outcome(decision_id, final_items)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): record_raci_outcome upsert; mark_* delegate to it"
```

---

## Task 3: Wire web `save_raci` to record the correction (Section A)

**Files:**
- Modify: `app/routers/dashboard.py:1880-1898` (after `await session.commit()` in `save_raci`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
def test_save_raci_records_outcome_source():
    """save_raci must call record_raci_outcome with the new assignments."""
    import inspect
    from app.routers import dashboard
    src = inspect.getsource(dashboard.save_raci)
    assert "record_raci_outcome" in src, "save_raci must record the correction for learning"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_save_raci_records_outcome_source -v`
Expected: FAIL — assertion: save_raci must record the correction.

- [ ] **Step 3: Write minimal implementation**

In `app/routers/dashboard.py`, inside `save_raci`, immediately after `logger.info(f"save_raci: new_assignments: {new_assignments}")` (line ~1880), add:

```python
    # Record this correction for learning (creates/updates RACISuggestion)
    try:
        from app.services.raci_service import record_raci_outcome
        final_items = [{"user_id": uid, "role": role} for uid, role in new_assignments.items()]
        await record_raci_outcome(decision_id, final_items)
    except Exception as e:
        logger.warning(f"save_raci: record_raci_outcome failed for decision {decision_id}: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py::test_save_raci_records_outcome_source -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routers/dashboard.py tests/test_raci_learning.py
git commit -m "feat(raci): web save_raci records correction for learning"
```

---

## Task 4: `build_raci_context` — single learned-signal block (Section D unify)

**Files:**
- Modify: `app/services/raci_service.py` — add `build_raci_context`; refactor `generate_raci_for_decision` and `assign_raci_from_ai` to use it.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
@pytest.mark.asyncio
async def test_build_raci_context_returns_text_and_meta(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import app.services.raci_service as rs
    import app.services.lessons_service as ls

    async def fake_patterns(dtype, session):
        return "דפוסי RACI..."
    monkeypatch.setattr(ls, "get_raci_patterns", fake_patterns)
    monkeypatch.setattr(rs, "_get_raci_few_shots", AsyncMock(return_value="דוגמאות..."))
    monkeypatch.setattr(rs, "_get_active_rules", AsyncMock(return_value="כללים..."))
    monkeypatch.setattr(rs, "_count_corrections", AsyncMock(return_value={"past_edits": 4, "rules": 3, "patterns": 1}))

    decision = MagicMock()
    decision.type.value = "normal"
    session = AsyncMock()

    text, meta = await rs.build_raci_context(decision, session)
    assert "כללים" in text and "דוגמאות" in text
    assert meta["past_edits"] == 4
    assert meta["rules"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_build_raci_context_returns_text_and_meta -v`
Expected: FAIL — `AttributeError: ... has no attribute 'build_raci_context'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/raci_service.py`:

```python
async def _count_corrections(session: AsyncSession) -> dict:
    """Counts for the learning footprint: edited corrections, active rules, accepted suggestions."""
    from sqlalchemy import func as _f
    try:
        edits = await session.scalar(
            select(_f.count()).select_from(RACISuggestion)
            .where(RACISuggestion.outcome == RACISuggestionStatusEnum.EDITED)
        ) or 0
        rules = await session.scalar(
            select(_f.count()).select_from(RACIRule).where(RACIRule.is_active == True)
        ) or 0
        accepted = await session.scalar(
            select(_f.count()).select_from(RACISuggestion)
            .where(RACISuggestion.outcome == RACISuggestionStatusEnum.ACCEPTED)
        ) or 0
        return {"past_edits": int(edits), "rules": int(rules), "patterns": int(accepted)}
    except Exception:
        return {"past_edits": 0, "rules": 0, "patterns": 0}


async def build_raci_context(decision, session: AsyncSession) -> tuple[str, dict]:
    """Single learned-signal block (patterns + few-shots + active rules) for the RACI prompt.
    Returns (context_text, context_meta). Used by both assignment paths."""
    parts = []
    try:
        from app.services.lessons_service import get_raci_patterns
        patterns = await get_raci_patterns(decision.type.value, session)
        if patterns:
            parts.append(patterns)
    except Exception:
        pass
    try:
        few_shots = await _get_raci_few_shots(session)
        if few_shots:
            parts.append(few_shots)
    except Exception:
        pass
    try:
        rules = await _get_active_rules(session)
        if rules:
            parts.append(rules)
    except Exception:
        pass
    meta = await _count_corrections(session)
    return "\n\n".join(parts), meta
```

Refactor `generate_raci_for_decision`: replace the block that builds `raci_patterns`, `few_shots`, `active_rules` (lines ~572-580) with:

```python
            context_text, _context_meta = await build_raci_context(decision, session)
```

and in its prompt f-string replace the three interpolations
```
{raci_patterns}
{few_shots}
{active_rules}
```
with a single
```
{context_text}
```

Refactor `assign_raci_from_ai`: replace its `raci_patterns` block (lines ~174-180) with:

```python
            context_text, _context_meta = await build_raci_context(decision, session)
```

and in its prompt replace `{raci_patterns}` with `{context_text}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): build_raci_context unifies learned signals across both paths"
```

---

## Task 5: Make תחום אחריות a primary matching key (Section E)

**Files:**
- Modify: `app/services/raci_service.py` — add `_build_raci_prompt`; use it in `generate_raci_for_decision` and `assign_raci_from_ai`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
def test_raci_prompt_makes_responsibilities_primary():
    from app.services.raci_service import _build_raci_prompt
    prompt = _build_raci_prompt(
        submitter_str="דנה | מהנדסת",
        type_he="רגיל",
        summary="תקלה במכרז ספקים",
        action="לפרסם מכרז חדש",
        users_desc="- ID=7 | דנה | תחום אחריות: מכרזים ורכש",
        context_text="",
    )
    # responsibilities must be named as the primary signal for R and override default
    assert "תחום האחריות" in prompt
    assert "השיקול העיקרי" in prompt
    assert "גוברים על ברירת המחדל" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_raci_prompt_makes_responsibilities_primary -v`
Expected: FAIL — `cannot import name '_build_raci_prompt'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/raci_service.py`:

```python
def _build_raci_prompt(submitter_str: str, type_he: str, summary: str, action: str,
                       users_desc: str, context_text: str) -> str:
    """Shared RACI prompt. תחום אחריות is the primary signal for R/C; rules/edits override defaults."""
    return f"""אתה מומחה לניהול RACI בארגונים.

הגדרות תפקידים:
- R (Responsible) = האחראי לביצוע ההחלטה
- A (Accountable) = בעל הסמכות הסופית — חייב להיות אחד בלבד, ורצוי מנהל בכיר
- C (Consulted) = מייעץ — צריך להישאל לפני ביצוע
- I (Informed) = מקבל עדכון בלבד לאחר הביצוע

מגיש: {submitter_str}
סוג החלטה: {type_he}
סיכום: {summary}
פעולה מומלצת: {action}

משתמשים זמינים:
{users_desc}

{context_text}

הנחיות RACI (לפי סדר חשיבות):
1. בחר Responsible לפי ההתאמה בין הבעיה/הפעולה לבין תחום האחריות של המשתמש — זהו השיקול העיקרי, לא רק ההיררכיה.
2. בחר Consulted לפי תחומי אחריות משיקים לבעיה (עד 3).
3. בחר Accountable מהדרגים הגבוהים — מנהל אגף או סגן מנהל אגף.
4. הוסף Informed לכל מי שצריך לדעת אך לא לפעול.
5. לכל משתמש תפקיד אחד בלבד; חייב להיות בדיוק A אחד.
6. כללים ותיקוני עבר גוברים על ברירת המחדל.
7. בשדה reason ציין במפורש איזה תחום אחריות תאם לבחירה.

הנחיות responsibility_updates:
- אם הסיבה לבחירת משתמש מצביעה על תחום אחריות שאינו רשום — הוסף אותו.
- כתוב ביטויים קצרים (2-5 מילים), בעברית.
- אל תחזור על מה שכבר רשום.
- אם אין מה להוסיף — השאר רשימה ריקה.

החזר JSON בלבד:
{{
  "raci_distribution": [{{"user_id": מספר, "role": "R|A|C|I", "reason": "סיבה קצרה כולל תחום שתאם"}}],
  "responsibility_updates": [{{"user_id": מספר, "learned": "תחום חדש שנלמד"}}]
}}"""
```

In both `generate_raci_for_decision` and `assign_raci_from_ai`, replace the inline `prompt = f"""..."""` assignment with:

```python
            prompt = _build_raci_prompt(
                submitter_str=submitter_str,
                type_he=type_he,
                summary=decision.summary or "—",
                action=decision.recommended_action or "—",
                users_desc=chr(10).join(users_desc),
                context_text=context_text,
            )
```

Also change the roster line builder in BOTH functions so תחום is prominent — replace:
```python
                resp_str = f", תחום: {u.responsibilities}" if u.responsibilities else ""
```
with:
```python
                resp_str = f" | תחום אחריות: {u.responsibilities}" if u.responsibilities else ""
```
(apply the same replacement in `get_ai_raci_suggestions_from_text` for consistency).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): תחום אחריות is primary R/C signal; shared prompt builder"
```

---

## Task 6: Patterns from corrections, not feedback gate (Section D)

**Files:**
- Modify: `app/services/lessons_service.py:151-204` (`get_raci_patterns`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
def test_get_raci_patterns_uses_corrections_not_feedback_gate():
    import inspect
    from app.services import lessons_service
    src = inspect.getsource(lessons_service.get_raci_patterns)
    # Must draw on RACISuggestion corrections, not only feedback_score>=4
    assert "RACISuggestion" in src
    assert "feedback_score >= 4" not in src.replace(" ", "").replace("\n", "") or "RACISuggestion" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_get_raci_patterns_uses_corrections_not_feedback_gate -v`
Expected: FAIL — `RACISuggestion` not referenced.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `get_raci_patterns` in `app/services/lessons_service.py` with a version that aggregates from accepted/edited `RACISuggestion.final_assignments` joined to decisions of the same type, falling back to feedback-scored decisions only if no corrections exist:

```python
async def get_raci_patterns(decision_type: str, session: AsyncSession) -> str:
    """Which users were assigned which RACI roles in past ACCEPTED/EDITED suggestions
    for this decision type. Corrections are the primary signal (no feedback gate)."""
    try:
        from app.models import RACISuggestion, RACISuggestionStatusEnum, Decision as _Decision, User as _User

        rows = (await session.execute(
            select(RACISuggestion.final_assignments)
            .join(_Decision, RACISuggestion.decision_id == _Decision.id)
            .where(_Decision.type == decision_type)
            .where(RACISuggestion.outcome.in_([
                RACISuggestionStatusEnum.ACCEPTED, RACISuggestionStatusEnum.EDITED
            ]))
            .where(RACISuggestion.final_assignments.isnot(None))
            .order_by(RACISuggestion.accepted_at.desc())
            .limit(30)
        )).scalars().all()

        if not rows:
            return ""

        users_q = await session.execute(select(_User))
        umap = {u.id: u for u in users_q.scalars().all()}

        RACI_HE = {"R": "ביצוע", "A": "סמכות", "C": "יועץ", "I": "לידיעה"}
        # count (role, user) frequency across corrections
        counts: dict[tuple[str, int], int] = {}
        for assignments in rows:
            for item in (assignments or []):
                try:
                    key = (str(item["role"]).upper(), int(item["user_id"]))
                except (KeyError, TypeError, ValueError):
                    continue
                counts[key] = counts.get(key, 0) + 1

        if not counts:
            return ""

        by_raci: dict[str, list[str]] = {}
        for (role_val, uid), cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            u = umap.get(uid)
            if not u:
                continue
            desc = u.username
            if u.job_title:
                desc += f" ({u.job_title})"
            if u.responsibilities:
                desc += f" — {u.responsibilities}"
            by_raci.setdefault(role_val, []).append(f"{desc} [{cnt}×]")

        lines = [f"דפוסי RACI מתיקוני העבר עבור החלטות מסוג {decision_type}:"]
        for role_val, labels in by_raci.items():
            lines.append(f"  {role_val} ({RACI_HE.get(role_val, role_val)}): {', '.join(labels[:3])}")
        lines.append("→ כאשר משתמש עם תחום אחריות תואם קיים ברשימה — העדף אותו לאותו תפקיד RACI.")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"get_raci_patterns failed: {e}")
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py::test_get_raci_patterns_uses_corrections_not_feedback_gate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/lessons_service.py tests/test_raci_learning.py
git commit -m "feat(raci): patterns derive from corrections, drop feedback>=4 gate"
```

---

## Task 7: Few-shots prioritize EDITED corrections (Section D)

**Files:**
- Modify: `app/services/raci_service.py` — `_get_raci_few_shots`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
def test_few_shots_prioritizes_edited_and_raises_limit():
    import inspect
    from app.services import raci_service
    src = inspect.getsource(raci_service._get_raci_few_shots)
    assert "limit: int = 8" in src, "few-shot limit should be raised to 8"
    assert "EDITED" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_few_shots_prioritizes_edited_and_raises_limit -v`
Expected: FAIL — default limit still 4.

- [ ] **Step 3: Write minimal implementation**

In `_get_raci_few_shots`, change the signature default from `limit: int = 4` to `limit: int = 8`. After fetching `rows`, sort EDITED ahead of ACCEPTED before building lines — replace the `.order_by(RACISuggestion.accepted_at.desc())` query result handling by adding, immediately after `rows = (... ).all()`:

```python
        # corrections (EDITED) carry more signal than approvals (ACCEPTED) — surface them first
        rows = sorted(
            rows,
            key=lambda r: (0 if r[0].outcome == RACISuggestionStatusEnum.EDITED else 1),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raci_learning.py::test_few_shots_prioritizes_edited_and_raises_limit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): few-shots prioritize EDITED corrections, raise limit to 8"
```

---

## Task 8: Learning footprint in the proposal (Section C)

**Files:**
- Modify: `app/services/raci_service.py` — `generate_raci_for_decision` returns meta; `propose_raci_to_submitter` shows footprint.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_raci_learning.py
def test_footprint_line_formatter():
    from app.services.raci_service import _footprint_line
    assert _footprint_line({"rules": 3, "past_edits": 4, "patterns": 2}) == \
        "📚 התבסס על: 3 כללים, 4 תיקוני עבר, 2 דוגמאות"
    assert _footprint_line({"rules": 0, "past_edits": 0, "patterns": 0}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raci_learning.py::test_footprint_line_formatter -v`
Expected: FAIL — `cannot import name '_footprint_line'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/raci_service.py`:

```python
def _footprint_line(meta: dict) -> str:
    """One-line learning footprint for the proposal; empty if nothing was learned yet."""
    rules = meta.get("rules", 0)
    edits = meta.get("past_edits", 0)
    pats = meta.get("patterns", 0)
    if not (rules or edits or pats):
        return ""
    return f"📚 התבסס על: {rules} כללים, {edits} תיקוני עבר, {pats} דוגמאות"
```

Thread meta through generation: in `generate_raci_for_decision`, capture meta from the `build_raci_context` call (rename `_context_meta` → `context_meta`) and include it in the return. Change its return signature from `(valid_items, user_names, parsed)` to `(valid_items, user_names, parsed, context_meta)`; update the early-return tuples to `[], {}, {}, {}`.

In `propose_raci_to_submitter`, update the unpack:
```python
    valid_items, user_names, parsed, context_meta = await generate_raci_for_decision(decision_id)
```
and append the footprint to `msg` before sending:
```python
    _fp = _footprint_line(context_meta)
    if _fp:
        msg += f"\n\n<i>{_fp}</i>"
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/test_raci_learning.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/raci_service.py tests/test_raci_learning.py
git commit -m "feat(raci): learning footprint line in RACI proposal"
```

---

## Task 9: Surface per-user reason on the decision page (Section C)

**Files:**
- Modify: `app/templates/raci_intelligence.html` — show the AI `reason` per assignment from `suggested_assignments`.

- [ ] **Step 1: Inspect the current render**

Run: `python -m pytest tests/test_raci_learning.py -v` (baseline green) and open `app/templates/raci_intelligence.html` to find where `s.suggested_assignments` is iterated.

- [ ] **Step 2: Add the reason display**

In `app/templates/raci_intelligence.html`, where each suggested assignment is listed, render its `reason` when present:

```html
{% for a in s.suggested_assignments %}
  <span class="raci-chip">{{ a.role }}: {{ user_map.get(a.user_id, a.user_id) }}</span>
  {% if a.reason %}<small class="text-muted">— {{ a.reason }}</small>{% endif %}
{% endfor %}
```

(Match existing markup/classes in the file; only add the `{% if a.reason %}` fragment if not already shown.)

- [ ] **Step 3: Manual verification**

Run the app, open `/dashboard/raci-intelligence`, confirm each suggestion shows the AI's per-user reason text. Note: this is a template-only change; no unit test.

- [ ] **Step 4: Commit**

```bash
git add app/templates/raci_intelligence.html
git commit -m "feat(raci): show AI per-user reason on raci-intelligence page"
```

---

## Task 10: Full regression + manual end-to-end

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: all pass (existing + new `test_raci_learning.py`).

- [ ] **Step 2: Restart the app**

Per CLAUDE.md operational mode. If on Railway, redeploy; if local Docker, `docker-compose restart fastapi`.

- [ ] **Step 3: Manual end-to-end (Section success criteria)**

1. Edit a user's תחום אחריות on the dashboard. Submit a new decision whose problem matches that תחום. Confirm the R/C assignment now picks that user — success criterion 3.
2. Edit a RACI on the web. Open `/dashboard/raci-intelligence` and confirm an `EDITED` row appeared (criterion 1). Add an `edit_reason`, click "analyze reasons", confirm a `RACIRule` is created.
3. Submit another decision; confirm the Telegram proposal shows the `📚 התבסס על:` footprint with non-zero counts (criterion 2).

- [ ] **Step 4: Final commit (if any doc/notes updated)**

```bash
git add -A
git commit -m "chore(raci): verify learning loop end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** A (Tasks 2,3 + Telegram already wired via mark_* delegation), B (Task 3 feeds existing pipeline; Task 9 visibility), C (Tasks 8,9), D (Tasks 4,6,7 + temperature instruction in Task 5 prompt), E (Task 5). All sections mapped.
- **Type consistency:** `record_raci_outcome(decision_id, final_items)`, `build_raci_context(decision, session) -> (text, meta)`, `_diff_outcome`, `_footprint_line`, `_build_raci_prompt` used consistently. `generate_raci_for_decision` return arity changed to 4 — updated at its sole caller `propose_raci_to_submitter` (Task 8). Verify no other caller exists before finishing Task 8 (`grep -rn generate_raci_for_decision app/`).
- **No schema changes** — confirmed against `models.py`.
