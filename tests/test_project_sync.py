"""Tests for project_sync — master-file parsing and Project upsert.

Parsing bugs here silently corrupt every downstream answer, so the pure
parsers are pinned tightly and the sync loop is exercised end-to-end
against a real generated XLSX.
"""
from datetime import date
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from sqlalchemy import select

from app.models import Project
from app.services.project_sync import (
    _build_column_map,
    _clean_brief,
    _extract_weekly_report,
    _parse_date,
    _read_file,
    sync_projects_file,
)


# ── _build_column_map ────────────────────────────────────────────────────────

def test_column_map_exact_hebrew_headers():
    cols = ["זיהוי", "שם פרויקט", "שלב", 'מנה"פ', "יעד חשמול"]
    m = _build_column_map(cols)
    assert m["זיהוי"] == "project_identifier"
    assert m["שם פרויקט"] == "name"
    assert m["שלב"] == "stage"
    assert m['מנה"פ'] == "manager"
    assert m["יעד חשמול"] == "estimated_finish_date"


def test_column_map_weekly_columns_sentinel():
    cols = ["זיהוי", "פירוט שבועי 01/06/2026", "פירוט שבועי 08/06/2026"]
    m = _build_column_map(cols)
    assert m["פירוט שבועי 01/06/2026"] == "__weekly__"
    assert m["פירוט שבועי 08/06/2026"] == "__weekly__"


def test_column_map_fuzzy_case_insensitive_wbs():
    # rapidfuzz 3.x needs an explicit processor to lowercase — regression guard
    m = _build_column_map(["WBS ID"])
    assert m.get("WBS ID") == "project_identifier"


def test_column_map_keeps_highest_score_per_field():
    # Both map to "stage"; the exact match must win and the weaker one dropped
    m = _build_column_map(["סטטוס הפרויקט על ציר הזמן", "שלב"])
    stage_cols = [c for c, f in m.items() if f == "stage"]
    assert len(stage_cols) == 1


def test_column_map_unmatched_column_excluded():
    m = _build_column_map(["totally unrelated header xyz"])
    assert m == {}


# ── _parse_date ──────────────────────────────────────────────────────────────

def test_parse_date_none_and_nan():
    assert _parse_date(None) is None
    assert _parse_date(float("nan")) is None


def test_parse_date_string_and_timestamp():
    assert _parse_date("2026-05-30") == date(2026, 5, 30)
    assert _parse_date(pd.Timestamp("2026-01-15")) == date(2026, 1, 15)


def test_parse_date_garbage_returns_none():
    assert _parse_date("לא תאריך") is None


# ── _extract_weekly_report ───────────────────────────────────────────────────

def test_extract_weekly_report_last_non_empty_wins():
    row = pd.Series({"w1": "עדכון ישן", "w2": None, "w3": "עדכון חדש"})
    assert _extract_weekly_report(row, ["w1", "w2", "w3"]) == "עדכון חדש"


def test_extract_weekly_report_all_empty_returns_none():
    row = pd.Series({"w1": None, "w2": float("nan")})
    assert _extract_weekly_report(row, ["w1", "w2"]) is None


# ── _clean_brief ─────────────────────────────────────────────────────────────

def test_clean_brief_strips_english_wrapper():
    assert _clean_brief("Here is a brief Hebrew summary: הפרויקט מתקדם") == "הפרויקט מתקדם"


def test_clean_brief_strips_stacked_prefixes_and_quotes():
    assert _clean_brief('Summary: "הפרויקט בשלב ביצוע."') == "הפרויקט בשלב ביצוע"


def test_clean_brief_hebrew_text_untouched():
    assert _clean_brief("הפרויקט מתקדם לפי לוח זמנים") == "הפרויקט מתקדם לפי לוח זמנים"


# ── _read_file ───────────────────────────────────────────────────────────────

def _write_master_xlsx(path, extra_title_row=False):
    df = pd.DataFrame({
        "זיהוי": ["TST-01", "TST-02", None],
        "שם פרויקט": ["פרויקט אחד", "פרויקט שניים", "בלי מזהה"],
        "שלב": ["תכנון", "ביצוע", "תכנון"],
        "יעד חשמול": ["2026-12-01", None, None],
        "פירוט שבועי 01/06/2026": ["עדכון ראשון", None, None],
        "פירוט שבועי 08/06/2026": ["עדכון אחרון", "התקדמות", None],
    })
    if extra_title_row:
        # Simulate a merged-title first row above the real header
        import openpyxl
        df.to_excel(path, index=False, startrow=1)
        wb = openpyxl.load_workbook(path)
        wb.active.cell(row=1, column=1, value="דוח מאסטר")
        wb.save(path)
    else:
        df.to_excel(path, index=False)
    return df


def test_read_file_plain_header(tmp_path):
    p = tmp_path / "master.xlsx"
    _write_master_xlsx(p)
    df = _read_file(str(p))
    assert "זיהוי" in df.columns
    assert len(df) == 3


def test_read_file_skips_title_row(tmp_path):
    p = tmp_path / "master_titled.xlsx"
    _write_master_xlsx(p, extra_title_row=True)
    df = _read_file(str(p))
    assert "זיהוי" in df.columns


def test_read_file_unsupported_extension(tmp_path):
    p = tmp_path / "master.txt"
    p.write_text("hello")
    assert _read_file(str(p)).empty


def test_read_file_missing_file():
    assert _read_file("/nonexistent/nope.xlsx").empty


# ── sync_projects_file end-to-end ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_creates_projects_from_xlsx(db_session, tmp_path):
    p = tmp_path / "master.xlsx"
    _write_master_xlsx(p)

    with patch("app.services.project_sync.generate_all_briefs", new_callable=AsyncMock), \
         patch("app.services.project_sync._trigger_reports_after_sync", new_callable=AsyncMock):
        result = await sync_projects_file(str(p))

    assert result["errors"] == []
    assert result["created"] == 2          # row without identifier skipped
    assert result["processed"] == 2

    proj = await db_session.scalar(
        select(Project).where(Project.project_identifier == "TST-01"))
    assert proj is not None
    assert proj.name == "פרויקט אחד"
    assert proj.stage == "תכנון"
    assert proj.estimated_finish_date == date(2026, 12, 1)
    # Weekly report = LAST non-empty weekly column
    assert proj.weekly_report == "עדכון אחרון"


@pytest.mark.asyncio
async def test_sync_updates_existing_and_reactivates(db_session, tmp_path):
    db_session.add(Project(project_identifier="TST-01", name="שם ישן",
                           stage="תכנון", is_active=False))
    await db_session.commit()

    p = tmp_path / "master.xlsx"
    _write_master_xlsx(p)

    with patch("app.services.project_sync.generate_all_briefs", new_callable=AsyncMock), \
         patch("app.services.project_sync._trigger_reports_after_sync", new_callable=AsyncMock):
        result = await sync_projects_file(str(p))

    assert result["updated"] >= 1
    proj = await db_session.scalar(
        select(Project).where(Project.project_identifier == "TST-01"))
    assert proj.name == "פרויקט אחד"      # updated from file
    assert proj.is_active is True          # reappeared → reactivated


@pytest.mark.asyncio
async def test_sync_missing_identifier_column_errors(db_session, tmp_path):
    p = tmp_path / "no_ident.xlsx"
    pd.DataFrame({"שם פרויקט": ["בלי זיהוי"]}).to_excel(p, index=False)

    result = await sync_projects_file(str(p))
    assert result["created"] == 0
    assert any("זיהוי" in e for e in result["errors"])
