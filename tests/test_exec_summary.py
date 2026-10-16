"""The master file's "תקציר הנהלה" sheet → the projects dashboard, and the
one-sheet sync that keeps the project count at the file's own number.
Synthetic rows only — the repo is public and the real file is inside information.
"""
import datetime
import re
from pathlib import Path

import pytest

from app.services import dashboard_domains as dd
from app.services import exec_summary as es
from app.services import project_sync

from tests.test_dashboard_stations import _render

ROWS = [
    ["מוקדים קריטיים | סטטוס 06.10.2026", None, None, None],
    ["בסיס: 12 פרויקטים", None, None, None],
    [None, None, None, None],
    ["סה״כ פרויקטים", 12, "עם סיכון/חסם", 5],
    ["מסומנים קריטיים", 3, "מחסור בבודקים", 0],          # a real 0 is a KPI value
    [None, None, None, None],
    ["עדיפות", "פרויקט", "סיכון / חסם", None],
    [1, "תחנה א", "חסר ציוד", None],
    ["2", "תחנה ב", 0, None],                             # 0 = an empty source cell
    ["מסרים מוצעים להנהלה", None, None, None],
    [1, "משאבים", "לאשר תיעדוף רוחבי", None],
    ["פרויקטים עם חוסר בבודקים", None, None, None],
    ["מזהה", "פרויקט", None, "עדכון אחרון"],
    ["X-1", "תחנה א", None, datetime.datetime(2026, 10, 6)],
]


def test_parse_follows_the_sheet_not_a_fixed_layout():
    p = es.parse_rows(ROWS, "תקציר הנהלה 07.10.26")
    assert p["as_of"] == "2026-10-07"
    assert p["title"].startswith("מוקדים") and p["subtitle"] == "בסיס: 12 פרויקטים"
    assert p["kpis"] == [["סה״כ פרויקטים", 12], ["עם סיכון/חסם", 5],
                         ["מסומנים קריטיים", 3], ["מחסור בבודקים", 0]]
    first, msgs, short = p["sections"]
    assert first["title"] is None and first["header"] == ["עדיפות", "פרויקט", "סיכון / חסם"]
    assert first["rows"] == [["1", "תחנה א", "חסר ציוד"], ["2", "תחנה ב", ""]]
    assert msgs["title"] == "מסרים מוצעים להנהלה" and msgs["header"] is None
    assert msgs["rows"] == [["1", "משאבים", "לאשר תיעדוף רוחבי"]]
    # the empty spacer column is dropped and the id column still points at the id
    assert short["header"] == ["מזהה", "פרויקט", "עדכון אחרון"]
    assert short["rows"] == [["X-1", "תחנה א", "06/10/2026"]] and short["id_col"] == 0


def test_more_kpis_and_rows_in_the_next_file_need_no_code():
    rows = ROWS[:5] + [["חדש השבוע", 7, None, None]] + ROWS[5:] + [["X-2", "תחנה ג", None, None]]
    p = es.parse_rows(rows, "תקציר הנהלה 14.10.26")
    assert p["kpis"][-1] == ["חדש השבוע", 7] and len(p["kpis"]) == 5
    assert len(p["sections"][-1]["rows"]) == 2


def test_pick_sheet_newest_dated_copy():
    names = ["דוח שבועי עדכני", "תקציר הנהלה 30.9.26", "תקציר הנהלה 07.10.26", "אחר"]
    assert es.pick_sheet(names) == "תקציר הנהלה 07.10.26"
    assert es.pick_sheet(["דוח שבועי עדכני"]) is None


def test_long_cells_are_clamped():
    p = es.parse_rows([["כותרת"], ["א", "ב"], ["x", "y" * 2000]])
    assert len(p["sections"][0]["rows"][0][1]) == es.CELL_CHARS


def _ps():
    return {"ok": True, "active": 800, "past_due": 0, "late": 0, "undated": 0, "short_staff": 0,
            "sectors": [], "stages": [], "watch": [], "watch_total": 0}


def test_dashboard_shows_the_summary_numbers_as_written():
    ex = {**es.parse_rows(ROWS, "תקציר הנהלה 07.10.26"), "as_of_il": "07/10/2026",
          "loaded_at_il": "14/10/2026 10:00"}
    html = _render({"missions": {"ok": False}, "projects": {**_ps(), "exec": ex}, "lessons": {"ok": False}})
    tile = html[html.index('data-zoom="projects"'):html.index('data-zoom="lessons"')]
    assert "<b>12</b><small>סה״כ פרויקטים" in tile and "800" not in tile
    assert "<b>5</b>עם סיכון/חסם" in tile
    view = html[html.index('data-view="projects"'):html.index('data-view="lessons"')]
    assert "מסרים מוצעים להנהלה" in view and "לאשר תיעדוף רוחבי" in view
    assert 'href="/dashboard/projects/p/X-1"' in view
    for label, _ in ex["kpis"]:
        assert label in view


def test_without_a_summary_the_computed_tile_stays():
    html = _render({"missions": {"ok": False}, "projects": _ps(), "lessons": {"ok": False}})
    assert 'data-live="p_active">800<' in html


class _Res:
    rowcount = 3


class _Session:
    def __init__(self):
        self.calls = 0

    async def execute(self, *_a, **_k):
        self.calls += 1
        return _Res()

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_deactivate_missing_never_runs_on_a_broken_parse():
    s = _Session()
    assert await project_sync.deactivate_missing(s, {"A", "B"}) == 0 and s.calls == 0
    assert await project_sync.deactivate_missing(s, []) == 0 and s.calls == 0
    ids = {f"P{i}" for i in range(project_sync.MIN_INVENTORY)}
    assert await project_sync.deactivate_missing(s, ids) == 3 and s.calls == 1


def test_master_upload_syncs_the_record_sheet_only():
    # 2026-10-16: syncing every sheet made 817 active projects out of a 263-row file
    src = Path("app/services/knowledge_service.py").read_text(encoding="utf-8")
    block = src[src.index("Sync to Projects table"):src.index("except Exception as sync_err")]
    assert "pick_master_sheet" in block
    assert not re.search(r"for \w+ in xls\.sheet_names", block)


def test_mine_scope_never_gets_the_division_summary():
    src = Path("app/services/dashboard_domains.py").read_text(encoding="utf-8")
    assert 'if not mine and out["projects"].get("ok"):' in src
    assert dd.MINE != dd.ALL
