"""מערכת לקחים — who is calling, and what the gateway lets through.

Pure functions only: the router loads rows and hands them in, so every rule
here is tested without a database (PLAN-lessons-module.md §2.1).

Identity: a Shan-AI user maps to ONE module profile (their linked profile, or
a lazily-created viewer `s<user_id>`) plus the referent groups they belong to.
The ids they may write as ("actor ids") are their own profile and those groups;
a module admin may act for anyone.

Actor fields were read off the React source (acumen-spark-hub, UserContext.tsx)
rather than guessed: `lessons.returned_by` holds the literal 'referent'/'admin',
`notifications.user_id` is the RECIPIENT and `referent_reviews.referent_id` is
the TARGET group on distribution — none of those is "who is acting", so none is
checked.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

ADMIN = "admin"
VIEWER = "viewer"
REFERENT = "referent"

# The 17 tables PostgREST serves. Anything else (referent_members, the OpenAPI
# root) is not reachable through the gateway.
EXPOSED_TABLES: frozenset[str] = frozenset({
    "profiles", "equipment", "lesson_categories", "projects", "lessons",
    "lesson_implementations", "lesson_workflow_events", "referent_reviews",
    "form_field_configs", "form_field_options", "feedback", "notifications", "activity_logs",
    "ai_feedback", "ai_candidate_insights", "ai_global_insights", "ai_user_preferences",
})
RPC_ALLOWED: frozenset[str] = frozenset({"apply_lesson_workflow_event"})

# Query params that are not a row filter (PostgREST's own keywords).
NON_FILTER_PARAMS: frozenset[str] = frozenset({"select", "order", "limit", "offset", "columns", "on_conflict"})

# Request headers forwarded to PostgREST. authorization/apikey are dropped:
# PostgREST runs every request as lessons_anon; identity is enforced here.
FORWARD_REQUEST_HEADERS: tuple[str, ...] = (
    "content-type", "accept", "prefer", "range", "range-unit", "accept-profile", "content-profile",
)
FORWARD_RESPONSE_HEADERS: tuple[str, ...] = (
    "content-type", "content-range", "content-location", "preference-applied",
)

# Fields that name WHO is acting, per table: must be one of the caller's actor ids.
ACTOR_FIELDS: dict[str, tuple[str, ...]] = {
    "lessons": ("approved_by",),
    "lesson_implementations": ("responded_by",),
    "lesson_workflow_events": ("actor_id",),
    "activity_logs": ("user_id",),
    "feedback": ("user_id",),
    "ai_feedback": ("user_id",),
    "ai_user_preferences": ("user_id",),
    "ai_candidate_insights": ("reviewed_by",),
    "ai_global_insights": ("approved_by",),
}
# Checked on insert only: editing someone's lesson keeps its creator.
CREATE_ACTOR_FIELDS: dict[str, tuple[str, ...]] = {
    "lessons": ("created_by",),
}

# What a viewer may write: its own trail and its own settings, nothing that
# changes a lesson or a project.
VIEWER_WRITABLE: frozenset[str] = frozenset({
    "activity_logs", "feedback", "ai_feedback", "ai_user_preferences", "notifications", "profiles",
})

# The only profile fields a non-admin may change. assigned_projects is written
# by the SPA when a project manager creates/reassigns a project (it syncs the
# manager's list); email_preferences only on the caller's own profile.
NON_ADMIN_PROFILE_FIELDS: frozenset[str] = frozenset({"assigned_projects", "email_preferences"})
SELF_ONLY_PROFILE_FIELDS: frozenset[str] = frozenset({"email_preferences"})

WRITE_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "PUT", "DELETE"})


@dataclass(frozen=True)
class Identity:
    """The caller as the module sees them."""
    shan_user_id: int
    profile: dict[str, Any]
    groups: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def profile_id(self) -> str:
        return str(self.profile["id"])

    @property
    def role(self) -> str:
        return str(self.profile.get("role") or VIEWER)

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def is_viewer(self) -> bool:
        # Belonging to a referent group is a way to act, so a member is not read-only.
        return self.role == VIEWER and not self.groups

    @property
    def actor_ids(self) -> frozenset[str]:
        return frozenset({self.profile_id, *(str(g["id"]) for g in self.groups)})

    def role_of(self, actor_id: str) -> str | None:
        if actor_id == self.profile_id:
            return self.role
        for g in self.groups:
            if str(g["id"]) == actor_id:
                return str(g.get("role") or REFERENT)
        return None


def viewer_profile_row(user_id: int, username: str | None, email: str | None) -> dict[str, Any]:
    """The profile lazily created for a Shan-AI user with no module profile."""
    return {
        "id": f"s{user_id}",
        "name": (username or f"user {user_id}").strip(),
        "email": email or "",
        "role": VIEWER,
        "shan_user_id": user_id,
    }


def pick_profile(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Of the login profiles linked to one user, the real one beats a lazy viewer.

    An admin linking a real profile to someone who already visited leaves the
    old `s<id>` viewer row behind; the link must win without anyone deleting it.
    """
    logins = [r for r in rows if r.get("is_login", True)]
    if not logins:
        return None
    return min(logins, key=lambda r: (r.get("role") == VIEWER, str(r["id"])))


def me_payload(identity: Identity) -> dict[str, Any]:
    """Body of GET /lessons/api/me — what the SPA builds its user from."""
    return {
        "profile": identity.profile,
        "groups": list(identity.groups),
        "shan_user_id": identity.shan_user_id,
        "is_admin": identity.is_admin,
    }


def parse_path(rest_path: str) -> tuple[str, str | None]:
    """'/lessons' → ('lessons', None); '/rpc/fn' → ('rpc', 'fn')."""
    parts = [p for p in rest_path.split("/") if p]
    if not parts:
        return "", None
    if parts[0] == "rpc":
        return "rpc", (parts[1] if len(parts) > 1 else "")
    return parts[0], None


def has_filter(params: list[tuple[str, str]]) -> bool:
    return any(k not in NON_FILTER_PARAMS for k, _ in params)


def _rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        return [body]
    return []


def parse_json_body(raw: bytes) -> tuple[Any, str | None]:
    """(parsed, error). An empty body is None, not an error."""
    if not raw or not raw.strip():
        return None, None
    try:
        return json.loads(raw), None
    except (ValueError, UnicodeDecodeError):
        return None, "Request body is not valid JSON"


def _filter_targets_self(params: list[tuple[str, str]], identity: Identity) -> bool:
    """True when the row filter is exactly `id=eq.<caller's own profile id>`."""
    filters = [(k, v) for k, v in params if k not in NON_FILTER_PARAMS]
    return filters == [("id", f"eq.{identity.profile_id}")]


def check_request(
    method: str,
    rest_path: str,
    params: list[tuple[str, str]],
    body: Any,
    identity: Identity,
) -> tuple[int, str] | None:
    """Gateway rule for one PostgREST call. None = forward it; else (status, message)."""
    method = method.upper()
    table, fn = parse_path(rest_path)

    if table == "rpc":
        if fn not in RPC_ALLOWED:
            return 404, "Function not available"
        if method not in {"POST", "GET", "HEAD"}:
            return 405, "Method not allowed"
        if fn == "apply_lesson_workflow_event":
            return _check_workflow_rpc(method, params, body, identity)
        return None

    if table not in EXPOSED_TABLES:
        return 404, "Not found"
    if method in {"GET", "HEAD"}:
        return None
    if method not in WRITE_METHODS:
        return 405, "Method not allowed"

    if method in {"PATCH", "DELETE"} and not has_filter(params):
        return 400, "Bulk update/delete without a filter is blocked"
    if table == "lesson_workflow_events" and method != "POST":
        return 403, "Workflow history is append-only"

    if identity.is_admin:
        return None

    if identity.is_viewer and table not in VIEWER_WRITABLE:
        return 403, "משתמש צפייה בלבד אינו יכול לשנות נתונים"

    rows = _rows(body)

    if table == "profiles":
        if method != "PATCH":
            return 403, "Only an admin can add or remove users"
        allowed = SELF_ONLY_PROFILE_FIELDS if identity.is_viewer else NON_ADMIN_PROFILE_FIELDS
        for row in rows:
            extra = set(row) - allowed
            if extra:
                return 403, f"Only an admin can change: {', '.join(sorted(extra))}"
            if set(row) & SELF_ONLY_PROFILE_FIELDS and not _filter_targets_self(params, identity):
                return 403, "You can change only your own preferences"
        return None

    fields = ACTOR_FIELDS.get(table, ())
    if method in {"POST", "PUT"}:
        fields = fields + CREATE_ACTOR_FIELDS.get(table, ())
    for row in rows:
        for f in fields:
            value = row.get(f)
            if value is not None and str(value) not in identity.actor_ids:
                return 403, f"{f} must be you or a group you belong to"
    return None


def _check_workflow_rpc(method: str, params: list[tuple[str, str]], body: Any,
                        identity: Identity) -> tuple[int, str] | None:
    if method != "POST":
        return 405, "Use POST"
    if not isinstance(body, dict):
        return 400, "Expected a JSON object"
    if identity.is_admin:
        return None
    if identity.is_viewer:
        return 403, "משתמש צפייה בלבד אינו יכול לשנות סטטוס לקח"
    actor = str(body.get("p_actor_id") or "")
    if actor not in identity.actor_ids:
        return 403, "p_actor_id must be you or a group you belong to"
    # The function trusts p_actor_role (it refuses only 'viewer'), so the role
    # claimed must be the role that actor really has.
    if body.get("p_actor_role") != identity.role_of(actor):
        return 403, "p_actor_role does not match the acting profile"
    return None


def forward_headers(incoming: dict[str, str]) -> dict[str, str]:
    """Headers sent to PostgREST: an allow-list, with the schema pinned to lessons."""
    out = {k: v for k, v in ((h, incoming.get(h)) for h in FORWARD_REQUEST_HEADERS) if v}
    out["accept-profile"] = "lessons"
    out["content-profile"] = "lessons"
    return out
