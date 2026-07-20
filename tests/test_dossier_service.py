"""Project dossiers (second brain phase 2) — drip generation, hash gating, hooks."""
from unittest.mock import patch

from sqlalchemy import select, text

from app.models import Project, ProjectDossier, SystemFlag, User
from app.services import dossier_service, memory_service


async def _make_project(db_session, ident="P-DOS-1", name="תחנת חדרה") -> Project:
    p = Project(project_identifier=ident, name=name, manager="רות כהן",
                stage="ביצוע", is_active=True)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


def test_extract_dossier_request():
    assert dossier_service.extract_dossier_request("תיק פרויקט חדרה") == "חדרה"
    assert dossier_service.extract_dossier_request("תיק פרוייקט רעות?") == "רעות"
    assert dossier_service.extract_dossier_request("תיק פרויקט") == ""
    assert dossier_service.extract_dossier_request("מה התיק של חדרה") is None
    assert dossier_service.extract_dossier_request("תיק רפואי") is None


async def test_project_dossiers_table_exists(db_session):
    res = await db_session.execute(text("SELECT to_regclass('public.project_dossiers')"))
    assert res.scalar() is not None, "project_dossiers table missing"


async def test_mark_dirty_upserts(db_session):
    p = await _make_project(db_session)
    await dossier_service.mark_dirty([p.id])
    dossier = await db_session.scalar(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))
    assert dossier is not None and dossier.is_dirty is True
    # Second call is a no-op upsert, not a duplicate row
    await dossier_service.mark_dirty([p.id])
    rows = (await db_session.execute(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))).scalars().all()
    assert len(rows) == 1


async def test_drip_generates_then_hash_skips(db_session, mock_llm_chat):
    p = await _make_project(db_session)
    await dossier_service.mark_dirty([p.id])

    mock_llm_chat.side_effect = None
    mock_llm_chat.return_value = "📌 מצב נוכחי: בביצוע, מנהלת רות כהן."

    made = await dossier_service.process_dirty_dossiers()
    assert made == 1
    dossier = await db_session.scalar(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))
    assert dossier.is_dirty is False
    assert "רות כהן" in dossier.content
    assert dossier.source_hash

    # Dirty again with UNCHANGED inputs → dirty clears without an LLM call
    calls_before = mock_llm_chat.call_count
    await dossier_service.mark_dirty([p.id])
    made = await dossier_service.process_dirty_dossiers()
    assert made == 0
    assert mock_llm_chat.call_count == calls_before
    dossier = await db_session.scalar(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))
    assert dossier.is_dirty is False


async def test_kill_switch_pauses_generation(db_session, mock_llm_chat):
    p = await _make_project(db_session, ident="P-DOS-2", name="רעות")
    await dossier_service.mark_dirty([p.id])
    db_session.add(SystemFlag(key=dossier_service.DOSSIER_KILL_FLAG, value="1"))
    await db_session.commit()

    made = await dossier_service.process_dirty_dossiers()
    assert made == 0
    dossier = await db_session.scalar(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))
    assert dossier.is_dirty is True, "kill switch must leave dossiers dirty, not consume them"


async def test_saving_project_memory_marks_dossier_dirty(db_session):
    p = await _make_project(db_session, ident="P-DOS-3", name="גליל עליון")
    user = User(username="dossier_tester", telegram_id=999_000_222)
    db_session.add(user)
    await db_session.commit()

    async def _fake_embed(_):
        return [0.0] * 384

    with patch("app.services.embedding_service.embed", side_effect=_fake_embed):
        await memory_service.save_memory(
            db_session, content="הקבלן בגליל עליון הוחלף", user_id=user.id, project_id=p.id)

    dossier = await db_session.scalar(
        select(ProjectDossier).where(ProjectDossier.project_id == p.id))
    assert dossier is not None and dossier.is_dirty is True


async def test_get_dossier_text_by_identifier(db_session):
    p = await _make_project(db_session, ident="P-DOS-4", name="נגב")
    db_session.add(ProjectDossier(project_id=p.id, content="תוכן תיק", is_dirty=False))
    await db_session.commit()
    assert await dossier_service.get_dossier_text_by_identifier("P-DOS-4", db_session) == "תוכן תיק"
    assert await dossier_service.get_dossier_text_by_identifier("לא-קיים", db_session) is None
