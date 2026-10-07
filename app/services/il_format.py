"""How a date reads on screen: DD/MM/YYYY, Israel time (user instruction 2026-10-07).

The engine keeps ISO dates (they sort, compare and go into JSON as-is); only
what a person reads is converted. Two filters, registered on every Jinja
environment that renders the patterns pages (`register`):
- `il_date`  — one date value (ISO string, date or datetime) → "06/10/2026";
- `il_dates` — every ISO date inside a free-text string (engine caveats,
  rule reasons, AI prose quoting its context) → the same format;
- `il_datetime` — a naive-UTC timestamp (how the DB stores them) → Israel-local
  "07/10/2026 15:14".
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

IL = ZoneInfo("Asia/Jerusalem")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def il_date(v: object) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    s = str(v)
    m = _ISO.fullmatch(s.strip())
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else s


def il_dates(text: object) -> str:
    if text is None:
        return ""
    return _ISO.sub(lambda m: f"{m.group(3)}/{m.group(2)}/{m.group(1)}", str(text))


def il_datetime(v: datetime | None) -> str:
    if not v:
        return "—"
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(IL).strftime("%d/%m/%Y %H:%M")


def register(env) -> None:
    env.filters.update(il_date=il_date, il_dates=il_dates, il_datetime=il_datetime)


def template_env():
    """A plain Jinja environment with the filters — what the tests render with."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("app/templates"))
    register(env)
    return env
