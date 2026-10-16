"""The master file's "תקציר הנהלה" sheet → the projects dashboard, and the
one-sheet sync that keeps the project count at the file's own number.
Synthetic rows only — the repo is public and the real file is inside information.
"""
import datetime
import re
from pathlib import Path

import pytest

from app.services import exec_summary as es
from app.services import project_sync


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
    assert [(k["label"], k["value"]) for k in p["kpis"]] == [
        ("סה״כ פרויקטים", 12), ("עם סיכון/חסם", 5), ("מסומנים קריטיים", 3), ("מחסור בבודקים", 0)]
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
    assert (p["kpis"][-1]["label"], p["kpis"][-1]["value"]) == ("חדש השבוע", 7) and len(p["kpis"]) == 5
    assert len(p["sections"][-1]["rows"]) == 2


def test_pick_sheet_newest_dated_copy():
    names = ["דוח שבועי עדכני", "תקציר הנהלה 30.9.26", "תקציר הנהלה 07.10.26", "אחר"]
    assert es.pick_sheet(names) == "תקציר הנהלה 07.10.26"
    assert es.pick_sheet(["דוח שבועי עדכני"]) is None


def test_long_cells_are_clamped():
    p = es.parse_rows([["כותרת"], ["א", "ב"], ["x", "y" * 2000]])
    assert len(p["sections"][0]["rows"][0][1]) == es.CELL_CHARS


SHEET = "דוח שבועי עדכני"
# the record sheet, 0-based rows: row 1 junk, row 2 header, data from Excel row 3
RECORD = [
    ["junk"],
    ["זיהוי", "פרויקט", "סיכון", "לטיפול", "בודקים"],
    ["A-1", "תחנה א", "חסר ציוד", "מנהל אגף", "כן"],
    ["A-2", "תחנה ב", "קבלן", None, None],
    ["A-3", "תחנה ג", None, None, "כן"],
    [None, "שורה בלי זיהוי", "סיכון", None, None],
]


@pytest.mark.parametrize("formula, ids", [
    (f"=COUNTA('{SHEET}'!B3:B6)", ["A-1", "A-2", "A-3"]),                    # the id-less row is not a project
    (f"=COUNTA('{SHEET}'!C3:C5)", ["A-1", "A-2"]),
    (f'=COUNTIF(\'{SHEET}\'!E3:E5,"<>")', ["A-1", "A-3"]),
    (f'=COUNTIF(\'{SHEET}\'!E3:E5,"כן")', ["A-1", "A-3"]),
    (f'=COUNTIFS(\'{SHEET}\'!C3:C5,"<>",\'{SHEET}\'!D3:D5,"")', ["A-2"]),  # risk with no handler
])
def test_resolve_re_runs_the_files_own_formula(formula, ids):
    assert es.resolve(formula, SHEET, RECORD, 0) == ids


@pytest.mark.parametrize("formula", [
    "=SUM(A1:A4)",                                          # not a count
    f"=COUNTA('אחר'!B3:B6)",                                # another sheet
    f'=COUNTIF(\'{SHEET}\'!E3:E5,">5")',                  # a criterion we do not re-run
    f'=COUNTIFS(\'{SHEET}\'!C3:C5,"<>",\'{SHEET}\'!D4:D6,"")',  # ranges that do not line up
    None,
])
def test_resolve_refuses_what_it_cannot_repeat_exactly(formula):
    assert es.resolve(formula, SHEET, RECORD, 0) is None


def test_parse_keeps_the_formula_behind_each_kpi():
    formulas = [[None] * 4 for _ in ROWS]
    formulas[3] = [None, f"=COUNTA('{SHEET}'!B3:B6)", None, "=1+1"]
    p = es.parse_rows(ROWS, "תקציר הנהלה 07.10.26", formulas)
    assert p["kpis"][0]["formula"].startswith("=COUNTA") and p["kpis"][1]["formula"] == "=1+1"
    assert p["kpis"][2]["formula"] is None


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
