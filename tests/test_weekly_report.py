"""Tests for weekly report v2 — ReportHistory model, service API, cron skip."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_report_history_model_importable():
    from app.models import ReportHistory
    row = ReportHistory(user_id=1, sections={"prologue": "hi"}, sent_via="telegram")
    assert row.user_id == 1
    assert row.sections["prologue"] == "hi"
    assert row.raw_data is None  # default


# ── Task 2 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_report_returns_sections_dict(db_session):
    """generate_report_for_user returns a dict with 5 keys."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99991
    user.username = "test_gen"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"פתיח","decisions":"החלטות",'
        '"projects":"פרויקטים","summary":"סיכום","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert "prologue" in sections
    assert "decisions" in sections
    assert "projects" in sections
    assert "summary" in sections
    assert "delta" in sections


@pytest.mark.asyncio
async def test_generate_report_saves_history_row(db_session):
    """generate_report_for_user persists a ReportHistory row."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum, ReportHistory
    from sqlalchemy import select

    user = MagicMock(spec=User)
    user.id = 99992
    user.username = "test_save"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    fake_json = (
        '{"prologue":"p","decisions":"d","projects":"pr","summary":"s","delta":null}'
    )
    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, return_value=fake_json):
        await generate_report_for_user(user, db_session, triggered_by_id=1, sent_via="dashboard")

    row = await db_session.scalar(
        select(ReportHistory).where(ReportHistory.user_id == 99992)
    )
    assert row is not None
    assert row.sent_via == "dashboard"
    assert row.sections["prologue"] == "p"


@pytest.mark.asyncio
async def test_generate_report_fallback_on_llm_error(db_session):
    """When LLM raises, sections has a non-empty prologue and others are None."""
    from app.services.weekly_report_service import generate_report_for_user
    from app.models import User, RoleEnum

    user = MagicMock(spec=User)
    user.id = 99993
    user.username = "test_fallback"
    user.role = RoleEnum.PROJECT_MANAGER
    user.manager_id = None

    with patch("app.services.weekly_report_service.llm_chat",
               new_callable=AsyncMock, side_effect=Exception("timeout")):
        sections = await generate_report_for_user(user, db_session)

    assert isinstance(sections, dict)
    assert sections["prologue"]  # non-empty fallback message


@pytest.mark.asyncio
async def test_send_report_sends_non_empty_sections():
    """send_report_to_user calls bot.send_message once per non-null section."""
    from app.services.weekly_report_service import send_report_to_user

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    sections = {
        "prologue":  "פתיח",
        "decisions": "החלטות",
        "projects":  "פרויקטים",
        "summary":   "סיכום",
        "delta":     None,  # null → no message
    }
    await send_report_to_user(mock_bot, 12345, sections)
    assert mock_bot.send_message.call_count == 4  # delta skipped


@pytest.mark.asyncio
async def test_cron_skips_viewer(db_session):
    """send_weekly_reports_cron does not send to VIEWER users."""
    from app.services.weekly_report_service import send_weekly_reports_cron
    from app.models import User, RoleEnum

    viewer = MagicMock(spec=User)
    viewer.telegram_id = 7777777001
    viewer.role = RoleEnum.VIEWER
    viewer.id = 88801
    viewer.username = "viewer_skip"

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [viewer]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    with patch("app.database.async_session_maker", return_value=mock_session):
        await send_weekly_reports_cron(mock_bot)

    mock_bot.send_message.assert_not_called()


# ── Task 1 (new) ─────────────────────────────────────────────────────────────

def test_decisions_summary_splits_critical_from_sample():
    """CRITICAL and UNCERTAIN go to critical_urgent; INFO/NORMAL go to sample."""
    from app.services.weekly_report_service import _decisions_summary
    from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum
    from unittest.mock import MagicMock, AsyncMock
    from datetime import datetime
    import asyncio

    def _make_decision(id_, dtype):
        d = MagicMock(spec=Decision)
        d.id = id_
        d.type = dtype
        d.status = DecisionStatusEnum.PENDING
        d.summary = f"summary {id_}"
        d.recommended_action = f"action {id_}"
        d.created_at = datetime(2026, 5, 1)
        d.is_relevant = True
        return d

    decisions = [
        _make_decision(1, DecisionTypeEnum.CRITICAL),
        _make_decision(2, DecisionTypeEnum.INFO),
        _make_decision(3, DecisionTypeEnum.UNCERTAIN),
        _make_decision(4, DecisionTypeEnum.NORMAL),
        _make_decision(5, DecisionTypeEnum.CRITICAL),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = decisions
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    from app.models import RoleEnum
    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER

    result = asyncio.run(
        _decisions_summary(user, mock_session, datetime(2026, 4, 24))
    )

    cu_ids = {d["id"] for d in result["critical_urgent"]}
    sample_ids = {d["id"] for d in result["sample"]}

    assert cu_ids == {1, 3, 5}
    assert sample_ids == {2, 4}
    assert all("recommended_action" in d for d in result["critical_urgent"])


def test_decisions_summary_critical_urgent_capped_at_8():
    """critical_urgent never exceeds 8 entries."""
    from app.services.weekly_report_service import _decisions_summary
    from app.models import Decision, DecisionTypeEnum, DecisionStatusEnum, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    from datetime import datetime
    import asyncio

    decisions = []
    for i in range(12):
        d = MagicMock(spec=Decision)
        d.id = i
        d.type = DecisionTypeEnum.CRITICAL
        d.status = DecisionStatusEnum.PENDING
        d.summary = f"s{i}"
        d.recommended_action = f"a{i}"
        d.created_at = datetime(2026, 5, i % 28 + 1)
        d.is_relevant = True
        decisions.append(d)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = decisions
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER

    result = asyncio.run(
        _decisions_summary(user, mock_session, datetime(2026, 4, 24))
    )

    assert len(result["critical_urgent"]) == 8


# ── Task 2 (new) ─────────────────────────────────────────────────────────────

def test_project_stage_map_returns_tuple_with_name_map():
    """_project_stage_map returns (stage_map, name_map) both keyed by identifier."""
    from app.services.weekly_report_service import _project_stage_map
    from app.models import RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("P001", "תכנון", "פרויקט ראשון"),
        ("P002", "ביצוע", None),  # name_map should fall back to identifier
    ]
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    stage_map, name_map = asyncio.run(
        _project_stage_map(user, mock_session)
    )

    assert stage_map == {"P001": "תכנון", "P002": "ביצוע"}
    assert name_map["P001"] == "פרויקט ראשון"
    assert name_map["P002"] == "P002"   # fallback to identifier when name is None


# ── Task 3 (new) ─────────────────────────────────────────────────────────────

def test_projects_behind_schedule_sorted_by_type_order():
    """הקמה projects appear before ניידות even if ניידות is more overdue."""
    from app.services.weekly_report_service import _projects_behind_schedule
    from app.models import Project, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    from datetime import date
    import asyncio

    today = date(2026, 5, 30)

    def _make_proj(identifier, name, ptype, finish_date):
        p = MagicMock(spec=Project)
        p.project_identifier = identifier
        p.name = name
        p.project_type = ptype
        p.stage = "ביצוע"
        p.estimated_finish_date = finish_date
        p.weekly_report_brief = ""
        p.manager = "מנהל"
        return p

    nadut  = _make_proj("N001", "פרויקט ניידות", "ניידות",  date(2026, 2, 19))  # 100 days behind
    hakama = _make_proj("H001", "פרויקט הקמה",   "הקמה",    date(2026, 5, 25))  # 5 days behind

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [nadut, hakama]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.run(
        _projects_behind_schedule(user, mock_session, today)
    )

    # הקמה must come first despite fewer days behind
    assert result[0]["project"].startswith("פרויקט הקמה")
    assert result[1]["project"].startswith("פרויקט ניידות")


# ── Task 4 (new) ─────────────────────────────────────────────────────────────

def test_risky_projects_sorted_by_type_order():
    """הרחבה risk project appears before שוש risk project."""
    from app.services.weekly_report_service import _risky_projects
    from app.models import Project, RoleEnum
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    def _make_risky(identifier, name, ptype):
        p = MagicMock(spec=Project)
        p.project_identifier = identifier
        p.name = name
        p.project_type = ptype
        p.stage = "ביצוע"
        p.risks = "סיכון כלשהו"
        p.weekly_report_brief = ""
        return p

    shoresh  = _make_risky("S001", "פרויקט שוש",   "שוש")
    harchava = _make_risky("HR01", "פרויקט הרחבה", "הרחבה")

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [shoresh, harchava]
    mock_execute = MagicMock()
    mock_execute.scalars.return_value = mock_scalars
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_execute)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.run(
        _risky_projects(user, mock_session)
    )

    assert result[0]["project"].startswith("פרויקט הרחבה")
    assert result[1]["project"].startswith("פרויקט שוש")


# ── Task 5 (new) ─────────────────────────────────────────────────────────────

def test_project_type_summary_structure():
    """_project_type_summary returns dict keyed by all 4 TYPE_ORDER types."""
    from app.services.weekly_report_service import _project_type_summary
    from app.models import RoleEnum
    from app.services.projects_menu_service import TYPE_ORDER
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("הקמה",  10, 3, 2),
        ("הרחבה", 5,  1, 0),
    ]
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    user = MagicMock()
    user.role = RoleEnum.DIVISION_MANAGER
    user.username = "admin"

    result = asyncio.run(
        _project_type_summary(user, mock_session)
    )

    assert set(result.keys()) == set(TYPE_ORDER)
    assert result["הקמה"]  == {"active": 10, "delayed": 3, "at_risk": 2}
    assert result["הרחבה"] == {"active": 5,  "delayed": 1, "at_risk": 0}
    assert result["שוש"]    == {"active": 0, "delayed": 0, "at_risk": 0}
    assert result["ניידות"] == {"active": 0, "delayed": 0, "at_risk": 0}
