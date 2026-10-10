"""מערכת לקחים — import from the Lovable (Supabase) system (PLAN-lessons-module.md §3).

Ported from lessons-handoff/migrate.ts. READ-ONLY toward the source: GETs with
the public anon key, nothing else. Runs inside the app (the sandbox cannot
reach Supabase), triggered by a Shan-AI admin from /lessons/api/_import.

Safety, in order:
1. Every table is fetched in full (`Prefer: count=exact`); a short read aborts.
2. Drift: a source column the target lacks would be dropped silently by
   json_populate_recordset — that aborts before anything is written. Only
   `profiles.password` is dropped on purpose.
3. One transaction: truncate + load + checks. Any failure rolls all of it back,
   so the module is never left half-imported.
4. Module-local state survives a re-import: Shan-AI links, referent group
   members, project links and the lazy viewer profiles.
5. People links (§1.1) are applied only when the Shan-AI user's name is the
   same set of words as the profile's — a wrong link shows someone else's
   lessons, so a mismatch is reported, never forced.

Never touches a table outside schema `lessons`; public.users is only read.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import text

from app.services.lessons_schema import TABLES

logger = logging.getLogger(__name__)

PAGE = 1000
DROPPED_SOURCE_COLUMNS: dict[str, frozenset[str]] = {"profiles": frozenset({"password"})}
# Owner decisions (PLAN §1.1 + 2026-10-10: "עמר דוד" is Shan-AI user 15).
LINKS: tuple[tuple[str, int], ...] = (
    ("u1", 3),
    ("u1773149118417", 28),
    ("u1773053807361", 29),
    ("u2", 24),
    ("u1773009448642", 26),
    ("u1773127270329", 15),
)
# Role accounts that stay entities but are never a login (PLAN §1.1).
NO_LOGIN_NAMES: frozenset[str] = frozenset({"מנהל פרויקט - עבר", "צפייה בלבד"})
_IMPORT_LOCK = 7_340_518_202


class ImportAbort(Exception):
    """Stop before (or roll back) any write; the message is shown to the admin."""


def name_words(name: str | None) -> frozenset[str]:
    """'משה ברקוביץ ' and 'ברקוביץ משה' are the same person; spacing is noise."""
    return frozenset(w for w in re.split(r"\s+", (name or "").strip()) if w)


def drift(source_cols: dict[str, set[str]], target_cols: dict[str, set[str]]) -> dict[str, list[str]]:
    """Source columns the target would silently drop, per table (empty = safe)."""
    out: dict[str, list[str]] = {}
    for table, cols in source_cols.items():
        lost = cols - target_cols.get(table, set()) - DROPPED_SOURCE_COLUMNS.get(table, frozenset())
        if lost:
            out[table] = sorted(lost)
    return out


def clean_rows(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drop = DROPPED_SOURCE_COLUMNS.get(table, frozenset())
    return [{k: v for k, v in r.items() if k not in drop} for r in rows] if drop else rows


def insert_columns(rows: list[dict[str, Any]], target: set[str]) -> list[str]:
    """Columns named in the data AND present in the target, in a stable order."""
    seen: dict[str, None] = {}
    for r in rows:
        for k in r:
            if k in target:
                seen[k] = None
    return list(seen)


def plan_links(profiles: dict[str, str], users: dict[int, str]) -> tuple[list[tuple[str, int]], list[str]]:
    """(links to apply, problems). profiles: id→name in the import; users: id→username."""
    ok, problems = [], []
    for pid, uid in LINKS:
        if pid not in profiles:
            problems.append(f"{pid}: לא קיים בייבוא")
        elif uid not in users:
            problems.append(f"{pid} → {uid}: משתמש Shan-AI לא קיים")
        elif name_words(profiles[pid]) != name_words(users[uid]):
            problems.append(f"{pid} ({profiles[pid].strip()}) → {uid} ({users[uid].strip()}): השמות שונים, לא קושר")
        else:
            ok.append((pid, uid))
    return ok, problems


async def fetch_all(client: httpx.AsyncClient, base: str, key: str, table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected = -1
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    offset = 0
    while True:
        r = await client.get(f"{base}/rest/v1/{table}",
                             params={"select": "*", "order": "id.asc", "offset": offset, "limit": PAGE},
                             headers=headers)
        if r.status_code >= 400:
            raise ImportAbort(f"קריאת {table} מהמקור נכשלה: {r.status_code}")
        total = (r.headers.get("content-range") or "").split("/")[-1]
        if total.isdigit():
            expected = int(total)
        batch = r.json()
        rows.extend(batch)
        offset += PAGE
        if not batch or (len(rows) >= expected if expected >= 0 else len(batch) < PAGE):
            break
    if expected >= 0 and len(rows) != expected:
        raise ImportAbort(f"{table}: נקראו {len(rows)} מתוך {expected}")
    return rows


async def fetch_source(base: str, key: str) -> dict[str, list[dict[str, Any]]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        return {t: await fetch_all(client, base.rstrip("/"), key, t) for t in TABLES}


async def _target_columns(conn) -> dict[str, set[str]]:
    rows = (await conn.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'lessons'"
    ))).all()
    out: dict[str, set[str]] = {}
    for t, c in rows:
        out.setdefault(t, set()).add(c)
    return out


async def run_import(engine, base: str, key: str, *, write: bool) -> dict[str, Any]:
    """Fetch, check, and (write=True) replace schema lessons' data in one transaction."""
    if not base or not key:
        raise ImportAbort("LESSONS_SRC_URL / LESSONS_SRC_KEY לא מוגדרים")
    data = await fetch_source(base, key)
    source_cols = {t: set().union(*(r.keys() for r in rows)) if rows else set() for t, rows in data.items()}
    report: dict[str, Any] = {"mode": "import" if write else "dry-run",
                              "source": {t: len(r) for t, r in data.items()}}

    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _IMPORT_LOCK})
        target_cols = await _target_columns(conn)
        lost = drift(source_cols, target_cols)
        if lost:
            raise ImportAbort(f"המבנה במקור השתנה — עמודות שהיו נזרקות: {lost}")

        profiles = {r["id"]: r.get("name") or "" for r in data["profiles"]}
        users = {uid: name for uid, name in (await conn.execute(text(
            "SELECT id, username FROM public.users WHERE id = ANY(:ids)"
        ), {"ids": [u for _, u in LINKS]})).all()}
        links, problems = plan_links(profiles, users)
        report["links"] = [f"{p} → {u}" for p, u in links]
        report["link_problems"] = problems
        if not write:
            report["ok"] = True
            return report

        # Module-local state that the source knows nothing about.
        kept_links = {r[0]: (r[1], r[2]) for r in (await conn.execute(text(
            "SELECT id, shan_user_id, is_login FROM lessons.profiles WHERE shan_user_id IS NOT NULL"
        ))).all()}
        kept_viewers = [dict(r) for r in (await conn.execute(text(
            "SELECT id, name, email, role, shan_user_id FROM lessons.profiles WHERE NOT (id = ANY(:ids))"
        ), {"ids": list(profiles)})).mappings().all()]
        kept_members = (await conn.execute(text(
            "SELECT profile_id, shan_user_id FROM lessons.referent_members"
        ))).all()
        kept_projects = (await conn.execute(text(
            "SELECT id, shan_project_identifier FROM lessons.projects WHERE shan_project_identifier IS NOT NULL"
        ))).all()

        # Triggers off while loading: the source's own lessons_count/stage_index
        # are the truth, not a recomputation. FK checks stay on (load order is FK-safe).
        for t in (*TABLES, "referent_members"):
            await conn.execute(text(f"ALTER TABLE lessons.{t} DISABLE TRIGGER USER"))
        await conn.execute(text(
            "TRUNCATE " + ", ".join(f"lessons.{t}" for t in (*TABLES, "referent_members")) + " RESTART IDENTITY"
        ))
        for t in TABLES:
            rows = clean_rows(t, data[t])
            if not rows:
                continue
            cols = insert_columns(rows, target_cols[t])
            col_sql = ", ".join(f'"{c}"' for c in cols)
            await conn.execute(text(
                f"INSERT INTO lessons.{t} ({col_sql}) OVERRIDING SYSTEM VALUE "
                f"SELECT {col_sql} FROM json_populate_recordset(NULL::lessons.{t}, CAST(:j AS json))"
            ), {"j": json.dumps(rows, ensure_ascii=False)})
        for t in TABLES:
            if t == "profiles":
                continue
            await conn.execute(text(
                f"SELECT setval(pg_get_serial_sequence('lessons.{t}', 'id'), "
                f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM lessons.{t}), 1), "
                f"(SELECT COUNT(*) > 0 FROM lessons.{t}))"
            ))

        # Restore module-local state, then apply the owner's rules.
        for pid, (uid, is_login) in kept_links.items():
            await conn.execute(text(
                "UPDATE lessons.profiles SET shan_user_id = :u, is_login = :l WHERE id = :p"
            ), {"u": uid, "l": is_login, "p": pid})
        for v in kept_viewers:
            await conn.execute(text(
                "INSERT INTO lessons.profiles (id, name, email, role, shan_user_id) "
                "VALUES (:id, :name, :email, :role, :shan_user_id) ON CONFLICT (id) DO NOTHING"
            ), v)
        await conn.execute(text(
            "UPDATE lessons.profiles SET is_login = false WHERE role = 'referent' OR name = ANY(:n)"
        ), {"n": list(NO_LOGIN_NAMES)})
        for pid, uid in links:
            # Never override a link an admin already set or changed.
            await conn.execute(text(
                "UPDATE lessons.profiles SET shan_user_id = :u WHERE id = :p AND shan_user_id IS NULL"
            ), {"u": uid, "p": pid})
        for pid, uid in kept_members:
            await conn.execute(text(
                "INSERT INTO lessons.referent_members (profile_id, shan_user_id) "
                "SELECT CAST(:p AS text), CAST(:u AS integer) WHERE EXISTS (SELECT 1 FROM lessons.profiles WHERE id = :p AND role = 'referent') "
                "ON CONFLICT DO NOTHING"
            ), {"p": pid, "u": uid})
        for pid, ident in kept_projects:
            await conn.execute(text(
                "UPDATE lessons.projects SET shan_project_identifier = :i WHERE id = :p"
            ), {"i": ident, "p": pid})
        for t in (*TABLES, "referent_members"):
            await conn.execute(text(f"ALTER TABLE lessons.{t} ENABLE TRIGGER USER"))

        target = {}
        for t in TABLES:
            target[t] = (await conn.execute(text(f"SELECT count(*) FROM lessons.{t}"))).scalar_one()
        # Viewer profiles are module-local, so profiles may hold more than the source.
        extra_profiles = len([v for v in kept_viewers if v["id"] not in profiles])
        bad = {t: (len(data[t]), n) for t, n in target.items()
               if n != len(data[t]) + (extra_profiles if t == "profiles" else 0)}
        if bad:
            raise ImportAbort(f"אי-התאמה בספירות (מקור, יעד): {bad} — הכל בוטל")
        report["target"] = target
        report["kept"] = {"links": len(kept_links), "viewers": extra_profiles,
                          "members": len(kept_members), "project_links": len(kept_projects)}
        await conn.execute(text("NOTIFY pgrst, 'reload schema'"))
    report["ok"] = True
    logger.info("lessons import done: %s", report["target"])
    return report
