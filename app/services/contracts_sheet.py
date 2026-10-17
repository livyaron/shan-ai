"""The master file's "סטטוס התקשרויות קבלנים ותיחורים" sheet → its own projects page.

One row per work in the contracting pipeline (a new substation, an expansion,
a fire-fighting tank…). The procurement desk writes the status as a running
diary ("ב10.06 התקיים סיור … ב17.05 התקבל מכתב זכייה ע"ש X בסך Y ₪ …"), so
everything shown beyond the raw cells is READ out of that prose by rules —
no model, no number invented:

    events      the diary split at each "ב<DD.MM>" → dated steps
    milestones  `LADDER` — the furthest step the text says already HAPPENED
                (a "צפי ל…" / "נקבע ל…" phrase is a plan, never a step reached)
    money       an amount after a received award = the award; after
                "חבילת מסירה" / "דרישה" = the package estimate
    runway      days from the report date to the ground-entry target, for a
                work that has no signed order yet — the page's headline risk

The sheet carries its own "as of" (a lone date in the header row, else the
newest date in its diary). Past/plan is judged against THAT date — the writer's
"now" — while the runway is measured from the report date, and the page says
how far apart the two are: a sheet left untouched for months is a finding.

Row colours: only colours the sheet's own legend names are read (a row with a
label and a filled cell, no project) — an unexplained colour is never guessed.

`parse_rows` is pure (tested on synthetic rows); `read_file` is the only file
access; `save` / `latest` the only DB access, both best-effort.
"""
from __future__ import annotations

import datetime
import logging
import re
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import target_reading

logger = logging.getLogger(__name__)

SHEET_MARKERS = ("התקשרויות", "תיחור")      # either one names the sheet
RUNWAY_SOON_DAYS = 45      # ground entry this close with no signed order = at risk
STALE_DAYS = 21            # the sheet's own date this far behind the report = stale
SILENT_DAYS = 30           # a live row whose diary's last step is older than this
CELL_CHARS = 2000

# --- header → field (first match wins; matched against the header text) -------
_HEADERS = (
    ("ident", re.compile(r"מספר\s*ה?פרויקט|זיהוי|מזהה")),
    ("procedure", re.compile(r"סוג\s*הליך|הליך")),
    ("manager", re.compile(r"מנהל\s*פרויקט|מנה\S?פ")),
    ("transfer", re.compile(r"העברה\s*לרכש|לרכש")),
    ("status", re.compile(r"סטטוס")),
    ("ground", re.compile(r"היתר|קרקע")),
    ("plan", re.compile(r"ת\.?\s*פ\.?|תכנית|תוכנית")),
    ("forecast", re.compile(r"מסתמן")),
    ("name", re.compile(r"שם|עבודה|תיאור")),
)

# --- the pipeline, in order. (key, label, reached-pattern) -------------------
DOCS, PROCUREMENT, TOUR, BIDS, SELECTION, AWARD, ORDER = (
    "docs", "procurement", "tour", "bids", "selection", "award", "order")
LADDER = (
    (DOCS, "הכנת מסמכים", None),               # the entry state: nothing reached yet
    (PROCUREMENT, "הועבר לרכש", re.compile(r"הועבר\S*\s+לרכש")),
    (TOUR, "סיור קבלנים", re.compile(
        r"התקיים\s+סיור|סיור\s+(?:ה?קבלנים\s+)?התקיים|התקיימו\s+\d+\s+(?:מפגשי|סיורי)")),
    (BIDS, "הגשת הצעות", re.compile(r"הוגשו\s+(?:ה)?הצעות|התקבלו\s+(?:ה)?הצעות|בבדיקת\s+הצעות")),
    (SELECTION, "בחירת קבלן", re.compile(
        r"אישור\s+(?:ה)?קבלן\s+בו?ועדה|התקיים\s+דיון\s+לבחירת\s+קבלן|נבחר\s+(?:ה)?קבלן")),
    (AWARD, "הודעת זכייה", re.compile(
        r"התקבל\S*\s+(?:מכתב|הודעת)\s+זכי|נשלחה\s+הזמנה\s+לחתימת")),
    (ORDER, "הזמנה חתומה", re.compile(
        r"(?:התקבלה|יש)\s+הזמנה\s+חתומה|שהחזיר\s+חתום|הסתיים\W*$")),
)
STAGE_KEYS = [k for k, _, _ in LADDER]
STAGE_LABELS = {k: label for k, label, _ in LADDER}
# a dated submission ("הגשה ב14.07") / tour ("סיור נקבע ל16.07") counts once its date passed
_DATED_STEP = (
    (TOUR, re.compile(r"(?:נקבע\s+סיור\s+ל?|סיור\s+(?:ה?קבלנים\s+)?נקבע\s+ל?|סיור\s+ב)\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)")),
    (BIDS, re.compile(r"(?:הגש(?:ה|ות|ת\s+הצעות)\s+(?:נדחתה\s+)?(?:ל|ב)?)\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)")),
)
# what the text says is planned next ("צפי לבחירת קבלן 28.05")
_PLANNED = re.compile(
    r"(?:צפי\s+ל?|צפוי\S*\s+|נקבע\S*\s+ל|יקבל\S*\s+)(?P<what>סיור|הגש\S*|בחירת\s+קבלן|מכתב\s+זכי\S*|הודעת\s+זכי\S*)"
    r"[^.\d]{0,20}?(?P<d>\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)")
_TOUR_CANCELLED = re.compile(r"(?:ה)?סיור\S*\s+(?:ה?קבלנים\s+)?(?:התבטל|בוטל)|לבטל\s+את\s+(?:ה)?סיור")
_PUSHED = re.compile(r"^.{0,40}?נדח\S*\s+(?:שוב\s+)?ל\s*(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)")
_FROZEN = re.compile(r"מוקפא|להקפיא|בהקפאה|הקפאת\s+הטיפול")
_CANCELLED = re.compile(r"בוטל\S*\s+ה(?:הליך|מכרז)|ביטול\s+ה(?:הליך|מכרז)")
_POSTPONED = re.compile(r"נדח\S*|לדחות|דחיית|ביקש\S*\s+לדחות")
_EVENT = re.compile(r"(?<![\u0590-\u05FF\w])ב\s?(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d/])")
_AMOUNT = re.compile(r"(?:(?:בסך|סך|בסכום)\s*(?:של\s*)?(?P<a>\d{1,3}(?:,\d{3})+|\d{4,}))"
                     r"|(?:(?P<b>\d{1,3}(?:,\d{3})+)\s*(?:₪|ש\S?ח))")
_AWARD_AT = re.compile(r"(?:מכתב|הודעת)\s+זכי\S*")
_PACKAGE_AT = re.compile(r"חבילת\s+(?:ה)?מסירה|דרישה")
_CONTRACTOR = re.compile(r"(?:ע\S?ש|לקבלן|לחתימת\s+(?:ה)?קבלן)\s+[\"״]?(?P<n>[^\"״,;()]{2,40})")
_NAME_END = re.compile(r"\s+(?:בסך|מס|הזמנה|שהחזיר|ב?\d)|\.(?=\s|[\u0590-\u05FF]{2})|\s*$")
_ORDER_NO = re.compile(r"(?<!\d)(4\d{9})(?!\d)")
_CODE = re.compile(r"\bW[A-Z]{2}-\d[\d-]*\d")
_SHEET_DATE = re.compile(r"^\s*(\d{1,2})[./](\d{1,2})[./](\d{2,4})\s*$")
_TRANSFER_NOT = re.compile(r"לא\s*יועבר")
_PERMIT_GOT = re.compile(r"התקבל\S*\s+היתר")
_MONTH_YY = re.compile(r"(\d{1,2})[/.](\d{2})(?![\d/.])")      # "צפי עליה לקרקע 11/26"

# bottlenecks the diary keeps naming — counted, each row once per topic
TOPICS = (
    ("annex5", "נספח 5", re.compile(r"נספח\s*5")),
    ("rft", "RFT / DR", re.compile(r"RFT|אר\.?\s?אפ\.?\s?טי|\bDR\b|די\.?\s?אר")),
    ("insurance", "ביטוח / ערבות", re.compile(r"ביטוח|ערבות")),
    ("municipal", "עירייה / היתר", re.compile(r"עירי|ראש\s+העיר|היתר")),
    ("outages", "הפסקות", re.compile(r"הפסק")),
    ("manpower", "כוח אדם / משגיחים", re.compile(r"משגיח|כ\S?א\b|כוח\s+אדם")),
    ("agreement", "תוקף הסכם מסגרת", re.compile(r"תוקף\s+ה?הסכם|ההסכם\s+(?:הסתיים|יפוג)|להמיר|מתווה\s+חדש")),
    ("signatures", "סבב חתימות", re.compile(r"בחתימות|סבב\s+חתימות|לחתימת")),
)


def _clean(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        v = v.date()
    if isinstance(v, datetime.date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = " ".join(str(v).replace("\u200b", "").split())
    return s if len(s) <= CELL_CHARS else s[:CELL_CHARS - 1] + "…"


def _as_date(v: Any) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return None


def _short_date(d: int, m: int, y: int | None, ref: datetime.date) -> datetime.date | None:
    """"ב10.06" — the year is the writer's: the as-of year, unless that lands
    more than half a year past the as-of date (then it was last year)."""
    if y is not None and y < 100:
        y += 2000
    try:
        out = datetime.date(y or ref.year, m, d)
    except ValueError:
        return None
    if y is None and (out - ref).days > 183:
        out = out.replace(year=out.year - 1)
    if not (target_reading.MIN_YEAR <= out.year <= target_reading.MAX_YEAR):
        return None
    return out


def _dotted(s: str, ref: datetime.date) -> datetime.date | None:
    parts = [p for p in s.split(".") if p]
    if len(parts) < 2:
        return None
    try:
        return _short_date(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else None, ref)
    except ValueError:
        return None


def sheet_as_of(header: list, rows: list[list], cols: dict[str, int]) -> datetime.date | None:
    """The sheet's own date: a lone date in the header row, else the newest event."""
    for v in header:
        d = _as_date(v)
        if d:
            return d
        m = _SHEET_DATE.match(str(v or ""))
        if m:
            day, mo, y = (int(g) for g in m.groups())
            try:
                return datetime.date(y + 2000 if y < 100 else y, mo, day)
            except ValueError:
                continue
    return None


def events(text: str, ref: datetime.date) -> list[dict]:
    """The diary split at each "ב<DD.MM>": [{date, text}] in written order.
    Text before the first date is kept as an undated lead."""
    out: list[dict] = []
    marks = [(m.start(), m.end(), _short_date(int(m.group(1)), int(m.group(2)),
                                              int(m.group(3)) if m.group(3) else None, ref))
             for m in _EVENT.finditer(text)]
    marks = [(p, e, d) for p, e, d in marks if d]
    lead = text[: marks[0][0]] if marks else text
    lead = lead.strip(" .,;-")
    if lead:
        out.append({"date": None, "text": lead})
    for i, (p, e, d) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        seg = text[e:end].strip(" .,;-")      # the date is the step's label; the text follows it
        if seg:
            out.append({"date": d.isoformat(), "text": seg})
    return out


def _dated_steps(text: str, as_of: datetime.date) -> list[tuple[str, datetime.date]]:
    """(stage, date) for every scheduled tour / submission — a step postponed
    ("נדחתה ל06.07 ואח"כ שוב נדחתה ל13.7") counts at its LAST date; a tour the
    text cancels does not count at all."""
    out = []
    for key, rx in _DATED_STEP:
        if key == TOUR and _TOUR_CANCELLED.search(text):
            continue
        for m in rx.finditer(text):
            d = _dotted(m.group(1), as_of)
            pos = m.end()
            while True:
                push = _PUSHED.match(text[pos:])
                if not push:
                    break
                d = _dotted(push.group(1), as_of) or d
                pos += push.end()
            if d:
                out.append((key, d))
    return out


def _reached(text: str, as_of: datetime.date, transfer: datetime.date | None) -> tuple[str, list[str]]:
    """(furthest stage reached, every stage the text proves) — a plan is not a step."""
    proved = set()
    if transfer and transfer <= as_of:
        proved.add(PROCUREMENT)
    for key, _, rx in LADDER:
        if rx and rx.search(text):
            proved.add(key)
    for key, d in _dated_steps(text, as_of):
        if d <= as_of:
            proved.add(key)
    stage = DOCS
    for key in STAGE_KEYS:
        if key in proved:
            stage = key
    return stage, [k for k in STAGE_KEYS if k in proved]


def _next_planned(text: str, as_of: datetime.date, stage: str) -> dict | None:
    """The earliest planned step beyond the stage reached, still ahead of the sheet's date."""
    found = []
    for m in _PLANNED.finditer(text):
        d = _dotted(m.group("d"), as_of)
        if d and d >= as_of:
            found.append((d, " ".join(m.group("what").split())))
    here = STAGE_KEYS.index(stage)
    for key, d in _dated_steps(text, as_of):
        if d > as_of and STAGE_KEYS.index(key) > here:
            found.append((d, STAGE_LABELS[key]))
    if not found:
        return None
    d, what = min(found)
    return {"what": what, "date": d.isoformat()}


def _money(text: str) -> tuple[int | None, int | None]:
    """(award amount, package estimate) — by what the amount follows."""
    award = package = None
    for m in _AMOUNT.finditer(text):
        value = int((m.group("a") or m.group("b")).replace(",", ""))
        before = text[max(0, m.start() - 90): m.start()]
        if award is None and _AWARD_AT.search(before):
            award = value
        elif package is None and _PACKAGE_AT.search(before):
            package = value
    return award, package


def _contractor(text: str) -> str | None:
    """The last contractor the diary names on an award or an order sent for signing."""
    found = None
    for m in _CONTRACTOR.finditer(text):
        name = m.group("n")
        name = name[: _NAME_END.search(name).start()].strip(" -.")
        if len(name) >= 2:
            found = name
    return found


def _ground(cell: Any, as_of: datetime.date) -> dict:
    """The permit / ground-entry cell → {ground, permit, permit_received, text}."""
    text = _clean(cell)
    out = {"text": text, "ground": None, "permit": None, "permit_received": False, "bare": False}
    d = _as_date(cell)
    if d:
        # a bare date in the "היתר \\ עלייה לקרקע" column: the runway reads it, the page says it is unlabelled
        out.update(ground=d.isoformat(), bare=True)
        return out
    if not text:
        return out
    out["permit_received"] = bool(_PERMIT_GOT.search(text))
    for key, rx in (("ground", r"קרקע"), ("permit", r"היתר")):
        for m in re.finditer(rx, text):
            # the date nearest to the phrase (after it first, else just before it)
            after = target_reading.find_dates(text[m.end(): m.end() + 24])
            before = target_reading.find_dates(text[max(0, m.start() - 24): m.start()])
            pick = after[0][1] if after else (before[-1][1] if before else None)
            if pick is None:
                short = _MONTH_YY.match(text[m.end():].lstrip(" :-"))
                pick = target_reading._month_end(int(short.group(1)), 2000 + int(short.group(2))) if short else None
            if pick:
                out[key] = pick.isoformat()
    if out["permit_received"]:
        out["permit"] = None      # a received permit is history, not a target
    return out


def _target(cell: Any) -> dict:
    """A ת.פ. / מסתמן cell → {date, text, kind} — prose read by target_reading."""
    d = _as_date(cell)
    text = _clean(cell)
    if d:
        return {"date": d.isoformat(), "text": text, "kind": target_reading.DATE, "label": None}
    r = target_reading.read_text(text)
    if not r:
        return {"date": None, "text": "", "kind": None, "label": None}
    return {"date": r["date"] if r["kind"] in target_reading.DATED else None,
            "text": text, "kind": r["kind"], "label": r["label"]}


def _procedure_family(text: str) -> str:
    s = text.replace("״", '"')
    if re.search(r"(?:עבר|הומר|יצא)\S*\s+(?:ל|כ)פומבי", s):
        return "פומבי (הוסב)"
    if re.search(r'שו"?ש\s*צפון', s):
        return 'שו"ש צפון'
    if re.search(r'שו"?ש\s*דרום', s):
        return 'שו"ש דרום'
    if re.search(r"פומבי|מכרז", s):
        return "מכרז פומבי"
    if re.search(r"הקמ", s):
        return "הקמה"
    return "אחר" if s.strip() else "לא צוין"


def _months(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return round((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days / 30.44, 1)


def _header_map(header: list, rows: list[list]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        s = _clean(h)
        if not s or _SHEET_DATE.match(s):
            continue
        for key, rx in _HEADERS:
            if key not in cols and rx.search(s):
                cols[key] = i
                break
    if "name" not in cols:
        # the file leaves the name column's header empty: the unnamed column with the most text
        taken = set(cols.values())
        best, best_n = None, 0
        for i in range(len(header)):
            if i in taken or _clean(header[i]):
                continue
            n = sum(1 for r in rows if i < len(r) and isinstance(r[i], str) and len(r[i].strip()) > 3)
            if n > best_n:
                best, best_n = i, n
        if best is not None:
            cols["name"] = best
    return cols


def _legend(rows: list[list], fills: list[list], cols: dict[str, int]) -> dict[str, str]:
    """{colour: label} from the sheet's own legend: a row with no project whose
    one text cell sits next to (or on) a filled cell."""
    out: dict[str, str] = {}
    name_col = cols.get("name")
    for r, f in zip(rows, fills):
        if name_col is not None and name_col < len(r) and _clean(r[name_col]):
            continue
        texts = [(i, _clean(v)) for i, v in enumerate(r) if _clean(v)]
        colours = [(i, c) for i, c in enumerate(f or []) if c]
        if len(texts) == 1 and colours:
            out[colours[0][1]] = texts[0][1]
    return out


def parse_rows(rows: list[list], fills: list[list] | None = None, sheet: str = "",
               report_date: datetime.date | None = None) -> dict:
    """Rows of raw cell values (row 0 = header) + each cell's fill colour → the page payload."""
    report_date = report_date or datetime.date.today()
    fills = fills or [[] for _ in rows]
    header_i = next((i for i, r in enumerate(rows) if sum(1 for v in r if _clean(v)) >= 3), 0)
    header = list(rows[header_i]) if rows else []
    body = [list(r) for r in rows[header_i + 1:]]
    body_fills = [list(f) for f in fills[header_i + 1:]] + [[]] * len(body)
    cols = _header_map(header, body)
    legend = _legend(body, body_fills, cols)

    def cell(r: list, key: str) -> Any:
        i = cols.get(key)
        return r[i] if i is not None and i < len(r) else None

    stated = sheet_as_of(header, body, cols)
    items: list[dict] = []
    for r, f in zip(body, body_fills):
        name = _clean(cell(r, "name"))
        if not name:
            continue
        items.append({"_r": r, "_f": f, "name": name})
    # the writer's "now": the header's date, else the newest diary step (dated against the report)
    newest = None
    for it in items:
        for e in events(_clean(cell(it["_r"], "status")), report_date):
            if e["date"] and (newest is None or e["date"] > newest):
                newest = e["date"]
    as_of = stated or (datetime.date.fromisoformat(newest) if newest else report_date)

    out_rows = []
    for n, it in enumerate(items):
        r, f = it["_r"], it["_f"]
        status = _clean(cell(r, "status"))
        transfer_raw = cell(r, "transfer")
        transfer_text = _clean(transfer_raw)
        transfer = _as_date(transfer_raw) or (_dotted(re.sub(r"[^\d.]", "", transfer_text.split()[0]).replace("..", "."), as_of)
                                              if transfer_text and transfer_text[0].isdigit() else None)
        evs = events(status, as_of)
        stage, proved = _reached(status, as_of, transfer)
        marks = sorted({legend[c] for c in f if c and c in legend})
        frozen = bool(_FROZEN.search(status)) or any("הקפא" in m for m in marks)
        award, package = _money(status + " " + it["name"])
        g = _ground(cell(r, "ground"), as_of)
        plan, fc = _target(cell(r, "plan")), _target(cell(r, "forecast"))
        # the newest step already behind the writer (a dated plan ahead is not an update)
        last_event = max((e["date"] for e in evs if e["date"] and e["date"] <= as_of.isoformat()),
                         default=None)
        signed = stage == ORDER
        runway = None
        if not signed and not frozen and g["ground"]:
            runway = (datetime.date.fromisoformat(g["ground"]) - report_date).days
        days_open = None
        if transfer and transfer <= as_of:
            end = report_date
            if stage in (AWARD, ORDER):
                award_rx = LADDER[STAGE_KEYS.index(AWARD)][2]
                award_ev = next((e["date"] for e in evs if e["date"] and award_rx.search(e["text"])), None)
                end = datetime.date.fromisoformat(award_ev) if award_ev else None
            days_open = (end - transfer).days if end else None
        ident = _clean(cell(r, "ident")).rstrip(":").strip()
        codes = [ident] if ident and " " not in ident else []      # the id cell itself, whatever its pattern
        for src in (ident, it["name"]):
            for c in _CODE.findall(src):
                if c not in codes:
                    codes.append(c)
        if signed:
            group = "signed"
        elif frozen:
            group = "frozen"
        elif _CANCELLED.search(status):
            group = "cancelled"
        elif stage == AWARD:
            group = "awarded"
        elif stage in (TOUR, BIDS, SELECTION):
            group = "tender"
        elif stage == PROCUREMENT:
            group = "procurement"
        else:
            group = "docs"
        out_rows.append({
            "n": n + 1,
            "ident": ident,
            "codes": codes,
            "name": it["name"],
            "procedure": _clean(cell(r, "procedure")),
            "family": _procedure_family(_clean(cell(r, "procedure"))),
            "manager": _clean(cell(r, "manager")),
            "transfer_text": transfer_text,
            "transfer": transfer.isoformat() if transfer else None,
            "outside_procurement": bool(_TRANSFER_NOT.search(transfer_text)),
            "status": status,
            "events": evs,
            "last_event": last_event,
            "stage": stage,
            "stage_label": STAGE_LABELS[stage],
            "stage_index": STAGE_KEYS.index(stage),
            "proved": proved,
            "next": None if signed or frozen else _next_planned(status, as_of, stage),
            "group": group,
            "phase": PHASE_OF[group],
            "frozen": frozen,
            "marks": marks,
            "postponed": bool(_POSTPONED.search(status)),
            "award_amount": award,
            "package_amount": package,
            "contractor": _contractor(status) if stage in (AWARD, ORDER) else None,
            "order_no": (_ORDER_NO.findall(status) or [None])[-1],
            "ground": g,
            "plan": plan,
            "forecast": fc,
            "slip_months": _months(fc["date"], plan["date"]),
            "runway_days": runway,
            "days_open": days_open,
            "silent_days": ((as_of - datetime.date.fromisoformat(last_event)).days
                            if last_event and not signed and not frozen else None),
            "topics": [k for k, _, rx in TOPICS if rx.search(status)],
        })
    return {
        "sheet": sheet,
        "as_of": as_of.isoformat(),
        "as_of_stated": bool(stated),
        "report_date": report_date.isoformat(),
        "stale_days": (report_date - as_of).days,
        "legend": legend,
        "columns": {k: _clean(header[i]) or ("שם העבודה" if k == "name" else k) for k, i in cols.items()},
        "rows": out_rows,
    }


GROUPS = (   # (key, label, status-palette tone) — order = how far along
    ("docs", "הכנת מסמכים", "muted"),
    ("procurement", "ברכש, לפני תיחור", "info"),
    ("tender", "בתיחור", "info"),
    ("awarded", "זכייה — ממתין להזמנה", "warning"),
    ("signed", "הזמנה חתומה", "good"),
    ("frozen", "מוקפא", "serious"),
    ("cancelled", "בוטל", "muted"),
)
GROUP_LABELS = {k: label for k, label, _ in GROUPS}
# four phases for the stacked chart — colours = reference dark slots 1–4, validated on #0f1826
PHASES = (
    ("pre", "לפני תיחור", ("docs", "procurement")),
    ("tender", "בתיחור", ("tender",)),
    ("contracted", "זכייה / הזמנה", ("awarded", "signed")),
    ("stopped", "מוקפא / בוטל", ("frozen", "cancelled")),
)
PHASE_OF = {g: key for key, _, gs in PHASES for g in gs}


def analyse(payload: dict) -> dict:
    """KPIs, the funnel, risks and bottlenecks — every number counts rows of the payload."""
    rows = payload.get("rows") or []
    live = [r for r in rows if r["group"] not in ("signed", "frozen", "cancelled")]
    counts = {k: sum(1 for r in rows if r["group"] == k) for k, _, _ in GROUPS}
    funnel = []
    for i, key in enumerate(STAGE_KEYS):
        reached = [r for r in rows if r["stage_index"] >= i and r["group"] != "cancelled"]
        funnel.append({"key": key, "label": STAGE_LABELS[key], "reached": len(reached),
                       "here": sum(1 for r in rows if r["stage"] == key and r["group"] != "cancelled")})
    at_risk = sorted([r for r in live if r["runway_days"] is not None and r["runway_days"] <= RUNWAY_SOON_DAYS],
                     key=lambda r: r["runway_days"])
    overdue = [r for r in at_risk if r["runway_days"] < 0]
    durations = [r["days_open"] for r in rows if r["stage"] in (AWARD, ORDER) and r["days_open"] is not None
                 and r["days_open"] >= 0]
    open_age = sorted([r for r in live if r["days_open"] is not None], key=lambda r: -r["days_open"])
    silent = sorted([r for r in live if (r["silent_days"] or 0) >= SILENT_DAYS], key=lambda r: -r["silent_days"])
    families: dict[str, dict] = {}
    for r in rows:
        fam = families.setdefault(r["family"], {"family": r["family"], "total": 0,
                                                **{k: 0 for k, _, _ in GROUPS},
                                                "phases": {k: 0 for k, _, _ in PHASES}})
        fam["total"] += 1
        fam[r["group"]] += 1
        fam["phases"][r["phase"]] += 1
    topics = []
    for key, label, _ in TOPICS:
        hit = [r for r in rows if key in r["topics"] and r["group"] not in ("signed", "cancelled")]
        if hit:
            topics.append({"key": key, "label": label, "count": len(hit)})
    topics.sort(key=lambda t: -t["count"])
    slips = [r for r in rows if r["slip_months"] is not None and r["slip_months"] > 0.5]
    awarded_total = sum(r["award_amount"] or 0 for r in rows if r["stage"] in (AWARD, ORDER))
    not_sent = [r for r in live if not r["transfer"] and not r["outside_procurement"]]

    # findings — each one a rule over the rows above, with the filter that shows its rows
    findings: list[dict] = []
    stale = payload.get("stale_days", 0)
    if stale >= STALE_DAYS:
        findings.append({"tone": "critical", "filter": "",
                         "text": f"הגיליון עצמו לא עודכן {stale} ימים — כל סטטוס כאן נכון לתאריך הגיליון, לא לתאריך הדוח."})
    if overdue:
        names = ", ".join(r["name"] for r in overdue[:3])
        findings.append({"tone": "critical", "filter": "overdue",
                         "text": f"לפי הגיליון, {len(overdue)} עבודות בלי הזמנה חתומה כבר עברו את יעד העלייה לקרקע"
                                 f" ({names}{'…' if len(overdue) > 3 else ''})."})
    soon = [r for r in at_risk if r["runway_days"] >= 0]
    if soon:
        findings.append({"tone": "serious", "filter": "risk",
                         "text": f"{len(soon)} עבודות נוספות מגיעות ליעד העלייה לקרקע בתוך {RUNWAY_SOON_DAYS} יום בלי הזמנה חתומה."})
    if topics:
        t = topics[0]
        findings.append({"tone": "warning", "filter": f"topic:{t['key']}",
                         "text": f"החסם שחוזר הכי הרבה ביומן: {t['label']} — מוזכר ב-{t['count']} עבודות שעוד לא נחתמו."})
    if not_sent:
        findings.append({"tone": "warning", "filter": "group:docs",
                         "text": f"{len(not_sent)} עבודות עוד לא הועברו לרכש (״טרם״) — הן לפני תחילת ההליך."})
    if open_age:
        o = open_age[0]
        findings.append({"tone": "info", "filter": "",
                         "text": f"ההליך הפתוח הארוך ביותר: {o['name']} — {o['days_open']} ימים מהעברה לרכש, בשלב {o['stage_label']}."})
    if durations:
        findings.append({"tone": "info", "filter": "",
                         "text": f"חציון מהעברה לרכש ועד הודעת זכייה: {int(statistics.median(durations))} ימים"
                                 f" (n={len(durations)}{', מדגם קטן' if len(durations) < 5 else ''})."})
    if counts["frozen"]:
        findings.append({"tone": "serious", "filter": "group:frozen",
                         "text": f"{counts['frozen']} עבודות מוקפאות (לפי היומן או לפי צבע המקרא בגיליון)."})
    converted = sum(1 for r in rows if r["family"] == "פומבי (הוסב)")
    if converted:
        findings.append({"tone": "info", "filter": "family:פומבי (הוסב)",
                         "text": f"{converted} הליכים הוסבו מהסכם מסגרת למכרז פומבי."})
    if slips:
        findings.append({"tone": "warning", "filter": "slip",
                         "text": f"ב-{len(slips)} עבודות יעד החשמול המסתמן מאוחר מתוכנית הפיתוח (עד {max(r['slip_months'] for r in slips):g} חודשים)."})

    return {
        "findings": findings,
        "max_family": max((f["total"] for f in families.values()), default=0),
        "max_open": max((r["days_open"] for r in open_age), default=0),
        "max_runway": max((abs(r["runway_days"]) for r in rows if r["runway_days"] is not None), default=0),
        "not_sent": len(not_sent),
        "total": len(rows),
        "live": len(live),
        "counts": counts,
        "funnel": funnel,
        "at_risk": at_risk,
        "overdue": overdue,
        "median_days_to_award": int(statistics.median(durations)) if durations else None,
        "n_days_to_award": len(durations),
        "open_age": open_age,
        "silent": silent,
        "families": sorted(families.values(), key=lambda f: -f["total"]),
        "topics": topics,
        "slips": sorted(slips, key=lambda r: -r["slip_months"]),
        "awarded_total": awarded_total,
        "awarded_n": sum(1 for r in rows if r["award_amount"] and r["stage"] in (AWARD, ORDER)),
        "postponed": sum(1 for r in live if r["postponed"]),
        "outside": sum(1 for r in rows if r["outside_procurement"]),
        "converted": sum(1 for r in rows if r["family"] == "פומבי (הוסב)"),
        "stale": payload.get("stale_days", 0) >= STALE_DAYS,
    }


def pick_sheet(names: list[str]) -> str | None:
    return next((n for n in names if all(m in n for m in SHEET_MARKERS)), None) or \
        next((n for n in names if any(m in n for m in SHEET_MARKERS)), None)


def _rgb(cell) -> str | None:
    f = getattr(cell, "fill", None)
    if not f or not f.fill_type:
        return None
    c = f.fgColor
    if getattr(c, "type", None) != "rgb" or not isinstance(c.rgb, str):
        return None
    rgb = c.rgb[-6:].upper()
    return None if rgb in ("FFFFFF", "000000") else rgb


def read_file(file_path: str, report_date: datetime.date | None = None) -> dict | None:
    """The contracts sheet of an XLSX, parsed; None when the workbook has none."""
    if not str(file_path).lower().endswith((".xlsx", ".xlsm")):
        return None
    import openpyxl
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)   # fills need the full reader
    except Exception as exc:
        logger.warning(f"contracts_sheet: cannot open {file_path}: {exc}")
        return None
    try:
        name = pick_sheet(wb.sheetnames)
        if name is None:
            logger.info(f"contracts_sheet: no contracts sheet in {file_path} — page keeps the previous one")
            return None
        ws = wb[name]
        rows, fills = [], []
        last = 0
        for row in ws.iter_rows():
            vals = [c.value for c in row]
            rows.append(vals)
            fills.append([_rgb(c) for c in row])
            if any(_clean(v) for v in vals):
                last = len(rows)
        rows, fills = rows[:last], fills[:last]
    finally:
        wb.close()
    payload = parse_rows(rows, fills, name, report_date)
    if not payload["rows"]:
        logger.warning(f"contracts_sheet: sheet '{name}' parsed empty")
        return None
    return payload


async def save(session: AsyncSession, payload: dict, source_file: str | None) -> None:
    from app.models import ProjectContractSheet
    try:
        session.add(ProjectContractSheet(
            sheet_name=(payload.get("sheet") or "")[:120],
            as_of=datetime.date.fromisoformat(payload["as_of"]) if payload.get("as_of") else None,
            report_date=datetime.date.fromisoformat(payload["report_date"]) if payload.get("report_date") else None,
            source_file=(source_file or "")[:300],
            payload=payload,
        ))
        await session.commit()
        logger.info(f"contracts_sheet: saved '{payload.get('sheet')}' — {len(payload.get('rows') or [])} rows")
    except Exception:
        logger.exception("contracts_sheet: save failed")
        await session.rollback()


async def save_from_file(file_path: str, source_file: str | None,
                         report_date: datetime.date | None = None) -> None:
    """Parse + save in a session of its own — called after a LIVE sync only. Never raises."""
    import asyncio

    from app.database import async_session_maker
    try:
        payload = await asyncio.get_running_loop().run_in_executor(None, read_file, file_path, report_date)
        if payload:
            async with async_session_maker() as session:
                await save(session, payload, source_file)
    except Exception:
        logger.exception("contracts_sheet: save_from_file failed")


async def latest(session: AsyncSession) -> dict | None:
    """The sheet of the newest LIVE master upload (history replays never save one)."""
    from app.models import ProjectContractSheet
    try:
        row = (await session.execute(
            select(ProjectContractSheet.payload, ProjectContractSheet.created_at)
            .order_by(ProjectContractSheet.id.desc()).limit(1)
        )).first()
    except Exception:
        logger.exception("contracts_sheet: latest failed")
        await session.rollback()
        return None
    if not row:
        return None
    payload, created = row
    return {**payload, "loaded_at": created.isoformat() if created else None}
