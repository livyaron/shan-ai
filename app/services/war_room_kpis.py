"""חדר מבצעים — the four KPI cards, and the board filter each one turns on.

One click on a number shows exactly the missions it counts; a second click on
the same card clears it. This module is the single source of truth for which
KPIs exist, which stat each one reads, and which rows it selects — the router,
the layouts and the tests all read it from here and never re-spell a key.

The counters themselves stay board-wide on purpose: a KPI row that shrank to
match its own filter would stop being a board summary the moment you used it.
"""

import datetime
from urllib.parse import urlencode

from sqlalchemy import Select

from app.models import Mission, MissionStatusEnum
from app.services.missions_menu_service import ACTIVE_STATUSES, quadrant_flags

# The window "הושלמו השבוע" looks back over. Same figure the stat row uses.
DONE_WEEK_DAYS = 7

# key → (stats key it displays, label, tone). Order is the order on screen, and
# it matches the row that was already there — this feature adds behaviour to the
# existing cards, it does not re-cut them.
KPIS: list[tuple[str, str, str, str]] = [
    ("open",      "open",      "משימות פתוחות",   "cyan"),
    ("do",        "do_now",    "🔥 בצע עכשיו",     "red"),
    ("overdue",   "overdue",   "⚠️ באיחור",        "amber"),
    ("done_week", "done_week", "✅ הושלמו השבוע",  "green"),
]

KPI_KEYS = [key for key, *_ in KPIS]
KPI_LABELS = {key: label for key, _stat, label, _tone in KPIS}


def is_known(key: str | None) -> bool:
    return key in KPI_LABELS


def resolve(key: str | None) -> str:
    """An unknown or absent ?kpi= means "no KPI filter" — never a 404.

    A stale bookmark or a hand-edited URL should still answer with the board.
    """
    return key if is_known(key) else ""


def week_ago(now: datetime.datetime | None = None) -> datetime.datetime:
    """The naive-UTC cutoff for "השבוע" — completed_at is stored naive UTC."""
    return (now or datetime.datetime.utcnow()) - datetime.timedelta(days=DONE_WEEK_DAYS)


def owns_status(key: str) -> bool:
    """Does this KPI decide the status itself?

    Every KPI does: each one names a specific slice of the board (active, or
    closed this week), so while one is on, the status <select> must not also
    constrain the query — the two would fight and "הושלמו השבוע" would return
    an empty board while showing a non-zero count.
    """
    return is_known(key)


def apply_filter(
    stmt: Select,
    key: str,
    today: datetime.date,
    now: datetime.datetime | None = None,
) -> Select:
    """Narrow a mission query to exactly the rows one KPI card counts."""
    if key == "open":
        return stmt.where(Mission.status.in_(ACTIVE_STATUSES))
    if key == "do":
        urgent, important = quadrant_flags("do")
        return stmt.where(
            Mission.status.in_(ACTIVE_STATUSES),
            Mission.is_urgent.is_(urgent),
            Mission.is_important.is_(important),
        )
    if key == "overdue":
        return stmt.where(
            Mission.status.in_(ACTIVE_STATUSES),
            Mission.due_date.isnot(None),
            Mission.due_date < today,
        )
    if key == "done_week":
        return stmt.where(
            Mission.status == MissionStatusEnum.DONE.value,
            Mission.completed_at >= week_ago(now),
        )
    return stmt


def build_cards(
    stats: dict[str, int],
    active: str,
    params: dict[str, object] | None = None,
    path: str = "/dashboard/war-room",
) -> list[dict]:
    """The KPI row as data: what each card says, and where clicking it goes.

    The href carries the rest of the filter bar (owner / status / free text /
    style) untouched, so a KPI click narrows what is already on screen instead
    of resetting it. Clicking the active card drops the `kpi` param — that is
    the whole "click again to clear", with no extra button.
    """
    base = {k: v for k, v in (params or {}).items() if v not in (None, "", 0)}
    cards = []
    for key, stat, label, tone in KPIS:
        on = key == active
        query = dict(base)
        query.pop("kpi", None)
        if not on:
            query["kpi"] = key
        cards.append({
            "key": key,
            "label": label,
            "tone": tone,
            "value": stats.get(stat, 0),
            "active": on,
            "href": f"{path}?{urlencode(query)}" if query else path,
        })
    return cards
