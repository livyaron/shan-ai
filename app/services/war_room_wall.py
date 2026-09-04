"""חדר מבצעים — the wall display's data shaping (style=wall).

The wall shows the WHOLE active board over time instead of a truncated top-5:
missions are sorted worst-first, cut into fixed-size pages, and the page rotates
in the browser. This module is the single source of truth for the ordering, the
colour tone of a card and the page size — the router, the template and the tests
all read them from here, and nothing re-spells a tone key inline.

Pure functions over already-loaded ORM objects: no DB access, no I/O, so it is
testable without Postgres.
"""

import datetime

from app.models import Mission
from app.services import missions_menu_service as oms
from app.services.missions_report_service import AT_RISK_DAYS, SILENT_DAYS

# How many mission cards fit on one page of a TV at ~4m, and how long each page
# stays up. A full cycle is therefore len(pages) * ROTATE_SECONDS.
WALL_PAGE_SIZE = 6
ROTATE_SECONDS = 15

# SILENT_DAYS comes from missions_report_service so the wall and the AI summary
# call the same missions "unreported" — on the wall this is a separate signal
# from being late: work can be on time and still be unreported, and unreported
# is what surprises a manager.

# Beyond this, "late" stops being a slip and becomes a management problem.
DEEP_LATE_DAYS = 7

# tone key → (legend label, rank). Colour lives in the template's CSS; the rank
# is what puts the worst card on page 1.
TONES: dict[str, tuple[str, int]] = {
    "late-deep": (f"איחור מעל {DEEP_LATE_DAYS} ימים", 0),
    "late":      ("באיחור", 1),
    "today":     ("יעד היום", 2),
    "soon":      (f"עד {AT_RISK_DAYS} ימים", 3),
    "week":      ("השבוע", 4),
    "nodate":    ("ללא תאריך יעד", 5),
    "later":     ("מאוחר יותר", 6),
}

# Order the legend is drawn in — worst first, same as the board itself.
LEGEND = [(key, label) for key, (label, _rank) in sorted(TONES.items(), key=lambda kv: kv[1][1])]


def tone_for(m: Mission, today: datetime.date) -> str:
    """Which colour band a mission sits in. Colour = time, never quadrant.

    Painting urgency AND importance AND lateness onto one card turns the wall
    into a carnival that nobody can read from across the room, so the quadrant
    rides as a text tag instead.
    """
    if m.due_date is None:
        return "nodate"
    delta = (m.due_date - today).days
    if delta < -DEEP_LATE_DAYS:
        return "late-deep"
    if delta < 0:
        return "late"
    if delta == 0:
        return "today"
    if delta <= AT_RISK_DAYS:
        return "soon"
    if delta <= 7:
        return "week"
    return "later"


def days_late(m: Mission, today: datetime.date) -> int:
    return (today - m.due_date).days if m.due_date and m.due_date < today else 0


def _last_update(m: Mission):
    """Newest status update, or None. Never lazy-loads (MissingGreenlet under asyncio)."""
    updates = [u for u in oms.get_mission_updates(m) if u.created_at]
    return max(updates, key=lambda u: u.created_at) if updates else None


def days_since(dt: datetime.datetime | None, today: datetime.date) -> int | None:
    if dt is None:
        return None
    return (today - dt.date()).days


def relative_day(days: int | None) -> str:
    if days is None:
        return "אין דיווח"
    if days <= 0:
        return "היום"
    if days == 1:
        return "אתמול"
    return f"לפני {days} ימים"


def severity_key(m: Mission, today: datetime.date) -> tuple:
    """Sort key — worst first. Deterministic, so the rotation order never jitters
    between two renders of the same board."""
    tone = tone_for(m, today)
    rank = TONES[tone][1]
    silent = days_since(_last_update_at(m), today)
    return (
        rank,
        -days_late(m, today),
        0 if (m.is_urgent and m.is_important) else 1,
        -(silent if silent is not None else 999),
        m.due_date or datetime.date.max,
        m.id or 0,
    )


def _last_update_at(m: Mission) -> datetime.datetime | None:
    u = _last_update(m)
    return u.created_at if u else None


def card_for(m: Mission, today: datetime.date) -> dict:
    """One mission, flattened to exactly what the wall draws.

    A dict rather than the ORM object on purpose: the template stays free of
    logic, and every rule here is unit-testable without a database.
    """
    tone = tone_for(m, today)
    late = days_late(m, today)
    upd = _last_update(m)
    silent = days_since(upd.created_at if upd else None, today)

    if late:
        big, unit = str(late), "ימים באיחור"
    elif tone == "today":
        big, unit = "היום", ""
    elif tone == "nodate":
        big, unit = "—", "ללא יעד"
    else:
        big, unit = m.due_date.strftime("%d/%m"), f"בעוד {(m.due_date - today).days} ימים"

    text = " ".join((upd.text or "").split()) if upd else ""
    return {
        "id": m.id,
        "title": m.title or "",
        "owner": (m.owner.username if m.owner else "—"),
        "quadrant": oms.quadrant_label(oms.quadrant_key(m)),
        "tone": tone,
        "big": big,
        "unit": unit,
        "update_text": text,
        "update_who": (upd.author_name or (upd.author.username if upd and upd.author else "—")) if upd else "",
        "update_when": relative_day(silent),
        "update_close": bool(upd is not None and getattr(upd, "kind", None) == "close"),
        "silent_days": silent,
        # Never reported, or not reported in SILENT_DAYS — both are "nobody is talking".
        "is_silent": (silent is None) or (silent >= SILENT_DAYS),
    }


def closed_card_for(m: Mission, today: datetime.date) -> dict:
    """A recently-closed mission — the cycle's last page, so the wall also shows output."""
    upd = _last_update(m)
    return {
        "id": m.id,
        "title": m.title or "",
        "owner": (m.owner.username if m.owner else "—"),
        "quadrant": oms.quadrant_label(oms.quadrant_key(m)),
        "when": oms.format_stamp_il(m.completed_at) if m.completed_at else "—",
        "note": " ".join((upd.text or "").split()) if upd else "",
    }


def build_pages(
    missions: list[Mission],
    closed: list[Mission],
    today: datetime.date,
    page_size: int = WALL_PAGE_SIZE,
) -> list[dict]:
    """The whole active board, worst-first, cut into rotating pages.

    Every active mission lands on exactly one page — including the undated ones,
    which the old top-5 wall could never show at all.
    """
    cards = [card_for(m, today) for m in sorted(missions, key=lambda m: severity_key(m, today))]
    pages = [
        {"kind": "missions", "cards": cards[i:i + page_size]}
        for i in range(0, len(cards), page_size)
    ] or [{"kind": "missions", "cards": []}]
    if closed:
        pages.append({
            "kind": "closed",
            "cards": [closed_card_for(m, today) for m in closed[:page_size]],
        })
    return pages


def refresh_seconds(page_count: int) -> int:
    """When the page may reload itself: at the END of a full cycle, never mid-page.

    A fixed 60s meta-refresh (what the wall used to carry) would cut the rotation
    in half and restart it from page 1, so the last pages would never be seen.
    """
    return max(60, page_count * ROTATE_SECONDS + 3)
