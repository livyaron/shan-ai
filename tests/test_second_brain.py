"""Second brain (memory_notes) — capture parsing, retrieval, hygiene, snapshot diffs.

Embedding is mocked with orthogonal deterministic vectors so vector-path tests
run without downloading the FastEmbed model: texts mentioning חדרה share one
vector, everything else another (cosine distance 0 within a bucket, 1 across).
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.models import MemoryNote, Project, SystemFlag, User
from app.services import memory_service


_VEC_HADERA = [1.0] + [0.0] * 383
_VEC_OTHER = [0.0, 1.0] + [0.0] * 382


async def _fake_embed(txt: str):
    return _VEC_HADERA if "חדרה" in txt else _VEC_OTHER


@pytest.fixture
def mock_embed():
    with patch("app.services.embedding_service.embed", side_effect=_fake_embed):
        yield


async def _make_user(db_session, name="mem_tester") -> User:
    user = User(username=name, telegram_id=999_000_111)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Pure parsing — no DB
# ---------------------------------------------------------------------------

def test_extract_remember_content_variants():
    assert memory_service.extract_remember_content(
        "זכור שדני אחראי על תחנת חדרה") == "דני אחראי על תחנת חדרה"
    assert memory_service.extract_remember_content(
        "תזכור: השנאי בחדרה הוחלף") == "השנאי בחדרה הוחלף"
    assert memory_service.extract_remember_content(
        "זכור כי הקבלן החדש התחיל") == "הקבלן החדש התחיל"
    assert memory_service.extract_remember_content(
        "‏זכור ש דני אחראי") == "דני אחראי"


def test_extract_remember_content_rejects_non_commands():
    assert memory_service.extract_remember_content("מה זכור לך על חדרה?") is None
    assert memory_service.extract_remember_content("זכורים לי ימים יפים") is None
    assert memory_service.extract_remember_content("מי אחראי על חדרה?") is None
    # Too short to be a useful fact
    assert memory_service.extract_remember_content("זכור ש") is None


def test_extract_remember_content_sanitizes_quotes():
    out = memory_service.extract_remember_content('זכור שהקבלן "אלקטרה" זכה במכרז')
    assert '"' not in out
    assert "״" in out


def test_recall_query_detection_and_topic():
    assert memory_service.is_recall_query("מה אתה זוכר על חדרה?")
    assert memory_service.is_recall_query("מה אתה זוכר")
    assert not memory_service.is_recall_query("מה הסטטוס של חדרה?")
    assert memory_service.extract_recall_topic("מה אתה זוכר על חדרה?") == "חדרה"
    assert memory_service.extract_recall_topic("מה אתה זוכר") == ""


def test_build_change_fact():
    fact = memory_service.build_change_fact(
        {"field": "manager", "old": "דוד לוי", "new": "רות כהן", "name": "תחנת חדרה"},
        on_date=date(2026, 7, 19),
    )
    assert "מנהל הפרויקט" in fact
    assert "דוד לוי" in fact and "רות כהן" in fact
    assert "תחנת חדרה" in fact and "19.07.2026" in fact

    d_fact = memory_service.build_change_fact(
        {"field": "estimated_finish_date", "old": date(2026, 1, 1),
         "new": date(2026, 6, 30), "name": "רעות"},
    )
    assert "01.01.2026" in d_fact and "30.06.2026" in d_fact

    # First fill (old None) and untracked fields produce no fact
    assert memory_service.build_change_fact(
        {"field": "manager", "old": None, "new": "רות", "name": "X"}) is None
    assert memory_service.build_change_fact(
        {"field": "weekly_report", "old": "a", "new": "b", "name": "X"}) is None


def test_format_memory_context():
    note = MemoryNote(content="דני אחראי על חדרה", created_at=datetime(2026, 7, 19))
    ctx = memory_service.format_memory_context([note])
    assert ctx.startswith("🧠")
    assert "1. [19.07.2026] דני אחראי על חדרה" in ctx
    assert memory_service.format_memory_context([]) == ""


# ---------------------------------------------------------------------------
# DB — schema, save/retrieve, hygiene
# ---------------------------------------------------------------------------

async def test_memory_notes_table_exists(db_session):
    res = await db_session.execute(text("SELECT to_regclass('public.memory_notes')"))
    assert res.scalar() is not None, "memory_notes table missing"


async def test_save_and_retrieve_by_vector_and_keyword(db_session, mock_embed):
    user = await _make_user(db_session)
    hit = await memory_service.save_memory(
        db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)
    miss = await memory_service.save_memory(
        db_session, content="ההספק בתחנה הדרומית תקין", user_id=user.id)

    notes = await memory_service.get_relevant_memories("מי אחראי בחדרה?", db_session)
    ids = [n.id for n in notes]
    assert hit.id in ids, "relevant memory not retrieved"
    assert miss.id not in ids, "distance cutoff failed — unrelated memory leaked in"


async def test_retrieval_keyword_path_handles_hebrew_prefix(db_session, mock_embed):
    user = await _make_user(db_session)
    note = await memory_service.save_memory(
        db_session, content="הגנרטור בגליל שודרג", user_id=user.id)
    # Vector bucket for this question differs from the note's — only the ILIKE
    # path (with ב-prefix stripping on "בגליל") can find it.
    with patch("app.services.embedding_service.embed", side_effect=lambda t: _VEC_HADERA):
        notes = await memory_service.get_relevant_memories("מה קורה בגליל?", db_session)
    assert note.id in [n.id for n in notes]


async def test_kill_switch_disables_retrieval(db_session, mock_embed):
    user = await _make_user(db_session)
    await memory_service.save_memory(
        db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)
    db_session.add(SystemFlag(key=memory_service.MEMORY_KILL_FLAG, value="1"))
    await db_session.commit()
    assert await memory_service.get_relevant_memories("מי אחראי בחדרה?", db_session) == []


async def test_forget_excludes_from_retrieval(db_session, mock_embed):
    user = await _make_user(db_session)
    note = await memory_service.save_memory(
        db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)
    assert await memory_service.forget_memory(db_session, note.id, user.id) is True
    assert await memory_service.get_relevant_memories("מי אחראי בחדרה?", db_session) == []
    # Second forget is a no-op
    assert await memory_service.forget_memory(db_session, note.id, user.id) is False


async def test_expired_note_excluded(db_session, mock_embed):
    user = await _make_user(db_session)
    await memory_service.save_memory(
        db_session, content="עובדה זמנית על חדרה", user_id=user.id,
        valid_until=datetime.utcnow() - timedelta(days=1))
    assert await memory_service.get_relevant_memories("מה קורה בחדרה?", db_session) == []


async def test_link_project_high_confidence_only(db_session):
    p1 = Project(project_identifier="P-HAD-1", name="תחנת חדרה", is_active=True)
    p2 = Project(project_identifier="P-REU-1", name="רעות", is_active=True)
    db_session.add_all([p1, p2])
    await db_session.commit()

    pid, label = await memory_service.link_project("הקבלן של תחנת חדרה הוחלף", db_session)
    assert pid == p1.id and label == "תחנת חדרה"

    # No project mentioned → no link
    pid, _ = await memory_service.link_project("ישיבת הנהלה נדחתה", db_session)
    assert pid is None

    # Two projects mentioned → ambiguous → no link (wrong link is worse than null)
    pid, _ = await memory_service.link_project("תחנת חדרה וגם רעות מתעכבות", db_session)
    assert pid is None


async def test_record_project_changes_and_supersession(db_session, mock_embed):
    project = Project(project_identifier="P-HAD-9", name="תחנת חדרה", is_active=True)
    db_session.add(project)
    await db_session.commit()

    change = {"project_id": project.id, "identifier": "P-HAD-9", "name": "תחנת חדרה",
              "field": "manager", "old": "דוד", "new": "רות"}
    assert await memory_service.record_project_changes([change]) == 1

    notes = await memory_service.get_relevant_memories("מי מנהל את חדרה?", db_session)
    assert len(notes) == 1
    assert "רות" in notes[0].content
    assert notes[0].source == memory_service.SOURCE_SNAPSHOT

    # A newer change to the same (project, field) supersedes the previous note
    change2 = {**change, "old": "רות", "new": "יעל"}
    await memory_service.record_project_changes([change2])
    notes = await memory_service.get_relevant_memories("מי מנהל את חדרה?", db_session)
    assert len(notes) == 1, "superseded note leaked into retrieval"
    assert "יעל" in notes[0].content


async def test_route_passes_memory_to_project_path(db_session, mock_embed, mock_llm_chat):
    """The council's routing-gap fix: "מי אחראי על חדרה?" routes to project_tools
    (never reaching RAG) — the taught fact must be passed into that path."""
    user = await _make_user(db_session)
    await memory_service.save_memory(
        db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)

    spied_kwargs: dict = {}

    async def _spy(text, session, user_data, **kwargs):
        spied_kwargs.update(kwargs)
        return "כרטיס פרויקט", None

    with patch("app.services.project_tools.answer_project_query", side_effect=_spy):
        from app.services.ask_router import route
        result = await route("מי אחראי על חדרה?", db_session, user.id, log_to_db=False)

    assert result.path == "project_tools"
    assert "דני אחראי על תחנת חדרה" in spied_kwargs.get("memory_context", ""), (
        "memory context not injected into the project answer path")


async def test_rag_prepends_memory_context(db_session, mock_embed, mock_llm_chat):
    """The taught fact must appear in the RAG prompt (prepended, pre-truncation)."""
    user = await _make_user(db_session)
    await memory_service.save_memory(
        db_session, content="דני אחראי על תחנת חדרה", user_id=user.id)

    captured: list = []

    async def _capture(usage, messages=None, **kw):
        captured.append((usage, messages))
        return "תשובה"

    mock_llm_chat.side_effect = _capture
    with patch("app.services.knowledge_service.embed", side_effect=_fake_embed):
        from app.services.knowledge_service import answer_with_full_context
        result = await answer_with_full_context(
            "ספר לי על המצב בחדרה", db_session, user.id, log_to_db=False)

    assert "דני אחראי על תחנת חדרה" in str(captured), (
        "taught fact did not reach the RAG prompt")
    assert "🧠 זיכרון ארגוני" in result.get("sources_text", "")
