"""Second brain phase 3 (auto-extraction) + Option D (session memory) + phase 0 guard."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select, text

from app.models import (
    ConversationSummary, MemoryNote, Message, QueryLog, RoleEnum, SystemFlag, User,
)
from app.services import extraction_service, job_guard, memory_service, session_summary_service


async def _fake_embed(_txt: str):
    return [1.0] + [0.0] * 383


async def _make_user(db_session, name="extract_tester") -> User:
    user = User(username=name, telegram_id=999_000_333, role=RoleEnum.PROJECT_MANAGER)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Phase 0 — job guard
# ---------------------------------------------------------------------------

async def test_job_guard_tables_exist(db_session):
    for t in ("job_runs", "conversation_summaries"):
        res = await db_session.execute(text(f"SELECT to_regclass('public.{t}')"))
        assert res.scalar() is not None, f"{t} table missing"


async def test_job_guard_single_claim(db_session):
    assert await job_guard.claim(db_session, "test_job", "2026-07-20") is True
    assert await job_guard.claim(db_session, "test_job", "2026-07-20") is False
    # Different key → new claim
    assert await job_guard.claim(db_session, "test_job", "2026-07-21") is True


# ---------------------------------------------------------------------------
# Phase 3 — extraction pre-filter and pipeline
# ---------------------------------------------------------------------------

def test_worth_extracting_filters_noise():
    ok = extraction_service._worth_extracting
    assert ok("הקבלן אלקטרה התחיל לעבוד בתחנת חדרה השבוע")
    assert not ok("מה הסטטוס של חדרה?")          # question
    assert not ok("כמה פרויקטים יש?")             # question prefix
    assert not ok("תודה")                          # too short
    assert not ok("זכור שדני אחראי על חדרה")      # explicit — already saved
    assert not ok("מה אתה זוכר על חדרה")          # recall command
    assert not ok("תיק פרויקט חדרה")              # dossier command


async def test_run_extraction_end_to_end(db_session, mock_llm_chat):
    user = await _make_user(db_session)
    m1 = Message(user_id=user.id, content="הקבלן אלקטרה התחיל לעבוד בתחנת חדרה")
    m2 = Message(user_id=user.id, content="נראה לי שאולי כדאי לבדוק משהו")
    db_session.add_all([m1, m2])
    await db_session.commit()

    async def _llm(usage, messages=None, **kw):
        if usage == "memory_extraction":
            return json.dumps({"facts": [
                {"fact": "הקבלן אלקטרה עובד בתחנת חדרה", "confidence": "high"},
                {"fact": "אולי צריך לבדוק משהו", "confidence": "low"},
            ]}, ensure_ascii=False)
        if usage == "memory_adjudication":
            return '{"verdict": "NEW", "target": null}'
        return ""

    mock_llm_chat.side_effect = _llm
    with patch("app.services.embedding_service.embed", side_effect=_fake_embed):
        stats = await extraction_service.run_extraction()

    assert stats["scanned"] == 2
    assert stats["saved"] == 2
    notes = (await db_session.execute(
        select(MemoryNote).where(MemoryNote.source == extraction_service.SOURCE_AUTO)
    )).scalars().all()
    by_status = {n.status for n in notes}
    assert by_status == {"active", "pending"}, "high→active, low→pending"

    # High-water mark advanced — a second run scans nothing new (and the daily
    # job-guard claim also blocks it)
    flag = await db_session.scalar(
        select(SystemFlag).where(SystemFlag.key == extraction_service.HWM_FLAG))
    assert int(flag.value) == m2.id
    stats2 = await extraction_service.run_extraction()
    assert stats2["scanned"] == 0 and stats2["saved"] == 0


async def test_update_verdict_supersedes_old_note(db_session, mock_llm_chat):
    user = await _make_user(db_session, name="extract_tester2")
    with patch("app.services.embedding_service.embed", side_effect=_fake_embed):
        old = await memory_service.save_memory(
            db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)
        db_session.add(Message(user_id=user.id, content="רות מחליפה את דני באחריות על חדרה"))
        await db_session.commit()

        async def _llm(usage, messages=None, **kw):
            if usage == "memory_extraction":
                return json.dumps({"facts": [
                    {"fact": "רות אחראית על תחנת חדרה", "confidence": "high"}]},
                    ensure_ascii=False)
            if usage == "memory_adjudication":
                return '{"verdict": "UPDATE", "target": 1}'
            return ""

        mock_llm_chat.side_effect = _llm
        stats = await extraction_service.run_extraction()

    assert stats["superseded"] == 1
    await db_session.refresh(old)
    assert old.superseded_by_id is not None, "old fact must be superseded (recency wins)"
    with patch("app.services.embedding_service.embed", side_effect=_fake_embed):
        current = await memory_service.get_relevant_memories("מי אחראי על חדרה", db_session)
    contents = " ".join(n.content for n in current)
    assert "רות" in contents and "דני אחראי" not in contents


async def test_expire_stale_pending(db_session):
    note = MemoryNote(content="עובדה ישנה", status="pending",
                      source=extraction_service.SOURCE_AUTO,
                      created_at=datetime.utcnow() - timedelta(days=31))
    db_session.add(note)
    await db_session.commit()
    expired = await extraction_service.expire_stale_pending(db_session)
    assert expired == 1
    await db_session.refresh(note)
    assert note.status == "rejected"


async def test_weekly_digest_and_approval(db_session):
    active = MemoryNote(content="עובדה פעילה", status="active",
                        source=extraction_service.SOURCE_AUTO)
    pending = MemoryNote(content="עובדה ממתינה", status="pending",
                         source=extraction_service.SOURCE_AUTO)
    db_session.add_all([active, pending])
    await db_session.commit()

    digest, pending_list = await extraction_service.build_weekly_digest(db_session)
    assert "עובדה פעילה" in digest and "עובדה ממתינה" in digest
    assert [n.id for n in pending_list] == [pending.id]

    assert await extraction_service.approve_pending(db_session, pending.id) is True
    await db_session.refresh(pending)
    assert pending.status == "active"
    # Second approval is a no-op
    assert await extraction_service.approve_pending(db_session, pending.id) is False


# ---------------------------------------------------------------------------
# Option D — session memory
# ---------------------------------------------------------------------------

async def test_exchange_log_merges_both_sides(db_session):
    user = await _make_user(db_session, name="summary_tester")
    db_session.add(Message(user_id=user.id, content="מה קורה עם חדרה?"))
    db_session.add(QueryLog(user_id=user.id, question="מה קורה עם חדרה?",
                            ai_response="הפרויקט בשלב ביצוע"))
    await db_session.commit()

    log = await session_summary_service.build_exchange_log(
        user.id, db_session, since=datetime.utcnow() - timedelta(hours=1))
    assert "משתמש: מה קורה עם חדרה?" in log
    assert "בוט: הפרויקט בשלב ביצוע" in log


async def test_summarize_user_upserts_rolling_summary(db_session, mock_llm_chat):
    user = await _make_user(db_session, name="summary_tester2")
    db_session.add(Message(user_id=user.id, content="דיברנו על העברת השנאי בחדרה"))
    await db_session.commit()

    mock_llm_chat.side_effect = None
    mock_llm_chat.return_value = "המשתמש עוסק בהעברת השנאי בחדרה."

    summary = await session_summary_service.summarize_user(user.id, db_session)
    assert "השנאי" in summary
    assert await session_summary_service.get_summary(user.id, db_session) == summary

    # Second pass folds the previous summary in (upsert, single row)
    mock_llm_chat.return_value = "עודכן: השנאי הועבר."
    await session_summary_service.summarize_user(user.id, db_session)
    rows = (await db_session.execute(
        select(ConversationSummary).where(ConversationSummary.user_id == user.id)
    )).scalars().all()
    assert len(rows) == 1 and rows[0].summary == "עודכן: השנאי הועבר."


async def test_summary_kill_switch(db_session):
    db_session.add(SystemFlag(key=session_summary_service.SUMMARY_KILL_FLAG, value="1"))
    await db_session.commit()
    assert await session_summary_service.run_daily_summaries() == 0
