"""Project sync service — parses uploaded XLSX/CSV master file and upserts Project records."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from rapidfuzz import process as rf_process
from sqlalchemy import select

from app.database import async_session_maker
from app.models import Project
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)

# ── Column name mapping: Hebrew header → model field ──────────────────────
KNOWN_COLUMNS: dict[str, str] = {
    "זיהוי":                           "project_identifier",
    "שם פרויקט":                       "name",
    "שם הפרויקט":                      "name",
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
}

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
            # Probe first two rows to detect header row offset
            kw = dict(sheet_name=sheet_name) if sheet_name else {}
            probe = pd.read_excel(path, engine="openpyxl", nrows=2, header=None, **kw)
            header_row = 1 if (len(probe) > 0 and probe.iloc[0].isna().all()) else 0
            df = pd.read_excel(path, engine="openpyxl", header=header_row, **kw)
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

        # Fuzzy match against known column keys
        result = rf_process.extractOne(
            actual_col,
            KNOWN_COLUMNS.keys(),
            score_cutoff=FUZZY_CUTOFF,
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

def _parse_date(val: Any):
    """Parse a cell value as a date. Returns datetime.date or None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        parsed = pd.to_datetime(val, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


# ── AI Briefing generation ────────────────────────────────────────────────

async def _generate_weekly_brief(weekly_report: str | None) -> str | None:
    """
    Generate a short 1-2 sentence AI briefing from the weekly report.
    Returns the brief (max 500 chars) or None if generation fails/no input.
    """
    if not weekly_report or not weekly_report.strip():
        return None

    try:
        # Truncate very long reports to avoid token limits
        report_preview = weekly_report[:1000] if len(weekly_report) > 1000 else weekly_report

        prompt = (
            f"Generate a VERY brief (50-80 characters max) Hebrew summary of this project update. "
            f"Use 1-2 sentences. Be concise and actionable:\n\n{report_preview}"
        )
        messages = [{"role": "user", "content": prompt}]
        brief = await llm_chat("project_brief", messages, max_tokens=150, temperature=0.2)

        if not brief or not brief.strip():
            return None

        # Truncate to 500 chars to fit in DB column
        if len(brief) > 500:
            brief = brief[:497] + "..."

        logger.info(f"Generated brief ({len(brief)} chars): {brief[:80]}...")
        return brief.strip()
    except Exception as exc:
        logger.warning(f"Brief generation failed (will use truncated text): {exc}")
        return None


# ── Main async entry point ────────────────────────────────────────────────

async def sync_projects_file(file_path: str, sheet_name: str | None = None) -> dict:
    """
    Parse project master file and upsert Project records to DB.

    Called as a BackgroundTasks callback — creates its own async session.
    sheet_name: specific sheet to read (detected by process_master_file); None = first sheet.
    Returns result dict: {"processed": N, "created": N, "updated": N, "errors": [...]}
    """
    result = {"processed": 0, "created": 0, "updated": 0, "errors": []}

    # 1. Read file in executor thread (pandas is synchronous/blocking)
    loop = asyncio.get_event_loop()
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

    # 3. Open DB session and process rows
    async with async_session_maker() as session:
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

                # Build field dict for all mapped columns
                fields: dict[str, Any] = {}
                for actual_col, model_field in col_map.items():
                    if model_field in ("__weekly__", "project_identifier"):
                        continue
                    val = row[actual_col]
                    if model_field in ("dev_plan_date", "estimated_finish_date"):
                        fields[model_field] = _parse_date(val)
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

                if existing:
                    # Only update fields that actually changed
                    changed = False
                    for attr, new_val in fields.items():
                        old_val = getattr(existing, attr, None)
                        # Normalise for comparison: treat None and "" as equal
                        old_norm = old_val if old_val not in (None, "") else None
                        new_norm = new_val if new_val not in (None, "") else None
                        if old_norm != new_norm:
                            setattr(existing, attr, new_val)
                            changed = True
                    if changed:
                        existing.last_updated = datetime.utcnow()
                        result["updated"] += 1
                    # else: no-op — last_updated stays as-is
                else:
                    # Create new
                    project = Project(project_identifier=ident, **fields)
                    session.add(project)
                    result["created"] += 1

                # Commit per row — progress is saved immediately
                await session.commit()

            except Exception as exc:
                logger.error(f"project_sync: row {row_idx} error: {exc}")
                result["errors"].append(f"שגיאה בשורה {row_idx}: {exc}")

    logger.info(
        f"project_sync complete: {result['processed']} rows, "
        f"{result['created']} created, {result['updated']} updated, "
        f"{len(result['errors'])} errors"
    )

    # Spawn brief generation as a background task (don't wait for it)
    asyncio.create_task(generate_all_briefs())

    return result


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
