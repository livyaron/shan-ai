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


# ── structured table references (the file's shape since 10.11.26) ─────────

TABLE_RECORD = [
    ["junk"],
    ["זיהוי", "פרויקט", "חוסר בבודקים ", "חוסר במשגיחים ", "סיכון"],      # the file writes a trailing space
    ["A-1", "תחנה א", "כן", "לא", "קבלן"],
    ["A-2", "תחנה ב", "לא", "כן", None],
    ["A-3", "תחנה ג", "כן", None, "היתר"],
    [None, "סה״כ", "כן", None, None],                                     # a totals row has no identifier
]


@pytest.mark.parametrize("formula, ids", [
    ('=COUNTIF(טבלה13[[חוסר בבודקים ]],"כן")', ["A-1", "A-3"]),          # exactly as the file writes it
    ('=COUNTIF(טבלה13[חוסר במשגיחים],"כן")', ["A-2"]),                   # single brackets, no trailing space
    ('=COUNTA(Table1[סיכון])', ["A-1", "A-3"]),
    ('=COUNTIFS(טבלה13[[חוסר בבודקים ]],"כן",טבלה13[סיכון],"<>")', ["A-1", "A-3"]),
])
def test_a_table_column_reference_is_re_run_over_the_record_sheet(formula, ids):
    assert es.resolve(formula, SHEET, TABLE_RECORD, 0) == ids


@pytest.mark.parametrize("formula", [
    '=COUNTIF(טבלה13[[עמודה שאין]],"כן")',                                # no such column
    '=COUNTIF(טבלה13[[#All],[חוסר בבודקים ]],"כן")',                      # a special item is not re-run
    '=COUNTIF(טבלה13[@[חוסר בבודקים ]],"כן")',
])
def test_a_table_reference_that_cannot_be_repeated_exactly_is_refused(formula):
    assert es.resolve(formula, SHEET, TABLE_RECORD, 0) is None


def test_a_column_name_that_appears_twice_is_refused():
    rows = [["זיהוי", "סיכון", "סיכון"], ["A-1", "x", None]]
    assert es.resolve('=COUNTA(T[סיכון])', SHEET, rows, 0) is None


def test_unresolved_counts_every_card_that_opens_no_list():
    payload = {"kpis": [{"formula": None, "ids": None}, {"formula": "=COUNTIF(x)", "ids": None}, {"formula": "=COUNTA(y)", "ids": ["A"]}]}
    assert es.unresolved(payload) == 2 and es.unresolved(None) == 0


# ── a card the file gives as a typed number gets its definition in code ───

import datetime as _dt   # noqa: E402

_D = _dt.datetime
RULE_RECORD = [
    ["junk"],
    ["זיהוי", "שם פרויקט", "יעד תכנית פיתוח ", "יעד חשמול מסתמן", 'יעד חשמול מאושר ע"י מנהל האגף', "פירוט שבועי 04/11/2026",
     "פירוט שבועי 11/11/2026", "פירוט סיכונים וחסמים עיקריים ", "לטיפול", "לטיפול נוסף (במידה ונדרש)", "חוסר בבודקים ",
     "פרויקטים קריטים ", "יעד מסתמן לחישוב"],
    #        name      dev               fc                approved          w04   w11     risk     handle   handle2  testers critical calc
    ["A-1", "תחנה א", _D(2026, 12, 31), _D(2027, 6, 30),  _D(2027, 3, 31),  "x",  "עדכון", "קבלן",  "מנה״פ", None,    "כן",   "קריטי", _D(2027, 6, 30)],
    ["A-2", "תחנה ב", _D(2026, 12, 31), _D(2026, 12, 31), _D(2026, 12, 31), "x",  None,    "היתר",  None,    "סמנכ״ל", "לא",  "קריטי", _D(2026, 12, 31)],
    ["A-3", "תחנה ג", _D(2026, 12, 31), _D(2027, 1, 10),  None,             None, "עדכון", "רכש",   None,    None,    None,   None,    _D(2027, 1, 10)],
    ["A-4", "תחנה ד", _D(2026, 12, 31), "לא ידוע",        None,             None, "עדכון", None,    None,    None,    "כן",   None,    _D(2027, 9, 1)],
    [None, "סה״כ", None, None, None, None, None, "x", None, None, "כן", "x", None],
]


@pytest.mark.parametrize("label, value, ids", [
    ("סה״כ פרויקטים", 4, ["A-1", "A-2", "A-3", "A-4"]),
    ("עם סיכון/חסם", 3, ["A-1", "A-2", "A-3"]),
    ("מסומנים קריטיים", 2, ["A-1", "A-2"]),
    ("קריטי + סיכון", 2, ["A-1", "A-2"]),
    ("סיכון ללא טיפול מוגדר", 2, ["A-2", "A-3"]),                       # the earlier files' card: one handling column
    ("סיכון ללא גורם בשני שדות הטיפול", 1, ["A-3"]),                    # the 10.11.26 card: both columns empty
    ("קריטי ללא עדכון 11.11", 1, ["A-2"]),                              # the week named in the label
    ("קריטי ללא עדכון 04.11", 0, []),
    ("מחסור בבודקים", 2, ["A-1", "A-4"]),                               # "לא" is not a shortage
])
def test_a_typed_number_card_is_listed_by_its_definition(label, value, ids):
    found, how = es.rule_ids(label, value, RULE_RECORD, 0)
    assert found == ids and how


def test_the_delay_cards_take_the_candidate_that_matches_the_files_number():
    # the forecast column: A-1 (+6 months) and A-3 (+10 days) → 2; more than a month → 1;
    # the "for calculation" column also dates A-4 → 3
    assert es.rule_ids("דחייה מול תכנית פיתוח", 2, RULE_RECORD, 0)[0] == ["A-1", "A-3"]
    assert es.rule_ids("דחייה מול תכנית פיתוח", 1, RULE_RECORD, 0)[0] == ["A-1"]
    ids, how = es.rule_ids("דחייה מול תכנית פיתוח", 3, RULE_RECORD, 0)
    assert ids == ["A-1", "A-3", "A-4"] and "לחישוב" in how
    assert es.rule_ids("דחייה מול תכנית פיתוח", 99, RULE_RECORD, 0)[0] == ["A-1", "A-3"]     # no match → the plain definition
    assert es.rule_ids("דחייה מול יעד מאושר", 1, RULE_RECORD, 0)[0] == ["A-1"]


def test_an_unknown_label_or_a_missing_column_is_left_unlisted():
    assert es.rule_ids("כרטיס שלא הכרנו", 5, RULE_RECORD, 0) is None
    no_risk = [[c for i, c in enumerate(r) if i != 7] if len(r) > 7 else r for r in RULE_RECORD]
    assert es.rule_ids("עם סיכון/חסם", 3, no_risk, 0) is None


def test_the_rule_is_used_only_when_no_formula_could_be_re_run_and_is_shown_on_the_list():
    from app.services import project_kpis
    payload = {"sheet": "תקציר הנהלה 10.11.26", "kpis": [{"label": "עם סיכון/חסם", "value": 3, "ids": ["A-1"], "rule": "העמודה מלאה"}]}
    label, help_, rows = project_kpis.rows_for("f0", [{"identifier": "A-1"}, {"identifier": "A-9"}], "2026-11-11", payload)
    assert label == "עם סיכון/חסם" and "ההגדרה: העמודה מלאה" in help_ and [r["identifier"] for r in rows] == ["A-1"]
    src = (Path(es.__file__)).read_text(encoding="utf-8")
    assert 'if k["ids"] is None:' in src and src.index('k["ids"] = resolve(') < src.index("found = rule_ids(")
