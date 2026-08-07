"""Pure-function tests for the חדר מבצעים XLSX report + AI focus summary.

Detached model instances only — no DB, matching test_missions_menu_service.py.
"""

import datetime
from datetime import date, timedelta
from io import BytesIO

import pytest
from sqlalchemy.orm import configure_mappers, class_mapper

from app.models import Mission, User
from app.services import missions_report_service as mrs

configure_mappers()
_mission_mgr = class_mapper(Mission).class_manager
_user_mgr = class_mapper(User).class_manager

TODAY = date(2026, 7, 15)
NOW = datetime.datetime(2026, 7, 15, 6, 0)


def _make_user(**kwargs):
    defaults = dict(id=1, username="דני לוי", telegram_id=111)
    defaults.update(kwargs)
    u = _user_mgr.new_instance()
    for k, v in defaults.items():
        setattr(u, k, v)
    return u


def _make_mission(**kwargs):
    defaults = dict(
        id=1,
        title="בדיקת שנאי בתחנת שדרות",
        description=None,
        is_urgent=True,
        is_important=True,
        status="open",
        owner_id=1,
        created_by_id=1,
        due_date=None,
        created_at=datetime.datetime(2026, 7, 1, 8, 0),
        updated_at=datetime.datetime(2026, 7, 10, 8, 0),
        completed_at=None,
    )
    owner = kwargs.pop("owner", _make_user())
    defaults.update(kwargs)
    m = _mission_mgr.new_instance()
    for k, v in defaults.items():
        setattr(m, k, v)
    m.owner = owner
    m.created_by = owner
    return m


# ── due_bucket boundaries ──────────────────────────────────────────────────

@pytest.mark.parametrize("delta,expected", [
    (-1, "באיחור"),
    (0, "היום"),
    (1, f"עד {mrs.AT_RISK_DAYS} ימים"),
    (3, f"עד {mrs.AT_RISK_DAYS} ימים"),
    (4, "השבוע"),
    (7, "השבוע"),
    (8, "מאוחר יותר"),
])
def test_due_bucket_boundaries(delta, expected):
    assert mrs.due_bucket(TODAY + timedelta(days=delta), TODAY) == expected


def test_due_bucket_no_date():
    assert mrs.due_bucket(None, TODAY) == "ללא תאריך יעד"


def test_bucket_order_covers_every_bucket():
    produced = {mrs.due_bucket(TODAY + timedelta(days=d), TODAY) for d in range(-2, 30)}
    produced.add(mrs.due_bucket(None, TODAY))
    assert produced == set(mrs.BUCKET_ORDER)


# ── priority weight mirrors the SQL case() ─────────────────────────────────

def test_priority_weight_ordering():
    assert mrs._priority_weight(_make_mission(is_urgent=True, is_important=True)) == 0
    assert mrs._priority_weight(_make_mission(is_urgent=False, is_important=True)) == 1
    assert mrs._priority_weight(_make_mission(is_urgent=True, is_important=False)) == 2
    assert mrs._priority_weight(_make_mission(is_urgent=False, is_important=False)) == 3


# ── stats ──────────────────────────────────────────────────────────────────

def test_stats_overdue_rate_and_buckets():
    active = [
        _make_mission(id=1, due_date=TODAY - timedelta(days=2)),
        _make_mission(id=2, due_date=TODAY),
        _make_mission(id=3, due_date=TODAY + timedelta(days=10)),
        _make_mission(id=4, due_date=None),
    ]
    stats = mrs._compute_stats(active, [], TODAY, NOW)
    assert stats["total_open"] == 4
    assert stats["overdue"] == 1
    assert stats["overdue_rate"] == 25
    assert stats["buckets"]["באיחור"] == 1
    assert stats["buckets"]["היום"] == 1
    assert stats["buckets"]["מאוחר יותר"] == 1
    assert stats["buckets"]["ללא תאריך יעד"] == 1


def test_stats_per_owner_aggregation():
    a, b = _make_user(id=1, username="דני"), _make_user(id=2, username="רותם")
    active = [
        _make_mission(id=1, owner=a, due_date=TODAY - timedelta(days=3)),
        _make_mission(id=2, owner=a, due_date=TODAY - timedelta(days=1)),
        _make_mission(id=3, owner=a, due_date=TODAY + timedelta(days=5)),
        _make_mission(id=4, owner=b, due_date=TODAY + timedelta(days=5)),
    ]
    stats = mrs._compute_stats(active, [], TODAY, NOW)
    by_name = {r["owner"]: r for r in stats["owners"]}
    assert by_name["דני"]["open"] == 3
    assert by_name["דני"]["overdue"] == 2
    assert by_name["דני"]["overdue_rate"] == 67
    assert by_name["רותם"]["overdue"] == 0
    # Most-overdue owner sorts first — that's the row a manager should see immediately.
    assert stats["owners"][0]["owner"] == "דני"


def test_stats_cycle_time_and_throughput():
    closed = [
        _make_mission(
            id=10, status="done",
            created_at=datetime.datetime(2026, 7, 1, 8, 0),
            completed_at=datetime.datetime(2026, 7, 11, 8, 0),
        ),
        _make_mission(
            id=11, status="done",
            created_at=datetime.datetime(2026, 6, 25, 8, 0),
            completed_at=datetime.datetime(2026, 6, 29, 8, 0),
        ),
    ]
    stats = mrs._compute_stats([], closed, TODAY, NOW)
    assert stats["closed_30d"] == 2
    assert stats["closed_7d"] == 1        # only the 11/07 one is inside 7 days
    assert stats["avg_cycle_30d"] == 7.0  # (10 + 4) / 2


def test_stats_stale_missions():
    active = [
        _make_mission(id=1, updated_at=datetime.datetime(2026, 5, 1, 8, 0)),
        _make_mission(id=2, updated_at=datetime.datetime(2026, 7, 14, 8, 0)),
    ]
    stats = mrs._compute_stats(active, [], TODAY, NOW)
    assert stats["stale"] == 1


def test_stats_empty_board_does_not_divide_by_zero():
    stats = mrs._compute_stats([], [], TODAY, NOW)
    assert stats["overdue_rate"] == 0
    assert stats["avg_age_open"] == 0.0
    assert stats["avg_cycle_30d"] == 0.0


# ── insights ───────────────────────────────────────────────────────────────

def test_insights_always_return_something():
    stats = mrs._compute_stats([], [], TODAY, NOW)
    out = mrs.compute_insights(stats)
    assert out and all({"sev", "icon", "headline", "action"} <= set(i) for i in out)


def test_insights_flag_concentrated_overdue_load():
    a, b = _make_user(id=1, username="דני"), _make_user(id=2, username="רותם")
    active = [_make_mission(id=i, owner=a, due_date=TODAY - timedelta(days=2))
              for i in range(1, 5)]
    active.append(_make_mission(id=9, owner=b, due_date=TODAY + timedelta(days=4)))
    stats = mrs._compute_stats(active, [], TODAY, NOW)
    assert any("דני" in i["headline"] for i in mrs.compute_insights(stats))


# ── workbook ───────────────────────────────────────────────────────────────

def _sample_data():
    a = _make_user(id=1, username="דני")
    active = [
        _make_mission(id=1, owner=a, due_date=TODAY - timedelta(days=2)),
        _make_mission(id=2, owner=a, due_date=TODAY + timedelta(days=2)),
        _make_mission(id=3, owner=a, due_date=TODAY + timedelta(days=20)),
    ]
    closed = [_make_mission(
        id=10, owner=a, status="done", due_date=TODAY - timedelta(days=5),
        created_at=datetime.datetime(2026, 7, 1, 8, 0),
        completed_at=datetime.datetime(2026, 7, 6, 8, 0),
    )]
    stats = mrs._compute_stats(active, closed, TODAY, NOW)
    return {
        "open_rows": [{
            "id": m.id, "title": m.title, "description": "", "quadrant": "בצע עכשיו",
            "urgent": "כן", "important": "כן", "status": "פתוחה", "owner": "דני",
            "created_by": "דני", "due": "01/01/2026", "days_to_due": 1,
            "days_overdue": None, "age_days": 5, "created_at": "", "updated_at": "",
            "_overdue": m.due_date < TODAY, "_at_risk": False,
        } for m in active],
        "closed_rows": [{
            "id": 10, "title": "סגורה", "quadrant": "בצע עכשיו", "status": "הושלמה",
            "owner": "דני", "due": "10/07/2026", "completed_at": "06/07/2026 08:00",
            "cycle_days": 5, "on_time": "כן", "created_at": "",
        }],
        "stats": stats,
        "meta": {"generated_at": "15/07/2026 06:00", "date_slug": "15-07-2026"},
    }


def test_build_workbook_structure():
    from openpyxl import load_workbook

    data = _sample_data()
    payload = mrs.build_workbook(data, ai_text="• תובנה לדוגמה — פעולה לדוגמה")
    assert isinstance(payload, bytes) and payload[:2] == b"PK"

    wb = load_workbook(BytesIO(payload))
    assert wb.sheetnames == [mrs.SHEET_SUMMARY, mrs.SHEET_OPEN, mrs.SHEET_CLOSED]

    # Hebrew sheets must render right-to-left or every column reads backwards.
    for name in wb.sheetnames:
        assert wb[name].sheet_view.rightToLeft is True

    ws_open = wb[mrs.SHEET_OPEN]
    assert [c.value for c in ws_open[1]] == mrs.OPEN_HEADERS
    assert ws_open.max_row == len(data["open_rows"]) + 1
    assert ws_open.freeze_panes == "A2"

    ws_closed = wb[mrs.SHEET_CLOSED]
    assert [c.value for c in ws_closed[1]] == mrs.CLOSED_HEADERS
    assert ws_closed.max_row == len(data["closed_rows"]) + 1


def test_build_workbook_survives_empty_board():
    from openpyxl import load_workbook

    stats = mrs._compute_stats([], [], TODAY, NOW)
    data = {"open_rows": [], "closed_rows": [], "stats": stats,
            "meta": {"generated_at": "15/07/2026 06:00", "date_slug": "15-07-2026"}}
    wb = load_workbook(BytesIO(mrs.build_workbook(data, ai_text="")))
    assert len(wb.sheetnames) == 3


def test_build_workbook_without_ai_text():
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(mrs.build_workbook(_sample_data(), ai_text="")))
    ws = wb[mrs.SHEET_SUMMARY]
    texts = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    # The deterministic insight block still renders when the LLM produced nothing.
    assert any("תובנות AI" in t for t in texts)
    assert any("אינן זמינות" in t for t in texts)


# ── focus summary fallback ─────────────────────────────────────────────────

def test_focus_summary_plain_fallback_lists_buckets():
    a = _make_user(id=1, username="דני")
    groups = {
        "late": [_make_mission(id=1, owner=a, due_date=TODAY - timedelta(days=2))],
        "today": [],
        "soon": [_make_mission(id=2, owner=a, due_date=TODAY + timedelta(days=2))],
        "nodate": [],
    }
    text = mrs._format_at_risk_plain(groups, TODAY)
    assert text.startswith("‏")  # RTL mark on the first line (CLAUDE.md §5)
    assert "באיחור" in text
    assert "אינו זמין" in text


def test_focus_summary_plain_fallback_when_board_clean():
    groups = {"late": [], "today": [], "soon": [], "nodate": []}
    text = mrs._format_at_risk_plain(groups, TODAY)
    assert "אין משימות באיחור" in text


async def test_build_focus_summary_falls_back_when_llm_raises(monkeypatch):
    a = _make_user(id=1, username="דני")
    groups = {
        "late": [_make_mission(id=1, owner=a, due_date=TODAY - timedelta(days=2))],
        "today": [], "soon": [], "nodate": [],
    }

    async def _fake_collect(_session):
        return groups

    async def _boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(mrs, "collect_at_risk", _fake_collect)
    monkeypatch.setattr(mrs.oms, "today_il", lambda: TODAY)
    from app.services import llm_router
    monkeypatch.setattr(llm_router, "llm_chat", _boom)

    text = await mrs.build_focus_summary(session=None)
    # A dead LLM must degrade to the raw list, never to an exception.
    assert "אינו זמין" in text


async def test_build_focus_summary_escapes_model_output(monkeypatch):
    a = _make_user(id=1, username="דני")
    groups = {
        "late": [_make_mission(id=1, owner=a, due_date=TODAY - timedelta(days=2))],
        "today": [], "soon": [], "nodate": [],
    }

    async def _fake_collect(_session):
        return groups

    async def _inject(*args, **kwargs):
        return "<script>alert(1)</script> הערכת מצב"

    monkeypatch.setattr(mrs, "collect_at_risk", _fake_collect)
    monkeypatch.setattr(mrs.oms, "today_il", lambda: TODAY)
    from app.services import llm_router
    monkeypatch.setattr(llm_router, "llm_chat", _inject)

    text = await mrs.build_focus_summary(session=None)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# ── menu keyboard guard ────────────────────────────────────────────────────

def test_menu_keyboard_exposes_report_buttons():
    from app.services.missions_menu_service import get_menu_keyboard

    kb = get_menu_keyboard({"do": 1, "plan": 0, "delegate": 0, "backlog": 0})
    tokens = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "om:xls" in tokens
    assert "om:sum" in tokens
    # Colon-free tail so the om:* dispatcher can keep using split(':').
    assert all(t.count(":") == 1 for t in ("om:xls", "om:sum"))
