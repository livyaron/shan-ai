"""מערכת לקחים P1 — schema, grants, gateway guards and /me. No database.

The grants tests read the SQL itself: lessons_anon may be granted only the
lessons schema, so it can never read public.users. The live proof is
lessons_schema.ISOLATION_CHECK, run at startup and reported on /_status.
"""
import re

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import lessons_gateway as gw
from app.services import lessons_access as la
from app.services import lessons_schema as ls

ALL_SQL = ls.SCHEMA_STATEMENTS + ls.ROLE_STATEMENTS


# ------------------------------------------------------------------ schema SQL

def test_no_ddl_against_public_except_the_users_links():
    for stmt in ALL_SQL:
        stripped = re.sub(r"REFERENCES public\.users\(id\)", "", stmt)
        assert "public." not in stripped.lower(), stmt[:120]


def test_users_link_is_set_null_on_profiles_and_cascade_on_membership():
    sql = "\n".join(ls.SCHEMA_STATEMENTS)
    assert "shan_user_id integer REFERENCES public.users(id) ON DELETE SET NULL" in sql
    assert "shan_user_id integer NOT NULL REFERENCES public.users(id) ON DELETE CASCADE" in sql


def test_every_table_lives_in_schema_lessons():
    sql = "\n".join(ls.SCHEMA_STATEMENTS)
    for t in ls.TABLES:
        assert f"CREATE TABLE IF NOT EXISTS lessons.{t} (" in sql, t
    assert "CREATE TABLE IF NOT EXISTS lessons.referent_members (" in sql
    creates = re.findall(r"CREATE TABLE IF NOT EXISTS (\S+) \(", sql)
    assert all(c.startswith("lessons.") for c in creates)
    assert len(ls.TABLES) == 17


def test_gateway_exposes_exactly_the_module_tables():
    assert la.EXPOSED_TABLES == set(ls.TABLES)
    assert "referent_members" not in la.EXPOSED_TABLES


def test_profiles_has_no_password():
    profiles = next(s for s in ls.SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS lessons.profiles" in s)
    assert "password" not in profiles
    assert "is_login boolean NOT NULL DEFAULT true" in profiles
    assert "ALTER TABLE lessons.profiles DROP COLUMN IF EXISTS password" in ls.SCHEMA_STATEMENTS


def test_projects_carry_optional_shan_identifier_without_fk():
    projects = next(s for s in ls.SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS lessons.projects" in s)
    line = next(ln for ln in projects.splitlines() if "shan_project_identifier" in ln)
    assert "varchar(100)" in line and "REFERENCES" not in line and "NOT NULL" not in line


def test_every_function_pins_search_path_and_is_schema_qualified():
    fns = [s for s in ls.SCHEMA_STATEMENTS if s.startswith("CREATE OR REPLACE FUNCTION")]
    assert len(fns) == 5
    for f in fns:
        assert f.startswith("CREATE OR REPLACE FUNCTION lessons.")
        assert "SET search_path TO 'lessons'" in f
    triggers = [s for s in ls.SCHEMA_STATEMENTS if s.startswith("CREATE OR REPLACE TRIGGER")]
    assert len(triggers) == 4
    for t in triggers:
        assert re.search(r" ON lessons\.\w+ ", t) and "EXECUTE FUNCTION lessons." in t


def test_every_sequence_is_owned_by_its_column():
    seqs = [s for s in ls.SCHEMA_STATEMENTS if s.startswith("CREATE SEQUENCE")]
    owned = [s for s in ls.SCHEMA_STATEMENTS if s.startswith("ALTER SEQUENCE")]
    assert len(seqs) == len(owned) == 14
    for s in owned:
        assert re.fullmatch(r"ALTER SEQUENCE lessons\.\w+_id_seq OWNED BY lessons\.\w+\.id", s)


def test_ddl_is_idempotent():
    for s in ls.SCHEMA_STATEMENTS:
        head = s.split("(")[0]
        assert ("IF NOT EXISTS" in s or "IF EXISTS" in s or "OR REPLACE" in s
                or s.startswith("ALTER SEQUENCE")), head[:80]


# ------------------------------------------------------------------ grants

def test_anon_is_granted_only_schema_lessons():
    grants = [s for s in ls.ROLE_STATEMENTS if s.startswith("GRANT")]
    for g in grants:
        if g == f"GRANT {ls.ANON_ROLE} TO {ls.AUTHENTICATOR_ROLE}":
            continue
        assert re.search(r" ON (SCHEMA lessons|ALL (TABLES|SEQUENCES) IN SCHEMA lessons|FUNCTION lessons\.)", g), g
        assert "public" not in g.lower()


def test_anon_cannot_log_in_and_authenticator_cannot_inherit():
    roles = ls.ROLE_STATEMENTS[0]
    assert f"CREATE ROLE {ls.ANON_ROLE} NOLOGIN" in roles
    assert f"CREATE ROLE {ls.AUTHENTICATOR_ROLE} LOGIN NOINHERIT" in roles


def test_history_is_append_only_and_membership_is_not_postgrests():
    assert f"REVOKE UPDATE, DELETE ON lessons.lesson_workflow_events FROM {ls.ANON_ROLE}" in ls.ROLE_STATEMENTS
    assert f"REVOKE ALL ON lessons.referent_members FROM {ls.ANON_ROLE}" in ls.ROLE_STATEMENTS
    # The revokes must come after the blanket grant, or the grant re-opens them.
    blanket = ls.ROLE_STATEMENTS.index(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lessons TO {ls.ANON_ROLE}")
    for s in ls.ROLE_STATEMENTS:
        if s.startswith("REVOKE"):
            assert ls.ROLE_STATEMENTS.index(s) > blanket


def test_isolation_check_looks_at_every_schema_but_lessons():
    q = ls.ISOLATION_CHECK
    assert "has_table_privilege('lessons_anon'" in q
    assert "n.nspname NOT IN ('lessons', 'pg_catalog', 'information_schema')" in q
    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"c.oid, '{priv}')" in q


def test_authenticator_password_is_never_interpolated_unsafely():
    assert ls.authenticator_password_sql(None) is None
    assert ls.authenticator_password_sql("short") is None
    assert ls.authenticator_password_sql("abc'; DROP ROLE x; --aaaaaaaaaa") is None
    # Low-entropy on purpose: a realistic-looking literal trips secret scanners.
    pw = "test_" + "x" * 12 + "-0"
    assert ls.authenticator_password_sql(pw) == f"ALTER ROLE lessons_authenticator PASSWORD '{pw}'"


@pytest.mark.asyncio
async def test_ensure_schema_never_raises():
    class Boom:
        def begin(self):
            raise RuntimeError("db down")

    status = await ls.ensure_schema(Boom())
    assert status["schema"] is False and "db down" in status["error"]


# ------------------------------------------------------------------ identity

def _ident(role="project_manager", pid="u2", groups=()):
    return la.Identity(shan_user_id=24, profile={"id": pid, "role": role, "name": "x"},
                       groups=tuple({"id": g, "role": "referent", "name": g} for g in groups))


def test_pick_profile_prefers_the_linked_profile_over_a_lazy_viewer():
    rows = [{"id": "s24", "role": "viewer", "is_login": True},
            {"id": "u2", "role": "project_manager", "is_login": True}]
    assert la.pick_profile(rows)["id"] == "u2"
    assert la.pick_profile([{"id": "r1", "role": "referent", "is_login": False}]) is None
    assert la.pick_profile([]) is None


def test_viewer_row_is_namespaced_and_read_only():
    row = la.viewer_profile_row(41, " דנה ", None)
    assert row == {"id": "s41", "name": "דנה", "email": "", "role": "viewer", "shan_user_id": 41}


def test_actor_ids_are_self_plus_groups():
    i = _ident(groups=("r_civil", "r_elec"))
    assert i.actor_ids == {"u2", "r_civil", "r_elec"}
    assert i.role_of("r_civil") == "referent"
    assert i.role_of("u1") is None


def test_a_viewer_in_a_group_is_not_read_only():
    assert _ident(role="viewer", pid="s5").is_viewer
    assert not _ident(role="viewer", pid="s5", groups=("r1",)).is_viewer


# ------------------------------------------------------------------ guards

def chk(method, path, params=(), body=None, ident=None):
    return la.check_request(method, path, list(params), body, ident or _ident())


def test_reads_pass_and_unknown_tables_do_not():
    assert chk("GET", "/lessons", [("select", "*")]) is None
    assert chk("GET", "/referent_members")[0] == 404
    assert chk("GET", "/")[0] == 404
    assert chk("POST", "/rpc/anything", body={})[0] == 404


def test_bulk_patch_or_delete_without_filter_is_blocked_even_for_admin():
    admin = _ident(role="admin", pid="u1")
    assert chk("PATCH", "/lessons", [("select", "*")], {"risk": "high"}, admin)[0] == 400
    assert chk("DELETE", "/lessons", [("order", "id")], None, admin)[0] == 400
    assert chk("DELETE", "/lessons", [("id", "eq.5")], None, admin) is None


def test_workflow_history_is_insert_only():
    admin = _ident(role="admin", pid="u1")
    assert chk("PATCH", "/lesson_workflow_events", [("id", "eq.1")], {"note": "x"}, admin)[0] == 403
    assert chk("DELETE", "/lesson_workflow_events", [("id", "eq.1")], None, admin)[0] == 403
    assert chk("POST", "/lesson_workflow_events", body={"actor_id": "u2"}) is None


def test_actor_must_be_self_or_own_group():
    me = _ident(groups=("r_civil",))
    assert chk("POST", "/lesson_workflow_events", body={"actor_id": "u2"}, ident=me) is None
    assert chk("POST", "/lesson_workflow_events", body={"actor_id": "r_civil"}, ident=me) is None
    assert chk("POST", "/lesson_workflow_events", body={"actor_id": "u1"}, ident=me)[0] == 403
    assert chk("POST", "/activity_logs", body=[{"user_id": "u2"}, {"user_id": "u9"}], ident=me)[0] == 403
    assert chk("PATCH", "/lessons", [("id", "eq.3")], {"approved_by": "u1"}, me)[0] == 403
    assert chk("PATCH", "/lesson_implementations", [("id", "eq.3")], {"responded_by": "r_civil"}, me) is None


def test_created_by_is_checked_on_insert_only():
    assert chk("POST", "/lessons", body={"created_by": "u1", "title": "t"})[0] == 403
    assert chk("POST", "/lessons", body={"created_by": "u2", "title": "t"}) is None
    # Editing a lesson someone else created carries their id along.
    assert chk("PATCH", "/lessons", [("id", "eq.3")], {"created_by": "u1", "title": "t"}) is None


def test_recipient_and_target_fields_are_not_actor_fields():
    # notifications.user_id = recipient; referent_reviews.referent_id = target group;
    # lessons.returned_by = the literal 'referent'/'admin' (UserContext.tsx).
    assert chk("POST", "/notifications", body={"user_id": "u1", "title": "t", "type": "x"}) is None
    assert chk("POST", "/referent_reviews", body={"referent_id": "r_elec", "lesson_id": 1}) is None
    assert chk("PATCH", "/lessons", [("id", "eq.3")], {"returned_by": "referent"}) is None


def test_admin_may_act_for_anyone():
    admin = _ident(role="admin", pid="u1")
    assert chk("POST", "/lesson_workflow_events", body={"actor_id": "u9"}, ident=admin) is None
    assert chk("POST", "/profiles", body={"id": "u99", "name": "n", "email": "e"}, ident=admin) is None
    assert chk("PATCH", "/profiles", [("id", "eq.u2")], {"role": "admin"}, admin) is None


def test_non_admin_profile_writes():
    me = _ident()
    assert chk("POST", "/profiles", body={"id": "u99"}, ident=me)[0] == 403
    assert chk("DELETE", "/profiles", [("id", "eq.u3")], None, me)[0] == 403
    # role escalation and re-linking are admin-only
    assert chk("PATCH", "/profiles", [("id", "eq.u2")], {"role": "admin"}, me)[0] == 403
    assert chk("PATCH", "/profiles", [("id", "eq.u2")], {"shan_user_id": 3}, me)[0] == 403
    # own e-mail preferences: yes; someone else's: no
    assert chk("PATCH", "/profiles", [("id", "eq.u2")], {"email_preferences": {}}, me) is None
    assert chk("PATCH", "/profiles", [("id", "eq.u3")], {"email_preferences": {}}, me)[0] == 403
    assert chk("PATCH", "/profiles", [("id", "eq.u2"), ("name", "eq.x")], {"email_preferences": {}}, me)[0] == 403
    # a PM creating a project syncs the manager's assigned_projects
    assert chk("PATCH", "/profiles", [("id", "eq.u3")], {"assigned_projects": [1, 2]}, me) is None


def test_viewer_writes_only_its_own_trail():
    v = _ident(role="viewer", pid="s7")
    assert chk("POST", "/lessons", body={"created_by": "s7", "title": "t"}, ident=v)[0] == 403
    assert chk("PATCH", "/projects", [("id", "eq.1")], {"risk": "high"}, v)[0] == 403
    assert chk("POST", "/activity_logs", body={"user_id": "s7"}, ident=v) is None
    assert chk("PATCH", "/profiles", [("id", "eq.s7")], {"email_preferences": {}}, v) is None
    assert chk("PATCH", "/profiles", [("id", "eq.u3")], {"assigned_projects": [1]}, v)[0] == 403


def test_workflow_rpc_actor_and_role_must_match():
    me = _ident(groups=("r_civil",))
    body = {"p_lesson_id": 1, "p_event_type": "x", "p_status_after": "y",
            "p_actor_id": "u2", "p_actor_name": "n", "p_actor_role": "project_manager"}
    assert chk("POST", "/rpc/apply_lesson_workflow_event", body=body, ident=me) is None
    assert chk("POST", "/rpc/apply_lesson_workflow_event",
               body={**body, "p_actor_id": "r_civil", "p_actor_role": "referent"}, ident=me) is None
    assert chk("POST", "/rpc/apply_lesson_workflow_event",
               body={**body, "p_actor_role": "admin"}, ident=me)[0] == 403
    assert chk("POST", "/rpc/apply_lesson_workflow_event",
               body={**body, "p_actor_id": "u1"}, ident=me)[0] == 403
    viewer = _ident(role="viewer", pid="s7")
    assert chk("POST", "/rpc/apply_lesson_workflow_event",
               body={**body, "p_actor_id": "s7", "p_actor_role": "viewer"}, ident=viewer)[0] == 403
    assert chk("GET", "/rpc/apply_lesson_workflow_event", ident=me)[0] == 405


def test_forward_headers_drop_credentials_and_pin_the_schema():
    out = la.forward_headers({"authorization": "Bearer x", "apikey": "k", "cookie": "access_token=t",
                              "prefer": "return=representation", "accept-profile": "public",
                              "content-type": "application/json"})
    assert "authorization" not in out and "apikey" not in out and "cookie" not in out
    assert out["accept-profile"] == "lessons" and out["content-profile"] == "lessons"
    assert out["prefer"] == "return=representation"


def test_bad_json_is_an_error_and_empty_is_not():
    assert la.parse_json_body(b"") == (None, None)
    assert la.parse_json_body(b"{oops")[1]
    assert la.parse_json_body(b'[{"a":1}]') == ([{"a": 1}], None)


# ------------------------------------------------------------------ HTTP layer

@pytest.fixture
def as_user():
    def _set(identity):
        async def fake():
            return identity
        app.dependency_overrides[gw.current_identity] = fake
    yield _set
    app.dependency_overrides.clear()


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json=[{"id": 1}],
                              headers={"content-range": "0-0/1", "x-secret": "no"})

    monkeypatch.setattr(gw, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_api_without_session_is_401_json_not_a_redirect():
    async with _client() as c:
        for path in ("/lessons/api/me", "/lessons/api/rest/v1/lessons"):
            r = await c.get(path, follow_redirects=False)
            assert r.status_code == 401, path
            assert r.json()["detail"]["login"] == "/login?next=/lessons"


@pytest.mark.asyncio
async def test_me_returns_profile_and_groups(as_user):
    as_user(_ident(groups=("r_civil",)))
    async with _client() as c:
        r = await c.get("/lessons/api/me")
    body = r.json()
    assert r.status_code == 200
    assert body["profile"]["id"] == "u2" and [g["id"] for g in body["groups"]] == ["r_civil"]
    assert body["is_admin"] is False


@pytest.mark.asyncio
async def test_rest_is_forwarded_with_clean_headers(as_user, upstream):
    as_user(_ident())
    async with _client() as c:
        r = await c.get("/lessons/api/rest/v1/lessons?select=*&order=id.asc",
                        headers={"authorization": "Bearer x", "apikey": "k", "prefer": "count=exact",
                                 "cookie": "access_token=t"})
    assert r.status_code == 200 and r.json() == [{"id": 1}]
    assert r.headers["content-range"] == "0-0/1" and "x-secret" not in r.headers
    sent = upstream[0]
    assert sent.url.path == "/lessons" and sent.url.params["order"] == "id.asc"
    assert "authorization" not in sent.headers and "apikey" not in sent.headers and "cookie" not in sent.headers
    assert sent.headers["accept-profile"] == "lessons"


@pytest.mark.asyncio
async def test_blocked_write_never_reaches_postgrest(as_user, upstream):
    as_user(_ident())
    async with _client() as c:
        r = await c.patch("/lessons/api/rest/v1/profiles?id=eq.u2", json={"role": "admin"})
    assert r.status_code == 403 and upstream == []


@pytest.mark.asyncio
async def test_postgrest_down_is_a_503(as_user, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(gw, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    as_user(_ident())
    async with _client() as c:
        r = await c.get("/lessons/api/rest/v1/lessons")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_ai_functions_are_a_friendly_503_and_unknown_paths_404_json(as_user):
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/summarize-lessons", json={})
        assert r.status_code == 503 and "AI" in r.json()["error"]
        r = await c.get("/lessons/api/nope")
        assert r.status_code == 404 and r.json() == {"error": "Not found"}


@pytest.mark.asyncio
async def test_status_is_admin_only(as_user):
    as_user(_ident())
    async with _client() as c:
        assert (await c.get("/lessons/api/_status")).status_code == 403


def test_storage_keys_cannot_escape_the_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(gw.settings, "LESSONS_UPLOAD_DIR", str(tmp_path))
    assert gw.storage_path("a/b.docx") == (tmp_path / "a" / "b.docx").resolve()
    for bad in ("../x", "a/../../x", "/etc/passwd", "", "a\\..\\x", "a\x00b", "."):
        assert gw.storage_path(bad) is None, bad


@pytest.mark.asyncio
async def test_storage_upload_read_and_no_silent_overwrite(as_user, tmp_path, monkeypatch):
    monkeypatch.setattr(gw.settings, "LESSONS_UPLOAD_DIR", str(tmp_path))
    as_user(_ident())
    async with _client() as c:
        up = await c.post("/lessons/api/storage/v1/object/lesson-files/123/file.txt",
                          files={"": ("file.txt", b"hello", "text/plain")})
        assert up.status_code == 200 and up.json()["Key"] == "lesson-files/123/file.txt"
        again = await c.post("/lessons/api/storage/v1/object/lesson-files/123/file.txt",
                             files={"": ("file.txt", b"other", "text/plain")})
        assert again.status_code == 409
        got = await c.get("/lessons/api/storage/v1/object/public/lesson-files/123/file.txt")
        assert got.status_code == 200 and got.content == b"hello"
        assert (await c.get("/lessons/api/storage/v1/object/public/other/x")).status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_upload(as_user, tmp_path, monkeypatch):
    monkeypatch.setattr(gw.settings, "LESSONS_UPLOAD_DIR", str(tmp_path))
    as_user(_ident(role="viewer", pid="s7"))
    async with _client() as c:
        r = await c.post("/lessons/api/storage/v1/object/lesson-files/x.txt",
                         content=b"hi", headers={"content-type": "text/plain"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_email_needs_a_key_and_a_sandbox_target(as_user, monkeypatch):
    as_user(_ident())
    monkeypatch.setattr(gw.settings, "RESEND_API_KEY", "")
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/send-notification-email",
                         json={"to": "a@b.c", "subject": "s", "body": "b"})
    assert r.status_code == 503
    monkeypatch.setattr(gw.settings, "RESEND_API_KEY", "k")
    monkeypatch.setattr(gw.settings, "LESSONS_EMAIL_SANDBOX", True)
    monkeypatch.setattr(gw.settings, "LESSONS_EMAIL_SANDBOX_TO", "")
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/send-notification-email",
                         json={"to": "a@b.c", "subject": "s", "body": "b"})
    assert r.status_code == 503 and "SANDBOX_TO" in r.json()["error"]


@pytest.mark.asyncio
async def test_email_in_sandbox_goes_only_to_the_sandbox_inbox(as_user, monkeypatch):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "e1"})

    monkeypatch.setattr(gw, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(gw.settings, "RESEND_API_KEY", "k")
    monkeypatch.setattr(gw.settings, "LESSONS_EMAIL_SANDBOX", True)
    monkeypatch.setattr(gw.settings, "LESSONS_EMAIL_SANDBOX_TO", "owner@example.com")
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/send-notification-email",
                         json={"to": "someone@example.com", "subject": "<b>s</b>", "body": "<p>b</p>"})
    assert r.status_code == 200 and r.json()["success"] is True
    import json as _j
    payload = _j.loads(sent[0].content)
    assert payload["to"] == ["owner@example.com"]
    assert "&lt;b&gt;s&lt;/b&gt;" in payload["html"] and "<p>b</p>" in payload["html"]


def test_router_is_mounted_and_startup_runs_the_schema():
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/lessons/api/me" in paths and "/lessons/api/rest/v1/{path:path}" in paths
    from pathlib import Path
    main = Path("app/main.py").read_text(encoding="utf-8")
    assert "ensure_schema as _ensure_lessons_schema" in main


@pytest.mark.asyncio
async def test_missing_schema_is_a_503_not_a_500(monkeypatch):
    from sqlalchemy.exc import ProgrammingError

    from app.database import get_db_session

    async def user(request, session):
        return type("U", (), {"id": 5, "username": "x", "email": None})()

    async def broken(session, u):
        raise ProgrammingError("SELECT", {}, Exception('relation "lessons.profiles" does not exist'))

    async def no_db():
        yield None

    monkeypatch.setattr(gw, "_session_user", user)
    monkeypatch.setattr(gw, "load_identity", broken)
    app.dependency_overrides[get_db_session] = no_db
    try:
        async with _client() as c:
            r = await c.get("/lessons/api/me")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
