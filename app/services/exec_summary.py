"""The "תקציר הנהלה" sheet of the master workbook → the projects dashboard.

The PM department writes a management summary inside the weekly master file
(KPI pairs, a priorities table, proposed messages, a shortage table). The
dashboard shows THAT, as written — it is the division's own reading of the
week, and the number of KPIs and rows changes from file to file, so nothing
here assumes a fixed layout:

    a row with one text cell            → title / subtitle / section title
    a row of (label, number) pairs      → KPIs
    the first all-short-text row of a section → its header
    anything else                       → a row of the current section

`parse_rows` is pure (tested on synthetic rows); `read_file` is the only file
access; `save` / `latest` the only DB access, both best-effort.
"""
import datetime
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SHEET_MARKER = "תקציר הנהלה"
RECORD_MARKER = "עדכני"     # = project_sync.MASTER_SHEET_MARKER: the record sheet the formulas count
DRAFT_MARKER = "טיוטה"
HEADER_CELL_CHARS = 40        # a header cell is a short label, never a sentence
CELL_CHARS = 600              # one cell on screen; the file keeps the rest
ID_HEADERS = ("מזהה", "זיהוי")  # this column links to the project page

_SHEET_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def sheet_date(name: str) -> datetime.date | None:
    m = _SHEET_DATE.search(name or "")
    if not m:
        return None
    d, mo, y = (int(g) for g in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def pick_sheet(names: list[str]) -> str | None:
    """The summary sheet; with several dated copies, the newest date wins."""
    found = [n for n in names if SHEET_MARKER in n]
    if not found:
        return None
    return max(found, key=lambda n: (sheet_date(n) or datetime.date.min, found.index(n)))


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_text(v) -> bool:
    return _is_num(v) or (isinstance(v, str) and bool(_NUMERIC.match(v.strip())))


def _cell(v) -> str:
    """A table cell as text. A bare 0 is an empty source cell seen through a formula."""
    if v is None or (_is_num(v) and v == 0) or (isinstance(v, str) and v.strip() == "0"):
        return ""
    if isinstance(v, datetime.datetime):
        v = v.date()
    if isinstance(v, datetime.date):
        return v.strftime("%d/%m/%Y")
    if _is_num(v):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    return s if len(s) <= CELL_CHARS else s[:CELL_CHARS - 1] + "…"


def _kpi_pairs(cells: list) -> list[tuple[str, Any, int]] | None:
    """[label, number, label, number…] → (label, value, value's column index);
    None when the row is not that."""
    vals = [(i, v) for i, v in enumerate(cells) if v is not None and str(v).strip() != ""]
    if not vals or len(vals) % 2:
        return None
    pairs = []
    for (_, label), (col, value) in zip(vals[::2], vals[1::2]):
        if not isinstance(label, str) or _numeric_text(label) or not _is_num(value):
            return None
        pairs.append((label.strip(), int(value) if float(value).is_integer() else value, col))
    return pairs


def _is_header(cells: list) -> bool:
    vals = [v for v in cells if v is not None and str(v).strip() != ""]
    return len(vals) >= 2 and all(
        isinstance(v, str) and not _numeric_text(v) and len(v.strip()) <= HEADER_CELL_CHARS
        for v in vals)


def parse_rows(rows: list[list], sheet: str = "", formula_rows: list[list] | None = None) -> dict:
    """Rows of raw cell values (column order kept) → the dashboard payload.

    formula_rows: the same sheet read WITH formulas — each KPI keeps the formula
    behind its number, so the number can be re-counted over the record sheet
    (`resolve`) and every card opens the exact projects it counts."""
    out = {"sheet": sheet, "as_of": None, "title": None, "subtitle": None,
           "kpis": [], "sections": []}
    d = sheet_date(sheet)
    out["as_of"] = d.isoformat() if d else None
    section = None
    for row_i, raw in enumerate(rows):
        cells = list(raw)
        while cells and (cells[-1] is None or str(cells[-1]).strip() == ""):
            cells.pop()
        filled = [v for v in cells if v is not None and str(v).strip() != ""]
        if not filled:
            continue
        if len(filled) == 1 and isinstance(filled[0], str) and not _numeric_text(filled[0]):
            text = filled[0].strip()
            if out["title"] is None and not out["kpis"] and not out["sections"]:
                out["title"] = text
            elif out["subtitle"] is None and not out["kpis"] and not out["sections"]:
                out["subtitle"] = text
            else:
                section = {"title": text, "header": None, "rows": [], "id_col": None}
                out["sections"].append(section)
            continue
        pairs = _kpi_pairs(cells)
        if pairs and section is None:
            for label, value, col in pairs:
                f = None
                if formula_rows and row_i < len(formula_rows) and col < len(formula_rows[row_i]):
                    f = formula_rows[row_i][col]
                out["kpis"].append({"label": label, "value": value,
                                    "formula": f if isinstance(f, str) and f.startswith("=") else None,
                                    "ids": None})
            continue
        if section is None:
            section = {"title": None, "header": None, "rows": [], "id_col": None}
            out["sections"].append(section)
        if section["header"] is None and not section["rows"] and _is_header(cells):
            section["header"] = [_cell(v) for v in cells]
            section["id_col"] = next(
                (i for i, h in enumerate(section["header"]) if h.strip() in ID_HEADERS), None)
            continue
        section["rows"].append([_cell(v) for v in cells])

    # every row of a section is as wide as its widest row, so the table lines up
    for s in out["sections"]:
        width = max([len(s["header"] or [])] + [len(r) for r in s["rows"]])
        if s["header"] is not None:
            s["header"] += [""] * (width - len(s["header"]))
        for r in s["rows"]:
            r += [""] * (width - len(r))
        # a column that is empty in every row (a spacer) is not drawn
        keep = [i for i in range(width)
                if any(r[i] for r in s["rows"]) or (s["header"] and s["header"][i])]
        if s["header"] is not None:
            s["header"] = [s["header"][i] for i in keep]
        s["rows"] = [[r[i] for i in keep] for r in s["rows"]]
        if s["id_col"] is not None:
            s["id_col"] = keep.index(s["id_col"]) if s["id_col"] in keep else None
    out["sections"] = [s for s in out["sections"] if s["rows"]]
    return out


# ── Re-counting a KPI over the record sheet ────────────────────────────────
# The summary's numbers are Excel formulas over "דוח שבועי עדכני" — COUNTA /
# COUNTIF / COUNTIFS of filled or empty cells. Re-running them here gives the
# PROJECTS behind each number, so the dashboard counts them over the ACTIVE
# projects only (user decision 2026-10-16) and every card opens its own list.
_FUNC = re.compile(r"^=\s*(COUNTA|COUNTIFS|COUNTIF)\s*\((.*)\)\s*$", re.I | re.S)
_RANGE = re.compile(r"^(?:'((?:[^']|'')+)'|([^!'\s]+))!\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)$")


def _split_args(s: str) -> list[str]:
    out, cur, quoted = [], "", False
    for ch in s:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


def _criterion(arg: str):
    """A COUNTIF criterion → test(value) | None when not a form we can trust."""
    if not (arg.startswith('"') and arg.endswith('"')):
        return None
    c = arg[1:-1]
    if c == "<>":
        return _filled
    if c == "":
        return lambda v: not _filled(v)
    if c.startswith("<>"):
        return lambda v, x=c[2:]: str(v).strip() != x if _filled(v) else True
    c = c[1:] if c.startswith("=") else c
    if any(op in c for op in ("<", ">", "*", "?")):
        return None
    return lambda v, x=c: _filled(v) and str(v).strip() == x


def resolve(formula: str | None, sheet: str, rows: list[list], ident_col: int) -> list[str] | None:
    """The identifiers of the record-sheet rows a summary formula counts.
    rows: the record sheet, 0-based (rows[0] = Excel row 1). None = a formula
    this module does not re-run (the card then shows the file's own number)."""
    m = _FUNC.match(formula or "")
    if not m:
        return None
    func, args = m.group(1).upper(), _split_args(m.group(2))
    conds: list[tuple[int, int, int, Any]] = []      # (col, first row, last row, test)
    if func == "COUNTA":
        pairs = [(a, None) for a in args]
        if len(pairs) != 1:
            return None
    elif func == "COUNTIF" and len(args) == 2:
        pairs = [(args[0], args[1])]
    elif func == "COUNTIFS" and len(args) >= 2 and len(args) % 2 == 0:
        pairs = list(zip(args[::2], args[1::2]))
    else:
        return None
    for rng, crit in pairs:
        r = _RANGE.match(rng.strip())
        if not r:
            return None
        name = (r.group(1) or r.group(2) or "").replace("''", "'")
        if name.strip() != sheet.strip() or r.group(3) != r.group(5):
            return None
        test = _filled if crit is None else _criterion(crit)
        if test is None:
            return None
        conds.append((_col_index(r.group(3)), int(r.group(4)), int(r.group(6)), test))
    first, last = max(c[1] for c in conds), min(c[2] for c in conds)
    if any(c[1] != first or c[2] != last for c in conds):
        return None                                  # ranges must line up row by row
    ids = []
    for excel_row in range(first, last + 1):
        row = rows[excel_row - 1] if excel_row - 1 < len(rows) else []
        cell = lambda i: row[i] if i < len(row) else None   # noqa: E731
        if all(test(cell(col)) for col, _, _, test in conds):
            ident = cell(ident_col)
            if _filled(ident):
                ids.append(str(ident).strip())
    return ids


def _ident_col(rows: list[list]) -> int | None:
    for row in rows[:6]:
        for i, v in enumerate(row):
            if isinstance(v, str) and v.strip() == "זיהוי":
                return i
    return None


def read_file(file_path: str) -> dict | None:
    """The summary sheet of an XLSX, parsed, each KPI with the projects it counts;
    None when the workbook has none."""
    if not str(file_path).lower().endswith((".xlsx", ".xlsm")):
        return None
    import openpyxl
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        wf = openpyxl.load_workbook(file_path, data_only=False, read_only=True)
    except Exception as exc:
        logger.warning(f"exec_summary: cannot open {file_path}: {exc}")
        return None
    try:
        name = pick_sheet(wb.sheetnames)
        if name is None:
            logger.info(f"exec_summary: no '{SHEET_MARKER}' sheet in {file_path} — dashboard keeps the previous one")
            return None
        rows = [list(r) for r in wb[name].iter_rows(values_only=True)]
        formulas = [list(r) for r in wf[name].iter_rows(values_only=True)]
        record = next((n for n in wb.sheetnames if RECORD_MARKER in n and DRAFT_MARKER not in n), None)
        record_rows = [list(r) for r in wb[record].iter_rows(values_only=True)] if record else []
    finally:
        wb.close()
        wf.close()
    payload = parse_rows(rows, name, formulas)
    if not payload["kpis"] and not payload["sections"]:
        logger.warning(f"exec_summary: sheet '{name}' parsed empty")
        return None
    ident_col = _ident_col(record_rows)
    for k in payload["kpis"]:
        if record and ident_col is not None:
            k["ids"] = resolve(k["formula"], record, record_rows, ident_col)
        if k["ids"] is not None and len(k["ids"]) != k["value"]:
            logger.warning(f"exec_summary: '{k['label']}' — file says {k['value']}, re-count found "
                           f"{len(k['ids'])} rows with an identifier")
    return payload


async def save(session: AsyncSession, payload: dict, source_file: str | None) -> None:
    from app.models import ProjectExecSummary
    try:
        session.add(ProjectExecSummary(
            sheet_name=payload.get("sheet") or "",
            as_of=datetime.date.fromisoformat(payload["as_of"]) if payload.get("as_of") else None,
            source_file=(source_file or "")[:300],
            payload=payload,
        ))
        await session.commit()
        logger.info(f"exec_summary: saved '{payload.get('sheet')}' — {len(payload.get('kpis') or [])} KPIs, "
                    f"{len(payload.get('sections') or [])} tables")
    except Exception:
        logger.exception("exec_summary: save failed")
        await session.rollback()


async def save_from_file(file_path: str, source_file: str | None) -> None:
    """Parse + save in a session of its own — called after a LIVE sync only. Never raises."""
    import asyncio
    from app.database import async_session_maker
    try:
        payload = await asyncio.get_running_loop().run_in_executor(None, read_file, file_path)
        if payload:
            async with async_session_maker() as session:
                await save(session, payload, source_file)
    except Exception:
        logger.exception("exec_summary: save_from_file failed")


async def latest(session: AsyncSession) -> dict | None:
    """The summary of the newest LIVE master upload (history replays never save one)."""
    from app.models import ProjectExecSummary
    row = (await session.execute(
        select(ProjectExecSummary.payload, ProjectExecSummary.created_at)
        .order_by(ProjectExecSummary.id.desc()).limit(1)
    )).first()
    if not row:
        return None
    payload, created = row
    from app.services.il_format import il_date, il_datetime
    return {**payload,
            "as_of_il": il_date(payload.get("as_of")) if payload.get("as_of") else None,
            "loaded_at_il": il_datetime(created) if created else None}
