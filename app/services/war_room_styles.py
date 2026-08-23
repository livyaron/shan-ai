"""חדר מבצעים — the per-user board layouts and how a request resolves to one.

Every style renders the SAME router context through a different template; no
style has its own data model, permissions or write path. This module is the
single source of truth for which styles exist — the router, the templates and
the tests all read it from here, and nothing re-spells the keys inline.
"""

# (key, short name shown on the switcher, one-line "what is this for")
STYLES: list[tuple[str, str, str]] = [
    ("cyber", "לוח סייבר",  "הלוח הקיים — מטריצת דחיפות × חשיבות"),
    ("focus", "פוקוס",      "המשימות שלי להיום — נוח בטלפון"),
    ("wall",  "לוח מצב",    "מסך קיר — קריא ממרחק, ללא עריכה"),
    ("table", "טבלה",       "תצוגה צפופה — מיון, קיבוץ ופעולות באצווה"),
]

# NULL in users.war_room_style means "this one", so an existing user who never
# touched the switcher keeps exactly the screen they already know.
DEFAULT_STYLE = "cyber"

_TEMPLATES = {
    "cyber": "war_room.html",
    "focus": "war_room_focus.html",
    "wall":  "war_room_wall.html",
    "table": "war_room_table.html",
}

STYLE_KEYS = [key for key, *_ in STYLES]
STYLE_LABELS = {key: name for key, name, _ in STYLES}


def is_known(style: str | None) -> bool:
    return style in _TEMPLATES


def resolve(requested: str | None, saved: str | None) -> str:
    """Which style this request renders.

    `?style=` wins over the saved preference so one shared URL can pin the wall
    display without needing a user of its own; an unknown value in either place
    falls back rather than 404s — a stale bookmark should still show the board.
    """
    if is_known(requested):
        return requested  # type: ignore[return-value]
    if is_known(saved):
        return saved  # type: ignore[return-value]
    return DEFAULT_STYLE


def template_for(style: str) -> str:
    return _TEMPLATES.get(style, _TEMPLATES[DEFAULT_STYLE])
