"""A user's job title as Shan-AI shows it (user decision 2026-10-20).

The project-manager title is written gender-neutral — "מנהל/ת פרויקט" — so
nobody has to assign a gender to each user: "מנהלת פרויקט" was showing under
male PMs too. `neutral_title` is the ONLY place the rule lives; it runs on
every save (users page, personal profile) and once at startup over the
existing rows (app/main.py, idempotent). Any other title is kept as typed.
"""
from __future__ import annotations

NEUTRAL: dict[str, str] = {
    "מנהל פרויקט": "מנהל/ת פרויקט",
    "מנהלת פרויקט": "מנהל/ת פרויקט",
    "מנהל פרויקטים": "מנהל/ת פרויקטים",
    "מנהלת פרויקטים": "מנהל/ת פרויקטים",
}


def neutral_title(title: str | None) -> str | None:
    """The title to store: the neutral form for a gendered PM title, else as typed (trimmed)."""
    t = " ".join((title or "").split())
    if not t:
        return None
    return NEUTRAL.get(t, t)


def migration_statements() -> list[tuple[str, dict[str, str]]]:
    """(SQL, params) that rewrite the existing gendered titles. Idempotent."""
    return [("UPDATE users SET job_title = :new WHERE btrim(job_title) = :old", {"old": old, "new": new})
            for old, new in NEUTRAL.items()]
