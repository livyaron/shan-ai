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
# A structured reference to a column of the record sheet's table — טבלה13[[חוסר בבודקים ]] /
# Table1[Column]. The file moved to these on 10.11.26; with only cell ranges read,
# every such card lost its project list. Special items ([#All], [@col]) are not re-run.
_TABLE_COL = re.compile(r"^[^\[\]!'\s]+\[\[?([^\[\]#@]+?)\]?\]$")
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
        t = _TABLE_COL.match(rng.strip())
        if t:
            span = _table_column(rows, t.group(1))
            test = _filled if crit is None else _criterion(crit)
            if span is None or test is None:
                return None
            conds.append((*span, test))
            continue
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


def _table_column(rows: list[list], column: str) -> tuple[int, int, int] | None:
    """(col, first Excel row, last Excel row) of a record-table column named in
    a structured reference: the header row is the one holding "זיהוי", the name
    is compared trimmed (the file writes "חוסר בבודקים " with a trailing space),
    and it must appear exactly once — otherwise the card is not re-run."""
    want = column.strip()
    for h, row in enumerate(rows[:6]):
        if any(isinstance(v, str) and v.strip() == "זיהוי" for v in row):
            hits = [i for i, v in enumerate(row) if isinstance(v, str) and v.strip() == want]
            return (hits[0], h + 2, len(rows)) if len(hits) == 1 and len(rows) > h + 1 else None
    return None


# ── A card the file gives as a typed number: its definition, in code ───────
# From the 10.11.26 file most summary cards are plain numbers, no formula, so
# there is nothing to re-run. Their definitions are the file's own, read from
# the formulas of the earlier summaries (03.11 … 07.10), restated here over the
# record sheet's column NAMES (letters shift whenever a column is added). The
# two "דחייה מול…" cards never had a formula; each has a short list of candidate
# definitions and the one whose count equals the file's number is taken.
COL_NAME = "שם פרויקט"
COL_RISK = "פירוט סיכונים וחסמים עיקריים"
COL_HANDLE = "לטיפול"
COL_HANDLE_2 = "לטיפול נוסף (במידה ונדרש)"
COL_CRITICAL = "פרויקטים קריטים"
COL_TESTERS = "חוסר בבודקים"
COL_SUPERVISORS = "חוסר במשגיחים"
COL_STORAGE = "פרויקטים התלויים בחיבור למתקני אגירה"
COL_COMMS = "האם נדרש תכנון תקשורת?"
COL_DEV = "יעד תכנית פיתוח"
COL_FC = "יעד חשמול מסתמן"
COL_FC_CALC = "יעד מסתמן לחישוב"
COL_APPROVED = 'יעד חשמול מאושר ע"י מנהל האגף'
WEEKLY_PREFIX = "פירוט שבועי"
_LABEL_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})\s*$")


def _norm_label(t: object) -> str:
    return re.sub(r"\s+", " ", str(t or "").replace("״", '"').replace("”", '"')).strip()


def _yes(v) -> bool:
    return _filled(v) and str(v).strip() != "לא"


def _as_date(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    return v if isinstance(v, datetime.date) else None


def _later(a, b, days: int = 0) -> bool:
    x, y = _as_date(a), _as_date(b)
    return x is not None and y is not None and (x - y).days > days


def rule_ids(label: str, value, rows: list[list], ident_col: int) -> tuple[list[str], str] | None:
    """(identifiers, the definition in words) for a card that has no formula to
    re-run; None = a label this module has no definition for."""
    head_i = next((h for h, r in enumerate(rows[:6]) if any(isinstance(v, str) and v.strip() == "זיהוי" for v in r)), None)
    if head_i is None:
        return None
    header = [_norm_label(v) if isinstance(v, str) else "" for v in rows[head_i]]

    def col(name: str) -> int | None:
        hits = [i for i, h in enumerate(header) if h == _norm_label(name)]
        return hits[0] if len(hits) == 1 else None

    data = [r for r in rows[head_i + 1:] if ident_col < len(r) and _filled(r[ident_col])]

    def pick(test) -> list[str]:
        return [str(r[ident_col]).strip() for r in data if test(lambda i: r[i] if i is not None and i < len(r) else None)]

    name = _norm_label(label)
    risk, handle, handle2, critical = col(COL_RISK), col(COL_HANDLE), col(COL_HANDLE_2), col(COL_CRITICAL)
    need = lambda *cols: all(c is not None for c in cols)   # noqa: E731

    if name.startswith('סה"כ פרויקטים') and need(col(COL_NAME)):
        return pick(lambda c: _filled(c(col(COL_NAME)))), "כל שורה בגיליון הרשומות שיש לה שם פרויקט"
    if name == "עם סיכון/חסם" and need(risk):
        return pick(lambda c: _filled(c(risk))), f"העמודה ״{COL_RISK}״ מלאה"
    if name == "מסומנים קריטיים" and need(critical):
        return pick(lambda c: _filled(c(critical))), f"העמודה ״{COL_CRITICAL}״ מלאה"
    if name == "קריטי + סיכון" and need(critical, risk):
        return pick(lambda c: _filled(c(critical)) and _filled(c(risk))), "מסומן קריטי וגם יש לו סיכון/חסם"
    if name.startswith("סיכון ללא") and need(risk, handle):
        if "שני" in name and handle2 is not None:
            return (pick(lambda c: _filled(c(risk)) and not _filled(c(handle)) and not _filled(c(handle2))),
                    "יש סיכון/חסם, ושתי עמודות הטיפול ריקות")
        return pick(lambda c: _filled(c(risk)) and not _filled(c(handle))), "יש סיכון/חסם, והעמודה ״לטיפול״ ריקה"
    if name.startswith("קריטי ללא עדכון") and need(critical):
        weekly = [i for i, h in enumerate(header) if h.startswith(WEEKLY_PREFIX)]
        m = _LABEL_DATE.search(name)
        dated = [i for i in weekly if m and re.search(rf"\b0?{int(m.group(1))}/0?{int(m.group(2))}/", header[i])]
        week = (dated or weekly[-1:] or [None])[-1]
        if week is not None:
            return (pick(lambda c: _filled(c(critical)) and not _filled(c(week))),
                    f"מסומן קריטי, והעמודה ״{header[week]}״ ריקה")
    if name == "מחסור בבודקים" and need(col(COL_TESTERS)):
        return pick(lambda c: _yes(c(col(COL_TESTERS)))), f"העמודה ״{COL_TESTERS}״ מסומנת"
    if name == "מחסור במשגיחים" and need(col(COL_SUPERVISORS)):
        return pick(lambda c: _yes(c(col(COL_SUPERVISORS)))), f"העמודה ״{COL_SUPERVISORS}״ מסומנת"
    if name == "תלויים במתקני אגירה" and need(col(COL_STORAGE)):
        return pick(lambda c: _filled(c(col(COL_STORAGE)))), f"העמודה ״{COL_STORAGE}״ מלאה"
    if name == "נדרש תכנון תקשורת" and need(col(COL_COMMS)):
        return pick(lambda c: _yes(c(col(COL_COMMS)))), f"העמודה ״{COL_COMMS}״ מסומנת"
    target = {"דחייה מול תכנית פיתוח": (COL_DEV, "יעד תכנית הפיתוח"), "דחייה מול יעד מאושר": (COL_APPROVED, "היעד המאושר")}.get(name)
    if target and need(col(target[0])):
        base, fc, calc = col(target[0]), col(COL_FC), col(COL_FC_CALC)
        options = []                    # the file's own number picks among these; the first is the default
        for source, source_name in ((fc, "יעד החשמול המסתמן"), (calc, "היעד המסתמן לחישוב")):
            if source is None:
                continue
            for days, words in ((0, ""), (ps_month_days(), " ביותר מחודש")):
                options.append((pick(lambda c, s=source, d=days: _later(c(s), c(base), d)),
                                f"{source_name} מאוחר מ{target[1]}{words}"))
        if options:
            return next((o for o in options if isinstance(value, (int, float)) and len(o[0]) == value), options[0])
    return None


def ps_month_days() -> int:
    """One month in days — pattern_service's constant, not a second copy."""
    from app.services import pattern_service
    return int(pattern_service.MONTH_DAYS)


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
            if k["ids"] is None:        # no formula (or one we do not re-run): the definition in code
                found = rule_ids(k["label"], k.get("value"), record_rows, ident_col)
                if found:
                    k["ids"], k["rule"] = found
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


def unresolved(payload: dict | None) -> int:
    """How many of the summary's cards open no project list."""
    return sum(1 for k in (payload or {}).get("kpis") or [] if k.get("ids") is None)


async def reread_if_unresolved(session: AsyncSession) -> bool:
    """After a deploy that taught `resolve` a new formula shape: re-read the
    newest master file from disk and save its summary again, ONLY when that
    opens more cards than the stored one. Never raises; False = nothing changed."""
    import asyncio
    import os

    from app.models import KnowledgeFile
    try:
        stored = await latest(session)
        if not unresolved(stored):
            return False
        kf = (await session.execute(select(KnowledgeFile).where(KnowledgeFile.is_master)
                                    .order_by(KnowledgeFile.created_at.desc()).limit(1))).scalars().first()
        if not kf or not kf.file_path or not os.path.exists(kf.file_path):
            logger.info("exec_summary: master file is not on disk — cards stay as stored until the next upload")
            return False
        fresh = await asyncio.get_running_loop().run_in_executor(None, read_file, kf.file_path)
        if not fresh or fresh.get("sheet") != stored.get("sheet") or unresolved(fresh) >= unresolved(stored):
            return False
        await save(session, fresh, kf.original_name)
        return True
    except Exception:
        logger.exception("exec_summary: reread_if_unresolved failed")
        return False


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
