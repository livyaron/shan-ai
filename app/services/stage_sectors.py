"""Which sector owns a project, by the stage it is in (PLAN.md §5).

The single source of truth for the stage → sector map. The pattern engine, the
web views and the tests all read it from here — never re-spell a stage or a
sector key anywhere else.

A project belongs to the sector whose people move it to the next stage. One
stage (עבודה אזרחית והרכבות) belongs to two sectors at once, so per-sector
counts must never be summed into a division total.
"""

PLANNING = "planning"
SUPERVISION = "supervision"
EXECUTION = "execution"
PM_DEPT = "pm_dept"
UNKNOWN = "unknown"   # empty or unseen stage — a data-quality item, never dropped

SECTORS: dict[str, str] = {
    PLANNING: "מגזר תכנון",
    SUPERVISION: "מגזר פיקוח",
    EXECUTION: "מגזר ביצוע",
    PM_DEPT: "ניהול פרויקטים",
    UNKNOWN: "ללא סטטוס",
}

CLOSED_STAGES = frozenset({"הסתיים"})

STAGE_SECTORS: dict[str, tuple[str, ...]] = {
    "תכנון": (PLANNING,),
    "הקפאת תכולה": (PLANNING,),
    "הקפאת תצורה": (PLANNING,),
    "קבלת היתר": (PLANNING,),
    "עבודה אזרחית": (SUPERVISION,),
    "לקראת ביצוע": (SUPERVISION,),
    "הרכבה חשמלית": (EXECUTION,),
    "הרכבה חשמלית ובדיקות": (EXECUTION,),
    "בדיקות": (EXECUTION,),
    "עבודה אזרחית והרכבות": (SUPERVISION, EXECUTION),
    "בחירת קבלן": (PM_DEPT,),
    "הסכם- אגירת אנרגיה": (PM_DEPT,),
    "טופס 4": (PM_DEPT,),
}


def normalize_stage(stage: object) -> str:
    """Collapse whitespace; anything that is not text (None, a pandas NaN) is ""."""
    return " ".join(stage.split()) if isinstance(stage, str) else ""


def is_closed(stage: str | None) -> bool:
    return normalize_stage(stage) in CLOSED_STAGES


def sectors_for(stage: str | None) -> tuple[str, ...]:
    """Sectors that own a project in this stage. () for a closed project,
    (UNKNOWN,) for an empty or unseen stage."""
    s = normalize_stage(stage)
    if s in CLOSED_STAGES:
        return ()
    return STAGE_SECTORS.get(s, (UNKNOWN,))
