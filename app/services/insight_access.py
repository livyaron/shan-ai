"""Who sees which part of the patterns page (PLAN.md P3).

The single place that decides it. `scope_for` reads the user's role from the
DB (admin flag, `users.sector`, confirmed `manager_aliases`); `view_for` is a
pure function of that scope and the engine's output, so the rules are tested
without a database.

| viewer                 | projects            | league | sectors | attribution | admin-only sections |
|------------------------|---------------------|--------|---------|-------------|---------------------|
| admin                  | all                 | yes    | all     | all         | yes                 |
| PM department (pm_dept)| all                 | yes    | all     | all         | no                  |
| sector manager         | stages of the sector| no     | own row | own sector  | no                  |
| PM (linked alias)      | own                 | yes (D3)| no     | no          | no                  |

Leading indicators, risk topics, update waves and data health stay admin-only:
the indicators are not to be shown before they replicate (PLAN.md §8.4) and
the taxonomy is unvalidated (§8.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ManagerAlias, User
from app.services import stage_sectors as ss

ADMIN_SECTIONS = frozenset({"leading", "risk", "waves", "health", "lists"})


@dataclass(frozen=True)
class Scope:
    admin: bool = False
    sector: str | None = None                  # a stage_sectors key, or None
    managers: frozenset[str] = field(default_factory=frozenset)   # confirmed file names

    @property
    def allowed(self) -> bool:
        return self.admin or self.sector in ss.ASSIGNABLE_SECTORS or bool(self.managers)


async def scope_for(session: AsyncSession, user: User) -> Scope:
    names = (await session.execute(
        select(ManagerAlias.alias).where(ManagerAlias.user_id == user.id))).scalars().all()
    sector = getattr(user, "sector", None)
    return Scope(admin=bool(user.is_admin),
                 sector=sector if sector in ss.ASSIGNABLE_SECTORS else None,
                 managers=frozenset(names))


async def preview_scope(session: AsyncSession, as_user: str | None, as_sector: str | None,
                        as_manager: str | None) -> tuple[Scope, str] | None:
    """An admin's "view as": the scope another viewer would get, and a label.

    as_user → exactly that user's scope (their sector + confirmed names).
    as_sector → a sector manager with no names. as_manager → a PM by the name
    in the file, linked or not — so a link can be checked before it is saved.
    Admin power is never carried into a preview. None = no preview asked.
    """
    if as_user and as_user.isdigit():
        user = await session.get(User, int(as_user))
        if user is not None:
            sc = await scope_for(session, user)
            return Scope(admin=False, sector=sc.sector, managers=sc.managers), f"משתמש: {user.username}"
    if as_sector in ss.ASSIGNABLE_SECTORS:
        return Scope(sector=as_sector), ss.SECTORS[as_sector]
    if as_manager:
        return Scope(managers=frozenset({as_manager})), f'מנה"פ: {as_manager}'
    return None


async def preview_options(session: AsyncSession, p: dict) -> dict:
    """What the admin's "view as" selector offers: users that have a role,
    every sector, and every PM name the latest report carries."""
    linked = set((await session.execute(select(ManagerAlias.user_id).where(ManagerAlias.user_id.isnot(None)))).scalars())
    users = (await session.execute(select(User).order_by(User.username))).scalars().all()
    return {
        "users": [{"id": u.id, "name": u.username, "sector": ss.SECTORS.get(getattr(u, "sector", None) or "", "")}
                  for u in users if (getattr(u, "sector", None) in ss.ASSIGNABLE_SECTORS or u.id in linked)],
        "sectors": ss.ASSIGNABLE_SECTORS,
        "managers": sorted({r["manager"] for r in p.get("league", []) if r.get("manager")}),
    }


def summarize(projects: list[dict], as_of: str | None) -> dict:
    """The page's tiles, computed from whichever projects the viewer sees."""
    measured = [p for p in projects if p["forecast_moved"] is not None]
    based = [p for p in projects if p["baseline_moved"] is not None]
    return {
        "n": len(projects),
        "forecast_later": sum(p["forecast_moved"] for p in measured), "forecast_n": len(measured),
        "baseline_later": sum(p["baseline_moved"] for p in based), "baseline_n": len(based),
        "stuck": sum(p["stuck"] for p in projects),
        "stale": sum(p["stale"] for p in projects),
        "undated": sum(p["undated"] for p in projects),
        "past_due": sum(1 for p in projects if p["fc"] and as_of and p["fc"] < as_of),
    }


def view_for(scope: Scope, p: dict) -> dict:
    """What this viewer gets from the engine output `p`."""
    all_projects = p.get("projects", [])
    full = scope.admin or scope.sector == ss.PM_DEPT
    if full:
        projects = all_projects
    else:
        projects = [x for x in all_projects
                    if (scope.sector and scope.sector in x["sectors"]) or x["manager"] in scope.managers]

    sections = {"tiles", "projects"}
    if full or scope.managers:
        sections.add("league")                   # D3: every PM sees the named table
    if full or scope.sector:
        sections |= {"sectors", "attribution"}
    if scope.admin:
        sections |= ADMIN_SECTIONS

    sectors = p.get("sectors", [])
    attribution = p.get("metrics", {}).get("slip_attribution", {}).get("value", {})
    if not full and scope.sector:
        sectors = [s for s in sectors if s["sector"] == scope.sector]
        attribution = {k: v for k, v in attribution.items() if k == scope.sector}

    if scope.admin:
        title = "כל האגף"
    elif scope.sector == ss.PM_DEPT:
        title = ss.SECTORS[ss.PM_DEPT]
    elif scope.sector:
        title = ss.SECTORS[scope.sector]
    else:
        title = "הפרויקטים שלי"

    return {
        "title": title,
        "sections": sections,
        "projects": projects,
        "summary": summarize(projects, p.get("as_of")),
        "sectors": sectors,
        "attribution": attribution,
        "league": p.get("league", []) if "league" in sections else [],
    }



# ── Drill-down: the projects / events behind any number on the page ───────

_PROJECT_TESTS = {
    "forecast": ("יעד חשמול נדחה", lambda x: x["forecast_moved"] is True),
    "baseline": ("יעד תכנית פיתוח נדחה", lambda x: x["baseline_moved"] is True),
    "stuck": ("תקועים 12+ שבועות", lambda x: x["stuck"]),
    "stale": ("דיווח שבועי זהה", lambda x: x["stale"]),
    "undated": ("יעד חשמול כמלל", lambda x: x["undated"]),
    "late": ("מאחרים מול תכנית פיתוח", lambda x: x["late"]),
    "all": ("כל הפרויקטים", lambda x: True),
}
UNASSIGNED = "טרם הוקצה"   # the league's label for a project with no מנה"פ


def drill_for(view: dict, p: dict, kind: str, value: str = "", metric: str = "all") -> dict | None:
    """The rows behind one number. Always drawn from what `view` already lets
    this viewer see — a drill can never widen access. None = not allowed or
    not a known drill.

    kind: tile (value = a _PROJECT_TESTS key) | sector | manager (value = key /
    name, metric = a _PROJECT_TESTS key) | attribution (value = sector) |
    wave (value = interval start, metric = forecast|baseline|both) |
    risk (value = topic) | leading (value = topic, metric = with|without).
    """
    sections, projects = view["sections"], view["projects"]
    as_of = p.get("as_of")

    def project_rows(label: str, test) -> dict:
        rows = [x for x in projects if test(x)]
        return {"label": label, "kind": "projects", "rows": rows}

    if kind == "tile":
        if value == "past_due":
            return project_rows("יעד מסתמן כבר עבר", lambda x: bool(x["fc"] and as_of and x["fc"] < as_of))
        if value in _PROJECT_TESTS:
            return project_rows(_PROJECT_TESTS[value][0], _PROJECT_TESTS[value][1])
        return None

    if metric not in _PROJECT_TESTS and kind in ("sector", "manager"):
        return None
    m_label, m_test = _PROJECT_TESTS.get(metric, _PROJECT_TESTS["all"])

    if kind == "sector" and "sectors" in sections and any(s["sector"] == value for s in view["sectors"]):
        return project_rows(f"{ss.SECTORS.get(value, value)} · {m_label}",
                            lambda x: value in x["sectors"] and m_test(x))
    if kind == "manager" and "league" in sections:
        # The league names every PM, but a drill only lists projects this
        # viewer already sees — a PM drilling a colleague's row gets nothing.
        return project_rows(f"{value} · {m_label}",
                            lambda x: (x["manager"] or UNASSIGNED) == value and m_test(x))
    if kind == "attribution" and "attribution" in sections and value in view["attribution"]:
        rows = [e for e in p.get("events", []) if e["kind"] == "forecast" and value in e["sectors"]]
        return {"label": f"דחיות שנרשמו על {ss.SECTORS.get(value, value)}", "kind": "events", "rows": rows}
    if kind == "wave" and "waves" in sections:
        # A wave counts consecutive report pairs only; a move measured across
        # an undated gap (from → a later "to") belongs to no wave.
        to = next((w["to"] for w in p.get("metrics", {}).get("update_waves", {}).get("value", [])
                   if w["from"] == value), None)
        live = [e for e in p.get("events", []) if e["from"] == value and e["to"] == to and e["live"]]
        if metric == "both":
            both = ({e["identifier"] for e in live if e["kind"] == "forecast"}
                    & {e["identifier"] for e in live if e["kind"] == "baseline"})
            rows = [e for e in live if e["identifier"] in both]
        elif metric in ("forecast", "baseline"):
            rows = [e for e in live if e["kind"] == metric]
        else:
            return None
        return {"label": f"גל {value} · {metric}", "kind": "events", "rows": rows}
    if kind == "risk" and "risk" in sections:
        return project_rows(f"מזכירים: {value}", lambda x: value in x["risk_cats"])
    if kind == "leading" and "leading" in sections and metric in ("with", "without"):
        want = metric == "with"
        return project_rows(f"{value} — {'הוזכר' if want else 'לא הוזכר'} בדוח הראשון",
                            lambda x: x["forecast_moved"] is not None and (value in x["early_cats"]) is want)
    return None
