# Shan-AI Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four targeted improvements: typing indicator for VIEWER path, smart project disambiguation, multi-turn conversation context, and a weekly intelligence report with cron + manual triggers.

**Architecture:** Sequential by complexity. Each task is self-contained with no cross-task dependencies. B3 and A4 are pure additions to existing files. A2 adds state helpers then injects into 4 call sites. C4 is a new service wired into the existing APScheduler, /command, and dashboard.

**Tech Stack:** python-telegram-bot v21, APScheduler 3.x, SQLAlchemy async, Groq (llm_router), FastAPI, Jinja2

---

## File Map

| Task | Creates | Modifies |
|------|---------|----------|
| B3 | — | `telegram_polling.py` |
| A4 | `tests/test_disambiguation.py` | `project_tools.py`, `ask_router.py`, `telegram_state.py`, `telegram_polling.py` |
| A2 | `tests/test_conversation_context.py` | `telegram_state.py`, `telegram_polling.py`, `telegram_routing.py`, `claude_service.py`, `knowledge_service.py`, `ask_router.py` |
| C4 | `app/services/weekly_report_service.py`, `tests/test_weekly_report.py` | `eval_cron.py`, `telegram_polling.py`, `dashboard.py`, `dashboard.html` |

---

## Task 1: B3 — Typing Indicator for VIEWER Path

The typing indicator (`send_chat_action`) already fires for non-VIEWER users at line ~537 in `handle_message`, but is placed **after** the `if user.role == _RE.VIEWER` early-return block. VIEWER users exit before reaching it.

**File:** `app/services/telegram_polling.py`

- [ ] **Step 1: Locate the two blocks in `handle_message`**

Find this sequence (around lines 530–539):
```python
            # Viewer: separate read-only pipeline
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await self._handle_viewer_message(update, context, user, text.strip())
                return

            # Show typing indicator
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )
```

- [ ] **Step 2: Move `send_chat_action` before the VIEWER check**

Replace the above with:
```python
            # Show typing indicator for all role-bearing users (including VIEWER)
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )

            # Viewer: separate read-only pipeline
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await self._handle_viewer_message(update, context, user, text.strip())
                return
```

- [ ] **Step 3: Verify no test regressions**

```bash
docker-compose exec fastapi python -m pytest tests/test_viewer_role.py -v
```
Expected: all existing viewer tests PASS.

- [ ] **Step 4: Manual smoke test**

Start the bot locally. Log in as a VIEWER user. Send any text message. Confirm the "typing…" indicator appears in Telegram before the reply arrives.

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_polling.py
git commit -m "fix(ux): show typing indicator for VIEWER path in handle_message"
```

---

## Task 2: A4 — Smart Project Disambiguation

When `by_identifier` intent returns 2–4 name-matched projects with no exact code match, instead of dumping all cards, the bot pauses and offers inline buttons.

**Files:**
- Modify: `app/services/project_tools.py` (by_identifier branch of `answer_project_query`)
- Modify: `app/services/ask_router.py` (detect disambiguation sentinel)
- Modify: `app/services/telegram_state.py` (add `_awaiting_disambiguation`)
- Modify: `app/services/telegram_polling.py` (handle `path="disambiguation"` and `disambig:` callbacks)
- Create: `tests/test_disambiguation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_disambiguation.py`:
```python
"""Tests for smart project disambiguation (A4)."""
import pytest
import json


def test_disambig_sentinel_is_string():
    """Sentinel format is stable."""
    sentinel = f"__DISAMBIG__:{json.dumps(['רעות', 'רהט'], ensure_ascii=False)}"
    assert sentinel.startswith("__DISAMBIG__:")
    candidates = json.loads(sentinel[len("__DISAMBIG__:"):])
    assert candidates == ["רעות", "רהט"]


@pytest.mark.asyncio
async def test_awaiting_disambiguation_state():
    """_awaiting_disambiguation dict is importable and mutable."""
    from app.services.telegram_state import _awaiting_disambiguation
    _awaiting_disambiguation[999] = "מה פרויקט X?"
    assert _awaiting_disambiguation[999] == "מה פרויקט X?"
    del _awaiting_disambiguation[999]
    assert 999 not in _awaiting_disambiguation
```

- [ ] **Step 2: Run — verify FAIL (ImportError on `_awaiting_disambiguation`)**

```bash
docker-compose exec fastapi python -m pytest tests/test_disambiguation.py -v
```
Expected: `ImportError: cannot import name '_awaiting_disambiguation'`

- [ ] **Step 3: Add `_awaiting_disambiguation` to `telegram_state.py`**

Add at the end of `app/services/telegram_state.py`:
```python
# { telegram_id (int): original_question (str) }  — waiting for user to pick one project
_awaiting_disambiguation: dict[int, str] = {}
```

- [ ] **Step 4: Run — verify both tests PASS**

```bash
docker-compose exec fastapi python -m pytest tests/test_disambiguation.py -v
```
Expected: PASS

- [ ] **Step 5: Add disambiguation signal to `project_tools.py`**

In `app/services/project_tools.py`, find the `by_identifier` branch of `answer_project_query` (around line 548):

```python
        elif intent == "by_identifier":
            matches = await find_projects_by_identifier(param, session)
            if not matches:
                context_str = f"לא נמצא פרויקט בזיהוי '{param}'."
            elif len(matches) == 1:
                data = matches[0]
                user_data["last_project"] = data["project_identifier"]
                current_project_id = data["project_identifier"]
                context_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                user_data.pop("last_project", None)
                current_project_id = None
                overflow_note = "\n\n⚠️ מוצגים 10 ראשונים בלבד — יש עוד תוצאות." if len(matches) == 10 else ""
                divider = "\n━━━━━━━━━━━━━━━━━━\n"
                cards = divider.join(
                    _format_project_card(p, i + 1, len(matches))
                    for i, p in enumerate(matches)
                )
                answer = cards + overflow_note
                log_id = await _log_query(text, answer, intent, None, session, user_id)
                return answer, log_id
```

Replace the `else:` block with:
```python
            else:
                # 2–4 ambiguous matches → signal disambiguation to the caller
                if 2 <= len(matches) <= 4:
                    identifiers = [p["project_identifier"] for p in matches]
                    return f"__DISAMBIG__:{json.dumps(identifiers, ensure_ascii=False)}", None
                # 5+ matches — show all cards (original behaviour)
                user_data.pop("last_project", None)
                current_project_id = None
                overflow_note = "\n\n⚠️ מוצגים 10 ראשונים בלבד — יש עוד תוצאות." if len(matches) == 10 else ""
                divider = "\n━━━━━━━━━━━━━━━━━━\n"
                cards = divider.join(
                    _format_project_card(p, i + 1, len(matches))
                    for i, p in enumerate(matches)
                )
                answer = cards + overflow_note
                log_id = await _log_query(text, answer, intent, None, session, user_id)
                return answer, log_id
```

- [ ] **Step 6: Add disambiguation detection to `ask_router.py`**

In `app/services/ask_router.py`, find the project query block (around line 177):
```python
    if _is_project_query(question):
        try:
            answer, log_id = await project_tools.answer_project_query(
                question, session, {}, user_id=user_id,
            )
            return await _finish(AnswerResult(
                answer=answer,
                sources_used=[{"source": "projects_db"}],
                log_id=log_id,
                path="project_tools",
                intent=None,
                param=None,
                has_files=True,
                has_decisions=False,
                file_names=[],
                sources_text="📂 מסד הפרויקטים",
            ), [])
        except Exception:
            logger.warning("project_tools failed, falling through to RAG", exc_info=True)
```

Replace with:
```python
    if _is_project_query(question):
        try:
            import json as _json
            answer, log_id = await project_tools.answer_project_query(
                question, session, {}, user_id=user_id,
            )
            if isinstance(answer, str) and answer.startswith("__DISAMBIG__:"):
                candidates = _json.loads(answer[len("__DISAMBIG__:"):])
                return await _finish(AnswerResult(
                    answer=_json.dumps(candidates, ensure_ascii=False),
                    sources_used=[{"source": "disambiguation", "candidates": candidates}],
                    log_id=None,
                    path="disambiguation",
                    intent="by_identifier",
                    param=None,
                    has_files=False,
                    has_decisions=False,
                    file_names=[],
                    sources_text="",
                ), [])
            return await _finish(AnswerResult(
                answer=answer,
                sources_used=[{"source": "projects_db"}],
                log_id=log_id,
                path="project_tools",
                intent=None,
                param=None,
                has_files=True,
                has_decisions=False,
                file_names=[],
                sources_text="📂 מסד הפרויקטים",
            ), [])
        except Exception:
            logger.warning("project_tools failed, falling through to RAG", exc_info=True)
```

- [ ] **Step 7: Handle `path="disambiguation"` in `telegram_polling.py`**

In `handle_message`, find the project/knowledge/null route block (around line 580):
```python
            if ai_route in ("project", "knowledge", None):
                kb = None
                try:
                    from app.services.ask_router import route as _ask_route
                    result = await _ask_route(text, session, user.id, log_to_db=True)
                    answer = result.answer
                    if result.path == "decision":
                        reply = f"‏{answer}"
                    elif result.path == "project_tools":
                        reply = f"‏{answer}"
                    else:
                        reply = f"‏\U0001F916 <b>תשובה:</b>\n\n{_html.escape(answer)}"
                        if result.sources_text:
                            reply += f"\n\n<i>{_html.escape(result.sources_text)}</i>"
                    if result.log_id:
                        kb = _feedback_keyboard(result.log_id)
                except Exception:
                    logger.warning("ask_router.route failed", exc_info=True)
                    reply = "‏לא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return
```

Replace with:
```python
            if ai_route in ("project", "knowledge", None):
                kb = None
                try:
                    import json as _json
                    from app.services.ask_router import route as _ask_route
                    from app.services.telegram_state import _awaiting_disambiguation
                    result = await _ask_route(text, session, user.id, log_to_db=True)

                    if result.path == "disambiguation":
                        candidates = _json.loads(result.answer)
                        _awaiting_disambiguation[telegram_id] = text
                        buttons = []
                        row = []
                        for c in candidates:
                            row.append(InlineKeyboardButton(f"📁 {c}", callback_data=f"disambig:{c}"))
                            if len(row) == 2:
                                buttons.append(row)
                                row = []
                        if row:
                            buttons.append(row)
                        buttons.append([InlineKeyboardButton("❌ ביטול", callback_data="disambig:__cancel__")])
                        await update.message.reply_text(
                            "‏🔍 מצאתי מספר פרויקטים תואמים — על איזה מהם התכוונת?",
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        return

                    answer = result.answer
                    if result.path == "decision":
                        reply = f"‏{answer}"
                    elif result.path == "project_tools":
                        reply = f"‏{answer}"
                    else:
                        reply = f"‏\U0001F916 <b>תשובה:</b>\n\n{_html.escape(answer)}"
                        if result.sources_text:
                            reply += f"\n\n<i>{_html.escape(result.sources_text)}</i>"
                    if result.log_id:
                        kb = _feedback_keyboard(result.log_id)
                except Exception:
                    logger.warning("ask_router.route failed", exc_info=True)
                    reply = "‏לא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                    return
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return
```

- [ ] **Step 8: Add `disambig:` callback handler in `handle_callback`**

In `handle_callback`, after the `pm:` block (around line 747), before `try: parts = data.split(":")`, add:

```python
        # Disambiguation — user selected a project from the ambiguous-match keyboard
        if data.startswith("disambig:"):
            from app.services.telegram_state import _awaiting_disambiguation
            from app.services import project_tools as _pt
            identifier = data[len("disambig:"):]
            if identifier == "__cancel__":
                _awaiting_disambiguation.pop(telegram_id, None)
                await query.edit_message_text("‏❌ הבקשה בוטלה.")
                return
            _awaiting_disambiguation.pop(telegram_id, None)
            async with async_session_maker() as _dis_session:
                _dis_user = await _dis_session.scalar(
                    select(User).where(User.telegram_id == telegram_id)
                )
                if not _dis_user or not _dis_user.role:
                    await query.answer("לא מורשה")
                    return
                answer, log_id = await _pt.answer_project_query(
                    identifier, _dis_session, {},
                    user_id=_dis_user.id,
                    precomputed_intent="by_identifier",
                    precomputed_param=identifier,
                )
            await query.edit_message_text(
                f"‏{answer}",
                parse_mode="HTML",
                reply_markup=_feedback_keyboard(log_id) if log_id else None,
            )
            return
```

- [ ] **Step 9: Run tests**

```bash
docker-compose exec fastapi python -m pytest tests/test_disambiguation.py tests/test_ask_router.py tests/test_projects_menu_service.py -v
```
Expected: all PASS

- [ ] **Step 10: Manual smoke test**

Send a message that matches 2–3 projects by substring (e.g., if two projects have names containing the same word). Confirm the bot sends an inline keyboard with project identifier buttons. Click one. Confirm the bot replies with that project's card.

- [ ] **Step 11: Commit**

```bash
git add app/services/project_tools.py app/services/ask_router.py \
        app/services/telegram_state.py app/services/telegram_polling.py \
        tests/test_disambiguation.py
git commit -m "feat(ux): smart project disambiguation for 2-4 ambiguous matches (A4)"
```

---

## Task 3: A2 — Multi-turn Conversation Context

Adds in-memory conversation context (last 5 exchanges, TTL 30 min) injected at 3 call sites.

**Files:**
- Modify: `app/services/telegram_state.py`
- Modify: `app/services/telegram_polling.py`
- Modify: `app/services/telegram_routing.py`
- Modify: `app/services/claude_service.py`
- Modify: `app/services/knowledge_service.py`
- Modify: `app/services/ask_router.py`
- Create: `tests/test_conversation_context.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conversation_context.py`:
```python
"""Tests for multi-turn conversation context (A2)."""
import time
import pytest


def test_append_and_get_context():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345001
    clear_context(tid)

    append_context(tid, "user", "מה פרויקט רעות?")
    append_context(tid, "assistant", "פרויקט רעות בשלב הקמה.")

    ctx = get_context(tid)
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[0]["content"] == "מה פרויקט רעות?"
    assert ctx[1]["role"] == "assistant"
    clear_context(tid)


def test_context_maxlen():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345002
    clear_context(tid)

    for i in range(7):
        append_context(tid, "user", f"msg {i}")
    ctx = get_context(tid)
    # get_context returns last 3 of the stored 5
    assert len(ctx) == 3
    assert ctx[-1]["content"] == "msg 6"
    clear_context(tid)


def test_context_ttl_expired():
    from app.services import telegram_state as ts
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345003
    clear_context(tid)

    append_context(tid, "user", "test")
    # Manually expire by backdating the timestamp
    ctx_deque = ts._conversation_context[tid]
    last = ctx_deque[-1]
    ctx_deque[-1] = {**last, "ts": time.time() - 1801}

    result = get_context(tid)
    assert result == []
    assert tid not in ts._conversation_context


def test_clear_context():
    from app.services.telegram_state import append_context, get_context, clear_context
    tid = 12345004
    append_context(tid, "user", "hello")
    clear_context(tid)
    assert get_context(tid) == []
```

- [ ] **Step 2: Run — verify FAIL**

```bash
docker-compose exec fastapi python -m pytest tests/test_conversation_context.py -v
```
Expected: `ImportError: cannot import name 'append_context'`

- [ ] **Step 3: Add context helpers to `telegram_state.py`**

Add at the end of `app/services/telegram_state.py`:
```python
from collections import deque
import time as _time

_CONTEXT_MAXLEN = 5
_CONTEXT_TTL = 1800.0   # 30 minutes
_CONTEXT_INJECT = 3     # how many entries to inject into prompts

# { telegram_id (int): deque of {"role": str, "content": str, "ts": float} }
_conversation_context: dict[int, deque] = {}


def get_context(telegram_id: int) -> list[dict]:
    """Return last _CONTEXT_INJECT entries; clear and return [] if TTL expired."""
    if telegram_id not in _conversation_context:
        return []
    dq = _conversation_context[telegram_id]
    if not dq:
        return []
    if _time.time() - dq[-1]["ts"] > _CONTEXT_TTL:
        del _conversation_context[telegram_id]
        return []
    return list(dq)[-_CONTEXT_INJECT:]


def append_context(telegram_id: int, role: str, content: str) -> None:
    """Append one exchange entry; creates the deque on first call."""
    if telegram_id not in _conversation_context:
        _conversation_context[telegram_id] = deque(maxlen=_CONTEXT_MAXLEN)
    _conversation_context[telegram_id].append({
        "role": role,
        "content": content[:500],
        "ts": _time.time(),
    })


def clear_context(telegram_id: int) -> None:
    """Remove all context for this user."""
    _conversation_context.pop(telegram_id, None)
```

- [ ] **Step 4: Run — verify all 4 context tests PASS**

```bash
docker-compose exec fastapi python -m pytest tests/test_conversation_context.py -v
```
Expected: PASS

- [ ] **Step 5: Inject context into `telegram_routing._ai_route_message`**

In `app/services/telegram_routing.py`, change the signature and prompt construction:

Old signature:
```python
async def _ai_route_message(text: str) -> dict:
```

New signature + context injection (replace the full function up to the `llm_chat` call):
```python
async def _ai_route_message(text: str, conversation_context: list[dict] | None = None) -> dict:
    """One LLM call: classify route (project/knowledge/decision) + extract intent+param."""
    t = text.strip()
    if not t.endswith("?") and any(t.startswith(p) for p in _DECISION_PREFIXES):
        logger.info(f"_ai_route_message: decision_prefix shortcut for: {t[:60]!r}")
        return {"route": "decision", "intent": "general", "param": None}

    from app.services.llm_router import llm_chat

    if conversation_context:
        ctx_lines = "\n".join(
            f"{'User' if e['role'] == 'user' else 'Bot'}: {e['content']}"
            for e in conversation_context
        )
        effective_text = f"הקשר שיחה:\n{ctx_lines}\n\nהודעה נוכחית: {text}"
    else:
        effective_text = text

    prompt = _ROUTING_PROMPT.replace("{text}", effective_text)
    try:
        response = await llm_chat(
```

(Leave the rest of the function unchanged.)

- [ ] **Step 6: Inject context into `ClaudeService.analyze`**

In `app/services/claude_service.py`, change the `analyze` method signature and body:

Old:
```python
    async def analyze(self, problem: str, user_role: str, past_context: str = "") -> dict:
        """Send problem to configured LLM and return parsed decision JSON."""
        # Replace straight quotes with Hebrew geresh to avoid breaking JSON
        clean_problem = problem.replace('"', '״').replace("'", "׳")
        parts = [f"תפקיד המגיש: {user_role}"]
        if past_context:
```

New (add `conversation_context` param and inject it):
```python
    async def analyze(self, problem: str, user_role: str, past_context: str = "",
                      conversation_context: list[dict] | None = None) -> dict:
        """Send problem to configured LLM and return parsed decision JSON."""
        clean_problem = problem.replace('"', '״').replace("'", "׳")
        parts = [f"תפקיד המגיש: {user_role}"]
        if conversation_context:
            ctx_lines = "\n".join(
                f"{'משתמש' if e['role'] == 'user' else 'מערכת'}: {e['content']}"
                for e in conversation_context
            )
            parts.append(
                "<CONVERSATION_CONTEXT>\n"
                f"{ctx_lines}\n"
                "</CONVERSATION_CONTEXT>\n"
                "(הקשר שיחה — לכיול הנמקה בלבד.)"
            )
        if past_context:
```

(Leave the rest of `analyze` unchanged — it already builds `parts` and joins them.)

- [ ] **Step 7: Add `conversation_context` param to `ask_router.route`**

In `app/services/ask_router.py`, change the `route` signature:

Old:
```python
async def route(
    question: str,
    session: AsyncSession,
    user_id: int,
    *,
    log_to_db: bool = True,
    snapshot_mode: bool = False,
) -> AnswerResult:
```

New:
```python
async def route(
    question: str,
    session: AsyncSession,
    user_id: int,
    *,
    log_to_db: bool = True,
    snapshot_mode: bool = False,
    conversation_context: list[dict] | None = None,
) -> AnswerResult:
```

Then pass it to the RAG call. Find the `# 3. Default RAG` block (around line 197):
```python
    # 3. Default RAG
    result = await ks.answer_with_full_context(
        question, session, user_id, log_to_db=log_to_db,
    )
```

Replace with:
```python
    # 3. Default RAG
    result = await ks.answer_with_full_context(
        question, session, user_id, log_to_db=log_to_db,
        conversation_context=conversation_context,
    )
```

- [ ] **Step 8: Add `conversation_context` param to `knowledge_service.answer_with_full_context`**

In `app/services/knowledge_service.py`, find the signature of `answer_with_full_context` (around line 1856):
```python
async def answer_with_full_context(
    question: str,
    session: AsyncSession,
    user_id: int,
    log_to_db: bool = True,
) -> dict:
```

Change to:
```python
async def answer_with_full_context(
    question: str,
    session: AsyncSession,
    user_id: int,
    log_to_db: bool = True,
    conversation_context: list[dict] | None = None,
) -> dict:
```

Then, inside the function, find where `question` is passed to the LLM (look for the first `llm_chat` call or the construction of the prompt). Immediately after the function signature docstring, add:

```python
    if conversation_context:
        ctx_lines = "\n".join(
            f"{'User' if e['role'] == 'user' else 'Bot'}: {e['content']}"
            for e in conversation_context
        )
        question = f"הקשר שיחה:\n{ctx_lines}\n\nשאלה נוכחית: {question}"
```

Place this block before any `question` variable is used for LLM calls inside the function.

- [ ] **Step 9: Wire context in `telegram_polling.py`**

In `handle_message`, add these imports at the top of the method (after the `telegram_id = ...` line):
```python
        from app.services.telegram_state import get_context, append_context, clear_context as _clear_ctx
        conv_ctx = get_context(telegram_id)
```

Then append the user's message after it's stored (after `await service._store_message(...)`):
```python
        append_context(telegram_id, "user", text)
```

Then pass `conv_ctx` to `_ai_route_message`:
```python
        routing = await _ai_route_message(text, conversation_context=conv_ctx)
```

And to `_ask_route`:
```python
                    result = await _ask_route(text, session, user.id, log_to_db=True,
                                              conversation_context=conv_ctx)
```

And to `decision_svc.analyze_only` (in the DECISION branch, pass context into the service):

In `DecisionService.analyze_only`, pass `conversation_context` to `self.claude.analyze`:
```python
        return await self.claude.analyze(text, role_str, combined_context,
                                         conversation_context=conversation_context)
```

To do this, also add the param to `analyze_only`:
```python
    async def analyze_only(self, user: User, text: str,
                           conversation_context: list[dict] | None = None) -> dict:
```

And call it from telegram_polling with:
```python
            pre_result = await decision_svc.analyze_only(user, text,
                                                          conversation_context=conv_ctx)
```

After any reply is sent back to the user (any `await update.message.reply_text(...)` that is the final reply), append the bot's response to context. Since replies happen at many exit points, wrap the main reply branches:

Find the final `await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)` for the project/knowledge/null route path and add after it:
```python
                append_context(telegram_id, "assistant", reply[:300])
```

Add the same pattern after the decision preview reply and after NORMAL/INFO replies.

Also clear context in `/start` and `/menu` handlers by calling `clear_context(telegram_id)` at the top of each.

- [ ] **Step 10: Run all context tests**

```bash
docker-compose exec fastapi python -m pytest tests/test_conversation_context.py tests/test_ask_router.py -v
```
Expected: PASS

- [ ] **Step 11: Manual smoke test**

Send: `"מה פרויקט רעות?"`
Bot replies with project card.
Then send: `"מה הסיכונים שלו?"`  
Bot should answer about **רעות's risks** without needing to re-state the project name.

- [ ] **Step 12: Commit**

```bash
git add app/services/telegram_state.py app/services/telegram_polling.py \
        app/services/telegram_routing.py app/services/claude_service.py \
        app/services/knowledge_service.py app/services/ask_router.py \
        app/services/decision_service.py \
        tests/test_conversation_context.py
git commit -m "feat(ai): multi-turn conversation context — last 5 exchanges in memory (A2)"
```

---

## Task 4: C4 — Weekly Intelligence Report

Adds a role-scoped AI report sent every Thursday at 17:00 Israel time, plus manual triggers via `/report` command and web dashboard button.

**Files:**
- Create: `app/services/weekly_report_service.py`
- Create: `tests/test_weekly_report.py`
- Modify: `app/services/eval_cron.py`
- Modify: `app/services/telegram_polling.py`
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/dashboard.html`

- [ ] **Step 1: Write the failing test**

Create `tests/test_weekly_report.py`:
```python
"""Tests for weekly intelligence report service (C4)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_generate_report_no_data_returns_fallback(db_session):
    """When no decisions or projects exist for this user, return fallback message."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99999
    user.username = "test_user_no_data"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    with patch("app.services.weekly_report_service.llm_chat", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "אין נתונים לסיכום."
        report = await generate_report_for_user(user, db_session)

    assert "‏" in report   # RTL mark present
    assert isinstance(report, str)


@pytest.mark.asyncio
async def test_weekly_report_skips_viewer(db_session):
    """send_weekly_reports skips users with VIEWER role."""
    from app.services.weekly_report_service import send_weekly_reports
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    # This just verifies send_weekly_reports runs without crashing when DB is empty.
    await send_weekly_reports(mock_bot)
    # No assertion on send_message count — depends on DB state.
    # This is a smoke test.
```

- [ ] **Step 2: Run — verify FAIL (ImportError)**

```bash
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py -v
```
Expected: `ImportError: No module named 'app.services.weekly_report_service'`

- [ ] **Step 3: Create `app/services/weekly_report_service.py`**

```python
"""Weekly intelligence report — role-scoped AI digest sent every Thursday at 17:00 Israel time.

send_weekly_reports(bot): iterate all active non-VIEWER users and send.
generate_report_for_user(user, session): role-scoped DB queries → Groq → Hebrew text.
Manual triggers: /report command (Telegram) and POST /dashboard/report/trigger (web).
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Decision, Project, RoleEnum, DecisionStatusEnum
from app.database import async_session_maker

logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    RoleEnum.PROJECT_MANAGER:          "מנהל פרויקט",
    RoleEnum.DEPARTMENT_MANAGER:       "מנהל מחלקה",
    RoleEnum.DEPUTY_DIVISION_MANAGER:  "סגן מנהל אגף",
    RoleEnum.DIVISION_MANAGER:         "מנהל אגף",
}

_REPORT_PROMPT = """\
אתה עוזר BI לצוות תשתיות חשמל. צור סיכום שבועי תמציתי בעברית (עד 300 מילה).
תפקיד המקבל: {role}

נתונים:
- החלטות השבוע: {decisions_json}
- פרויקטים עם סיכונים: {risks_json}
- פרויקטים לטיפול: {handle_json}

כלול: ספירה לפי סוג, אחוז אישורים, 2–3 ממצאים בולטים, אנומליות אם קיימות.
סיים עם משפט עידוד קצר.
טקסט עברית בלבד — ללא JSON, ללא markdown."""


async def generate_report_for_user(user: User, session: AsyncSession) -> str:
    """Build a role-scoped weekly report for one user. Returns Hebrew HTML-safe string."""
    from app.services.llm_router import llm_chat

    since = datetime.utcnow() - timedelta(days=7)
    role_label = _ROLE_LABELS.get(user.role, user.role.value if user.role else "משתמש")

    decisions_data = await _decisions_for_role(user, session, since)
    risks_data = await _risky_projects_for_role(user, session)
    handle_data = await _handle_projects_for_role(user, session)

    if not decisions_data and not risks_data and not handle_data:
        return f"‏📊 <b>דוח שבועי — {role_label}</b>\n\nלא נמצאו נתונים לסיכום השבוע."

    prompt = _REPORT_PROMPT.format(
        role=role_label,
        decisions_json=json.dumps(decisions_data, ensure_ascii=False),
        risks_json=json.dumps(risks_data[:5], ensure_ascii=False),
        handle_json=json.dumps(handle_data[:5], ensure_ascii=False),
    )
    try:
        body = await llm_chat(
            "weekly_report",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        return f"‏📊 <b>דוח שבועי — {role_label}</b>\n\n{body.strip()}"
    except Exception as exc:
        logger.error(f"Weekly report LLM call failed for user {user.id}: {exc}")
        return f"‏⚠️ לא הצלחתי לייצר דוח שבועי. נסה שוב מאוחר יותר."


async def send_weekly_reports(bot) -> None:
    """Send weekly report to every active non-VIEWER user that has a telegram_id."""
    async with async_session_maker() as session:
        stmt = select(User).where(
            User.telegram_id.isnot(None),
            User.role.isnot(None),
            User.role != RoleEnum.VIEWER,
        )
        users = (await session.execute(stmt)).scalars().all()

        for user in users:
            try:
                text = await generate_report_for_user(user, session)
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
                logger.info(f"Weekly report sent to user {user.id} ({user.username})")
            except Exception as exc:
                logger.error(f"Weekly report send failed for user {user.id}: {exc}")


# ── Role-scoped DB query helpers ──────────────────────────────────────────────

async def _decisions_for_role(user: User, session: AsyncSession, since: datetime) -> list[dict]:
    stmt = select(Decision).where(Decision.created_at >= since)

    if user.role == RoleEnum.PROJECT_MANAGER:
        stmt = stmt.where(Decision.submitter_id == user.id)
    elif user.role == RoleEnum.DEPARTMENT_MANAGER:
        sub_ids = await _subordinate_ids(user, session)
        stmt = stmt.where(or_(
            Decision.submitter_id == user.id,
            Decision.submitter_id.in_(sub_ids) if sub_ids else Decision.submitter_id == user.id,
        ))
    # DEPUTY / DIVISION_MANAGER: no filter — see all

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return []

    type_counts: dict[str, int] = {}
    approved = 0
    for d in rows:
        t = d.type.value if d.type else "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        if d.status == DecisionStatusEnum.APPROVED:
            approved += 1

    return [{
        "total": len(rows),
        "by_type": type_counts,
        "approval_rate_pct": round(approved / len(rows) * 100),
        "sample": [
            {"id": d.id, "type": d.type.value if d.type else "", "summary": (d.summary or "")[:80]}
            for d in rows[:8]
        ],
    }]


async def _risky_projects_for_role(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active,
        Project.risks.isnot(None),
        Project.risks != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    rows = (await session.execute(stmt)).scalars().all()
    return [{"identifier": p.project_identifier, "name": p.name or "", "risks": (p.risks or "")[:120]}
            for p in rows]


async def _handle_projects_for_role(user: User, session: AsyncSession) -> list[dict]:
    stmt = select(Project).where(
        Project.is_active,
        Project.to_handle.isnot(None),
        Project.to_handle != "",
    )
    if user.role == RoleEnum.PROJECT_MANAGER and user.username:
        stmt = stmt.where(Project.manager.ilike(f"%{user.username}%"))

    rows = (await session.execute(stmt)).scalars().all()
    return [{"identifier": p.project_identifier, "name": p.name or "", "to_handle": (p.to_handle or "")[:120]}
            for p in rows]


async def _subordinate_ids(user: User, session: AsyncSession) -> list[int]:
    stmt = select(User.id).where(User.manager_id == user.id)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
docker-compose exec fastapi python -m pytest tests/test_weekly_report.py -v
```
Expected: PASS

- [ ] **Step 5: Add `weekly_report` to `USAGE_LABELS` in `llm_router.py`**

In `app/services/llm_router.py`, add to the `USAGE_LABELS` dict:
```python
    "weekly_report":           "דוח שבועי",
```

- [ ] **Step 6: Add Thursday cron job to `eval_cron.py`**

In `app/services/eval_cron.py`, in `start_scheduler`, after the existing `sch.add_job(...)` call, add:
```python
    sch.add_job(
        _weekly_report_run,
        CronTrigger(day_of_week="thu", hour=17, minute=0, timezone="Asia/Jerusalem"),
        id="weekly_report",
        replace_existing=True,
    )
    logger.info("eval_cron: weekly_report job registered (Thu 17:00 Asia/Jerusalem)")
```

Then add the job function at the end of the file:
```python
async def _weekly_report_run() -> None:
    """Send weekly reports to all active users (Thursday 17:00 Israel time)."""
    from app.services.weekly_report_service import send_weekly_reports
    from app.services.telegram_polling import telegram_bot
    if telegram_bot.application and telegram_bot.application.bot:
        await send_weekly_reports(telegram_bot.application.bot)
    else:
        logger.warning("weekly_report_run: bot not available, skipping")
```

- [ ] **Step 7: Register `/report` command in `telegram_polling.py`**

In `TelegramPollingBot.initialize`, after the other `CommandHandler` registrations (around line 125):
```python
        self.application.add_handler(CommandHandler("report", self.handle_report))
```

Then add the handler method inside the class (after `handle_menu`):
```python
    async def handle_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/report — generate and send weekly intelligence report for the requesting user."""
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה.")
                return
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await update.message.reply_text("‏🔒 דוח שבועי אינו זמין לצופים.")
                return
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            from app.services.weekly_report_service import generate_report_for_user
            report = await generate_report_for_user(user, session)
        await update.message.reply_text(report, parse_mode="HTML")
```

- [ ] **Step 8: Add `/dashboard/report/trigger` endpoint to `dashboard.py`**

In `app/routers/dashboard.py`, add at the end of the file (before the last closing lines, or after a logical section):
```python
@router.post("/report/trigger", response_class=HTMLResponse)
async def trigger_weekly_report(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Admin-only: send weekly reports to all active users immediately."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    from app.services.weekly_report_service import send_weekly_reports
    from app.services.telegram_polling import telegram_bot

    if not telegram_bot.application or not telegram_bot.application.bot:
        return HTMLResponse(
            '<script>alert("הבוט אינו פעיל כרגע."); window.history.back();</script>'
        )

    import asyncio
    asyncio.create_task(send_weekly_reports(telegram_bot.application.bot))
    return HTMLResponse(
        '<script>alert("שליחת הדוחות החלה ברקע."); window.location.href="/dashboard";</script>'
    )
```

- [ ] **Step 9: Add trigger button to `dashboard.html`**

In `app/templates/dashboard.html`, find the admin section or the KPI card area. Add the following button after the existing admin action buttons (search for a section with forms or action buttons visible to admins):

```html
{% if current_user.is_admin %}
<form method="post" action="/dashboard/report/trigger"
      onsubmit="return confirm('שלוח דוח שבועי לכל המשתמשים עכשיו?');">
  <button type="submit" class="btn btn-outline-primary btn-sm ms-2">
    📊 שלח דוח שבועי עכשיו
  </button>
</form>
{% endif %}
```

Place this adjacent to other admin-only action buttons in the dashboard header or action bar.

- [ ] **Step 10: Restart and verify cron registration**

```bash
docker-compose restart fastapi
docker-compose logs fastapi | grep weekly_report
```
Expected output contains:
```
eval_cron: weekly_report job registered (Thu 17:00 Asia/Jerusalem)
```

- [ ] **Step 11: Test `/report` command**

In Telegram, send `/report`. Verify the bot responds with a weekly report card within ~10 seconds (Groq call).

- [ ] **Step 12: Test web dashboard button**

Open `/dashboard` in the browser while logged in as an admin. Find the "📊 שלח דוח שבועי עכשיו" button. Click it. Confirm the alert fires and you are redirected to `/dashboard`. Check logs for "Weekly report sent to user".

- [ ] **Step 13: Run all tests**

```bash
docker-compose exec fastapi python -m pytest tests/ -v --timeout=30
```
Expected: all existing tests PASS, new tests PASS.

- [ ] **Step 14: Commit**

```bash
git add app/services/weekly_report_service.py app/services/eval_cron.py \
        app/services/telegram_polling.py app/routers/dashboard.py \
        app/templates/dashboard.html app/services/llm_router.py \
        tests/test_weekly_report.py
git commit -m "feat(notifications): weekly intelligence report — cron + /report + dashboard trigger (C4)"
```

---

## Self-Review Notes

- B3: `send_chat_action` already existed for non-VIEWER — this task only moves it 6 lines up. Low risk.
- A4: Sentinel only fires for 2–4 matches. Existing 1-result and 5+ result paths are unchanged.
- A2: `conversation_context` is optional everywhere — all call sites work with `None`. No breaking change.
- C4: All DB queries have `is_active` and `role IS NOT NULL` guards. LLM failures are caught per-user without aborting the batch.
- `llm_chat("weekly_report")` will use Groq with fallback enabled (default config in `llm_router._get_config`).
