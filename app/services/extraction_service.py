"""Auto-extraction of durable facts from messages (second brain phase 3, Option B).

Nightly pipeline:
  1. High-water mark (`memory_extract_hwm` SystemFlag) — each run processes only
     messages newer than the last processed id; never re-sends history to Groq.
  2. One Groq call extracts candidate facts (with high/low confidence) from a
     capped batch of recent messages.
  3. Per candidate: cosine retrieval finds near memories (loose threshold), then
     an LLM adjudication call classifies SAME / UPDATE / CONTRADICTS / NEW —
     cosine alone cannot tell an update from a contradiction on short Hebrew
     facts (council finding).
  4. Anti-pileup review (single admin, no forcing function): high-confidence
     facts auto-activate; low-confidence land as `pending` (never injected);
     UPDATE/CONTRADICTS supersede the old note recency-wins (undo = forget);
     contradictions notify admins; pending notes expire after 30 days; a weekly
     digest lists the week's auto facts with approve/reject buttons.

Scope caveat (documented in the research doc): the bot sees only what users
deliberately send it in private chats — group chatter is not ingested.

Kill switch: SystemFlag `memory_extract_kill` = "1" pauses the pipeline.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryNote, Message, RoleEnum, SystemFlag, User

logger = logging.getLogger(__name__)

EXTRACT_KILL_FLAG = "memory_extract_kill"
HWM_FLAG = "memory_extract_hwm"

MAX_MESSAGES_PER_RUN = 40
MIN_MESSAGE_CHARS = 15
PENDING_EXPIRE_DAYS = 30
CANDIDATE_DISTANCE = 0.45   # loose — candidates only; the adjudicator decides

SOURCE_AUTO = "auto_extracted"

_EXTRACT_PROMPT = """אתה מחלץ עובדות ארגוניות ממערכת ניהול פרויקטים של תשתיות חשמל.
מהודעות המשתמשים למטה, חלץ רק עובדות עמידות ששוות לזכור לטווח ארוך: אחריות של אנשים, שינויי ציוד, קבלנים, אילוצים קבועים, תאריכים חשובים.

אל תחלץ: שאלות, בקשות, ברכות, דעות, עובדות זמניות חסרות ערך, או ניסוחים מעורפלים.
אם אין עובדות ראויות — החזר רשימה ריקה.

החזר JSON בלבד:
{"facts": [{"fact": "העובדה בעברית, משפט אחד שלם וברור", "confidence": "high|low"}]}"""

_ADJUDICATE_PROMPT = """עובדה חדשה: {fact}

עובדות קיימות בזיכרון:
{candidates}

סווג את היחס של העובדה החדשה לקיימות. החזר JSON בלבד:
{{"verdict": "SAME|UPDATE|CONTRADICTS|NEW", "target": <מספר העובדה הקיימת הרלוונטית או null>}}

- SAME: אותה עובדה בדיוק (גם אם מנוסחת אחרת)
- UPDATE: גרסה עדכנית של עובדה קיימת (אותו נושא, מידע חדש)
- CONTRADICTS: סותרת עובדה קיימת
- NEW: נושא שלא קיים בזיכרון"""


async def _flag_value(session: AsyncSession, key: str) -> Optional[str]:
    flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    return flag.value if flag else None


async def _set_flag(session: AsyncSession, key: str, value: str) -> None:
    flag = await session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    if flag:
        flag.value = value
    else:
        session.add(SystemFlag(key=key, value=value))
    await session.commit()


def _worth_extracting(text: str) -> bool:
    """Cheap pre-filter — don't send obvious non-facts to Groq."""
    from app.services.memory_service import extract_remember_content, is_recall_query
    from app.services.dossier_service import extract_dossier_request

    t = (text or "").strip()
    if len(t) < MIN_MESSAGE_CHARS or t.endswith("?"):
        return False
    if extract_remember_content(t) is not None:   # already saved explicitly
        return False
    if is_recall_query(t) or extract_dossier_request(t) is not None:
        return False
    _QUESTION_STARTS = ("מה ", "מי ", "כמה", "מתי", "איך", "האם", "אילו", "איזה", "תן ", "הצג")
    return not any(t.startswith(p) for p in _QUESTION_STARTS)


async def _extract_facts(messages: list[str]) -> list[dict]:
    """One Groq call → [{"fact": str, "confidence": "high"|"low"}]."""
    from app.services.claude_service import _extract_json
    from app.services.llm_router import llm_chat

    numbered = "\n".join(f"{i}. {m}" for i, m in enumerate(messages, 1))
    numbered = numbered.replace('"', "״")
    raw = await llm_chat(
        "memory_extraction",
        messages=[
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": numbered},
        ],
        max_tokens=800,
        temperature=0.1,
        json_mode=True,
    )
    try:
        parsed = _extract_json(raw)
        facts = parsed.get("facts", [])
        return [f for f in facts
                if isinstance(f, dict) and (f.get("fact") or "").strip()]
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"extraction: unparseable facts reply: {raw[:200]!r}")
        return []


async def _adjudicate(fact: str, candidates: list[MemoryNote]) -> dict:
    """LLM verdict on how a new fact relates to near memories. Defaults NEW."""
    from app.services.claude_service import _extract_json
    from app.services.llm_router import llm_chat

    if not candidates:
        return {"verdict": "NEW", "target": None}

    listing = "\n".join(f"{i}. {n.content}" for i, n in enumerate(candidates, 1))
    prompt = _ADJUDICATE_PROMPT.format(fact=fact.replace('"', "״"),
                                       candidates=listing.replace('"', "״"))
    try:
        raw = await llm_chat(
            "memory_adjudication",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.0,
            json_mode=True,
        )
        parsed = _extract_json(raw)
        verdict = parsed.get("verdict")
        if verdict not in ("SAME", "UPDATE", "CONTRADICTS", "NEW"):
            return {"verdict": "NEW", "target": None}
        target = parsed.get("target")
        idx = int(target) - 1 if isinstance(target, (int, str)) and str(target).isdigit() else None
        if verdict in ("UPDATE", "CONTRADICTS") and (idx is None or not (0 <= idx < len(candidates))):
            idx = 0   # closest candidate by distance
        parsed["target_idx"] = idx
        parsed["verdict"] = verdict
        return parsed
    except Exception as e:
        logger.warning(f"extraction: adjudication failed ({e}) — treating as NEW")
        return {"verdict": "NEW", "target": None}


async def run_extraction() -> dict:
    """Nightly job. Opens its own session. Returns run stats."""
    from app.database import async_session_maker
    from app.services import job_guard, memory_service

    stats = {"scanned": 0, "extracted": 0, "saved": 0, "superseded": 0,
             "contradictions": 0, "expired": 0}
    try:
        async with async_session_maker() as session:
            if await _flag_value(session, EXTRACT_KILL_FLAG) == "1":
                return stats
            if not await job_guard.claim(session, "memory_extraction"):
                return stats

            stats["expired"] = await expire_stale_pending(session)

            hwm = int(await _flag_value(session, HWM_FLAG) or 0)
            rows = (await session.execute(
                select(Message, User)
                .join(User, Message.user_id == User.id)
                .where(Message.id > hwm)
                .where(User.role.isnot(None))
                .where(User.role != RoleEnum.VIEWER)
                .order_by(Message.id.asc())
                .limit(MAX_MESSAGES_PER_RUN)
            )).all()
            if not rows:
                return stats
            stats["scanned"] = len(rows)
            last_id = rows[-1][0].id

            texts = [m.content for m, _u in rows if _worth_extracting(m.content or "")]
            facts = await _extract_facts(texts) if texts else []
            stats["extracted"] = len(facts)

            for item in facts:
                fact = item["fact"].strip().replace('"', "״")
                confidence = item.get("confidence", "low")
                try:
                    candidates = await memory_service.get_relevant_memories(
                        fact, session, limit=3, max_distance=CANDIDATE_DISTANCE)
                    ruling = await _adjudicate(fact, candidates)
                    verdict = ruling["verdict"]
                    if verdict == "SAME":
                        continue

                    project_id, _ = await memory_service.link_project(fact, session)
                    note = await memory_service.save_memory(
                        session, content=fact, user_id=None, project_id=project_id,
                        source=SOURCE_AUTO,
                        tags={"confidence": confidence},
                    )
                    if confidence != "high":
                        note.status = "pending"   # never injected until approved
                        await session.commit()
                    stats["saved"] += 1

                    # Supersede only when the new note is live — a pending note
                    # must not knock an active fact out of retrieval.
                    if confidence == "high" and verdict in ("UPDATE", "CONTRADICTS"):
                        idx = ruling.get("target_idx")
                        if idx is not None and idx < len(candidates):
                            old = candidates[idx]
                            old.superseded_by_id = note.id   # recency wins; undo = forget new
                            old.updated_at = datetime.utcnow()
                            await session.commit()
                            stats["superseded"] += 1
                        if verdict == "CONTRADICTS":
                            stats["contradictions"] += 1
                            await _notify_admins_contradiction(session, note, candidates, ruling)
                except Exception as e:
                    logger.warning(f"extraction: fact failed ({fact[:60]}): {e}")

            # Advance HWM even when some facts failed — never re-send history.
            await _set_flag(session, HWM_FLAG, str(last_id))
            logger.info(f"extraction run: {stats}")
    except Exception as e:
        logger.error(f"run_extraction failed: {e}", exc_info=True)
    return stats


async def expire_stale_pending(session: AsyncSession) -> int:
    """Pending auto-facts older than 30 days quietly expire (anti-pileup)."""
    cutoff = datetime.utcnow() - timedelta(days=PENDING_EXPIRE_DAYS)
    stale = (await session.execute(
        select(MemoryNote).where(
            MemoryNote.status == "pending",
            MemoryNote.created_at < cutoff,
        )
    )).scalars().all()
    for note in stale:
        note.status = "rejected"
        note.updated_at = datetime.utcnow()
    if stale:
        await session.commit()
    return len(stale)


async def _notify_admins_contradiction(session, note, candidates, ruling) -> None:
    """Contradiction resolved recency-wins — tell admins so they can undo."""
    try:
        from app.services.telegram_polling import telegram_bot
        bot = (telegram_bot.application.bot
               if telegram_bot and telegram_bot.application else None)
        if not bot:
            return
        idx = ruling.get("target_idx") or 0
        old_text = candidates[idx].content[:200] if idx < len(candidates) else "?"
        admins = (await session.execute(
            select(User).where(User.is_admin.is_(True), User.telegram_id.isnot(None))
        )).scalars().all()
        for admin in admins:
            try:
                await bot.send_message(
                    chat_id=admin.telegram_id,
                    text=("‏⚠️🧠 סתירה בזיכרון הארגוני — העובדה החדשה גברה:\n"
                          f"חדשה: {note.content[:200]}\n"
                          f"ישנה (הוחלפה): {old_text}\n"
                          "לביטול: מה אתה זוכר → 🗑 שכח"),
                )
            except Exception:
                pass
    except Exception:
        logger.warning("contradiction notify failed", exc_info=True)


# ---------------------------------------------------------------------------
# Weekly digest — batched review instead of per-fact pings
# ---------------------------------------------------------------------------

async def build_weekly_digest(session: AsyncSession) -> tuple[str, list[MemoryNote]]:
    """Digest text + the pending notes that need buttons. Empty text = nothing new."""
    week_ago = datetime.utcnow() - timedelta(days=7)
    notes = (await session.execute(
        select(MemoryNote)
        .where(
            MemoryNote.source == SOURCE_AUTO,
            MemoryNote.created_at >= week_ago,
            MemoryNote.status.in_(["active", "pending"]),
        )
        .order_by(MemoryNote.created_at.asc())
    )).scalars().all()
    if not notes:
        return "", []

    active = [n for n in notes if n.status == "active"]
    pending = [n for n in notes if n.status == "pending"][:8]

    lines = ["‏🧠 <b>סיכום שבועי — עובדות שנלמדו אוטומטית:</b>"]
    if active:
        lines.append(f"\n✅ הופעלו ({len(active)}):")
        lines += [f"• {n.content[:150]}" for n in active[:10]]
    if pending:
        lines.append(f"\n⏳ ממתינות לאישורך ({len(pending)}):")
        lines += [f"{i}. {n.content[:150]}" for i, n in enumerate(pending, 1)]
    lines.append("\nביטול עובדה: מה אתה זוכר → 🗑 שכח")
    return "\n".join(lines)[:3900], pending


async def send_weekly_digest() -> None:
    """Weekly admin digest with approve buttons for pending facts. Own session."""
    from app.database import async_session_maker
    from app.services import job_guard

    try:
        async with async_session_maker() as session:
            if not await job_guard.claim(session, "memory_weekly_digest"):
                return
            text, pending = await build_weekly_digest(session)
            if not text:
                return

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            from app.services.telegram_polling import telegram_bot
            bot = (telegram_bot.application.bot
                   if telegram_bot and telegram_bot.application else None)
            if not bot:
                return

            buttons = [
                [
                    InlineKeyboardButton(f"✅ אשר {i}", callback_data=f"mem_appr:{n.id}"),
                    InlineKeyboardButton(f"🗑 דחה {i}", callback_data=f"mem_forget:{n.id}"),
                ]
                for i, n in enumerate(pending, 1)
            ]
            kb = InlineKeyboardMarkup(buttons) if buttons else None

            admins = (await session.execute(
                select(User).where(User.is_admin.is_(True), User.telegram_id.isnot(None))
            )).scalars().all()
            for admin in admins:
                try:
                    await bot.send_message(chat_id=admin.telegram_id, text=text,
                                           parse_mode="HTML", reply_markup=kb)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"send_weekly_digest failed: {e}", exc_info=True)


async def approve_pending(session: AsyncSession, note_id: int) -> bool:
    """Admin approved a pending auto-fact — activate it."""
    note = await session.get(MemoryNote, note_id)
    if not note or note.status != "pending":
        return False
    note.status = "active"
    note.updated_at = datetime.utcnow()
    await session.commit()
    return True
