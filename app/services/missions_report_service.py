"""Operations Room (חדר מבצעים) reporting — XLSX board report + AI focus summary.

Pure service layer, same contract as missions_menu_service: never imports the bot,
callers do the sends. Consumed by the Telegram menu (om:xls / om:sum), the web
war-room router, and the daily cron.

Nothing here touches the schema — every figure is derived from existing `missions`
columns, so there is no ALTER TABLE to run on Railway.
"""

import datetime
import html as _html
import io
import logging

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Mission, MissionStatusEnum, User, RoleEnum
from app.services import missions_menu_service as oms

logger = logging.getLogger(__name__)

# A mission is "כמעט באיחור" when its due date is within this many days.
AT_RISK_DAYS = 3

# Active missions untouched for this long are flagged as stale on the summary sheet.
STALE_DAYS = 30

CLOSED_STATUSES = [MissionStatusEnum.DONE.value, MissionStatusEnum.CANCELLED.value]

# War-room operators: admins + the manager roles that already receive board-wide
# totals in the morning digest (eval_cron._missions_daily_digest).
MANAGER_ROLES = (
    RoleEnum.DEPARTMENT_MANAGER,
    RoleEnum.DEPUTY_DIVISION_MANAGER,
    RoleEnum.DIVISION_MANAGER,
)

OPEN_HEADERS = [
    "מזהה", "כותרת", "תיאור", "רביע", "דחוף", "חשוב", "סטטוס", "אחראי", "נוצר ע\"י",
    "תאריך יעד", "ימים ליעד", "ימים באיחור", "גיל המשימה (ימים)", "נוצרה בתאריך",
    "עודכנה לאחרונה",
]
OPEN_WIDTHS = [8, 42, 50, 20, 8, 8, 14, 18, 18, 14, 12, 14, 18, 16, 16]

CLOSED_HEADERS = [
    "מזהה", "כותרת", "רביע", "סטטוס", "אחראי", "תאריך יעד", "הושלמה בתאריך",
    "ימי ביצוע", "עמדה ביעד?", "נוצרה בתאריך",
]
CLOSED_WIDTHS = [8, 42, 20, 14, 18, 14, 16, 12, 14, 16]

SHEET_SUMMARY = "סיכום ותובנות"
SHEET_OPEN = "משימות פתוחות"
SHEET_CLOSED = "משימות סגורות"

# One AI narrative per calendar day, shared by the cron and every manual press.
_ai_cache: dict[str, str] = {}


# ── Helpers ────────────────────────────────────────────────────────────────

def _priority_weight(m: Mission) -> int:
    """Python mirror of missions_menu_service._priority_order()."""
    if m.is_urgent and m.is_important:
        return 0
    if m.is_important:
        return 1
    if m.is_urgent:
        return 2
    return 3


def _user_name(u: User | None) -> str:
    if u is None:
        return "—"
    return u.username or f"#{u.id}"


def _days_until(due: datetime.date | None, today: datetime.date) -> int | None:
    return None if due is None else (due - today).days


def _fmt_dt(dt: datetime.datetime | None) -> str:
    """DD/MM/YYYY HH:MM — digits are direction-neutral, so this survives RTL."""
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


def _age_days(created: datetime.datetime | None, today: datetime.date) -> int | None:
    return None if created is None else (today - created.date()).days


def due_bucket(due: datetime.date | None, today: datetime.date) -> str:
    """Which urgency bucket a due date falls into. Boundaries are inclusive at 0 and AT_RISK_DAYS."""
    if due is None:
        return "ללא תאריך יעד"
    delta = (due - today).days
    if delta < 0:
        return "באיחור"
    if delta == 0:
        return "היום"
    if delta <= AT_RISK_DAYS:
        return f"עד {AT_RISK_DAYS} ימים"
    if delta <= 7:
        return "השבוע"
    return "מאוחר יותר"


BUCKET_ORDER = ["באיחור", "היום", f"עד {AT_RISK_DAYS} ימים", "השבוע", "מאוחר יותר", "ללא תאריך יעד"]


def _sanitize(text: str | None) -> str:
    """Gershayim swap before any LLM call — straight quotes break the JSON reply (CLAUDE.md §5)."""
    if not text:
        return ""
    return text.replace('"', "״").replace("'", "׳")


# ── Recipients ─────────────────────────────────────────────────────────────

async def list_war_room_operators(session: AsyncSession) -> list[User]:
    """The people who run the war room: admins + manager roles, reachable on Telegram."""
    rows = await session.scalars(
        select(User)
        .where(
            User.telegram_id.isnot(None),
            or_(User.is_admin.is_(True), User.role.in_(MANAGER_ROLES)),
        )
        .order_by(User.id)
    )
    return list(rows.all())


# ── Data collection ────────────────────────────────────────────────────────

async def collect_report_data(session: AsyncSession) -> dict:
    """One pass over the whole board → flattened rows + every figure the summary sheet shows."""
    today = oms.today_il()
    now = datetime.datetime.utcnow()

    missions = list((await session.scalars(
        select(Mission).options(
            selectinload(Mission.owner), selectinload(Mission.created_by)
        )
    )).all())

    active = [m for m in missions if m.status in oms.ACTIVE_STATUSES]
    closed = [m for m in missions if m.status in CLOSED_STATUSES]

    # Overdue first, then Eisenhower weight, then soonest due date (undated last).
    active.sort(key=lambda m: (
        0 if oms.is_overdue(m, today) else 1,
        _priority_weight(m),
        m.due_date or datetime.date.max,
        -(m.id or 0),
    ))
    closed.sort(key=lambda m: (m.completed_at or datetime.datetime.min), reverse=True)

    open_rows = []
    for m in active:
        delta = _days_until(m.due_date, today)
        open_rows.append({
            "id": m.id,
            "title": m.title or "",
            "description": m.description or "",
            "quadrant": oms.quadrant_label(oms.quadrant_key(m)),
            "urgent": "כן" if m.is_urgent else "לא",
            "important": "כן" if m.is_important else "לא",
            "status": oms.STATUS_LABELS.get(m.status, m.status),
            "owner": _user_name(m.owner),
            "created_by": _user_name(m.created_by),
            "due": oms.format_due(m.due_date) if m.due_date else "",
            "days_to_due": delta,
            "days_overdue": -delta if (delta is not None and delta < 0) else None,
            "age_days": _age_days(m.created_at, today),
            "created_at": _fmt_dt(m.created_at),
            "updated_at": _fmt_dt(m.updated_at),
            "_overdue": oms.is_overdue(m, today),
            "_at_risk": delta is not None and 0 <= delta <= AT_RISK_DAYS,
        })

    closed_rows = []
    for m in closed:
        cycle = None
        if m.completed_at and m.created_at:
            cycle = max((m.completed_at - m.created_at).days, 0)
        on_time = "ללא יעד"
        if m.due_date and m.completed_at:
            on_time = "כן" if m.completed_at.date() <= m.due_date else "לא"
        closed_rows.append({
            "id": m.id,
            "title": m.title or "",
            "quadrant": oms.quadrant_label(oms.quadrant_key(m)),
            "status": oms.STATUS_LABELS.get(m.status, m.status),
            "owner": _user_name(m.owner),
            "due": oms.format_due(m.due_date) if m.due_date else "",
            "completed_at": _fmt_dt(m.completed_at),
            "cycle_days": cycle,
            "on_time": on_time,
            "created_at": _fmt_dt(m.created_at),
        })

    stats = _compute_stats(active, closed, today, now)
    return {
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "stats": stats,
        "meta": {
            "generated_at": datetime.datetime.now(oms._IL_TZ).strftime("%d/%m/%Y %H:%M"),
            "date_slug": today.strftime("%d-%m-%Y"),
        },
    }


def _compute_stats(
    active: list[Mission],
    closed: list[Mission],
    today: datetime.date,
    now: datetime.datetime,
) -> dict:
    total_open = len(active)
    overdue = [m for m in active if oms.is_overdue(m, today)]
    in_progress = [m for m in active if m.status == MissionStatusEnum.IN_PROGRESS.value]

    buckets = {b: 0 for b in BUCKET_ORDER}
    for m in active:
        buckets[due_bucket(m.due_date, today)] += 1

    quadrants = {key: 0 for key, *_ in oms.QUADRANTS}
    for m in active:
        quadrants[oms.quadrant_key(m)] += 1

    ages = [a for a in (_age_days(m.created_at, today) for m in active) if a is not None]
    avg_age = round(sum(ages) / len(ages), 1) if ages else 0.0

    cutoff_30 = now - datetime.timedelta(days=30)
    cutoff_7 = now - datetime.timedelta(days=7)
    done_30 = [m for m in closed
               if m.status == MissionStatusEnum.DONE.value
               and m.completed_at and m.completed_at >= cutoff_30]
    done_7 = [m for m in done_30 if m.completed_at and m.completed_at >= cutoff_7]
    cycles = [max((m.completed_at - m.created_at).days, 0)
              for m in done_30 if m.completed_at and m.created_at]
    avg_cycle = round(sum(cycles) / len(cycles), 1) if cycles else 0.0

    created_7 = len([m for m in (active + closed) if m.created_at and m.created_at >= cutoff_7])
    stale_cutoff = now - datetime.timedelta(days=STALE_DAYS)
    stale = [m for m in active if m.updated_at and m.updated_at < stale_cutoff]

    # Per-owner load — the row that exposes who is actually holding the overdue work.
    per_owner: dict[str, dict] = {}
    for m in active:
        row = per_owner.setdefault(_user_name(m.owner), {
            "owner": _user_name(m.owner), "open": 0, "in_progress": 0,
            "overdue": 0, "done_30": 0, "cycles": [],
        })
        row["open"] += 1
        if m.status == MissionStatusEnum.IN_PROGRESS.value:
            row["in_progress"] += 1
        if oms.is_overdue(m, today):
            row["overdue"] += 1
    for m in done_30:
        row = per_owner.setdefault(_user_name(m.owner), {
            "owner": _user_name(m.owner), "open": 0, "in_progress": 0,
            "overdue": 0, "done_30": 0, "cycles": [],
        })
        row["done_30"] += 1
        if m.completed_at and m.created_at:
            row["cycles"].append(max((m.completed_at - m.created_at).days, 0))

    owners = []
    for row in per_owner.values():
        cyc = row.pop("cycles")
        row["overdue_rate"] = round(row["overdue"] / row["open"] * 100) if row["open"] else 0
        row["avg_cycle"] = round(sum(cyc) / len(cyc), 1) if cyc else 0.0
        owners.append(row)
    owners.sort(key=lambda r: (-r["overdue"], -r["open"], r["owner"]))

    return {
        "total_open": total_open,
        "in_progress": len(in_progress),
        "overdue": len(overdue),
        "overdue_rate": round(len(overdue) / total_open * 100) if total_open else 0,
        "buckets": buckets,
        "quadrants": quadrants,
        "avg_age_open": avg_age,
        "avg_cycle_30d": avg_cycle,
        "closed_7d": len(done_7),
        "closed_30d": len(done_30),
        "created_7d": created_7,
        "stale": len(stale),
        "owners": owners,
    }


# ── Deterministic insights (always render, no LLM) ─────────────────────────

def compute_insights(stats: dict) -> list[dict]:
    """Rule-based insights, same shape as project_report_service._compute_insights."""
    out: list[dict] = []
    total = stats["total_open"]

    if stats["overdue"]:
        sev = "high" if stats["overdue_rate"] >= 30 else "medium"
        out.append({
            "sev": sev, "icon": "🔴",
            "headline": f"{stats['overdue']} משימות באיחור ({stats['overdue_rate']}% מהפתוחות)",
            "action": "עבור על גיליון המשימות הפתוחות — השורות האדומות בראש הרשימה.",
        })

    top = stats["owners"][0] if stats["owners"] else None
    if top and top["overdue"] >= 3 and stats["overdue"] and \
            top["overdue"] / stats["overdue"] >= 0.5:
        out.append({
            "sev": "high", "icon": "⚠️",
            "headline": f"{top['owner']} מחזיק {top['overdue']} מתוך {stats['overdue']} המשימות באיחור",
            "action": "שקול האצלה מחדש או הארכת יעדים — עומס מרוכז אצל אדם אחד.",
        })

    if stats["created_7d"] > stats["closed_7d"]:
        out.append({
            "sev": "medium", "icon": "📈",
            "headline": f"נפתחו {stats['created_7d']} משימות השבוע מול {stats['closed_7d']} שנסגרו",
            "action": "קצב הפתיחה עולה על קצב הסגירה — הלוח גדל.",
        })
    elif stats["closed_7d"] > stats["created_7d"]:
        out.append({
            "sev": "low", "icon": "✅",
            "headline": f"נסגרו {stats['closed_7d']} משימות השבוע מול {stats['created_7d']} שנפתחו",
            "action": "קצב הסגירה גבוה מקצב הפתיחה — הלוח מצטמצם.",
        })

    if stats["stale"]:
        out.append({
            "sev": "medium", "icon": "🕸",
            "headline": f"{stats['stale']} משימות פעילות ללא עדכון מעל {STALE_DAYS} ימים",
            "action": "בדוק אם הן עדיין רלוונטיות — או סגור אותן.",
        })

    do_now = stats["quadrants"].get("do", 0)
    if do_now:
        out.append({
            "sev": "high" if do_now >= 5 else "medium", "icon": "🔥",
            "headline": f"{do_now} משימות ברביע «בצע עכשיו» (דחוף וחשוב)",
            "action": "אלה המשימות לפתוח איתן את היום.",
        })

    backlog = stats["quadrants"].get("backlog", 0)
    if total and backlog / total >= 0.4:
        out.append({
            "sev": "low", "icon": "🧺",
            "headline": f"{backlog} משימות ({round(backlog / total * 100)}%) יושבות במאגר המשימות",
            "action": "מאגר תפוח מסתיר עדיפויות — נקה או תעדף מחדש.",
        })

    if stats["avg_cycle_30d"]:
        out.append({
            "sev": "low", "icon": "⏱",
            "headline": f"זמן ביצוע ממוצע ב-30 הימים האחרונים: {stats['avg_cycle_30d']} ימים",
            "action": "השווה לדוח הקודם כדי לראות אם הקצב משתפר.",
        })

    if not out:
        out.append({
            "sev": "low", "icon": "🟢",
            "headline": "אין ממצאים חריגים — הלוח נקי",
            "action": "אין משימות באיחור ואין עומס מרוכז.",
        })
    return out


# ── AI insights (day-cached) ───────────────────────────────────────────────

_INSIGHTS_PROMPT = """אתה אנליסט תפעולי של חדר מבצעים באגף תשתיות חשמל.
לפניך נתוני לוח משימות (מודל אייזנהאואר: דחוף × חשוב).

תפקידך: לכתוב 4-6 תובנות ניהוליות קצרות בעברית, כל אחת בשורה נפרדת בפורמט:
• <התובנה> — <הפעולה המומלצת>

כללים:
- התבסס אך ורק על המספרים שניתנו. אל תמציא נתונים.
- כל תובנה חייבת להצביע על משהו שדורש החלטה, לא לחזור על המספר.
- כתוב בעברית בלבד, ללא markdown וללא כותרות.
- חשוב ביותר: אל תשתמש במרכאות כפולות (") בתוך הטקסט — השתמש בגרש בודד (׳) בלבד.
"""


def _stats_block(stats: dict) -> str:
    """Compact Hebrew rendering of the figures — sent instead of raw rows to keep tokens small."""
    lines = [
        f"סה\"כ משימות פתוחות: {stats['total_open']}",
        f"בביצוע: {stats['in_progress']}",
        f"באיחור: {stats['overdue']} ({stats['overdue_rate']}%)",
        f"גיל ממוצע של משימה פתוחה: {stats['avg_age_open']} ימים",
        f"זמן ביצוע ממוצע (30 יום): {stats['avg_cycle_30d']} ימים",
        f"נסגרו ב-7 ימים: {stats['closed_7d']} | נסגרו ב-30 יום: {stats['closed_30d']}",
        f"נפתחו ב-7 ימים: {stats['created_7d']}",
        f"ללא עדכון מעל {STALE_DAYS} ימים: {stats['stale']}",
        "",
        "פילוח לפי רביע:",
    ]
    for key, emoji, verb, axis in oms.QUADRANTS:
        lines.append(f"- {verb} ({axis}): {stats['quadrants'].get(key, 0)}")
    lines.append("")
    lines.append("פילוח לפי תאריך יעד:")
    for b in BUCKET_ORDER:
        lines.append(f"- {b}: {stats['buckets'].get(b, 0)}")
    lines.append("")
    lines.append("עומס לפי אחראי (פתוחות / באיחור / אחוז איחור / הושלמו ב-30 יום):")
    for row in stats["owners"][:12]:
        lines.append(
            f"- {_sanitize(row['owner'])}: {row['open']} / {row['overdue']} / "
            f"{row['overdue_rate']}% / {row['done_30']}"
        )
    return "\n".join(lines)


async def get_ai_insights(data: dict) -> str:
    """Hebrew narrative for the summary sheet. One LLM call per calendar day, cached.

    Returns "" on any failure — the workbook must never fail because the LLM did.
    """
    key = oms.today_il().isoformat()
    if key in _ai_cache:
        return _ai_cache[key]

    from app.services.llm_router import llm_chat
    try:
        text = await llm_chat(
            "missions_insights",
            [
                {"role": "system", "content": _INSIGHTS_PROMPT},
                {"role": "user", "content": _stats_block(data["stats"])},
            ],
            max_tokens=900,
            temperature=0.3,
        )
        text = (text or "").strip()
        if text:
            _ai_cache[key] = text
        return text
    except Exception as e:
        logger.warning(f"missions_report: AI insights failed, continuing without: {e}")
        return ""


# ── Workbook ───────────────────────────────────────────────────────────────

def build_workbook(data: dict, ai_text: str = "") -> bytes:
    """Build the 3-sheet XLSX. Blocking (openpyxl) — call via run_in_executor."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    stats = data["stats"]
    meta = data["meta"]

    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    title_font = Font(bold=True, size=15, color="1F4E79")
    section_font = Font(bold=True, size=12, color="1F4E79")
    overdue_fill = PatternFill("solid", fgColor="FCE4E4")
    at_risk_fill = PatternFill("solid", fgColor="FFF3CD")
    wrap = Alignment(wrap_text=True, vertical="top")

    wb = Workbook()

    # ── Sheet 1: summary + insights ────────────────────────────────────
    ws = wb.active
    ws.title = SHEET_SUMMARY
    ws.sheet_view.rightToLeft = True
    for col, width in zip("ABCDEFG", (34, 16, 16, 16, 16, 16, 16)):
        ws.column_dimensions[col].width = width

    r = 1
    ws.cell(r, 1, "חדר מבצעים — דוח מצב").font = title_font
    r += 1
    ws.cell(r, 1, f"הופק בתאריך {meta['generated_at']}").font = Font(italic=True, size=10)
    r += 2

    ws.cell(r, 1, "מדדים מרכזיים").font = section_font
    r += 1
    kpis = [
        ("משימות פתוחות", stats["total_open"]),
        ("מתוכן בביצוע", stats["in_progress"]),
        ("באיחור", stats["overdue"]),
        ("אחוז איחור", f"{stats['overdue_rate']}%"),
        ("גיל ממוצע של משימה פתוחה (ימים)", stats["avg_age_open"]),
        ("זמן ביצוע ממוצע, 30 יום (ימים)", stats["avg_cycle_30d"]),
        ("נסגרו ב-7 הימים האחרונים", stats["closed_7d"]),
        ("נפתחו ב-7 הימים האחרונים", stats["created_7d"]),
        (f"ללא עדכון מעל {STALE_DAYS} ימים", stats["stale"]),
    ]
    for label, value in kpis:
        ws.cell(r, 1, label).font = Font(bold=True)
        ws.cell(r, 2, value)
        r += 1
    r += 1

    ws.cell(r, 1, "פילוח לפי תאריך יעד").font = section_font
    r += 1
    for b in BUCKET_ORDER:
        ws.cell(r, 1, b)
        ws.cell(r, 2, stats["buckets"].get(b, 0))
        r += 1
    r += 1

    ws.cell(r, 1, "פילוח לפי רביע").font = section_font
    r += 1
    for key, emoji, verb, axis in oms.QUADRANTS:
        ws.cell(r, 1, f"{emoji} {verb} ({axis})")
        ws.cell(r, 2, stats["quadrants"].get(key, 0))
        r += 1
    r += 1

    ws.cell(r, 1, "עומס לפי אחראי").font = section_font
    r += 1
    owner_hdr_row = r
    for c, label in enumerate(
        ["אחראי", "פתוחות", "בביצוע", "באיחור", "% איחור", "הושלמו 30 יום", "ממוצע ימי ביצוע"], 1
    ):
        cell = ws.cell(r, c, label)
        cell.font = hdr_font
        cell.fill = hdr_fill
    r += 1
    owner_first_row = r
    for row in stats["owners"]:
        ws.cell(r, 1, row["owner"])
        ws.cell(r, 2, row["open"])
        ws.cell(r, 3, row["in_progress"])
        ws.cell(r, 4, row["overdue"])
        ws.cell(r, 5, row["overdue_rate"])
        ws.cell(r, 6, row["done_30"])
        ws.cell(r, 7, row["avg_cycle"])
        if row["overdue"]:
            for c in range(1, 8):
                ws.cell(r, c).fill = overdue_fill
        r += 1
    owner_last_row = r - 1

    if stats["owners"]:
        chart = BarChart()
        chart.type = "col"
        chart.title = "עומס לפי אחראי — פתוחות מול באיחור"
        chart.height, chart.width = 8, 18
        chart.add_data(
            Reference(ws, min_col=2, max_col=4, min_row=owner_hdr_row, max_row=owner_last_row),
            titles_from_data=True,
        )
        chart.set_categories(
            Reference(ws, min_col=1, min_row=owner_first_row, max_row=owner_last_row)
        )
        ws.add_chart(chart, f"I{owner_hdr_row}")
    r += 1

    ws.cell(r, 1, "תובנות").font = section_font
    r += 1
    for ins in compute_insights(stats):
        ws.cell(r, 1, f"{ins['icon']} {ins['headline']}").font = Font(bold=True)
        ws.cell(r, 2, ins["action"]).alignment = wrap
        r += 1
    r += 1

    ws.cell(r, 1, "תובנות AI").font = section_font
    r += 1
    if ai_text:
        for line in ai_text.splitlines():
            if line.strip():
                ws.cell(r, 1, line.strip()).alignment = wrap
                r += 1
    else:
        ws.cell(r, 1, "תובנות ה-AI אינן זמינות כרגע — התובנות המחושבות שלמעלה מלאות.")
        r += 1

    # ── Sheet 2: open missions ─────────────────────────────────────────
    ws2 = wb.create_sheet(SHEET_OPEN)
    ws2.sheet_view.rightToLeft = True
    for i, (label, width) in enumerate(zip(OPEN_HEADERS, OPEN_WIDTHS), 1):
        cell = ws2.cell(1, i, label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        ws2.column_dimensions[get_column_letter(i)].width = width
    for ri, row in enumerate(data["open_rows"], start=2):
        values = [
            row["id"], row["title"], row["description"], row["quadrant"], row["urgent"],
            row["important"], row["status"], row["owner"], row["created_by"], row["due"],
            row["days_to_due"], row["days_overdue"], row["age_days"],
            row["created_at"], row["updated_at"],
        ]
        for ci, value in enumerate(values, 1):
            ws2.cell(ri, ci, value)
        fill = overdue_fill if row["_overdue"] else (at_risk_fill if row["_at_risk"] else None)
        if fill:
            for ci in range(1, len(OPEN_HEADERS) + 1):
                ws2.cell(ri, ci).fill = fill
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = f"A1:{get_column_letter(len(OPEN_HEADERS))}{max(1, len(data['open_rows']) + 1)}"

    # ── Sheet 3: closed missions ───────────────────────────────────────
    ws3 = wb.create_sheet(SHEET_CLOSED)
    ws3.sheet_view.rightToLeft = True
    for i, (label, width) in enumerate(zip(CLOSED_HEADERS, CLOSED_WIDTHS), 1):
        cell = ws3.cell(1, i, label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        ws3.column_dimensions[get_column_letter(i)].width = width
    for ri, row in enumerate(data["closed_rows"], start=2):
        values = [
            row["id"], row["title"], row["quadrant"], row["status"], row["owner"],
            row["due"], row["completed_at"], row["cycle_days"], row["on_time"],
            row["created_at"],
        ]
        for ci, value in enumerate(values, 1):
            ws3.cell(ri, ci, value)
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:{get_column_letter(len(CLOSED_HEADERS))}{max(1, len(data['closed_rows']) + 1)}"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def build_report_bytes(session: AsyncSession, with_ai: bool = True) -> tuple[bytes, str]:
    """Collect → insights → workbook. Returns (xlsx_bytes, hebrew_filename)."""
    import asyncio

    data = await collect_report_data(session)
    ai_text = await get_ai_insights(data) if with_ai else ""
    payload = await asyncio.get_event_loop().run_in_executor(
        None, build_workbook, data, ai_text
    )
    return payload, f"דוח_חדר_מבצעים_{data['meta']['date_slug']}.xlsx"


# ── Focus summary (the סיכום AI button) ────────────────────────────────────

_SUMMARY_PROMPT = f"""אתה ראש חדר מבצעים באגף תשתיות חשמל.
לפניך רשימת משימות שנמצאות בסיכון: באיחור, ליעד היום, או ליעד בתוך {AT_RISK_DAYS} ימים.

החזר בעברית בלבד, במבנה הבא ובלי כותרות נוספות:

הערכת מצב:
<2 עד 4 משפטים: מה הסיכון המרכזי כרגע ומה הגורם לו>

מה לעשות:
<רשימת מטלות מקובצת לפי אחראי. לכל אחראי שורת כותרת ואחריה מטלות בסדר עדיפות יורד.
כל מטלה במשפט אחד קצר שמתחיל בפועל.>

לטיפול הנהלה:
<רק משימות שבאיחור של יותר משבוע או קריטיות. אם אין — כתוב: אין>

כללים:
- התבסס אך ורק על המשימות שניתנו. אל תמציא משימות, שמות או תאריכים.
- אל תשתמש במרכאות כפולות (") — השתמש בגרש בודד (׳) בלבד.
- ללא markdown וללא תגיות HTML.
"""


async def collect_at_risk(session: AsyncSession) -> dict[str, list[Mission]]:
    """Active missions due within AT_RISK_DAYS (overdue included), bucketed by urgency."""
    today = oms.today_il()
    horizon = today + datetime.timedelta(days=AT_RISK_DAYS)

    missions = list((await session.scalars(
        select(Mission)
        .options(selectinload(Mission.owner))
        .where(
            Mission.status.in_(oms.ACTIVE_STATUSES),
            or_(
                Mission.due_date <= horizon,
                # Undated fires get surfaced too — they have no deadline to trip on.
                (Mission.due_date.is_(None)) & Mission.is_urgent.is_(True)
                & Mission.is_important.is_(True),
            ),
        )
    )).all())

    out: dict[str, list[Mission]] = {"late": [], "today": [], "soon": [], "nodate": []}
    for m in missions:
        if m.due_date is None:
            out["nodate"].append(m)
            continue
        delta = (m.due_date - today).days
        if delta < 0:
            out["late"].append(m)
        elif delta == 0:
            out["today"].append(m)
        else:
            out["soon"].append(m)
    for group in out.values():
        group.sort(key=lambda m: (_priority_weight(m), m.due_date or datetime.date.max))
    return out


_BUCKET_TITLES = {
    "late": "⚠️ באיחור",
    "today": "📌 ליעד היום",
    "soon": f"🟡 בתוך {AT_RISK_DAYS} ימים",
    "nodate": "🔥 דחוף וחשוב, ללא תאריך יעד",
}


def _format_at_risk_plain(groups: dict[str, list[Mission]], today: datetime.date) -> str:
    """Deterministic fallback — used whenever the LLM is unavailable."""
    lines = ["‏🧠 <b>משימות בסיכון</b>"]
    any_row = False
    for key in ("late", "today", "soon", "nodate"):
        group = groups.get(key) or []
        if not group:
            continue
        any_row = True
        lines.append("")
        lines.append(f"<b>{_BUCKET_TITLES[key]}</b>")
        for m in group:
            lines.append(f"• {oms.format_mission_line(m, today)}")
    if not any_row:
        return "‏🟢 <b>אין משימות באיחור או בסיכון</b>\nכל המשימות הפעילות עם יעד רחוק מ-" \
               f"{AT_RISK_DAYS} ימים."
    lines.append("")
    lines.append("<i>סיכום ה-AI אינו זמין כרגע — זו הרשימה הגולמית.</i>")
    return "\n".join(lines)


def _at_risk_context(groups: dict[str, list[Mission]], today: datetime.date) -> str:
    lines = []
    for key in ("late", "today", "soon", "nodate"):
        group = groups.get(key) or []
        if not group:
            continue
        lines.append(f"{_BUCKET_TITLES[key].split(' ', 1)[-1]}:")
        for m in group:
            due = oms.format_due(m.due_date) if m.due_date else "ללא יעד"
            late_txt = ""
            if m.due_date and m.due_date < today:
                late_txt = f", באיחור של {(today - m.due_date).days} ימים"
            lines.append(
                f"- {_sanitize(m.title)} | אחראי: {_sanitize(_user_name(m.owner))} | "
                f"יעד: {due}{late_txt} | {oms.quadrant_label(oms.quadrant_key(m))}"
            )
        lines.append("")
    return "\n".join(lines)


async def build_focus_summary(session: AsyncSession) -> str:
    """AI situation assessment + to-do list for everything overdue or due within AT_RISK_DAYS.

    Always returns sendable HTML: falls back to a plain bucketed listing if the LLM fails.
    """
    today = oms.today_il()
    groups = await collect_at_risk(session)
    if not any(groups.values()):
        return _format_at_risk_plain(groups, today)

    from app.services.llm_router import llm_chat
    try:
        raw = await llm_chat(
            "missions_summary",
            [
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": _at_risk_context(groups, today)},
            ],
            max_tokens=1200,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning(f"missions_report: focus summary LLM failed, using plain list: {e}")
        return _format_at_risk_plain(groups, today)

    raw = (raw or "").strip()
    if not raw:
        return _format_at_risk_plain(groups, today)

    counts = (
        f"⚠️ {len(groups['late'])} באיחור · "
        f"📌 {len(groups['today'])} להיום · "
        f"🟡 {len(groups['soon'])} בתוך {AT_RISK_DAYS} ימים"
    )
    # Escape the model output before wrapping it in our own tags — it must not inject markup.
    body = _html.escape(raw)
    text = f"‏🧠 <b>סיכום משימות בסיכון</b>\n<i>{counts}</i>\n\n{body}"

    from app.services.telegram_routing import _TG_MAX
    if len(text) > _TG_MAX:
        text = text[: _TG_MAX - 20] + "\n…(קוצר)"
    return text
