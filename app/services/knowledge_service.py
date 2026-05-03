"""Knowledge base service — file ingestion, chunking, embedding, and RAG search."""

import asyncio
import logging
import re
import time
import unicodedata
from contextvars import ContextVar
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.models import KnowledgeFile, KnowledgeChunk
from app.services.embedding_service import embed
from app.database import async_session_maker

logger = logging.getLogger(__name__)

# ─── Eval-loop shadow ContextVars (set by eval_verifier_service.shadow_config) ──
# When non-empty, they OVERRIDE the production config for the current async task.
# Defaults are empty so production paths are unaffected when no shadow is active.
_shadow_abbrevs: ContextVar[dict] = ContextVar("shadow_abbrevs", default={})
_shadow_stop_word_drops: ContextVar[set] = ContextVar("shadow_stop_word_drops", default=set())
_shadow_synonyms: ContextVar[dict] = ContextVar("shadow_synonyms", default={})
_shadow_prompt_override: ContextVar[dict] = ContextVar("shadow_prompt_override", default={})

# ─── DB-backed config caches (refreshed lazily by _ensure_eval_caches) ─────────
_DB_ABBREVS_CACHE: dict[str, str] = {}
_DB_STOP_WORD_DROPS_CACHE: set[str] = set()
_DB_PROMPT_OVERRIDES_CACHE: dict[str, str] = {}   # usage -> content
_EVAL_CACHE_TS: float = 0.0
_EVAL_CACHE_TTL: float = 30.0  # seconds, mirrors llm_router._cache

UPLOAD_DIR = Path("uploads")

# ─── Hebrew Normalization ───────────────────────────────────────────────────

_FINAL_LETTERS = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
_HEBREW_PREFIXES = frozenset("והבכלמש")


def normalize_hebrew(text: str) -> str:
    """Normalize Hebrew text for fuzzy matching:
    - Strip nikud (vowel marks U+05B0–U+05C7)
    - Convert final letters (ך,ם,ן,ף,ץ) to standard forms
    - Lowercase
    """
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u05B0-\u05C7]", "", text)
    return "".join(_FINAL_LETTERS.get(c, c) for c in text).lower()


def _word_forms(word: str) -> set[str]:
    """Return normalized forms of a word: with and without common Hebrew prefix."""
    norm = normalize_hebrew(word)
    forms = {norm}
    if len(norm) > 2 and norm[0] in _HEBREW_PREFIXES:
        forms.add(norm[1:])  # strip single-letter prefix (ו,ה,ב,כ,ל,מ,ש)
    return forms


def _question_word_forms(text: str) -> set[str]:
    """Build a set of all word forms (with/without prefix) from a question string."""
    forms: set[str] = set()
    for word in text.split():
        forms |= _word_forms(word)
    return forms
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100

# ─── Hebrew Abbreviation Expansion ───
# BEGIN_AUTOGEN_ABBREVS
HEBREW_ABBREVS = {
    "פ": "מנהל פרויקט",
    "מ": "מנהל פרויקט",
    "ב": "תכנון ובנייה",
    "חישמול": "חשמול",
    "חישמל": "חשמול",
}
# END_AUTOGEN_ABBREVS


async def _ensure_eval_caches(session: AsyncSession | None = None) -> None:
    """Refresh DB-backed config caches if stale. Cheap on cache-hit (single timestamp check).

    Always uses its OWN session — a missing-table error here must not poison the
    caller's transaction (asyncpg propagates InFailedSQLTransactionError otherwise).
    """
    global _EVAL_CACHE_TS, _DB_ABBREVS_CACHE, _DB_STOP_WORD_DROPS_CACHE, _DB_PROMPT_OVERRIDES_CACHE

    now = time.monotonic()
    if now - _EVAL_CACHE_TS < _EVAL_CACHE_TTL:
        return
    # Mark fresh up-front so a transient failure doesn't trigger a hot-loop of retries
    _EVAL_CACHE_TS = now

    try:
        from app.models import QuerySynonym, PromptOverride

        async with async_session_maker() as own_session:
            sent_stmt = select(QuerySynonym).where(
                QuerySynonym.original.in_(["__hebrew_abbrevs__", "__stop_word_drops__"])
            )
            sent_rows = (await own_session.execute(sent_stmt)).scalars().all()

            new_abbrevs: dict[str, str] = {}
            new_drops: set[str] = set()
            for row in sent_rows:
                if row.original == "__hebrew_abbrevs__":
                    # synonyms stored as ["k=v", "k=v", ...] for portability
                    for entry in (row.synonyms or []):
                        if isinstance(entry, str) and "=" in entry:
                            k, v = entry.split("=", 1)
                            new_abbrevs[k.strip()] = v.strip()
                        elif isinstance(entry, dict) and "k" in entry and "v" in entry:
                            new_abbrevs[entry["k"]] = entry["v"]
                elif row.original == "__stop_word_drops__":
                    new_drops = {s for s in (row.synonyms or []) if isinstance(s, str)}

            po_stmt = select(PromptOverride).where(PromptOverride.active.is_(True))
            po_rows = (await own_session.execute(po_stmt)).scalars().all()
            new_overrides = {row.usage: row.content for row in po_rows}

        _DB_ABBREVS_CACHE = new_abbrevs
        _DB_STOP_WORD_DROPS_CACHE = new_drops
        _DB_PROMPT_OVERRIDES_CACHE = new_overrides
    except Exception as e:
        # Common before first deploy: prompt_overrides table doesn't exist yet.
        # Keep previous cache values; do not raise — caller's transaction must stay alive.
        logger.debug(f"_ensure_eval_caches: skipped ({type(e).__name__}: {e})")


def _expand_hebrew_abbrevs(text: str) -> str:
    """Expand common Hebrew abbreviations before search/embedding.

    Effective dict = static HEBREW_ABBREVS | DB-backed cache | shadow override.
    Caller must have awaited _ensure_eval_caches(session) earlier in the async task.
    """
    effective = {**HEBREW_ABBREVS, **_DB_ABBREVS_CACHE, **_shadow_abbrevs.get()}
    for abbrev, full in effective.items():
        text = text.replace(abbrev, full)
    return text

# ─── Weekly Report Column Mapping ───
WEEKLY_REPORT_COLUMNS = {
    'project_name': ['project', 'שם הפרויקט', 'שם פרויקט', 'פרויקט', 'Project Name'],
    'wbs': ['wbs', 'זיהוי', 'קוד wbs', 'וביס', 'WBS ID'],
    'manager': ['manager', 'מנהל', 'מנה"פ', 'מנהל פרויקט', 'Manager'],
    'target_date': ['target', 'יעד', 'תאריך יעד', 'תאריך תכנון', 'Target Date'],
    'status': ['status', 'סטטוס', 'מצב', 'עדכון', 'Status', 'סטטוס'],
    'barrier': ['barrier', 'חסם', 'בעיה', 'סוגיה', 'מחסום', 'Barrier'],
}


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _find_context_columns(df) -> tuple[int, int]:
    """Find PROJECT and WBS column indices by keyword matching.
    Returns (project_col_idx, wbs_col_idx) or (None, None) if not found.
    """

    project_col_idx = None
    wbs_col_idx = None

    for i, col in enumerate(df.columns):
        col_lower = str(col).lower().strip()

        if project_col_idx is None and any(x in col_lower for x in ['project', 'שם', 'פרויקט']):
            project_col_idx = i

        if wbs_col_idx is None and any(x in col_lower for x in ['wbs', 'זיהוי', 'קוד']):
            wbs_col_idx = i

    return (project_col_idx, wbs_col_idx)


def _greedy_row_parser(row, col_indices: dict, col_names: list | None = None) -> str:
    """Greedy parser: capture all meaningful data from a row, prioritizing project/wbs.

    Returns a chunk like:
    PROJECT: Name | WBS: ID | ColumnHeader: Value | ...
    """
    import pandas as pd

    chunk_parts = []

    # 1. Extract PROJECT and WBS if they exist
    if 'project_idx' in col_indices and col_indices['project_idx'] is not None:
        project_val = row.iloc[col_indices['project_idx']]
        if pd.notna(project_val):
            project_str = str(project_val).strip()
            if project_str:
                chunk_parts.append(f"PROJECT: {project_str}")

    if 'wbs_idx' in col_indices and col_indices['wbs_idx'] is not None:
        wbs_val = row.iloc[col_indices['wbs_idx']]
        if pd.notna(wbs_val):
            wbs_str = str(wbs_val).strip()
            if wbs_str:
                chunk_parts.append(f"WBS: {wbs_str}")

    # 2. Capture all other non-empty values, labeled with their column header
    for i, val in enumerate(row):
        # Skip if this is project or wbs column (already added)
        if (col_indices.get('project_idx') == i) or (col_indices.get('wbs_idx') == i):
            continue

        if pd.notna(val):
            val_str = str(val).strip()
            # Include all non-empty values that aren't unnamed columns
            if val_str and not val_str.startswith('Unnamed') and val_str != 'nan':
                col_label = str(col_names[i]).strip() if col_names and i < len(col_names) else ""
                if col_label and not col_label.startswith('Unnamed'):
                    chunk_parts.append(f"{col_label}: {val_str}")
                elif len(val_str) > 5 and not val_str.isdigit():
                    chunk_parts.append(val_str)

    return " | ".join(chunk_parts) if chunk_parts else None


def extract_text(file_path: str, file_type: str) -> str:
    """Extract plain text from PDF, DOCX, or XLSX file."""
    path = Path(file_path)

    if file_type == "pdf":
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)

    elif file_type == "docx":
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    elif file_type == "xlsx":
        import pandas as pd
        try:
            # Read Excel with UTF-8 encoding
            xls = pd.ExcelFile(path, engine='openpyxl')
            rows = []

            for sheet_name in xls.sheet_names:
                # Try to detect if headers are in row 2 (skip row 0)
                try:
                    df_test = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl', nrows=2, header=None)
                    if len(df_test) > 0 and df_test.iloc[0].isna().all():
                        # Row 0 is empty, use row 1 as header
                        df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl', header=1)
                    else:
                        df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
                except Exception:
                    df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')

                # ─── GREEDY ROW PARSER (v2) ───
                # Find PROJECT and WBS column indices
                project_idx, wbs_idx = _find_context_columns(df)

                col_indices = {
                    'project_idx': project_idx,
                    'wbs_idx': wbs_idx,
                }
                col_names = list(df.columns)

                # Process each row with greedy parser (labeled with column headers)
                for row_idx, row in df.iterrows():
                    chunk = _greedy_row_parser(row, col_indices, col_names=col_names)
                    if chunk:
                        rows.append(chunk)


            return "\n".join(rows)
        except Exception as e:
            logger.warning(f"Failed to read XLSX with pandas, falling back: {e}")
            # Fallback to basic openpyxl
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            rows = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    row_text = " | ".join(str(c) for c in row if c is not None)
                    if row_text.strip():
                        rows.append(row_text)
            return "\n".join(rows)

    elif file_type == "csv":
        import csv
        rows = []
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Filter meaningful columns
                    row_data = {}
                    for key, val in row.items():
                        if val and str(val).strip():
                            row_data[key] = str(val).strip()
                    if row_data:
                        row_text = " | ".join(f"{k}: {v}" for k, v in row_data.items())
                        rows.append(row_text)
            return "\n".join(rows)
        except Exception as e:
            logger.warning(f"CSV read failed: {e}")
            return ""

    return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


# ---------------------------------------------------------------------------
# AI summary (one-liner via Groq)
# ---------------------------------------------------------------------------

async def _generate_summary(text_snippet: str, filename: str) -> str:
    """Generate a short Hebrew summary of the file using Groq."""
    try:
        from app.services.llm_router import llm_chat
        snippet = text_snippet[:2000]
        return await llm_chat("file_summary",
            messages=[
                {
                    "role": "system",
                    "content": "סכם את תוכן המסמך בעברית במשפט אחד קצר (עד 20 מילים).",
                },
                {
                    "role": "user",
                    "content": f"שם קובץ: {filename}\n\nתוכן:\n{snippet}",
                },
            ],
            max_tokens=80,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
        return filename


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

def _extract_text_smart(file_path: str, file_type: str, original_name: str) -> str:
    """Smart extraction: adds file-context prefix to every chunk + merged-cell backfill for xlsx."""
    import pandas as pd

    path = Path(file_path)
    file_label = original_name.replace("_", " ").rsplit(".", 1)[0]

    if file_type == "xlsx":
        try:
            xls = pd.ExcelFile(path, engine="openpyxl")
            rows = []

            for sheet_name in xls.sheet_names:
                context_prefix = f"[מקור: {file_label} | גיליון: {sheet_name}]"
                try:
                    df_test = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", nrows=2, header=None)
                    if len(df_test) > 0 and df_test.iloc[0].isna().all():
                        df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", header=1)
                    else:
                        df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
                except Exception:
                    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")

                project_idx, wbs_idx = _find_context_columns(df)
                col_indices = {"project_idx": project_idx, "wbs_idx": wbs_idx}
                col_names = list(df.columns)

                # ── Merged-cell backfill: track last seen PROJECT and WBS ──
                last_project: str | None = None
                last_wbs: str | None = None

                for _, row in df.iterrows():
                    # Check if PROJECT field is empty in this row
                    if project_idx is not None:
                        proj_val = row.iloc[project_idx]
                        if pd.notna(proj_val) and str(proj_val).strip():
                            last_project = str(proj_val).strip()
                        elif last_project:
                            # Backfill: inject last known project into this row
                            row = row.copy()
                            row.iloc[project_idx] = last_project

                    if wbs_idx is not None:
                        wbs_val = row.iloc[wbs_idx]
                        if pd.notna(wbs_val) and str(wbs_val).strip():
                            last_wbs = str(wbs_val).strip()
                        elif last_wbs:
                            row = row.copy()
                            row.iloc[wbs_idx] = last_wbs

                    chunk = _greedy_row_parser(row, col_indices, col_names=col_names)
                    if chunk:
                        rows.append(f"{context_prefix} {chunk}")

            return "\n".join(rows)

        except Exception as e:
            logger.warning(f"Smart xlsx extraction failed, falling back: {e}")
            return extract_text(file_path, file_type)

    elif file_type in ("pdf", "docx"):
        # For text files: use standard extraction but with 25% overlap (handled in process)
        return extract_text(file_path, file_type)

    return extract_text(file_path, file_type)


# ---------------------------------------------------------------------------
# Master File ETL Helpers (Phase 2)
# ---------------------------------------------------------------------------

def _find_weekly_report_sheet(path, xls):
    """Find the primary weekly-report sheet. Returns (sheet_name, df_raw) or (None, None)."""
    import pandas as pd
    WEEKLY_KEYWORDS = ['דוח שבועי', 'שבועי', 'weekly', 'עדכני']
    for sheet_name in xls.sheet_names:
        if any(kw in sheet_name.lower() for kw in WEEKLY_KEYWORDS):
            try:
                df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl', header=None)
                return sheet_name, df
            except Exception:
                continue
    # Fallback: scan first 5 sheets for keyword in cell content
    for sheet_name in xls.sheet_names[:5]:
        try:
            df_peek = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl', nrows=5, header=None)
            flat = ' '.join(str(v) for v in df_peek.values.flatten() if v is not None)
            if any(kw in flat for kw in WEEKLY_KEYWORDS):
                df_full = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl', header=None)
                return sheet_name, df_full
        except Exception:
            continue
    # Final fallback: first sheet
    if xls.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=xls.sheet_names[0], engine='openpyxl', header=None)
            return xls.sheet_names[0], df
        except Exception:
            pass
    return None, None


def _find_header_row(df_raw) -> int:
    """Detect which row index is the actual header (most non-null cells)."""
    best_row, best_count = 0, 0
    for i in range(min(6, len(df_raw))):
        non_null = df_raw.iloc[i].notna().sum()
        if non_null > best_count:
            best_count = non_null
            best_row = i
    return best_row


def _find_latest_weekly_detail_col(df):
    """Return (col_name, col_index) of the most recent weekly-update column."""
    date_pattern = re.compile(r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}')
    update_keywords = ['עדכון', 'פירוט', 'weekly', 'detail', 'update', 'שבועי']
    date_cols, update_cols = [], []
    for i, col in enumerate(df.columns):
        col_str = str(col).strip()
        if date_pattern.search(col_str):
            date_cols.append((i, col_str))
        elif any(kw in col_str.lower() for kw in update_keywords):
            update_cols.append((i, col_str))
    if date_cols:
        idx, name = date_cols[-1]
        return name, idx
    if update_cols:
        idx, name = update_cols[-1]
        return name, idx
    return None, None


def _find_status_col(df):
    """Return (col_name, col_index) of the status column."""
    STATUS_KEYWORDS = ['סטטוס', 'מצב', 'status', 'עדכון סטטוס']
    for i, col in enumerate(df.columns):
        if any(kw in str(col).lower().strip() for kw in STATUS_KEYWORDS):
            return str(col), i
    return None, None


def _find_manager_col(df):
    """Return (col_name, col_index) of the project manager column."""
    MANAGER_KEYWORDS = ['מנהל', 'מנה"פ', 'מנהל פרויקט', 'manager', "פ''מ", 'פ"מ']
    for i, col in enumerate(df.columns):
        if any(kw in str(col).lower().strip() for kw in MANAGER_KEYWORDS):
            return str(col), i
    return None, None


def _normalize_date(val) -> str:
    """Standardize a date cell value to DD/MM/YYYY string."""
    import pandas as pd
    from datetime import datetime as _dt
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    if isinstance(val, _dt):
        return val.strftime("%d/%m/%Y")
    val_str = str(val).strip()
    if re.match(r'^\d{2}/\d{2}/\d{4}$', val_str):
        return val_str
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d.%m.%Y', '%m/%d/%Y'):
        try:
            return _dt.strptime(val_str, fmt).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return val_str


async def process_master_file(file_id: int) -> None:
    """Master ETL: specialized processing for the is_master XLSX file.

    Pipeline:
    1. Find the primary weekly-report sheet
    2. Detect header row; backfill merged cells for Project/WBS columns
    3. Identify Manager, Status, and latest weekly-detail columns
    4. Transform each row into a structured 'Project Block' chunk
    5. Process secondary sheets normally, prefixed with SECONDARY_TAB
    6. Embed and store all chunks
    """
    import pandas as pd
    from sqlalchemy import delete as _delete

    async with async_session_maker() as session:
        kf = await session.get(KnowledgeFile, file_id)
        if not kf:
            return
        try:
            path = Path(kf.file_path)

            # Clear existing chunks
            await session.execute(_delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))
            kf.status = "processing"
            kf.chunk_count = 0
            await session.commit()

            # Load workbook
            xls = await asyncio.get_event_loop().run_in_executor(
                None, lambda: pd.ExcelFile(path, engine='openpyxl')
            )

            # Find primary weekly report sheet
            primary_sheet_name, df_raw = await asyncio.get_event_loop().run_in_executor(
                None, _find_weekly_report_sheet, path, xls
            )

            all_chunks: list[str] = []

            if primary_sheet_name is not None and df_raw is not None:
                header_row_idx = _find_header_row(df_raw)
                df = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: pd.read_excel(path, sheet_name=primary_sheet_name,
                                          engine='openpyxl', header=header_row_idx)
                )

                project_idx, wbs_idx = _find_context_columns(df)
                _, manager_idx = _find_manager_col(df)
                _, status_idx = _find_status_col(df)
                _, weekly_idx = _find_latest_weekly_detail_col(df)

                last_project: str | None = None
                last_wbs: str | None = None

                for _, row in df.iterrows():
                    row = row.copy()

                    # Backfill merged cells
                    if project_idx is not None:
                        pv = row.iloc[project_idx]
                        if pd.notna(pv) and str(pv).strip() and str(pv).strip().lower() not in ('nan', ''):
                            last_project = str(pv).strip()
                        elif last_project:
                            row.iloc[project_idx] = last_project

                    if wbs_idx is not None:
                        wv = row.iloc[wbs_idx]
                        if pd.notna(wv) and str(wv).strip() and str(wv).strip().lower() not in ('nan', ''):
                            last_wbs = str(wv).strip()
                        elif last_wbs:
                            row.iloc[wbs_idx] = last_wbs

                    def _cell(idx):
                        if idx is None:
                            return ""
                        v = row.iloc[idx]
                        return str(v).strip() if pd.notna(v) and str(v).strip() and str(v).strip().lower() != 'nan' else ""

                    project_name = _cell(project_idx)
                    wbs_val      = _cell(wbs_idx)
                    manager_val  = _cell(manager_idx)
                    status_val   = _cell(status_idx)
                    weekly_val   = _cell(weekly_idx)

                    # Skip empty rows and header-echo rows
                    if not project_name and not wbs_val:
                        continue
                    if project_name.lower() in ('project', 'שם פרויקט', 'פרויקט'):
                        continue

                    # Build core fields
                    core_parts = [
                        f"🏗️ Project: {project_name}",
                        f"WBS: {wbs_val}",
                        f"Manager: {manager_val}",
                        f"Status: {status_val}",
                        f"Update: {weekly_val}",
                    ]

                    # Collect indices that are already included in core fields
                    _included_idxs = {idx for idx in [project_idx, wbs_idx, manager_idx, status_idx, weekly_idx] if idx is not None}

                    # Append all other non-empty labeled columns
                    extra_parts = []
                    for col_i, col_name in enumerate(df.columns):
                        if col_i in _included_idxs:
                            continue
                        col_label = str(col_name).strip()
                        if not col_label or col_label.startswith('Unnamed') or col_label.lower() == 'nan':
                            continue
                        cell_val = row.iloc[col_i]
                        if pd.isna(cell_val):
                            continue
                        cell_str = str(cell_val).strip()
                        if not cell_str or cell_str.lower() in ('nan', '—', '-', ''):
                            continue
                        extra_parts.append(f"{col_label}: {cell_str}")

                    chunk = " | ".join(core_parts + extra_parts) if core_parts else None
                    if chunk:
                        all_chunks.append(chunk)

            # Process secondary sheets normally
            secondary_sheets = [s for s in xls.sheet_names if s != primary_sheet_name]
            for sheet_name in secondary_sheets:
                try:
                    df_sec = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda sn=sheet_name: pd.read_excel(path, sheet_name=sn, engine='openpyxl')
                    )
                    proj_idx_s, wbs_idx_s = _find_context_columns(df_sec)
                    col_indices_s = {'project_idx': proj_idx_s, 'wbs_idx': wbs_idx_s}
                    col_names_s = list(df_sec.columns)
                    last_p: str | None = None
                    last_w: str | None = None
                    for _, row in df_sec.iterrows():
                        row = row.copy()
                        if proj_idx_s is not None:
                            pv = row.iloc[proj_idx_s]
                            if pd.notna(pv) and str(pv).strip() and str(pv).strip().lower() != 'nan':
                                last_p = str(pv).strip()
                            elif last_p:
                                row.iloc[proj_idx_s] = last_p
                        if wbs_idx_s is not None:
                            wv = row.iloc[wbs_idx_s]
                            if pd.notna(wv) and str(wv).strip() and str(wv).strip().lower() != 'nan':
                                last_w = str(wv).strip()
                            elif last_w:
                                row.iloc[wbs_idx_s] = last_w
                        chunk_text_raw = _greedy_row_parser(row, col_indices_s, col_names=col_names_s)
                        if chunk_text_raw:
                            all_chunks.append(f"SECONDARY_TAB:{sheet_name} | {chunk_text_raw}")
                except Exception as e:
                    logger.warning(f"process_master_file: secondary sheet '{sheet_name}' failed: {e}")
                    continue

            if not all_chunks:
                kf.status = "error"
                kf.summary = "לא נמצא תוכן בקובץ ה-Master"
                await session.commit()
                return

            kf.summary = await _generate_summary(all_chunks[0][:2000], kf.original_name)

            for idx, chunk_content in enumerate(all_chunks):
                vector = await embed(chunk_content)
                session.add(KnowledgeChunk(
                    file_id=file_id,
                    chunk_idx=idx,
                    content=chunk_content,
                    embedding=vector,
                ))

            kf.chunk_count = len(all_chunks)
            kf.status = "ready"
            await session.commit()
            logger.info(f"process_master_file {file_id}: {len(all_chunks)} project-block chunks stored")

            # Sync to Projects table so the Projects tab reflects the master file
            try:
                from app.services.project_sync import sync_projects_file
                sync_result = await sync_projects_file(str(path), sheet_name=primary_sheet_name)
                logger.info(
                    f"process_master_file {file_id}: project sync complete — "
                    f"{sync_result['processed']} rows, "
                    f"{sync_result['created']} created, "
                    f"{sync_result['updated']} updated, "
                    f"{len(sync_result['errors'])} errors"
                )
            except Exception as sync_err:
                logger.warning(f"process_master_file {file_id}: project sync failed (non-fatal): {sync_err}")

        except Exception as e:
            logger.error(f"process_master_file error file {file_id}: {e}", exc_info=True)
            try:
                kf.status = "error"
                kf.summary = f"שגיאה בעיבוד Master: {str(e)[:100]}"
                await session.commit()
            except Exception:
                pass


async def reprocess_file_with_context(file_id: int) -> None:
    """Smart re-parse: header injection + merged-cell backfill + increased overlap for PDF/DOCX."""
    async with async_session_maker() as session:
        kf = await session.get(KnowledgeFile, file_id)
        if not kf:
            return

        # Clear existing chunks
        from sqlalchemy import delete as _delete
        await session.execute(_delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))
        kf.status = "processing"
        kf.chunk_count = 0
        await session.commit()

        try:
            # 1. Smart text extraction (merged-cell backfill + context prefix)
            raw_text = await asyncio.get_event_loop().run_in_executor(
                None, _extract_text_smart, kf.file_path, kf.file_type, kf.original_name
            )
            if not raw_text.strip():
                kf.status = "error"
                kf.summary = "לא נמצא טקסט בקובץ"
                await session.commit()
                return

            # 2. Chunking — row-per-line for Excel, 25% overlap for PDF/DOCX
            if kf.file_type in ("xlsx", "csv"):
                chunks = [line for line in raw_text.split("\n") if line.strip()]
            else:
                SMART_OVERLAP = 150  # 25% of 600
                chunks = chunk_text(raw_text, overlap=SMART_OVERLAP)

            # 3. Embed and store
            for idx, chunk_content in enumerate(chunks):
                vector = await embed(chunk_content)
                session.add(KnowledgeChunk(
                    file_id=file_id,
                    chunk_idx=idx,
                    content=chunk_content,
                    embedding=vector,
                ))

            kf.chunk_count = len(chunks)
            kf.status = "ready"
            await session.commit()
            logger.info(f"Smart reprocess file {file_id}: {len(chunks)} chunks with context injection")

        except Exception as e:
            logger.error(f"Smart reprocess error file {file_id}: {e}", exc_info=True)
            try:
                kf.status = "error"
                kf.summary = f"שגיאה בעיבוד חכם: {str(e)[:100]}"
                await session.commit()
            except Exception:
                pass


async def process_file(file_id: int) -> None:
    """Extract, chunk, embed, and store all chunks for a KnowledgeFile record."""
    async with async_session_maker() as session:
        kf = await session.get(KnowledgeFile, file_id)
        if not kf:
            return

        try:
            # 1. Extract text
            raw_text = await asyncio.get_event_loop().run_in_executor(
                None, extract_text, kf.file_path, kf.file_type
            )
            if not raw_text.strip():
                kf.status = "error"
                kf.summary = "לא נמצא טקסט בקובץ"
                await session.commit()
                return

            # 2. Generate AI summary from first 2000 chars
            kf.summary = await _generate_summary(raw_text[:2000], kf.original_name)

            # 3. Chunk — Excel/CSV rows stay intact (1 chunk = 1 row)
            if kf.file_type in ("xlsx", "csv"):
                chunks = [line for line in raw_text.split("\n") if line.strip()]
            else:
                chunks = chunk_text(raw_text)

            # 4. Embed each chunk and store
            for idx, chunk_content in enumerate(chunks):
                vector = await embed(chunk_content)
                chunk = KnowledgeChunk(
                    file_id=file_id,
                    chunk_idx=idx,
                    content=chunk_content,
                    embedding=vector,
                )
                session.add(chunk)

            kf.chunk_count = len(chunks)
            kf.status = "ready"
            await session.commit()
            logger.info(f"Processed file {file_id}: {len(chunks)} chunks")

        except Exception as e:
            logger.error(f"Error processing file {file_id}: {e}", exc_info=True)
            try:
                kf.status = "error"
                kf.summary = f"שגיאה בעיבוד: {str(e)[:100]}"
                await session.commit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _extract_keywords(query: str) -> list[str]:
    """Extract keywords from query, including dates and Hebrew terms.

    Examples: '2025' → ['2025'], 'תחמ"ש גליל' → ['תחמ"ש', 'גליל']
    """
    import re

    # Find years (4-digit numbers)
    years = re.findall(r'\b(20\d{2}|19\d{2})\b', query)

    # Find Hebrew terms (sequences of Hebrew characters)
    hebrew_terms = re.findall(r'[\u0590-\u05FF]+', query)

    # Find electrical notation like '400 ק"ו'
    electrical = re.findall(r'\d+\s*ק"ו', query)

    keywords = years + hebrew_terms + electrical
    return list(set(keywords))  # Remove duplicates


def _extract_query_phrases(query: str) -> list[str]:
    """Extract Hebrew phrases (multi-word) AND significant single terms from a query.

    This solves the "Bat Yam" problem: words like 'בת' and 'ים' are each only
    2 chars and get filtered out individually, but the phrase 'בת ים' is 5 chars
    and will match correctly.

    Returns phrases ordered longest-first so the most specific match wins.
    """
    # Multi-word phrases: 2–4 consecutive Hebrew words (handles city names, manager names)
    phrases = re.findall(r'(?:[\u0590-\u05FF]+\s+){1,3}[\u0590-\u05FF]+', query)
    phrases = [p.strip() for p in phrases if len(p.strip()) >= 3]

    # Single Hebrew words that are meaningful (allow len >= 2 for proper nouns)
    singles = [w for w in re.findall(r'[\u0590-\u05FF]+', query) if len(w) >= 2]

    # Longest first so SQL driver evaluates most-specific first
    combined = list({*phrases, *singles})
    combined.sort(key=len, reverse=True)
    return combined


DOMAIN_KEYWORD_MAP = [
    # Risk queries → add risk-level terms used in Excel
    (['סיכון', 'risk', 'ריסק'], ['סיכון', 'גבוה', 'בינוני', 'חסם', 'בעיה']),
    # Delay / barrier queries
    (['עיכוב', 'delay', 'חסם', 'barrier'], ['חסם', 'עיכוב', 'עצור', 'בעיה']),
    # Building permit queries
    (['היתר', 'היתר בניה', 'רישיון בניה'], ['היתר בניה', 'היתר']),
    # Development plan date queries
    (['תוכנית פיתוח', 'תאריך תוכנית', 'פיתוח'], ['תאריך תוכנית הפיתוח', 'תוכנית פיתוח']),
    # Electrification date queries — note: data stores "חשמול" (no י), user often writes "חישמול" (with י)
    (['חישמול', 'חשמול', 'תאריך חישמול', 'תאריך חשמול'],
     ['יעד חשמול', 'יעד חשמול מסתמן', 'חשמול']),
]


def _inject_domain_keywords(question: str, keywords: list[str]) -> list[str]:
    """Append domain-specific keywords when trigger terms appear in the question."""
    import re as _re
    question_lower = question.lower()
    extras: set[str] = set()
    for triggers, expansions in DOMAIN_KEYWORD_MAP:
        if any(t in question_lower for t in triggers):
            extras.update(expansions)
    # Year queries (e.g. "2025") → add target/date synonyms used in Excel columns
    if _re.search(r'\b20\d{2}\b', question):
        extras.update(['יעד', 'תאריך', 'מסתמן', 'תאריכים'])
    return list(set(keywords) | extras)


_COMMON_HEBREW_WORDS = {
    'כמה', 'מה', 'מי', 'יש', 'של', 'עם', 'על', 'כל', 'כן', 'לא', 'זה', 'את', 'זהו',
    'אם', 'הם', 'הן', 'הוא', 'היא', 'אנו', 'כי', 'או', 'גם', 'רק', 'כבר', 'עוד',
    'אין', 'היה', 'בכל', 'בו', 'בה', 'בהם', 'לכל', 'אחד', 'אחת', 'שיש', 'שאין',
    'כמות', 'רשימה', 'הכל', 'פרויקטים', 'פרויקט', 'כולל', 'סהכ', 'מופיעים', 'בדיווח',
}


def _has_proper_nouns(keywords: list[str]) -> list[str]:
    """Return keywords that look like proper nouns (short Hebrew, not common words)."""
    return [
        kw for kw in keywords
        if 2 <= len(kw) <= 8
        and re.search(r'^[\u0590-\u05FF]+$', kw)
        and kw not in _COMMON_HEBREW_WORDS
    ]


def _rerank_by_query_keywords(chunks: list[KnowledgeChunk], query: str) -> list[KnowledgeChunk]:
    """Move chunks containing query keywords to the top for better LLM context."""
    raw_kws = _extract_keywords(query)
    keywords = [kw.lower() for kw in raw_kws if len(kw) > 2]
    if not keywords:
        return chunks
    hits, misses = [], []
    for chunk in chunks:
        content_lower = chunk.content.lower()
        if any(kw in content_lower for kw in keywords):
            hits.append(chunk)
        else:
            misses.append(chunk)
    return hits + misses


async def search_knowledge(query: str, session: AsyncSession, limit: int = 30) -> list[KnowledgeChunk]:
    """Four-path retrieval + proper-noun boost + WBS cross-fetch, deduplicated.

    Path A: Top-N by cosine similarity (semantic understanding)
    Path B: Top-N by SQL ILIKE on extracted keywords (exact-string guarantees)
           → boosted allocation (2/3 of limit) when proper nouns detected
    Path B2: Dedicated PROJECT: <term> search for proper-noun / name queries
    Path C: Fetch ALL chunks sharing WBS values found in A+B (cross-chunk integrity)
    Path D: Full-scan — every chunk containing WBS:/PROJECT: markers (full-list queries only)
    Result: All unique chunks for full-list; up to limit otherwise.
    """
    import re as _re
    from sqlalchemy import or_

    half = limit // 2  # base allocation per path

    # Extract keywords and detect proper nouns
    keywords = _extract_keywords(query)
    keywords = _inject_domain_keywords(query, keywords)
    proper_nouns = _has_proper_nouns(keywords)
    name_query = bool(proper_nouns) or bool(_re.search(r'[\u0590-\u05FF]{3,}\s+[\u0590-\u05FF]{3,}', query))

    # Boost keyword allocation: full limit for proper nouns, 2/3 for other name queries
    if proper_nouns:
        kw_limit = limit          # maximum weight for exact project-name matches
    elif name_query:
        kw_limit = limit * 2 // 3
    else:
        kw_limit = half

    try:
        # ── Path A: Vector / semantic search ──────────────────────────────
        query_vector = await embed(query)
        vec_stmt = (
            select(KnowledgeChunk)
            .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
            .where(KnowledgeFile.status == "ready")
            .where(KnowledgeChunk.embedding.isnot(None))
            .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
            .limit(half)
        )
        vec_result = await session.execute(vec_stmt)
        vec_chunks = vec_result.scalars().all()

        # ── Path B: SQL ILIKE keyword + phrase search ─────────────────────
        # Uses >= 2 chars (not > 2) so 2-char proper nouns like 'בת', 'ים' are kept.
        # Also extracts multi-word phrases like 'בת ים' as single search units so
        # they are searched as a phrase and not split into two noisy 2-char terms.
        kw_chunks = []

        phrases = _extract_query_phrases(query)
        # Individual keywords: allow >= 2 chars (captures 'בת', 'ים', etc.)
        sig_kws = list({kw for kw in keywords if len(kw) >= 2})
        # Multi-word phrases (most specific): searched as a whole string
        multi_phrases = [p for p in phrases if ' ' in p]
        # Combined unique search terms — phrases first for priority
        all_terms = list({*multi_phrases, *sig_kws})

        if all_terms:
            kw_stmt = (
                select(KnowledgeChunk)
                .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                .where(KnowledgeFile.status == "ready")
                .where(or_(*[KnowledgeChunk.content.ilike(f"%{t}%") for t in all_terms]))
                .limit(kw_limit)
            )
            kw_result = await session.execute(kw_stmt)
            kw_chunks = kw_result.scalars().all()
            logger.info(f"Keyword path found {len(kw_chunks)} chunks for terms: {repr(all_terms[:8])}")

        # ── Path B2: Label-targeted search for proper nouns / phrases ────
        # Uses _extract_query_phrases to handle multi-word names like 'בת ים'
        # that are each ≤2 chars individually and get filtered from Path B.
        pn_chunks = []
        phrases = _extract_query_phrases(query)
        sig_phrases = [p for p in phrases if len(p) >= 2]
        if sig_phrases:
            # Search in structured labels: Project:, Manager:, WBS: — not just anywhere
            label_conditions = []
            for p in sig_phrases:
                label_conditions.append(KnowledgeChunk.content.ilike(f"%Project: %{p}%"))
                label_conditions.append(KnowledgeChunk.content.ilike(f"%Manager: %{p}%"))
                label_conditions.append(KnowledgeChunk.content.ilike(f"%WBS: %{p}%"))
            pn_stmt = (
                select(KnowledgeChunk)
                .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                .where(KnowledgeFile.status == "ready")
                .where(or_(*label_conditions))
                .limit(limit)
            )
            pn_result = await session.execute(pn_stmt)
            pn_chunks = pn_result.scalars().all()
            if pn_chunks:
                logger.info(f"Label search (B2) found {len(pn_chunks)} chunks for phrases: {sig_phrases[:5]}")

        # ── Deduplicate A+B+B2 — proper-noun hits go first ───────────────
        seen_ids: set[int] = set()
        merged: list[KnowledgeChunk] = []

        # B2 first so proper-noun PROJECT hits sit at the top of context
        for chunk in pn_chunks:
            if chunk.id not in seen_ids:
                merged.append(chunk)
                seen_ids.add(chunk.id)
        for chunk in kw_chunks:
            if chunk.id not in seen_ids:
                merged.append(chunk)
                seen_ids.add(chunk.id)
        for chunk in vec_chunks:
            if chunk.id not in seen_ids:
                merged.append(chunk)
                seen_ids.add(chunk.id)

        logger.info(
            f"Hybrid search: {len(vec_chunks)} vector + {len(kw_chunks)} keyword"
            f" + {len(pn_chunks)} proper-noun = {len(merged)} unique chunks"
        )

        # ── Path C: Cross-chunk WBS fetch ─────────────────────────────────
        # Extract all WBS values from A+B results, then fetch ALL chunks for those WBS IDs
        wbs_values = set()
        for chunk in merged:
            m = _re.search(r'WBS:\s*([^|\n]+)', chunk.content)
            if m:
                wbs_val = m.group(1).strip()
                if wbs_val:
                    wbs_values.add(wbs_val)

        if wbs_values:
            # For large limits (full-list queries) fetch all matching WBS chunks uncapped
            wbs_fetch_limit = max(500, limit * 4)
            wbs_stmt = (
                select(KnowledgeChunk)
                .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                .where(KnowledgeFile.status == "ready")
                .where(or_(*[KnowledgeChunk.content.ilike(f"%WBS: {wbs}%") for wbs in wbs_values]))
                .limit(wbs_fetch_limit)
            )
            wbs_result = await session.execute(wbs_stmt)
            wbs_chunks = wbs_result.scalars().all()
            added = 0
            for chunk in wbs_chunks:
                if chunk.id not in seen_ids:
                    merged.append(chunk)
                    seen_ids.add(chunk.id)
                    added += 1
            if added:
                logger.info(f"WBS cross-fetch added {added} additional chunks for WBS: {wbs_values}")

        # ── Path D: Full-scan for count/list queries ──────────────────────
        # Bypass all similarity caps — pull every row that looks like a project
        if limit >= 100:
            scan_stmt = (
                select(KnowledgeChunk)
                .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                .where(KnowledgeFile.status == "ready")
                .where(or_(
                    KnowledgeChunk.content.ilike("%WBS:%"),
                    KnowledgeChunk.content.ilike("%PROJECT:%"),
                    KnowledgeChunk.content.ilike("%שם פרויקט%"),
                ))
                .limit(2000)
            )
            scan_result = await session.execute(scan_stmt)
            scan_chunks = scan_result.scalars().all()
            added = 0
            for chunk in scan_chunks:
                if chunk.id not in seen_ids:
                    merged.append(chunk)
                    seen_ids.add(chunk.id)
                    added += 1
            if added:
                logger.info(f"Full-scan path D found {added} additional project chunks")

        # ── Context Hydration: ±N neighbours per retrieved chunk ─────────
        # Fetch surrounding rows so that a project name in chunk N and its
        # WBS/Status in chunk N±1..3 are always present together.
        # Full-list queries: lighter ±1 pass (Path D handles bulk retrieval).
        # Specific queries: deeper ±3 pass for maximum field coverage.
        hydration_window = 1 if limit >= 100 else 3
        if merged:
            neighbour_candidates: list[tuple[int, int]] = []  # (file_id, chunk_idx)
            for chunk in list(merged):
                for delta in range(-hydration_window, hydration_window + 1):
                    if delta == 0:
                        continue
                    idx = chunk.chunk_idx + delta
                    if idx >= 0:
                        neighbour_candidates.append((chunk.file_id, idx))

            if neighbour_candidates:
                from sqlalchemy import tuple_ as _tuple
                hydration_stmt = (
                    select(KnowledgeChunk)
                    .where(
                        _tuple(KnowledgeChunk.file_id, KnowledgeChunk.chunk_idx).in_(neighbour_candidates)
                    )
                )
                hydration_result = await session.execute(hydration_stmt)
                hydration_chunks = hydration_result.scalars().all()
                added = 0
                for chunk in hydration_chunks:
                    if chunk.id not in seen_ids:
                        merged.append(chunk)
                        seen_ids.add(chunk.id)
                        added += 1
                if added:
                    logger.info(f"Context hydration (±{hydration_window}) added {added} neighbour chunks")

        return_limit = min(len(merged), 2000) if limit >= 100 else limit
        logger.info(f"search_knowledge returning {return_limit} of {len(merged)} total chunks (limit={limit})")
        return merged[:return_limit]

    except Exception as e:
        logger.warning(f"Knowledge search failed: {e}")
        return []


def _extract_project_name(content: str) -> str | None:
    """Extract project name from a chunk with 'Project:' or 'PROJECT:' label.

    Handles both master file format (🏗️ Project: {name}) and legacy format.
    """
    import re
    # Try case-insensitive match for "Project:" (including emoji prefix)
    match = re.search(r'(?:🏗️\s*)?Project:\s*([^|\n]+)', content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _dedup_fragment_lines(contents: list[str]) -> list[str]:
    """Deduplicate overlapping fragments: remove exact-duplicate chunks,
    then strip repeated lines within merged text to maximise unique data."""
    # 1. Exact-chunk dedup
    seen_chunks: set[str] = set()
    unique: list[str] = []
    for c in contents:
        key = c.strip()
        if key not in seen_chunks:
            seen_chunks.add(key)
            unique.append(key)

    # 2. Collapse to unique non-empty lines across all fragments
    seen_lines: set[str] = set()
    result_lines: list[str] = []
    for fragment in unique:
        for line in fragment.split('\n'):
            stripped = line.strip()
            if stripped and stripped not in seen_lines:
                seen_lines.add(stripped)
                result_lines.append(stripped)

    return result_lines


def _trim_chunk_for_specific(content: str, max_update_chars: int = 300) -> str:
    """Trim a master-file chunk for specific queries.

    Keeps all structured fields (Project, WBS, Manager, Status, dates, etc.) but
    truncates the Update: field to `max_update_chars` to prevent context explosion.
    Single chunks now contain full weekly history which can be thousands of chars.
    """
    # Split on pipe delimiters, keep all parts, but truncate the Update field
    parts = content.split(" | ")
    trimmed_parts = []
    for part in parts:
        if part.strip().startswith("Update:") or part.strip().startswith("🏗️ Project:"):
            # Keep Update but cap length; keep Project field always
            if part.strip().startswith("Update:") and len(part) > max_update_chars:
                part = part[:max_update_chars] + "…"
        trimmed_parts.append(part)
    return " | ".join(trimmed_parts)


def format_knowledge_context(
    chunks: list[KnowledgeChunk],
    compact: bool = False,
    file_name_map: dict[int, str] | None = None,
) -> str:
    """Smart context grouping: merge fragments by project, deduplicate, then format for LLM.

    compact=True: extract only PROJECT/WBS/manager/date per chunk — used for full-list
    queries to stay within Groq's token limit while preserving every project name.
    file_name_map: {file_id → original_name} used to prefer 'עדכני'/'Final' files.
    """
    if not chunks:
        return ""

    if compact:
        return _format_compact_index(chunks, file_name_map=file_name_map)

    # ── Trim chunks to prevent context explosion ──────────────────────────
    # Each master-file chunk now contains full weekly history in the Update field.
    # Trim to keep structured fields (Project, WBS, Manager, Status, dates) but
    # cap the Update text at 300 chars so context stays under Groq's token limit.
    trimmed_contents = [_trim_chunk_for_specific(c.content) for c in chunks]

    # ── Group chunks by project name ──────────────────────────────────────
    groups: dict[str, list[str]] = {}
    for content in trimmed_contents:
        project = _extract_project_name(content) or "_ungrouped"
        groups.setdefault(project, []).append(content)

    # ── Build context blocks ───────────────────────────────────────────────
    lines = [
        f"מידע רלוונטי ממסמכי הארגון ({len(groups)} פרויקטים, {len(chunks)} פרגמנטים):\n",
    ]

    for project, contents in groups.items():
        header = "[פרגמנטים כלליים]" if project == "_ungrouped" else f"[פרויקט: {project}]"

        # Deduplicate overlapping fragments → unique lines only
        deduped_lines = _dedup_fragment_lines(contents)
        merged = "\n".join(deduped_lines)

        # Cap each project block at 3000 chars to allow full row data
        if len(merged) > 3000:
            merged = merged[:3000] + "... [קוצר]"

        lines.append(header)
        lines.append(merged)
        lines.append("")  # blank line between projects

    return "\n".join(lines)


def _format_compact_index(
    chunks: list[KnowledgeChunk],
    file_name_map: dict[int, str] | None = None,
) -> str:
    """Build a compact one-line-per-project index for full-list / count queries.

    Extracts only PROJECT, WBS, manager, and target-date fields so the total
    context stays well under Groq's 30 000-token TPM limit even for large files.
    Files whose name contains 'עדכני' or 'Final' win when the same project appears
    in multiple files (file_name_map must be provided for this to work).
    """
    _PRIORITY_MARKERS = ('עדכני', 'final', 'Final', 'FINAL')
    # Regex to extract labeled fields from chunks: Project/Manager/Status/WBS + date fields
    # Skip Update field to save tokens. Also support date fields like יעד תכנית פיתוח, יעד חשמול
    _fields = re.compile(
        r'((?:Project|Manager|Status|WBS):\s*[^|]+|'
        r'יעד\s+(?:תכנית|חשמול)[^|]*:\s*[^|]+|'
        r'מנהל[^|]*:\s*[^|]+|מנה"פ[^|]*:\s*[^|]+)',
        re.IGNORECASE,
    )

    def _is_priority(chunk: KnowledgeChunk) -> bool:
        if not file_name_map:
            return False
        name = file_name_map.get(chunk.file_id, "")
        return any(m in name for m in _PRIORITY_MARKERS)

    # First pass: collect all chunks per project, prefer priority-file chunks
    project_chunks: dict[str, KnowledgeChunk] = {}
    for chunk in chunks:
        project = _extract_project_name(chunk.content)
        if not project:
            continue
        existing = project_chunks.get(project)
        if existing is None or (_is_priority(chunk) and not _is_priority(existing)):
            project_chunks[project] = chunk

    rows: list[str] = []
    for project, chunk in project_chunks.items():
        matches = _fields.findall(chunk.content)
        # Filter out the Project field itself (we already have it)
        extras = [m.strip() for m in matches
                  if not m.lower().startswith("project:")]
        row = f"🏗️ Project: {project}"
        if extras:
            row += " | " + " | ".join(extras[:4])
        rows.append(row)

    if not rows:
        return "לא נמצאו פרויקטים במסד הידע."
    header = f"רשימת פרויקטים ({len(rows)} פרויקטים ייחודיים):\n"
    return header + "\n".join(rows)


# ---------------------------------------------------------------------------
# Decisions context for Q&A
# ---------------------------------------------------------------------------

async def answer_decisions_question(question: str, decisions_ctx: str) -> str:
    """Answer a question about the user's decisions in plain conversational Hebrew."""
    from app.services.llm_router import llm_chat
    system = (
        "אתה עוזר שעונה על שאלות לגבי החלטות של עובד. "
        "ענה בעברית בלבד, בשפה טבעית וקולחת — כמו אדם שעונה לעמית. "
        "אל תוסיף הקדמות, הסברים על המתודולוגיה, כוכביות, או כללים. "
        "אפשר להשתמש באימוג׳י. "
        "תשובה קצרה וממוקדת — רק מה שנשאל."
    )
    return await llm_chat(
        "decisions_answer",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"נתונים:\n{decisions_ctx}\n\nשאלה: {question}"},
        ],
        max_tokens=600,
        temperature=0.2,
    )


async def get_decisions_context(session: AsyncSession, user_id: int) -> str:
    """Fetch recent decisions submitted by this user and format as Q&A context."""
    from app.models import Decision
    stmt = (
        select(Decision)
        .where(Decision.submitter_id == user_id)
        .order_by(Decision.created_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    decisions = result.scalars().all()
    if not decisions:
        return ""
    lines = ["החלטות אחרונות של המשתמש:"]
    for d in decisions:
        date_str = d.created_at.strftime("%d/%m/%Y") if d.created_at else "—"
        lines.append(
            f"• [{d.type.value.upper()} | {d.status.value}] {d.summary or '—'} | "
            f"פעולה: {d.recommended_action or '—'} | תאריך: {date_str}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3: Anchor & Cross-Link Retrieval Helpers
# ---------------------------------------------------------------------------

async def _get_master_file_id(session: AsyncSession):
    """Return the id of the current master KnowledgeFile, or None."""
    result = await session.execute(
        select(KnowledgeFile.id).where(KnowledgeFile.is_master).limit(1)
    )
    return result.scalar_one_or_none()


def _extract_wbs_and_projects_from_chunks(chunks) -> tuple:
    """Extract WBS codes and Project Names from chunk content.

    Returns (wbs_codes: set[str], project_names: set[str]).
    """
    wbs_codes: set = set()
    project_names: set = set()
    wbs_pattern = re.compile(r'WBS:\s*([^|\n\r]+)', re.IGNORECASE)
    project_pattern = re.compile(r'(?:🏗️\s*)?Project:\s*([^|\n\r]+)', re.IGNORECASE)
    for chunk in chunks:
        content = chunk.content
        for m in wbs_pattern.finditer(content):
            val = m.group(1).strip()
            if val and val not in ('—', '-', 'nan', ''):
                wbs_codes.add(val)
        for m in project_pattern.finditer(content):
            val = m.group(1).strip()
            if val and len(val) > 2 and val.lower() not in ('nan', '—', 'project'):
                project_names.add(val)
    return wbs_codes, project_names


# ---------------------------------------------------------------------------
# Q&A
# ---------------------------------------------------------------------------

# Used when the user asks for details about a specific project
_TELEGRAM_FORMAT_RULES_DETAIL = (
    "\n\nפורמט חובה (Telegram-safe):"
    "\n- אסור בהחלט: טבלאות Markdown, תווי pipe (|), כותרות ###"
    "\n- חובה לכל פרויקט:"
    "\n  🏗️ **[שם הפרויקט]**"
    "\n  • WBS: [קוד]"
    "\n  • 👤 מנהל: [שם]"
    "\n  • סטטוס: 🟢/🟡/🔴 [תיאור]"
    "\n  • פירוט: [עדכון]"
    "\n- הפרד בין פרויקטים בשורה ריקה"
    "\n- נתונים כלליים: נקודות תבליט רגילות"
)

# Used when the user asks for a list of projects (without requesting details)
_TELEGRAM_FORMAT_RULES_LIST = (
    "\n\nפורמט חובה (Telegram-safe):"
    "\n- אסור בהחלט: טבלאות Markdown, תווי pipe (|), כותרות ###"
    "\n- כאשר מציגים רשימה של פרויקטים ללא בקשת פירוט — הצג לכל פרויקט שורה אחת בלבד:"
    "\n  🏗️ **[שם הפרויקט]** | WBS: [קוד]"
    "\n- אין להוסיף מנהל, סטטוס, פירוט, תאריכים או כל שדה אחר אלא אם נשאל במפורש"
    "\n- הפרד בין פרויקטים בשורה ריקה"
    "\n- נתונים כלליים (סכומים, ספירות): נקודות תבליט רגילות"
)

# Back-compat alias — replaced by the two variants above in answer_question()
_TELEGRAM_FORMAT_RULES = _TELEGRAM_FORMAT_RULES_DETAIL

async def answer_question(
    question: str,
    context: str,
    full_list: bool = False,
    specific: bool = False,
    learned_instructions: list[str] | None = None,
    bare_name: bool = False,
) -> str:
    """Use Groq to answer a question based on knowledge context."""
    try:
        from app.services.llm_router import llm_chat

        # Eval-loop hook: a shadow override (during shadow_config) takes precedence;
        # otherwise fall back to the active PromptOverride DB row for this usage slot.
        # `usage_slot` keys: rag_specific (single-fact answers) | rag_list (aggregations).
        usage_slot = "rag_specific" if specific else "rag_list"
        shadow_po = _shadow_prompt_override.get()
        override_content: str | None = (
            shadow_po.get(usage_slot)
            if shadow_po else _DB_PROMPT_OVERRIDES_CACHE.get(usage_slot)
        )

        # Build the learned-instructions addon (appended at END of system prompt with override framing)
        instructions_addon = ""
        if learned_instructions:
            instructions_text = "\n".join(f"- {inst}" for inst in learned_instructions)
            instructions_addon = (
                "\n\n## ⚠️ הוראות מחייבות (עדיפות עליונה — דורסות את כל הכללים למעלה):\n"
                + instructions_text
                + "\nבמקרה של סתירה בין הוראה כאן לכלל פורמט למעלה — ההוראה כאן גוברת.\n"
            )
            logger.info(f"Injecting {len(learned_instructions)} learned instructions into system prompt:")
            for i, inst in enumerate(learned_instructions):
                logger.info(f"  [{i + 1}] {inst[:120]}")

        # Detect whether the question asks for project details or just a list
        _DETAIL_KEYWORDS = {'פרטים', 'פירוט', 'פרטי', 'מידע', 'נתונים', 'הכל', 'כל', 'דווח', 'תאר', 'ספר', 'סקור'}
        _question_words = set(question.split())
        wants_details = specific or bare_name or any(w in _DETAIL_KEYWORDS for w in _question_words)
        format_rules = _TELEGRAM_FORMAT_RULES_DETAIL if wants_details else _TELEGRAM_FORMAT_RULES_LIST

        if specific:
            if override_content is not None:
                # Override always wraps with format_rules + instructions_addon so
                # downstream behaviour (telegram formatting, learned instructions) is preserved.
                system_prompt = override_content + format_rules + instructions_addon
                max_tokens = 400
                return await llm_chat(
                    "rag_answer",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"{context}\n\nשאלה: {question}"},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
            system_prompt = (
                "אתה מומחה לבקרת פרויקטים הנדסיים. "
                "קיבלת שאלה ספציפית הדורשת תשובה ממוקדת וקצרה."
                "\n\n## מדריך שמות שדות — מיפוי בין שמות טכניים בנתונים לשמות בשאלות:"
                "\n• 'יעד חשמול מסתמן' / 'יעד מסתמן' = תאריך חישמול / תאריך חשמול / מתי יחושמל"
                "\n• 'יעד תכנית פיתוח' = תאריך תוכנית פיתוח / תאריך ת\"פ / יעד ת\"פ"
                "\n• 'יעד מסתמן' (ללא 'חשמול') = תאריך יעד / תאריך סיום מסתמן / תאריך צפוי"
                "\n• 'Update:' / 'פירוט שבועי' = עדכון שבועי / מה קורה / מצב עדכני / פעילות אחרונה"
                "\n• 'Status:' = סטטוס / שלב / מצב הפרויקט"
                "\n• 'Manager:' / 'מנה\"פ:' = מנהל / מנהל הפרויקט"
                "\n• 'תו\"ב:' = תכנון ובנייה / רשות רישוי"
                "\nחובה: לפני שאתה כותב 'הנתון לא נמצא', בדוק אם השדה מופיע בשם אחר לפי המדריך הזה."
                "\n\nכללי חובה:"
                "\n1. ענה ישירות על מה שנשאל — שם, תאריך, מספר, סטטוס — בשורה אחת או שתיים לכל היותר."
                "\n2. אל תרחיב, אל תוסיף הקדמה, ואל תפרט פרויקטים אחרים שלא נשאלת עליהם."
                "\n3. אם יש כמה ערכים רלוונטיים (למשל כמה תאריכים), ציין את כולם בצורה תמציתית."
                "\n4. אם הנתון לא קיים בהקשר גם לאחר בדיקת כל שמות השדות האפשריים, כתוב 'הנתון לא נמצא במסד הידע'."
                '\n5. קיצורים: מנה"פ / פ"מ = מנהל פרויקט, תו"ב = תכנון ובנייה.'
                f"{format_rules}"
                "\n\nענה בעברית בלבד. תשובה קצרה וממוקדת בלבד."
                f"{instructions_addon}"
            )
            max_tokens = 400
        else:
            auditor_addon = (
                "\n\n## הוראות ספירה ואגרגציה (חובה לשאלות כמותיות):"
                "\n1. עבור על כל בלוק 🏗️ Project: בנפרד — כל בלוק = פרויקט ייחודי."
                "\n2. לספירה לפי מנהל: הסתכל ONLY על השדה `Manager: [שם]` בכל בלוק — התעלם לחלוטין מטקסט ה-Update."
                "\n3. לספירה לפי סטטוס: הסתכל ONLY על השדה `Status: [ערך]`."
                "\n4. אסור לדלג על פרויקטים. בסיום כתוב: **סה\"כ: X פרויקטים**."
                "\n5. אם שאלו 'כמה פרויקטים יש ל[מנהל]' — ספור כל בלוק שבו `Manager:` מכיל את שמו, ורשום את שמות הפרויקטים."
            ) if full_list else ""

            if override_content is not None:
                system_prompt = override_content + format_rules + (auditor_addon if full_list else "") + instructions_addon
                max_tokens = 2000
                return await llm_chat(
                    "rag_answer",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"{context}\n\nשאלה: {question}"},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
            system_prompt = (
                "אתה מומחה לבקרת פרויקטים הנדסיים של חברת החשמל."
                "קיבלת קטעי נתונים ממסד הידע של הארגון — זוהי רשימת פרויקטים מאסטר. "
                "\n\n## מדריך שמות שדות — מיפוי בין שמות טכניים בנתונים לשמות בשאלות:"
                "\n• 'יעד חשמול מסתמן' / 'יעד מסתמן' = תאריך חישמול / תאריך חשמול"
                "\n• 'יעד תכנית פיתוח' = תאריך תוכנית פיתוח / תאריך ת\"פ"
                "\n• 'Update:' / 'פירוט שבועי' = עדכון שבועי / פעילות אחרונה"
                "\n• 'Status:' = סטטוס / שלב / מצב"
                "\n• 'Manager:' / 'מנה\"פ:' = מנהל / מנהל פרויקט"
                "\n\nכללי חובה:"
                "\n1. עבור על כל פרויקט המופיע בהקשר — ספור פנימית, אל תפסיד אף אחד."
                "\n2. ערך חסר בנתונים = כתוב '—' — אל תשמיט פרויקטים."
                '\n3. קיצורים: מנה"פ / פ"מ = מנהל פרויקט, תו"ב = תכנון ובנייה.'
                "\n4. אם קטע מידע אינו רלוונטי לשאלה, התעלם ממנו בשקט."
                "\n5. אימות סופי: לפני שאתה מסכם, סרוק שנית את כל בלוקי 🏗️ Project: בהקשר. "
                "חפש גם התאמות חלקיות בשמות."
                f"{format_rules}"
                f"{auditor_addon}"
                + (
                    "\n\n## שם פרויקט בלבד — הצג כרטיס מלא:"
                    "\nהמשתמש כתב שם פרויקט בלבד. הצג את כל הנתונים הקיימים על הפרויקט: "
                    "מנהל, סטטוס, עדכון, יעד תכנית פיתוח, יעד חשמול מסתמן, תו\"ב, וכל שאר השדות הזמינים."
                    if bare_name else ""
                )
                + "\n\nענה בעברית בלבד."
                + instructions_addon
            )
            max_tokens = 2000

        return await llm_chat(
            "rag_answer",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{context}\n\nשאלה: {question}"},
            ],
            max_tokens=max_tokens,
            temperature=0.1 if specific else 0.3,
        )
    except Exception as e:
        logger.error(f"answer_question failed: {e}")
        return "שגיאה בעיבוד השאלה. נסה שוב מאוחר יותר."


async def _expand_query(question: str) -> str:
    """Use LLM to expand/clean the user's question for better search.

    For example: "Galil" → "תחמ"ג גליל מזרחי"
    This helps the embedding model find more relevant chunks.
    """
    try:
        from app.services.llm_router import llm_chat

        expansion = await llm_chat(
            "query_expansion",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "אתה עוזר בהרחבת שאילתות חיפוש. "
                        "מהשאלה/חיפוש של המשתמש, צור גרסה מורחבת עם מילים נרדפות ותרגומים רלוונטיים. "
                        "השב רק את השאילתה המורחבת, בלי הסברים. "
                        "דוגמה: 'חטיבת הולכה' → 'חטיבת הולכה והשנאה HVDC'"
                    ),
                },
                {"role": "user", "content": f"הרחב את השאילתה: {question}"},
            ],
            max_tokens=100,
            temperature=0.3,
            models=["llama-3.1-8b-instant"],  # fast model — frees primary quota for answer
        )
        return expansion.strip()
    except Exception as e:
        logger.warning(f"Query expansion failed: {e}, using original")
        return question


async def _apply_learned_synonyms(question: str, session: AsyncSession) -> str:
    """Append synonyms learned by the optimization engine to expand the query.
    Uses normalized Hebrew word-set matching to handle nikud, final letters, and prefixes.
    """
    try:
        from app.models import QuerySynonym
        # Only fetch TERMINOLOGY synonyms (not instructions)
        result = await session.execute(
            select(QuerySynonym).where(QuerySynonym.source != "instruction")
        )
        question_forms = _question_word_forms(question)
        applied = []
        for row in result.scalars():
            original_forms = _question_word_forms(row.original)
            # Check if any word form from the synonym's original matches any form in the question
            if original_forms & question_forms:
                question += " " + " ".join(row.synonyms)
                applied.append(row.original)
        if applied:
            logger.info(f"Applied learned synonyms for: {applied}")
    except Exception as e:
        logger.warning(f"_apply_learned_synonyms failed: {e}")
    return question


async def _get_learned_instructions(session: AsyncSession) -> list[str]:
    """Load answer-improvement instructions from the consolidated __global_instructions__ row."""
    try:
        from app.models import QuerySynonym
        row = await session.scalar(
            select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
        )
        return row.synonyms if row and row.synonyms else []
    except Exception as e:
        logger.warning(f"_get_learned_instructions failed: {e}")
        return []


_FULL_LIST_TRIGGERS = ['כמה', 'סה"כ', "סה''כ", 'רשימה', 'כל הפרויקטים', 'כל פרויקט',
                       'הכל', 'תרשום', 'תציג', 'פרויקטים יש', 'כמות']

# Specific-answer triggers: "who", "what date", "when", "how much/many X" (single value)
_SPECIFIC_QUESTION_PATTERNS = [
    r'מי\s+(ה?מנהל|אחראי|מטפל|מוביל)',          # who is the manager/responsible
    r'מה\s+(ה?תאריך|ה?יעד|ה?סטטוס|ה?מצב)',      # what is the date/target/status
    r'מתי\s+',                                    # when
    r'איזה\s+תאריך',                              # which date
    r'מה\s+ה?סטטוס',                             # what is the status
    r'כמה\s+(?!פרויקטים)',                        # how many/much (not "how many projects")
    r'מה\s+ה?חסם',                               # what is the barrier
    r'מי\s+מנהל',                                # who manages
    r'מה\s+ה?עדכון',                             # what is the update
    r'האם\s+',                                   # is/does (yes/no question)
]


def _is_full_list_query(question: str) -> bool:
    """Return True if the question is asking for a complete list or count."""
    q = question.lower()
    return any(t in q for t in _FULL_LIST_TRIGGERS)


def _is_specific_question(question: str) -> bool:
    """Return True if the question expects a single specific answer (date, name, status, etc.)."""
    for pattern in _SPECIFIC_QUESTION_PATTERNS:
        if re.search(pattern, question):
            return True
    return False


_QUESTION_WORDS = frozenset({
    'מה', 'מי', 'כמה', 'מתי', 'איזה', 'אילו', 'האם', 'כל', 'הכל',
    'רשום', 'תרשום', 'הצג', 'תציג', 'ספר', 'תאר', 'דווח', 'סקור',
    'כמות', 'רשימה', 'סה"כ', 'כמה', 'יש',
})


def _is_bare_name_query(question: str) -> bool:
    """Return True when the user sent just a project name (1–4 words, no question/list words).

    Examples: "חולה", "יאסיף", "בת ים", "בראון עמק הבכא"
    These should return the full project card, same as asking 'כל הנתונים על X'.
    """
    words = question.strip().split()
    if len(words) > 4:
        return False
    # If any word is a question/command word → not a bare name
    return not any(w in _QUESTION_WORDS for w in words)


def _extract_manager_name_from_question(question: str) -> str | None:
    """Extract a manager name from a question like 'כמה פרויקטים מנהל משה ברקוביץ?'.

    Returns the name as written by the user (first-name-first), or None if not found.
    The caller is responsible for also trying the reversed (last, first) DB format.
    """
    # Match patterns like "מנהל [Name]" or "של [Name]" or "עבור [Name]"
    patterns = [
        r'(?:מנהל|מנהלת|של מנהל|של מנהלת|עבור)\s+([\u0590-\u05FF\s\-\'"״]+?)(?:\?|$)',
        r'(?:פרויקטים\s+של|תחת\s+ניהול\s+של?)\s+([\u0590-\u05FF\s\-\'"״]+?)(?:\?|$)',
    ]
    for pat in patterns:
        m = re.search(pat, question.strip())
        if m:
            name = m.group(1).strip().rstrip('?').strip()
            # Must be at least 2 words and at least 4 chars total to avoid false positives
            if len(name) >= 4 and ' ' in name:
                return name
    return None


# All known project stages as they appear in the Status field of the master file.
# Also includes common user aliases → canonical stage name.
_STAGE_ALIASES: dict[str, str] = {
    # canonical names (map to themselves)
    "קבלת היתר": "קבלת היתר",
    "תכנון": "תכנון",
    "ביצוע": "ביצוע",
    "בדיקות": "בדיקות",
    "בחירת קבלן": "בחירת קבלן",
    "הסתיים": "הסתיים",
    "הקפאת תכולה": "הקפאת תכולה",
    "הקפאת תצורה": "הקפאת תצורה",
    "הרכבה חשמלית": "הרכבה חשמלית",
    "הרכבה חשמלית ובדיקות": "הרכבה חשמלית ובדיקות",
    "טופס 4": "טופס 4",
    "לקראת ביצוע": "לקראת ביצוע",
    "עבודה אזרחית": "עבודה אזרחית",
    # user aliases
    "היתר בניה": "קבלת היתר",
    "שלב היתר": "קבלת היתר",
    "היתר": "קבלת היתר",
    "הסכם אגירת אנרגיה": "הסכם- אגירת אנרגיה",
}


def _extract_stage_filter_from_question(question: str) -> str | None:
    """Return the canonical stage name if the question is asking about a specific project stage.

    Matches patterns like 'בשלב קבלת היתר', 'שלב תכנון', 'פרויקטים בהרכבה חשמלית'.
    Returns None if no stage is detected.
    """
    q = question.strip()
    # Try longest alias first so "הרכבה חשמלית ובדיקות" matches before "הרכבה חשמלית"
    for alias in sorted(_STAGE_ALIASES, key=len, reverse=True):
        if alias in q:
            canonical = _STAGE_ALIASES[alias]
            logger.info(f"Stage filter detected: '{alias}' → canonical '{canonical}'")
            return canonical
    return None


async def answer_with_full_context(
    question: str,
    session: AsyncSession,
    user_id: int,
    log_to_db: bool = True,
) -> dict:
    """Search knowledge base + decisions, then answer with two-step retrieval.

    Step 0: Hebrew abbreviation expansion + learned synonyms
    Step 1: LLM query expansion for better semantic understanding
    Step 2: Hybrid search (semantic + keyword) — limit boosted to 150 for full-list queries
    Step 3: LLM answers with formatted context
    Step 4: Log the query+answer to query_logs (skipped when log_to_db=False — used by eval verifier shadow runs)
    """
    from app.models import QueryLog

    # Save the original question for database logging (no expansion, no synonyms)
    original_question = question

    # Refresh eval-loop config caches (abbreviations, stop-word drops, prompt overrides)
    # before any sync helpers consume them.
    await _ensure_eval_caches(session)

    # Step 0: Abbreviation expansion + query-type detection on the RAW question
    # (must happen before learned-synonym expansion so noisy synonyms don't
    #  accidentally trigger full_list/specific flags)
    question = _expand_hebrew_abbrevs(question)
    full_list = _is_full_list_query(question)
    specific = _is_specific_question(question) and not full_list
    # Bare project name (e.g. "חולה", "יאסיף") → treat as full-detail request.
    # Keep specific=False so max_tokens=2000 allows the full project card.
    bare_name = _is_bare_name_query(question) and not full_list

    # Expand question with learned synonyms for search, but keep this in a separate
    # variable so the original (abbreviated but not expanded) is what gets stored
    search_question = await _apply_learned_synonyms(question, session)

    # Load learned answer-improvement instructions (injected into system prompt)
    learned_instructions = await _get_learned_instructions(session)

    # Step 1: Expand the query for better search
    # Skip expansion for full-list queries — keywords suffice and we save a Groq call
    if full_list:
        expanded_query = search_question
        logger.info(f"Full-list query — skipping expansion, using search version: {repr(search_question)}")
    else:
        expanded_query = await _expand_query(search_question)
        logger.info(f"Search question: {repr(search_question)} → Expanded: {repr(expanded_query)}")
    search_limit = 200 if full_list else 30
    logger.info(f"Full-list: {full_list} | Specific: {specific} → search_limit={search_limit}")

    # ── Step 2: Anchor & Cross-Link retrieval ─────────────────────────────
    master_file_id = await _get_master_file_id(session)

    if master_file_id is not None:
        # ── Step A: Master-file retrieval ─────────────────────────────────
        # For full-list / aggregation queries: pull master chunks with limits.
        # Limit to 500 chunks to stay within Groq's token limit (~30K).
        # Each chunk is one project line, so 500 chunks handles all 233 projects.
        # If more needed, the compact format will summarize efficiently.
        if full_list:
            # Detect filters to reduce context to only relevant chunks.
            # Both manager and stage filters can be combined.
            manager_filter = _extract_manager_name_from_question(question)
            stage_filter = _extract_stage_filter_from_question(question)

            if manager_filter or stage_filter:
                base_stmt = select(KnowledgeChunk).where(KnowledgeChunk.file_id == master_file_id)

                if manager_filter:
                    parts = manager_filter.split()
                    reversed_name = f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) >= 2 else manager_filter
                    last_name = parts[-1]
                    logger.info(f"Manager filter: '{manager_filter}' → DB form '{reversed_name}'")
                    base_stmt = base_stmt.where(
                        or_(
                            KnowledgeChunk.content.ilike(f"%Manager: %{reversed_name}%"),
                            KnowledgeChunk.content.ilike(f"%Manager: %{manager_filter}%"),
                            KnowledgeChunk.content.ilike(f"%Manager: {last_name},%"),
                            KnowledgeChunk.content.ilike(f"%Manager: {last_name} %"),
                        )
                    )

                if stage_filter:
                    logger.info(f"Stage filter: '{stage_filter}'")
                    base_stmt = base_stmt.where(
                        KnowledgeChunk.content.ilike(f"%| Status: {stage_filter} |%")
                    )

                filtered_result = await session.execute(base_stmt.order_by(KnowledgeChunk.chunk_idx))
                anchor_chunks = list(filtered_result.scalars().all())
                logger.info(f"Filtered chunks: {len(anchor_chunks)} "
                            f"(manager={manager_filter or 'any'}, stage={stage_filter or 'any'})")

                if not anchor_chunks:
                    logger.warning("Filters returned 0 chunks — falling back to full master load")
                    manager_filter = None
                    stage_filter = None

            if not manager_filter and not stage_filter:
                all_master_stmt = (
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.file_id == master_file_id)
                    .order_by(KnowledgeChunk.chunk_idx)
                    .limit(500)
                )
                all_master_result = await session.execute(all_master_stmt)
                anchor_chunks = list(all_master_result.scalars().all())
                logger.info(f"Full-list mode: pulled {len(anchor_chunks)} master-file chunks (capped at 500)")

        else:
            # Specific / semantic query: vector search on master file (top-20)
            query_vector_anchor = await embed(expanded_query)
            anchor_stmt = (
                select(KnowledgeChunk)
                .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                .where(KnowledgeChunk.file_id == master_file_id)
                .where(KnowledgeFile.status == "ready")
                .where(KnowledgeChunk.embedding.isnot(None))
                .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector_anchor))
                .limit(20)
            )
            anchor_result = await session.execute(anchor_stmt)
            anchor_chunks = list(anchor_result.scalars().all())

            # Step A+: phrase search on master file using the ORIGINAL question.
            # Critical: use `question` not `expanded_query` — the LLM expansion may
            # translate or rewrite Hebrew proper nouns, losing exact strings like 'בת ים'.
            # Search for phrases directly in content (not label-restricted) so nothing is missed.
            phrases = _extract_query_phrases(question)
            # Hebrew stop words that appear in questions but not in project data.
            # Effective set = static base − DB drops − shadow drops (eval-loop tunable).
            _STOP_WORDS_BASE = {'מה', 'כל', 'של', 'על', 'את', 'הנתונים', 'הפרויקט',
                           'בפרויקט', 'לגבי', 'אנא', 'תוכל', 'ספר', 'לי',
                           'כמה', 'מתי', 'איזה', 'אילו', 'יש', 'הם', 'הן',
                           'תן', 'הצג', 'רשום', 'תרשום', 'תציג',
                           # Generic project-domain words that appear in every chunk
                           # — must NOT be in sig_phrases or they drown out specific names
                           'פרויקט', 'תאריך', 'נתונים', 'מידע',
                           # Question words (not proper nouns)
                           'מי', 'איפה', 'למה', 'מדוע', 'כיצד', 'איך',
                           # Domain verbs/nouns appearing in every Manager/Status field
                           'מנהל', 'מנהלת', 'סטטוס', 'עדכון', 'שלב'}
            _STOP_WORDS = _STOP_WORDS_BASE - _DB_STOP_WORD_DROPS_CACHE - _shadow_stop_word_drops.get()
            # Multi-word phrases: filter out generic question phrases (stop-word combos)
            multi = [p for p in phrases if ' ' in p
                     and not all(w in _STOP_WORDS for w in p.split())]
            # Single words: only include if long enough to be a proper noun (>=3 chars)
            # and not a stop word — these are city/project names like "יזרעאל", "נתניה"
            proper_nouns = [p for p in phrases if ' ' not in p and len(p) >= 3
                            and p not in _STOP_WORDS]
            # Always use proper nouns; add multi-word phrases only if they exist
            sig_phrases = list(dict.fromkeys(proper_nouns + multi))  # deduplicated, order preserved
            if sig_phrases:
                phrase_stmt = (
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.file_id == master_file_id)
                    .where(or_(*[KnowledgeChunk.content.ilike(f"%{p}%") for p in sig_phrases]))
                    .limit(20)
                )
                phrase_result = await session.execute(phrase_stmt)
                seen_anchor = {c.id for c in anchor_chunks}
                phrase_hits_list = []
                for c in phrase_result.scalars().all():
                    if c.id not in seen_anchor:
                        phrase_hits_list.append(c)   # collect first, prepend below
                        seen_anchor.add(c.id)
                if phrase_hits_list:
                    # Prepend so phrase hits (exact project/keyword matches) appear
                    # BEFORE semantic chunks — ensures they survive the 10,000-char
                    # context truncation and the LLM always sees the target project.
                    anchor_chunks = phrase_hits_list + anchor_chunks
                    logger.info(f"Phrase label-search prepended {len(phrase_hits_list)} chunks for phrases: {sig_phrases[:5]}")

        logger.info(f"Anchor search: {len(anchor_chunks)} master-file chunks")

        # Step B & C — cross-link (skip for full_list to save tokens; focus on master file)
        cross_chunks: list = []
        if not full_list:
            # For specific queries, cross-link to supporting files
            wbs_codes, project_names = _extract_wbs_and_projects_from_chunks(anchor_chunks)
            logger.info(f"Extracted {len(wbs_codes)} WBS codes, {len(project_names)} project names from anchor")
            if wbs_codes or project_names:
                search_terms = list(wbs_codes) + [pn[:40] for pn in project_names]
                cross_stmt = (
                    select(KnowledgeChunk)
                    .join(KnowledgeFile, KnowledgeChunk.file_id == KnowledgeFile.id)
                    .where(KnowledgeFile.status == "ready")
                    .where(KnowledgeChunk.file_id != master_file_id)
                    .where(or_(*[KnowledgeChunk.content.ilike(f"%{t}%") for t in search_terms if t]))
                    .limit(search_limit * 2)
                )
                cross_result = await session.execute(cross_stmt)
                cross_chunks = list(cross_result.scalars().all())
                logger.info(f"Cross-link: {len(cross_chunks)} supporting chunks")

        # Step D — merge: master chunks (priority 1) + supporting chunks (priority 2)
        seen_ids: set = set()
        chunks: list = []
        for c in anchor_chunks:
            if c.id not in seen_ids:
                chunks.append(c)
                seen_ids.add(c.id)
        for c in cross_chunks:
            if c.id not in seen_ids:
                chunks.append(c)
                seen_ids.add(c.id)
        logger.info(f"Merged: {len(anchor_chunks)} master + {len(cross_chunks)} cross = {len(chunks)} total")

    else:
        # No master file — fall back to existing full hybrid search
        chunks = await search_knowledge(expanded_query, session, limit=search_limit)
        logger.info(f"No master file — standard hybrid search: {len(chunks)} chunks")

    # Step 2b: Semantic reranking — push chunks matching query keywords to the top
    if chunks:
        chunks = _rerank_by_query_keywords(chunks, question)

    # Step 2c: Metadata aggregation — for full-list queries WITHOUT master file,
    # pull ALL chunks from non-master files that appeared in results.
    # NOTE: We skip this if master_file_id is set, since we already got capped master chunks.
    # If we added this aggregation, it would pull all 200+ remaining master chunks,
    # exceeding the token limit. The compact formatter groups by project anyway.
    if full_list and chunks and master_file_id is None:
        relevant_file_ids = list({c.file_id for c in chunks})
        all_file_chunks_stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.file_id.in_(relevant_file_ids))
            .order_by(KnowledgeChunk.file_id, KnowledgeChunk.chunk_idx)
        )
        all_file_chunks_result = await session.execute(all_file_chunks_stmt)
        all_file_chunks = all_file_chunks_result.scalars().all()
        seen_ids_agg = {c.id for c in chunks}
        added = 0
        for c in all_file_chunks:
            if c.id not in seen_ids_agg:
                chunks.append(c)
                seen_ids_agg.add(c.id)
                added += 1
        if added:
            logger.info(f"Metadata aggregation added {added} chunks from {len(relevant_file_ids)} files")

    decisions_ctx = await get_decisions_context(session, user_id)

    # Build file-name map and apply file-priority sorting before formatting.
    # Files with 'עדכני' or 'Final' in their name are treated as authoritative;
    # their chunks are surfaced first so the LLM sees the freshest data.
    _PRIORITY_MARKERS = ('עדכני', 'final', 'Final', 'FINAL')
    file_name_map: dict[int, str] = {}
    if chunks:
        unique_fids = {c.file_id for c in chunks}
        for fid in unique_fids:
            kf = await session.get(KnowledgeFile, fid)
            if kf:
                file_name_map[fid] = kf.original_name

        def _priority_score(chunk: KnowledgeChunk) -> int:
            name = file_name_map.get(chunk.file_id, "")
            return 0 if any(m in name for m in _PRIORITY_MARKERS) else 1

        chunks = sorted(chunks, key=_priority_score)  # priority files first

    parts = []
    if chunks:
        parts.append(format_knowledge_context(chunks, compact=full_list, file_name_map=file_name_map))
    if decisions_ctx:
        parts.append(decisions_ctx)

    if not parts:
        answer = "לא נמצא מידע רלוונטי. העלה קבצים או הגש החלטות תחילה."
        log_id = None
        if log_to_db:
            from app.services.llm_router import get_last_llm_meta
            _provider, _is_fb = get_last_llm_meta()
            log = QueryLog(question=original_question, ai_response=answer, sources_used=[], user_id=user_id,
                           llm_provider=_provider or None, is_fallback=_is_fb or None)
            session.add(log)
            await session.commit()
            await session.refresh(log)
            log_id = log.id
        return {
            "answer": answer,
            "has_files": False,
            "has_decisions": False,
            "file_names": [],
            "sources_text": "",
            "log_id": log_id,
        }

    combined = "\n\n".join(parts)

    # Groq free tier: 12,000 tokens per request.
    # Hebrew text ≈ 0.85 tokens/char (much denser than English).
    # Budget: 12,000 - 2,000 (output) - 1,200 (system prompt) = 8,800 tokens → ~10,350 chars.
    MAX_CONTEXT_CHARS = 10_000
    if len(combined) > MAX_CONTEXT_CHARS:
        combined = combined[:MAX_CONTEXT_CHARS] + "\n\n[...ההקשר קוצר בשל מגבלת טוקנים]"
        logger.warning(f"⚠️ Context truncated to {MAX_CONTEXT_CHARS} chars to stay within Groq token limit")

    # Log context size for debugging
    context_tokens_approx = int(len(combined) * 0.85)  # Hebrew ≈ 0.85 tokens/char
    logger.info(f"Context for answer_question: {len(combined)} chars, ~{context_tokens_approx} tokens, "
                f"full_list={full_list}, specific={specific}")
    if context_tokens_approx > 8000:
        logger.warning(f"⚠️ Context is large ({context_tokens_approx} tokens) — answers may be cut short")

    answer = await answer_question(
        question, combined,
        full_list=full_list,
        specific=specific,
        learned_instructions=learned_instructions,
        bare_name=bare_name,
    )

    # Collect unique file names used
    file_names = []
    if chunks:
        seen = set()
        for chunk in chunks:
            if chunk.file_id not in seen:
                seen.add(chunk.file_id)
                kf = await session.get(KnowledgeFile, chunk.file_id)
                if kf:
                    file_names.append(kf.original_name)

    # Build a short sources line
    source_parts = []
    if decisions_ctx:
        source_parts.append("📋 מסד ההחלטות")
    if file_names:
        source_parts.append("📁 " + " | ".join(file_names))
    sources_text = "מקורות: " + " · ".join(source_parts) if source_parts else ""

    # Step 4: Log query to DB (skipped when log_to_db=False, e.g. eval-verifier shadow runs)
    sources_payload = [{"file": name} for name in file_names]
    if decisions_ctx:
        sources_payload.append({"source": "decisions_db"})
    log_id = None
    if log_to_db:
        from app.services.llm_router import get_last_llm_meta
        _provider, _is_fb = get_last_llm_meta()
        log = QueryLog(
            question=original_question,
            ai_response=answer,
            sources_used=sources_payload,
            user_id=user_id,
            llm_provider=_provider or None,
            is_fallback=_is_fb or None,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        log_id = log.id

    return {
        "answer": answer,
        "has_files": bool(chunks),
        "has_decisions": bool(decisions_ctx),
        "file_names": file_names,
        "sources_text": sources_text,
        "log_id": log_id,
    }
