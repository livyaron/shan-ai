# Fix RAG Context Leakage in Decision Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the AI from copying `recommended_action` and `risks` from past decisions instead of reasoning about the current problem.

**Architecture:** Three targeted edits: (1) strip `recommended_action` from the RAG context format, (2) reframe risk-pattern injection as optional hints not a list to copy, (3) harden the system prompt with an explicit anti-copy rule and restructure the user message so current problem comes last (recency bias).

**Tech Stack:** Python, `app/services/embedding_service.py`, `app/services/claude_service.py`, `app/services/lessons_service.py`

---

## File Map

| File | Change |
|------|--------|
| `app/services/embedding_service.py` | `format_past_context()` — remove `recommended_action` from output; show type + summary + lesson only |
| `app/services/claude_service.py` | `SYSTEM_PROMPT` — add explicit anti-copy instruction; `analyze()` — restructure user message: current problem last, past context labeled "for calibration only", wrapped in XML delimiters |
| `app/services/lessons_service.py` | `get_risk_patterns()` — reframe output header to "check if applicable, don't copy" |

---

## Task 1: Strip `recommended_action` from RAG past-context format

**Files:**
- Modify: `app/services/embedding_service.py:61-70`

**Problem:** Line 68 includes `→ {d.recommended_action}` from past decisions. The LLM treats this as a template for the *current* decision's recommended action.

- [ ] **Step 1: Read current implementation**

```bash
# Confirm current format_past_context output:
grep -n "recommended_action" app/services/embedding_service.py
```
Expected: line ~68 shows `→ {d.recommended_action}`

- [ ] **Step 2: Edit format_past_context in `app/services/embedding_service.py`**

Replace:
```python
def format_past_context(decisions: list[Decision]) -> str:
    """Format similar past decisions as context string for Groq."""
    if not decisions:
        return ""
    lines = ["החלטות עבר דומות (למידת ניסיון):"]
    for d in decisions:
        score = f" | ציון פידבק: {d.feedback_score}/5" if d.feedback_score else ""
        lines.append(f"• [{d.type.value.upper()}] {d.summary} → {d.recommended_action}{score}")
        if d.feedback_notes:
            lines.append(f"  לקח: {d.feedback_notes}")
    return "\n".join(lines)
```

With:
```python
def format_past_context(decisions: list[Decision]) -> str:
    """Format similar past decisions for calibration only — no recommended_action to avoid template copying."""
    if not decisions:
        return ""
    lines = ["החלטות עבר דומות — לכיול סיווג בלבד, אל תעתיק מהן:"]
    for d in decisions:
        score = f" | פידבק: {d.feedback_score}/5" if d.feedback_score else ""
        lines.append(f"• [{d.type.value.upper()}] {d.summary}{score}")
        if d.feedback_notes:
            lines.append(f"  לקח מהניסיון: {d.feedback_notes}")
    return "\n".join(lines)
```

- [ ] **Step 3: Verify no other callers depend on `recommended_action` in the output**

```bash
grep -rn "format_past_context" app/
```
Expected: only `decision_service.py` and `ask.py` call it — both just pass the string to the prompt, no parsing.

- [ ] **Step 4: Commit**

```bash
git add app/services/embedding_service.py
git commit -m "fix(rag): remove recommended_action from past-context format to stop template copying"
```

---

## Task 2: Reframe risk-pattern injection header

**Files:**
- Modify: `app/services/lessons_service.py:241`

**Problem:** Header says "סיכונים נפוצים בהחלטות X מהעבר" — the LLM reads this as "here are the risks for this decision" and copies them verbatim instead of evaluating applicability.

- [ ] **Step 1: Edit the header line in `get_risk_patterns()` in `app/services/lessons_service.py`**

Find and replace line ~241:
```python
        lines = [f"סיכונים נפוצים בהחלטות {decision_type} מהעבר:"]
```

With:
```python
        lines = [f"סיכונים שנצפו בעבר בהחלטות מסוג {decision_type} — בדוק האם רלוונטיים לבעיה הנוכחית, אל תעתיק אוטומטית:"]
```

- [ ] **Step 2: Commit**

```bash
git add app/services/lessons_service.py
git commit -m "fix(rag): reframe risk-patterns header to prevent automatic copy into current decision"
```

---

## Task 3: Harden system prompt + restructure user message

**Files:**
- Modify: `app/services/claude_service.py`

**Problem A:** `SYSTEM_PROMPT` has no explicit instruction that `recommended_action`, `risks`, and `assumptions` must be derived from the *current problem*, not from the injected context.

**Problem B:** `analyze()` builds user_message as: `[role]\n[past_context]\n[current_problem]` — current problem is last but the model saw the past context first and forms strong priors. Better to wrap in clear delimiters.

- [ ] **Step 1: Add anti-copy rule to `SYSTEM_PROMPT` in `app/services/claude_service.py`**

The final line of `SYSTEM_PROMPT` currently reads:
```python
  "suggested_raci": { ... }
}"""
```

After the closing `}"""` at the very bottom of the `SYSTEM_PROMPT` string (before the `"""`), add a newline and this instruction block:

Full replacement — change the end of `SYSTEM_PROMPT` from:
```python
  "suggested_raci": {
    "R": ["division_manager", "department_manager"],
    "A": "deputy_division_manager",
    "C": ["project_manager"],
    "I": [],
    "reason": "תיאור קצר של הנמקת ההקצאות"
  }
}"""
```

To:
```python
  "suggested_raci": {
    "R": ["division_manager", "department_manager"],
    "A": "deputy_division_manager",
    "C": ["project_manager"],
    "I": [],
    "reason": "תיאור קצר של הנמקת ההקצאות"
  }
}

כלל חובה: כל שדות הפלט (summary, recommended_action, risks, assumptions) חייבים לנבוע מהבעיה הנוכחית בלבד.
אל תעתיק ואל תשאל מהקשר העבר — הקשר העבר משמש אך ורק לכיול רמת הסיכון והסיווג.
אם הקשר העבר אינו רלוונטי לחלוטין לבעיה הנוכחית — התעלם ממנו לחלוטין."""
```

- [ ] **Step 2: Restructure user message in `analyze()` in `app/services/claude_service.py`**

Current code in `analyze()`:
```python
        user_message = f"תפקיד המגיש: {user_role}\n\n"
        if past_context:
            user_message += f"החלטות עבר רלוונטיות:\n{past_context}\n\n"
        user_message += f"בעיה/החלטה:\n{clean_problem}"
```

Replace with:
```python
        parts = [f"תפקיד המגיש: {user_role}"]
        if past_context:
            parts.append(
                f"<CONTEXT_FOR_CALIBRATION_ONLY>\n"
                f"{past_context}\n"
                f"</CONTEXT_FOR_CALIBRATION_ONLY>\n"
                f"(הקשר זה מיועד לכיול בלבד. אל תעתיק ממנו recommended_action, risks, או assumptions.)"
            )
        parts.append(
            f"<CURRENT_PROBLEM>\n"
            f"{clean_problem}\n"
            f"</CURRENT_PROBLEM>\n"
            f"נתח את הבעיה הנוכחית הנ״ל בלבד. כל שדות ה-JSON חייבים להתייחס לבעיה זו."
        )
        user_message = "\n\n".join(parts)
```

- [ ] **Step 3: Restart Docker and send a test decision**

```bash
docker-compose restart fastapi
docker logs -f shan-ai-api | grep -E "תגובת Groq|analyze_only|ERROR"
```

Send via Telegram: "תחמ״ש קסם תבנה בפורמט פתוח של 161 ק״ו"

Expected: preview shows `recommended_action` and `risks` about electrical substations / 161kV infrastructure, NOT about staffing or candidates.

- [ ] **Step 4: Commit**

```bash
git add app/services/claude_service.py
git commit -m "fix(prompt): add anti-copy rule + XML delimiters to prevent RAG context leakage into decision output"
```

---

## Task 4: Verify the fix end-to-end

- [ ] **Step 1: Check the Groq raw response in logs**

```bash
docker logs shan-ai-api 2>&1 | grep "תגובת Groq" | tail -5
```

The logged raw JSON should show `recommended_action` and `risks` that are topically about the current problem (electrical substation, 161kV) and not about unrelated past decisions (staffing, candidates, courses).

- [ ] **Step 2: Confirm past context is formatted without recommended_action**

Add a temporary `logger.info` in `analyze()` right before the `llm_chat` call to log the first 500 chars of `user_message`, then verify:

```python
logger.info(f"user_message preview: {user_message[:500]}")
```

Expected: the `<CONTEXT_FOR_CALIBRATION_ONLY>` block does NOT contain `→ recommended_action` text.

Remove the temporary log line after verification.

- [ ] **Step 3: Final commit if needed**

```bash
git add app/services/claude_service.py
git commit -m "chore: remove debug log from analyze()"
```

---

## Self-Review

**Spec coverage:**
- ✅ Root cause 1 (recommended_action in past context): Task 1
- ✅ Root cause 2 (risk patterns framing): Task 2
- ✅ Root cause 3 (no anti-copy rule, message structure): Task 3
- ✅ Verification: Task 4

**Placeholder scan:** All steps have exact file paths, exact code blocks, exact commands. No TBDs.

**Type consistency:** No new types introduced. All edits are string manipulation in existing functions.

**Edge cases considered:**
- `past_context=""` → `if past_context:` guard already present, no change needed
- `get_risk_patterns()` returns `""` when no data → no change to that path
- XML delimiters in Hebrew prompt: Groq/Llama handles XML-style markers well; tested pattern in production LLM prompts
