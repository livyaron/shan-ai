"""In-browser preview of knowledge-base files (DOCX / XLSX).

A browser can show a PDF by itself; Word and Excel it can only download. This
reads them into plain blocks the `file_open.html` template draws, so a click on
a file in the knowledge base opens it on screen. Pure: path in, data out.
Large workbooks are capped (`MAX_ROWS` / `MAX_COLS` per sheet) — the preview is
for reading, the download button is for the whole file.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

MAX_ROWS = 400
MAX_COLS = 40
MAX_SHEETS = 15


def _col_letter(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA — the header a reader matches against Excel."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


COL_LETTERS = [_col_letter(i) for i in range(MAX_COLS)]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, _dt.datetime):
        return v.strftime("%d/%m/%Y") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, _dt.date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def preview_docx(path: str) -> list[dict]:
    """Body in document order: {"kind": "p", "text", "style"} / {"kind": "table", "rows"}."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    blocks: list[dict] = []
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, doc)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            kind = "h" if style.lower().startswith(("heading", "title")) or style.startswith("כותרת") else "p"
            blocks.append({"kind": kind, "text": text})
        elif tag == "tbl":
            t = Table(el, doc)
            rows = []
            for r in t.rows:
                cells, prev = [], None
                for c in r.cells:
                    # merged cells come back once per grid column — keep one
                    if c._tc is prev:
                        continue
                    prev = c._tc
                    cells.append(c.text.strip())
                rows.append(cells)
            if any(any(c for c in r) for r in rows):
                blocks.append({"kind": "table", "rows": rows})
    return blocks


def preview_xlsx(path: str) -> list[dict]:
    """One dict per sheet: {"name", "rows", "truncated_rows", "truncated_cols"}."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    sheets: list[dict] = []
    try:
        for ws in wb.worksheets[:MAX_SHEETS]:
            rows: list[list[str]] = []
            more_rows = more_cols = False
            for i, raw in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS:
                    more_rows = True
                    break
                vals = [_cell(v) for v in raw]
                if len(vals) > MAX_COLS:
                    more_cols = more_cols or any(vals[MAX_COLS:])
                    vals = vals[:MAX_COLS]
                rows.append(vals)
            # trim empty trailing rows/columns (read-only mode reports the used range loosely)
            while rows and not any(rows[-1]):
                rows.pop()
            width = max((max((j + 1 for j, v in enumerate(r) if v), default=0) for r in rows), default=0)
            rows = [(r + [""] * width)[:width] for r in rows]
            sheets.append({
                "name": ws.title,
                "rows": rows,
                "truncated_rows": more_rows,
                "truncated_cols": more_cols,
            })
    finally:
        wb.close()
    return sheets
