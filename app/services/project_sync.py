"""Project sync service — parses uploaded XLSX/CSV master file and upserts Project records."""

import asyncio
import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pandas as pd
from rapidfuzz import process as rf_process
from rapidfuzz.utils import default_process as _rf_default_process
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session_maker
from app.models import Project, ProjectSnapshot, ProjectWeeklyEntry
from app.services import memory_service
from app.services.llm_router import llm_chat
from app.services.project_learning_service import save_snapshot

logger = logging.getLogger(__name__)

# ── Column name mapping: Hebrew header → model field ──────────────────────
KNOWN_COLUMNS: dict[str, str] = {
    "זיהוי":                           "project_identifier",
    "wbs":                             "project_identifier",
    "wbs id":                          "project_identifier",
    "קוד wbs":                         "project_identifier",
    "מזהה":                            "project_identifier",
    "שם פרויקט":                       "name",
    "שם הפרויקט":                      "name",
    "שם":                              "name",
    "סוג":                             "project_type",
    "סוג פרויקט":                      "project_type",
    "סוג תחנה":                        "project_type",
    "שלב":                             "stage",
    "שלב הפרויקט":                     "stage",
    "סטטוס":                           "stage",
    "סטטוס הפרויקט":                   "stage",
    "סטטוס הפרויקט על ציר הזמן":       "stage",
    'מנה"פ':                           "manager",
    "מנהל":                            "manager",
    "מנהל פרויקט":                     "manager",
    "אחראי":                           "manager",
    "פירוט סיכונים וחסמים עיקריים":    "risks",
    "סיכונים וחסמים":                  "risks",
    "סיכונים":                         "risks",
    "חסמים":                           "risks",
    "לטיפול":                          "to_handle",
    "טיפול":                           "to_handle",
    "לעיבוד":                          "to_handle",
    "פעולות":                          "to_handle",
    "תאריך תכנית פיתוח":               "dev_plan_date",
    "תאריך פיתוח":                     "dev_plan_date",
    "יעד תכנית פיתוח":                 "dev_plan_date",
    "יעד פיתוח":                       "dev_plan_date",
    "תכנית פיתוח":                     "dev_plan_date",
    "תאריך סיום משוער":                "estimated_finish_date",
    "תאריך סיום":                      "estimated_finish_date",
    "יעד חשמול מסתמן":                 "estimated_finish_date",
    "יעד חשמול":                       "estimated_finish_date",
    "חשמול":                           "estimated_finish_date",
    'תו"ב':                            "controller",
    "חוסר במשגיחים":                   "short_supervisors",
    "חוסר בבודקים":                    "short_testers",
    "פרויקטים קריטים":                 "critical_tier",
}

BOOL_FIELDS = ("short_supervisors", "short_testers")
DATE_FIELDS = ("dev_plan_date", "estimated_finish_date")
DRAFT_SHEET_MARKER = "טיוטה"   # "דוח שבועי טיוטה" — a draft copy, never the record

WEEKLY_REPORT_MARKER = "פירוט שבועי"  # substring match in column name
FUZZY_CUTOFF = 50  # Lowered from 60 for better coverage


# ── Blocking file reader (run in executor) ────────────────────────────────

def _read_file(file_path: str, sheet_name: str | None = None) -> pd.DataFrame:
    """
    Read XLSX or CSV file into DataFrame.
    Handles header row detection (skips all-NaN first row if present).
    sheet_name: explicit sheet to read (for XLSX); if None reads the first sheet.
    Returns empty DataFrame on error.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            kw = dict(sheet_name=sheet_name) if sheet_name else {}
            # Probe 8 rows — pick FIRST row whose non-null count >= 50% of max.
            # Dense data rows always score higher than headers on raw non-null count,
            # so "most non-null" is wrong; "first above threshold" correctly skips
            # empty/merged-title rows and lands on the actual header row.
            df_raw = pd.read_excel(path, engine="openpyxl", nrows=8, header=None, **kw)
            counts = [int(df_raw.iloc[i].notna().sum()) for i in range(len(df_raw))]
            threshold = max(3, max(counts) // 2) if counts else 3
            best_row = next((i for i, c in enumerate(counts) if c >= threshold), 0)
            df = pd.read_excel(path, engine="openpyxl", header=best_row, **kw)
        elif ext == ".csv":
            df = pd.read_csv(path, encoding="utf-8-sig")
        else:
            logger.error(f"project_sync: unsupported extension: {ext}")
            return pd.DataFrame()
    except Exception as exc:
        logger.error(f"project_sync: failed to read file {file_path}: {exc}")
        return pd.DataFrame()

    # Strip whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ── Fuzzy column matcher ──────────────────────────────────────────────────

def _build_column_map(df_columns: list[str]) -> dict[str, str]:
    """
    Build mapping: actual DataFrame column → model field name.
    Uses fuzzy matching (rapidfuzz) + substring match for weekly report columns.

    When multiple columns match the same DB field, keeps HIGHEST-SCORE match only.
    This prevents false positives (e.g., status column overwriting project name).

    Returns dict: {actual_col_name: model_field_name}
    Weekly-report columns map to "__weekly__" sentinel.
    """
    col_map: dict[str, str] = {}
    # Track best score per target field: {field_name: (score, actual_col)}
    best_per_field: dict[str, tuple[float, str]] = {}
    unmatched = []

    for actual_col in df_columns:
        # Weekly report columns: match by substring (e.g., "פירוט שבועי 15/12/2025")
        if WEEKLY_REPORT_MARKER in actual_col:
            col_map[actual_col] = "__weekly__"
            logger.info(f"  ✓ Weekly column: {actual_col}")
            continue

        # Explicit processor required — rapidfuzz 3.x does NOT auto-lowercase,
        # so 'WBS2' vs 'wbs' scores 0 without it.
        result = rf_process.extractOne(
            actual_col,
            KNOWN_COLUMNS.keys(),
            score_cutoff=FUZZY_CUTOFF,
            processor=_rf_default_process,
        )
        if result:
            best_key, score, _idx = result
            target_field = KNOWN_COLUMNS[best_key]

            # Only claim this field if no prior match OR this scores higher
            prior = best_per_field.get(target_field)
            if prior is None or score > prior[0]:
                if prior is not None:
                    # Remove the old lower-score column from map
                    old_col = prior[1]
                    del col_map[old_col]
                    logger.warning(
                        f"  ⚠ Replaced '{old_col}' (score {prior[0]:.0f}) with "
                        f"'{actual_col}' (score {score:.0f}) for field '{target_field}'"
                    )
                best_per_field[target_field] = (score, actual_col)
                col_map[actual_col] = target_field
                logger.info(f"  ✓ Matched: '{actual_col}' → '{best_key}' → '{target_field}' (score: {score:.0f})")
            else:
                logger.warning(
                    f"  ⚠ Skipped '{actual_col}' → '{target_field}' "
                    f"(score {score:.0f} < existing '{prior[1]}' score {prior[0]:.0f})"
                )
        else:
            unmatched.append(actual_col)
            logger.warning(f"  ✗ No match for column: '{actual_col}'")

    if unmatched:
        logger.warning(f"Unmatched columns ({len(unmatched)}): {unmatched}")

    return col_map


# ── Weekly report extraction ──────────────────────────────────────────────

def _extract_weekly_report(row: pd.Series, weekly_cols: list[str]) -> str | None:
    """
    Extract the LAST non-empty value from weekly_cols for this row.
    weekly_cols are already ordered by DataFrame column position (left-to-right = chronologically).
    """
    last_val = None
    for col in weekly_cols:
        val = row[col]
        if pd.notna(val):
            s = str(val).strip()
            if s and s.lower() != "nan":
                last_val = s
    return last_val


# ── Date parser ────────────────────────────────────────────────────────────

_MIN_YEAR, _MAX_YEAR = 2000, 2045   # the file holds typos like year 1


def _parse_date(val: Any):
    """Parse a cell value as a date. Returns datetime.date or None.

    Strings are day-first (Israeli DD/MM/YYYY). Years outside a sane window are
    typos, not dates — they used to crash date arithmetic downstream.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        parsed = pd.to_datetime(val, errors="coerce", dayfirst=isinstance(val, str))
        if pd.isna(parsed):
            return None
        d = parsed.date()
        return d if _MIN_YEAR <= d.year <= _MAX_YEAR else None
    except Exception:
        return None


def _split_finish_date(val: Any) -> tuple[Any, str | None]:
    """יעד חשמול מסתמן → (date | None, prose | None).

    A cell that is a date stays a date. A cell that is prose — even prose that
    opens with a date, like "01/07/2026 לא אפשרי, יתקבל יעד חדש" — is kept as
    text: that date is explicitly NOT the target, and dropping the text made
    those projects vanish from every date metric.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None, None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None, None
        if re.fullmatch(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", s):
            d = _parse_date(s)
            return (d, None) if d else (None, s)
        return None, s
    return _parse_date(val), None


def _parse_bool(val: Any) -> bool | None:
    """"כן"/"לא" marker cells → True/False; anything else → None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s in ("כן", "v", "V", "✓", "x", "X", "1", "True", "TRUE"):
        return True
    if s in ("לא", "0", "False", "FALSE"):
        return False
    return None


# ── Report date & weekly-column dates ─────────────────────────────────────

_DATE_IN_TEXT_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")


def _mk_date(day: int, month: int, year: int):
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return d if _MIN_YEAR <= d.year <= _MAX_YEAR else None


def _year4(y: str) -> int:
    return int(y) + 2000 if len(y) == 2 else int(y)


def report_date_from_filename(file_path: str):
    """"דוח שבועי לסמנכל ... 16.09.2026.xlsx" → date(2026, 9, 16); else None.

    Only a full day.month.year counts — a bare "1.7" inside a name is too
    ambiguous to date a whole report by.
    """
    name = Path(file_path).name
    for m in _DATE_IN_TEXT_RE.finditer(name):
        if m.group(3):
            d = _mk_date(int(m.group(1)), int(m.group(2)), _year4(m.group(3)))
            if d:
                return d
    return None


def weekly_column_date(col: str, report_date):
    """"פירוט שבועי 19/8/2026" → date. Year-less headers ("14.1") take the
    report's year, stepping back a year when that would land after the report.
    """
    m = _DATE_IN_TEXT_RE.search(col.split(WEEKLY_REPORT_MARKER, 1)[-1])
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if m.group(3):
        return _mk_date(day, month, _year4(m.group(3)))
    d = _mk_date(day, month, report_date.year)
    if d and d > report_date + timedelta(days=7):
        d = _mk_date(day, month, report_date.year - 1)
    return d


def resolve_report_date(file_path: str, weekly_cols: list[str]):
    """The date the file reports on: its name, else its newest weekly column,
    else today. Snapshots are stamped with this — not with the upload day."""
    d = report_date_from_filename(file_path)
    if d:
        return d
    today = date.today()
    col_dates = [wd for c in weekly_cols if (wd := weekly_column_date(c, today))]
    return max(col_dates) if col_dates else today


def _text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()[:16]


# ── AI Briefing generation ────────────────────────────────────────────────

_BRIEF_BAD_PREFIXES = [
    "here is a brief hebrew summary:",
    "here is a brief summary:",
    "here's a brief hebrew summary:",
    "here's a brief summary:",
    "brief hebrew summary:",
    "brief summary:",
    "summary:",
    "or, even more concise:",
    "or even more concise:",
    "or more concise:",
    "concise version:",
    "סיכום קצר:",
    "סיכום:",
]

# Regex: catch any leading  "EnglishWords...: " wrapper not in the explicit list
_BRIEF_ENGLISH_PREFIX_RE = re.compile(r'^[A-Za-z][A-Za-z ,\'"]*:\s*', re.IGNORECASE)


def _clean_brief(text: str) -> str:
    """Strip LLM meta-text wrappers from a generated brief."""
    t = text.strip()
    lower = t.lower()
    # Strip known bad prefixes (may repeat — e.g. two options on separate lines)
    changed = True
    while changed:
        changed = False
        for prefix in _BRIEF_BAD_PREFIXES:
            if lower.startswith(prefix):
                t = t[len(prefix):].strip()
                lower = t.lower()
                changed = True
    # Catch-all: strip any remaining  "EnglishLabel: " prefix
    t = _BRIEF_ENGLISH_PREFIX_RE.sub('', t).strip()
    # Strip surrounding quotes (straight, curly, Hebrew gershayim)
    t = t.strip('"\'""״')
    # Strip trailing punctuation artifacts
    t = re.sub(r'[.,"\'"״\s]+$', '', t).strip()
    return t


async def _generate_weekly_brief(weekly_report: str | None) -> str | None:
    """
    Generate a short 1-2 sentence AI briefing from the weekly report.
    Returns the brief (max 500 chars) or None if generation fails/no input.
    """
    if not weekly_report or not weekly_report.strip():
        return None

    try:
        report_preview = weekly_report[:1000] if len(weekly_report) > 1000 else weekly_report

        system = (
            "אתה מסכם עדכוני פרויקטים בעברית בלבד. "
            "החזר אך ורק את טקסט הסיכום — ללא מלל באנגלית, ללא כותרת, ללא מבוא, ללא ציטוטים, ללא חלופות. "
            "אסור להשתמש בביטויים כגון 'brief Hebrew summary:', 'Or, even more concise:', 'summary:' וכדומה."
        )
        prompt = (
            "סכם בעברית בלבד את עדכון הפרויקט הבא ב-1-2 משפטים קצרים (עד 100 תווים). "
            "ענה אך ורק בטקסט הסיכום עצמו.\n\n"
            f"{report_preview}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        brief = await llm_chat("project_brief", messages, max_tokens=150, temperature=0.2)

        if not brief or not brief.strip():
            return None

        brief = _clean_brief(brief)

        if not brief:
            return None

        if len(brief) > 500:
            brief = brief[:497] + "..."

        logger.info(f"Generated brief ({len(brief)} chars): {brief[:80]}...")
        return brief
    except Exception as exc:
        logger.warning(f"Brief generation failed (will use truncated text): {exc}")
        return None


# ── Main async entry point ────────────────────────────────────────────────

async def sync_projects_file(file_path: str, sheet_name: str | None = None,
                             force_history: bool = False) -> dict:
    """
    Parse project master file and upsert Project records to DB.

    Called as a BackgroundTasks callback — creates its own async session.
    sheet_name: specific sheet to read (detected by process_master_file); None =
    the weekly-report sheet of an XLSX (pick_master_sheet), else the first sheet.
    force_history: replay as history even when the report is newer than the DB —
    the backfill path, which must never touch live rows or notify anyone.
    Returns result dict: {"processed": N, "created": N, "updated": N, "errors": [...]}
    """
    result = {"processed": 0, "created": 0, "updated": 0, "errors": [], "identifiers": []}
    change_facts: list[dict] = []   # second-brain temporal facts (Option G)
    dirty_project_ids: list[int] = []   # dossiers to re-drip after this sync

    if sheet_name and DRAFT_SHEET_MARKER in sheet_name:
        # The master workbook carries a "דוח שבועי טיוטה" draft next to the real
        # sheet. Syncing it created phantom projects and snapshots.
        logger.info(f"project_sync: skipping draft sheet '{sheet_name}'")
        return result

    # 1. Read file in executor thread (pandas is synchronous/blocking)
    loop = asyncio.get_event_loop()
    if sheet_name is None:
        sheet_name = await loop.run_in_executor(None, pick_master_sheet, file_path)
    df = await loop.run_in_executor(None, _read_file, file_path, sheet_name)

    if df.empty:
        result["errors"].append("הקובץ ריק או לא ניתן לקריאה")
        return result

    # 2. Build column mapping (fuzzy + weekly sentinel)
    logger.info(f"Found {len(df.columns)} columns in file: {list(df.columns)}")
    col_map = _build_column_map(list(df.columns))
    logger.info(f"Column mapping result: {col_map}")

    # Weekly columns: preserve DataFrame column order (left-to-right = chronological)
    weekly_cols = [c for c in df.columns if col_map.get(c) == "__weekly__"]

    if "project_identifier" not in col_map.values():
        result["errors"].append(
            "לא נמצא עמודת זיהוי פרויקט בקובץ (חיפוש fuzzy נכשל)"
        )
        return result

    # The date this file reports on — snapshots are stamped with it, and a file
    # older than what the DB already holds is replayed as history (below).
    report_date = resolve_report_date(file_path, weekly_cols)
    result["report_date"] = report_date.isoformat()
    week_dates = {c: weekly_column_date(c, report_date) for c in weekly_cols}
    seen_idents: set[str] = set()

    # 3. Open DB session and process rows
    async with async_session_maker() as session:
        historical = force_history or report_date < await _report_horizon(session)
        result["historical"] = historical
        if historical:
            logger.info(f"project_sync: report {report_date} is older than the DB — replaying as history")
        for row_idx, row in df.iterrows():
            try:
                # Extract identifier (required)
                ident_col = next(
                    c for c, f in col_map.items() if f == "project_identifier"
                )
                raw_ident = row[ident_col]
                if pd.isna(raw_ident) or str(raw_ident).strip() == "":
                    continue  # skip rows without identifier

                ident = str(raw_ident).strip()
                if ident in seen_idents:
                    # The file does repeat an identifier now and then; the first
                    # row is the record, a later one must not silently replace it.
                    logger.warning(f"project_sync: duplicate identifier {ident} at row {row_idx} — skipped")
                    continue
                seen_idents.add(ident)

                # Build field dict for all mapped columns
                fields: dict[str, Any] = {}
                for actual_col, model_field in col_map.items():
                    if model_field in ("__weekly__", "project_identifier"):
                        continue
                    val = row[actual_col]
                    if model_field == "estimated_finish_date":
                        fields[model_field], fields["finish_date_text"] = _split_finish_date(val)
                    elif model_field in DATE_FIELDS:
                        fields[model_field] = _parse_date(val)
                    elif model_field in BOOL_FIELDS:
                        fields[model_field] = _parse_bool(val)
                    else:
                        if pd.notna(val):
                            s = str(val).strip()
                            fields[model_field] = s if s else None
                        else:
                            fields[model_field] = None

                # Weekly report: last non-empty value from weekly columns
                weekly_report = _extract_weekly_report(row, weekly_cols)
                fields["weekly_report"] = weekly_report

                # Note: AI brief generation is now deferred to generate_all_briefs() function
                # This keeps the sync loop fast and avoids Groq rate limit bottlenecks

                # Upsert logic
                stmt = select(Project).where(
                    Project.project_identifier == ident
                )
                existing = (await session.execute(stmt)).scalars().first()

                result["processed"] += 1
                if not historical:
                    # A replayed old report only adds history. Its identifiers are
                    # withheld on purpose: the caller deactivates every project
                    # missing from the list, and an old file lacks the new ones.
                    result["identifiers"].append(ident)

                if existing and historical:
                    pass   # the live row belongs to the newest report — untouched
                elif existing:
                    # Only update fields that actually changed
                    changed = False
                    weekly_changed = False
                    for attr, new_val in fields.items():
                        old_val = getattr(existing, attr, None)
                        # Normalise for comparison: treat None and "" as equal
                        old_norm = old_val if old_val not in (None, "") else None
                        new_norm = new_val if new_val not in (None, "") else None
                        if old_norm != new_norm:
                            setattr(existing, attr, new_val)
                            changed = True
                            if attr == "weekly_report":
                                weekly_changed = True
                            # Second brain (Option G): tracked field changes become
                            # temporal memory facts — deterministic, no LLM
                            if attr in memory_service.TRACKED_PROJECT_FIELDS and old_norm is not None:
                                change_facts.append({
                                    "project_id": existing.id,
                                    "identifier": ident,
                                    "name": fields.get("name") or existing.name or ident,
                                    "field": attr,
                                    "old": old_norm,
                                    "new": new_norm,
                                })
                    if not existing.is_active:
                        # Project reappeared in the master file — reactivate
                        existing.is_active = True
                        changed = True
                    if weekly_changed:
                        # Clear stale brief so generate_all_briefs regenerates it
                        existing.weekly_report_brief = None
                    if changed:
                        existing.last_updated = datetime.utcnow()
                        result["updated"] += 1
                        dirty_project_ids.append(existing.id)
                    # else: no-op — last_updated stays as-is
                else:
                    # Create new. A project first seen in an old report is one
                    # that has since left the file — keep it, but not as live.
                    project = Project(project_identifier=ident, **fields)
                    if historical:
                        project.is_active = False
                    session.add(project)
                    result["created"] += 1

                # Commit per row — progress is saved immediately
                await session.commit()
                if not existing and not historical:
                    dirty_project_ids.append(project.id)

                target = existing if existing is not None else project
                await _save_weekly_entries(session, target.id, row, week_dates, report_date)

                # Snapshot of THIS report: from the live row when the report is
                # current, from the file row itself when it is replayed history.
                _snap_source = (
                    SimpleNamespace(**{
                        **dict.fromkeys(_SNAPSHOT_SOURCE_FIELDS),
                        **fields,
                        "id": target.id, "is_active": True, "weekly_report_brief": None,
                        "last_updated": datetime.combine(report_date, datetime.min.time()),
                    })
                    if historical else target
                )
                try:
                    await save_snapshot(_snap_source, session, snapshot_date=report_date)
                    await session.commit()
                except Exception as snap_exc:
                    logger.warning(f"project_sync: snapshot failed for {ident}: {snap_exc}")
                    try:
                        await session.rollback()
                    except Exception:
                        pass

            except Exception as exc:
                logger.error(f"project_sync: row {row_idx} error: {exc}")
                result["errors"].append(f"שגיאה בשורה {row_idx}: {exc}")
                try:
                    await session.rollback()
                except Exception:
                    pass

    logger.info(
        f"project_sync complete: {result['processed']} rows, "
        f"{result['created']} created, {result['updated']} updated, "
        f"{len(result['errors'])} errors"
    )

    # Second brain: write snapshot-diff memory facts (deterministic, no LLM)
    if change_facts:
        asyncio.create_task(memory_service.record_project_changes(change_facts))

    # Second brain: mark changed/new projects' dossiers for drip regeneration
    if dirty_project_ids:
        from app.services.dossier_service import mark_dirty
        asyncio.create_task(mark_dirty(dirty_project_ids))

    if historical:
        # History changes no live row — nothing to brief, nothing to report.
        return result

    # Spawn brief generation as a background task (don't wait for it)
    asyncio.create_task(generate_all_briefs())

    # Trigger project reports for all enabled-schedule users after sync
    asyncio.create_task(_trigger_reports_after_sync())

    return result


# ── History helpers (PLAN.md P0) ──────────────────────────────────────────

# Snapshots written before P0 are stamped with the UPLOAD day, which runs a few
# days ahead of the report date (and further when a file is re-uploaded late).
# Until report-dated history exists, only a report this far behind them counts
# as old — otherwise the next genuine weekly file could be mistaken for history.
_LEGACY_SNAPSHOT_SLACK = timedelta(days=14)

# Every Project attribute save_snapshot reads — a replayed file row may lack
# some of these columns, and a missing one must read as None, not crash.
_SNAPSHOT_SOURCE_FIELDS = (
    "name", "project_type", "stage", "manager", "weekly_report", "risks", "to_handle",
    "dev_plan_date", "estimated_finish_date", "finish_date_text", "controller",
    "short_supervisors", "short_testers", "critical_tier",
)


async def _report_horizon(session) -> date:
    """Newest report date the DB already reflects. A file older than this is
    replayed as history instead of overwriting the live project rows."""
    newest_report = await session.scalar(select(func.max(ProjectWeeklyEntry.source_report_date)))
    if newest_report:
        return newest_report
    newest_snapshot = await session.scalar(select(func.max(ProjectSnapshot.snapshot_date)))
    if newest_snapshot:
        return newest_snapshot - _LEGACY_SNAPSHOT_SLACK
    return date.min


async def _save_weekly_entries(session, project_id: int, row: pd.Series,
                               week_dates: dict[str, Any], report_date) -> None:
    """Upsert every dated `פירוט שבועי` cell of this row. A row already written
    by a NEWER report is left alone — that report may have corrected it."""
    rows: dict[Any, dict] = {}   # by week — Postgres rejects one INSERT touching a key twice
    for col, week_date in week_dates.items():   # left→right, so the right-most column wins
        if week_date is None or week_date > report_date + timedelta(days=7):
            continue
        val = row[col]
        if pd.isna(val):
            continue
        text = str(val).strip()
        if not text or text.lower() == "nan":
            continue
        rows[week_date] = dict(project_id=project_id, week_date=week_date, text=text,
                               text_hash=_text_hash(text), source_report_date=report_date)
    if not rows:
        return
    stmt = pg_insert(ProjectWeeklyEntry).values(list(rows.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "week_date"],
        set_={"text": stmt.excluded.text, "text_hash": stmt.excluded.text_hash,
              "source_report_date": stmt.excluded.source_report_date,
              "updated_at": datetime.utcnow()},
        where=ProjectWeeklyEntry.source_report_date <= stmt.excluded.source_report_date,
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except Exception as exc:
        logger.warning(f"project_sync: weekly history failed for project {project_id}: {exc}")
        await session.rollback()


async def _trigger_reports_after_sync() -> None:
    """Fire-and-forget: send project reports to all enabled-schedule users after file sync."""
    try:
        from datetime import timedelta
        from app.models import ProjectReportSchedule, User
        from app.services.project_report_service import auto_send_project_report
        from app.services.telegram_polling import telegram_bot
        from sqlalchemy import select as _select

        bot = (telegram_bot.application.bot
               if telegram_bot and telegram_bot.application and telegram_bot.application.bot
               else None)

        async with async_session_maker() as session:
            schedules = (await session.execute(
                _select(ProjectReportSchedule).where(ProjectReportSchedule.enabled == True)
            )).scalars().all()

            for sched in schedules:
                if sched.last_sent_at and (datetime.utcnow() - sched.last_sent_at) < timedelta(minutes=30):
                    logger.info(f"project_sync: skipping report for user {sched.user_id} — sent recently")
                    continue
                user = await session.get(User, sched.user_id)
                if not user or not user.telegram_id:
                    continue
                logger.info(f"project_sync: triggering report for user {user.id} after file upload")
                ok = await auto_send_project_report(user, session, bot)
                if ok:
                    sched.last_sent_at = datetime.utcnow()
                    await session.commit()
    except Exception as exc:
        logger.error(f"_trigger_reports_after_sync failed: {exc}")


# ── Brief generation (async, per-row commit) ──────────────────────────────

async def generate_all_briefs() -> None:
    """
    Generate AI briefs for all projects that have a weekly_report but no brief yet.
    Commits per row — safe to interrupt and resume.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(Project).where(
                Project.weekly_report.isnot(None),
                Project.weekly_report_brief.is_(None)
            )
        )
        projects = result.scalars().all()

        # Also re-generate briefs that still carry LLM meta-text wrappers
        bad_result = await session.execute(
            select(Project).where(
                Project.weekly_report_brief.isnot(None)
            )
        )
        for p in bad_result.scalars().all():
            brief_lower = (p.weekly_report_brief or "").lower()
            if p.weekly_report_brief and (
                any(brief_lower.startswith(pfx) for pfx in _BRIEF_BAD_PREFIXES + ["here is", "here's"])
                or bool(_BRIEF_ENGLISH_PREFIX_RE.match(p.weekly_report_brief))
                or any(eng in brief_lower for eng in ["brief hebrew", "even more concise", "concise version"])
            ):
                p.weekly_report_brief = None
                projects = list(projects) + [p]
        await session.commit()

    logger.info(f"generate_all_briefs: {len(projects)} projects to process")

    for project in projects:
        brief = await _generate_weekly_brief(project.weekly_report)
        if brief:
            async with async_session_maker() as session:
                stmt = select(Project).where(Project.id == project.id)
                p = (await session.execute(stmt)).scalars().first()
                if p:
                    p.weekly_report_brief = brief
                    await session.commit()
                    logger.info(f"generate_all_briefs: saved brief for {p.project_identifier}")

    logger.info("generate_all_briefs: complete")


# ── Backfill (PLAN.md P1) ─────────────────────────────────────────────────

MASTER_SHEET_MARKER = "עדכני"   # "דוח שבועי עדכני" — the sheet that is the record


def pick_master_sheet(file_path: str) -> str | None:
    """The sheet of a master workbook that holds the weekly report.

    Newer files open with a one-cell "מאקרו1" sheet, so "the first sheet" synced
    nothing. Prefer the "…עדכני" sheet; else the first non-draft sheet with a
    זיהוי header; else None (first sheet — the old behaviour, and CSV).
    """
    if Path(file_path).suffix.lower() not in (".xlsx", ".xls"):
        return None
    try:
        names = pd.ExcelFile(file_path, engine="openpyxl").sheet_names
    except Exception:
        return None
    for n in names:
        if MASTER_SHEET_MARKER in n and DRAFT_SHEET_MARKER not in n:
            return n
    for n in names:
        if DRAFT_SHEET_MARKER in n:
            continue
        df = _read_file(file_path, n)
        if any(KNOWN_COLUMNS.get(c) == "project_identifier" for c in df.columns):
            return n
    return None


def _file_report_date(file_path: str):
    """Report date of a file before syncing it — used only to order a backfill."""
    sheet = pick_master_sheet(file_path)
    df = _read_file(file_path, sheet)
    return resolve_report_date(file_path, [c for c in df.columns if WEEKLY_REPORT_MARKER in c])


# Last backfill run, for the admin status poll. One process, one run at a time.
BACKFILL_STATUS: dict[str, Any] = {"running": False, "files": [], "started_at": None, "finished_at": None}


async def backfill_files(paths: list[tuple[str, str]]) -> None:
    """Replay historical master files, oldest report first, as history only.

    paths: (stored path, original filename). Every file is forced into history
    mode: live project rows are never touched, no identifiers are returned (so
    nothing is deactivated), and no briefs or project reports are sent.
    """
    loop = asyncio.get_event_loop()
    BACKFILL_STATUS.update(running=True, files=[], started_at=datetime.utcnow().isoformat(),
                           finished_at=None)
    try:
        dated = []
        for path, name in paths:
            try:
                d = await loop.run_in_executor(None, _file_report_date, path)
            except Exception as exc:
                BACKFILL_STATUS["files"].append({"name": name, "status": "error", "error": str(exc)})
                continue
            dated.append((d, path, name))
        dated.sort(key=lambda t: t[0])
        # List every file up front, so the page shows "N files, k done" rather
        # than only the one being loaded right now.
        queue = [{"name": name, "report_date": d.isoformat(), "status": "queued"}
                 for d, _path, name in dated]
        BACKFILL_STATUS["files"].extend(queue)
        for (d, path, name), entry in zip(dated, queue):
            entry["status"] = "running"
            try:
                r = await sync_projects_file(path, force_history=True)
                entry.update(status="done" if not r["errors"] else "done_with_errors",
                             processed=r["processed"], created=r["created"],
                             errors=r["errors"][:5])
            except Exception as exc:
                logger.error(f"backfill: {name} failed: {exc}")
                entry.update(status="error", error=str(exc))
    finally:
        BACKFILL_STATUS.update(running=False, finished_at=datetime.utcnow().isoformat())
