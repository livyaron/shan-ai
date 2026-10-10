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
from app.routers import lessons_spa as spa
from app.routers.login import safe_next
from app.services import lessons_access as la
from app.services import lessons_ai as ai
from app.services import lessons_import as li
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


def _client(**kw):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kw)


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
async def test_unknown_function_and_unknown_api_paths_are_404_json(as_user):
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/no-such-function", json={})
        assert r.status_code == 404
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


# ------------------------------------------------------------------ P2: the SPA at /lessons/

@pytest.fixture
def built(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>APP</html>")
    (tmp_path / "assets" / "index-abc.js").write_text("js")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    (tmp_path / "sw.js").write_text("sw")
    monkeypatch.setattr(spa, "SPA_DIR", tmp_path)
    return tmp_path


def _logged_in(monkeypatch, ok=True):
    import app.utils.session as sess
    monkeypatch.setattr(sess, "verify_token", lambda t: {"user_id": 1, "username": "u"} if ok else None)


@pytest.mark.asyncio
async def test_spa_route_without_session_goes_to_login_and_back(built, monkeypatch):
    _logged_in(monkeypatch, ok=False)
    async with _client() as c:
        r = await c.get("/lessons/projects/3", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/lessons/projects/3"


@pytest.mark.asyncio
async def test_spa_route_with_session_is_index_never_cached(built, monkeypatch):
    _logged_in(monkeypatch)
    async with _client() as c:
        for path in ("/lessons/", "/lessons/lessons/12", "/lessons/admin"):
            r = await c.get(path, cookies={"access_token": "t"})
            assert r.status_code == 200 and r.text == "<html>APP</html>", path
            assert r.headers["cache-control"] == "no-cache"
        r = await c.get("/lessons", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/lessons/"


@pytest.mark.asyncio
async def test_built_files_are_public_and_cached_by_kind(built, monkeypatch):
    _logged_in(monkeypatch, ok=False)
    async with _client() as c:
        js = await c.get("/lessons/assets/index-abc.js")
        assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
        # The browser fetches the manifest without cookies — gating it breaks install.
        man = await c.get("/lessons/manifest.webmanifest")
        assert man.status_code == 200 and man.headers["cache-control"] == "no-cache"
        assert (await c.get("/lessons/sw.js")).headers["cache-control"] == "no-cache"
        # An old hashed asset after a deploy is a 404, never index.html as JS.
        assert (await c.get("/lessons/assets/index-old.js")).status_code == 404


@pytest.mark.asyncio
async def test_api_paths_are_never_the_spa(built, monkeypatch, as_user):
    _logged_in(monkeypatch)
    as_user(_ident())
    async with _client() as c:
        for path in ("/lessons/api", "/lessons/api/", "/lessons/api/nope"):
            r = await c.get(path, cookies={"access_token": "t"})
            assert r.status_code == 404 and "APP" not in r.text, path


def test_spa_files_cannot_escape_the_build(built):
    assert spa.spa_file("assets/index-abc.js") is not None
    for bad in ("../x", "assets/../../etc/passwd", "a\\b", "x\x00", ""):
        assert spa.spa_file(bad) is None, bad


@pytest.mark.asyncio
async def test_unbuilt_server_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(spa, "SPA_DIR", tmp_path)
    _logged_in(monkeypatch)
    async with _client() as c:
        r = await c.get("/lessons/", cookies={"access_token": "t"})
    assert r.status_code == 503 and "לא נבנה" in r.text


def test_login_next_is_never_an_open_redirect():
    assert safe_next("/lessons/") == "/lessons/"
    assert safe_next("/lessons/projects/3?x=1") == "/lessons/projects/3?x=1"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil.example",
                "lessons", "/x\r\nSet-Cookie: a=b"):
        assert safe_next(bad) is None, bad


def test_navbar_links_to_the_module_and_docker_builds_it():
    from pathlib import Path
    assert 'href="/lessons/"' in Path("app/templates/_navbar.html").read_text(encoding="utf-8")
    docker = Path("Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-slim AS ui" in docker
    assert "COPY --from=ui /ui/dist /app/static/lessons" in docker


def test_ui_has_no_lovable_or_password_leftovers():
    from pathlib import Path
    ui = Path("lessons_ui")
    assert not (ui / ".env").exists() and not (ui / "supabase").exists()
    for f in [*ui.joinpath("src").rglob("*.ts"), *ui.joinpath("src").rglob("*.tsx"),
              ui / "vite.config.ts", ui / "index.html", ui / "package.json"]:
        if f.name == "types.ts":  # generated Supabase types: describe the old schema
            continue
        text = f.read_text(encoding="utf-8")
        assert "lovable" not in text.lower(), f
        assert "previewAuthStorage" not in text and "VITE_SUPABASE" not in text, f
    ctx = (ui / "src/context/UserContext.tsx").read_text(encoding="utf-8")
    assert "password" not in ctx and "remembered_user_id" not in ctx
    assert 'basename="/lessons"' in (ui / "src/App.tsx").read_text(encoding="utf-8")


# ------------------------------------------------------------------ P3: import from Lovable

def test_drift_allows_only_the_planned_password_drop():
    tgt = {"profiles": {"id", "name", "shan_user_id"}, "lessons": {"id", "title"}}
    assert li.drift({"profiles": {"id", "name", "password"}, "lessons": {"id", "title"}}, tgt) == {}
    assert li.drift({"lessons": {"id", "title", "new_col"}}, tgt) == {"lessons": ["new_col"]}


def test_password_never_reaches_the_target():
    rows = li.clean_rows("profiles", [{"id": "u1", "name": "x", "password": "secret"}])
    assert rows == [{"id": "u1", "name": "x"}]
    assert li.clean_rows("lessons", [{"id": 1, "password": "kept: not a profile"}])[0]["password"]


def test_insert_columns_skip_target_only_and_keep_order():
    rows = [{"id": 1, "name": "a", "gone": 1}, {"id": 2, "site": "s"}]
    assert li.insert_columns(rows, {"id", "name", "site", "shan_project_identifier"}) == ["id", "name", "site"]


def test_names_match_as_word_sets_not_strings():
    assert li.name_words("משה ברקוביץ ") == li.name_words("ברקוביץ  משה")
    assert li.name_words("עמר דוד") == li.name_words("דוד עמר")
    assert li.name_words("דוד עמר") != li.name_words("דוד עמרני")


def test_links_are_applied_only_when_the_names_agree():
    profiles = {"u1": "ירון ליב", "u2": "משה ברקוביץ", "u1773127270329": "עמר דוד",
                "u1773149118417": "נוי כהנאסיה", "u1773053807361": "עמית בלונסקי"}
    users = {3: "ירון ליב", 24: "משה ברקוביץ ", 15: "דוד עמר", 28: "נוי כהן", 29: "עמית בלונסקי"}
    ok, problems = li.plan_links(profiles, users)
    assert ("u1", 3) in ok and ("u2", 24) in ok and ("u1773127270329", 15) in ok
    assert ("u1773149118417", 28) not in ok  # different name → never forced
    assert any("u1773149118417" in p for p in problems)
    assert any("u1773009448642" in p for p in problems)  # not in this import


def test_links_match_the_owner_decisions():
    assert dict(li.LINKS) == {"u1": 3, "u1773149118417": 28, "u1773053807361": 29, "u2": 24,
                              "u1773009448642": 26, "u1773127270329": 15}


def _source(pages: dict[str, list], total_override: dict | None = None):
    def handler(req: httpx.Request):
        table = req.url.path.rsplit("/", 1)[-1]
        rows = pages.get(table, [])
        off, lim = int(req.url.params["offset"]), int(req.url.params["limit"])
        total = (total_override or {}).get(table, len(rows))
        assert req.method == "GET" and req.headers["apikey"] == "k"
        return httpx.Response(200, json=rows[off:off + lim], headers={"content-range": f"0-0/{total}"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_pages_through_everything(monkeypatch):
    monkeypatch.setattr(li, "PAGE", 2)
    rows = [{"id": i} for i in range(5)]
    async with _source({"notifications": rows}) as c:
        assert await li.fetch_all(c, "https://src", "k", "notifications") == rows


@pytest.mark.asyncio
async def test_a_short_read_aborts(monkeypatch):
    monkeypatch.setattr(li, "PAGE", 2)
    async with _source({"lessons": [{"id": 1}, {"id": 2}, {"id": 3}]}, {"lessons": 9}) as c:
        with pytest.raises(li.ImportAbort):
            await li.fetch_all(c, "https://src", "k", "lessons")


@pytest.mark.asyncio
async def test_import_without_source_config_aborts_before_any_io():
    with pytest.raises(li.ImportAbort):
        await li.run_import(object(), "", "", write=True)


def test_import_never_writes_outside_schema_lessons():
    from pathlib import Path
    src = Path("app/services/lessons_import.py").read_text(encoding="utf-8")
    for stmt in re.findall(r'"((?:INSERT|UPDATE|DELETE|ALTER)[^"]*)', src):
        assert "public." not in stmt and "lessons." in stmt, stmt
    truncate = next(ln for ln in src.splitlines() if '"TRUNCATE ' in ln)
    assert 'f"lessons.{t}" for t in (*TABLES, "referent_members")' in truncate
    assert "SELECT id, username FROM public.users" in src  # the one read of Shan-AI data


@pytest.mark.asyncio
async def test_import_endpoints_are_shan_admin_only(monkeypatch):
    from app.database import get_db_session

    async def no_db():
        yield None

    for is_admin, expected in ((False, 403), (True, 400)):
        async def user(request, session, _a=is_admin):
            return type("U", (), {"id": 3, "is_admin": _a})()
        monkeypatch.setattr(gw, "_session_user", user)
        app.dependency_overrides[get_db_session] = no_db
        try:
            async with _client() as c:
                r = await c.post("/lessons/api/_import?mode=bogus")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == expected


@pytest.mark.asyncio
async def test_import_page_requires_a_session():
    async with _client() as c:
        r = await c.get("/lessons/api/_import", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login?next=/lessons/api/_import"
        assert (await c.post("/lessons/api/_import?mode=dry")).status_code == 401


# ------------------------------------------------------------------ referent group members (admin)

def test_groups_page_escapes_every_name():
    from app.routers.lessons_admin import render_groups
    page = render_groups(
        [{"id": "r1", "name": 'רפרנט <script>x</script>'}],
        {"r1": [{"id": 7, "username": "דנה<b>"}]},
        [{"id": 7, "username": "דנה<b>", "job_title": "מהנדסת & ראש צוות"}],
        notice="<i>נוסף</i>",
    )
    assert "<script>" not in page and "<b>" not in page and "<i>" not in page
    assert "&lt;script&gt;" in page and "&amp; ראש צוות" in page
    assert 'action="/lessons/api/_groups/add"' in page and 'action="/lessons/api/_groups/remove"' in page


def test_groups_page_shows_empty_groups():
    from app.routers.lessons_admin import render_groups
    page = render_groups([{"id": "r1", "name": "רפרנט"}], {}, [])
    assert "אין חברים עדיין" in page


@pytest.mark.asyncio
async def test_group_admin_is_shan_admin_only_and_not_swallowed_by_the_gateway(monkeypatch):
    from app.database import get_db_session
    from app.routers import lessons_admin

    async def no_db():
        yield None

    async def not_admin(request, session):
        return None

    monkeypatch.setattr(lessons_admin, "_shan_admin", not_admin)
    _logged_in(monkeypatch)
    app.dependency_overrides[get_db_session] = no_db
    try:
        async with _client(cookies={"access_token": "t"}) as c:
            assert (await c.get("/lessons/api/_groups")).status_code == 403
            r = await c.post("/lessons/api/_groups/add", data={"profile_id": "r1", "shan_user_id": "7"})
            assert r.status_code == 403
            r = await c.post("/lessons/api/_groups/remove", data={"profile_id": "r1", "shan_user_id": "7"})
            assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_group_admin_writes_only_schema_lessons():
    from pathlib import Path
    src = Path("app/routers/lessons_admin.py").read_text(encoding="utf-8")
    stmts = re.findall(r'"((?:INSERT|UPDATE|DELETE)[^"]*)', src)
    assert stmts
    for stmt in stmts:
        assert ("lessons.referent_members" in stmt or "lessons.profiles" in stmt) and "public." not in stmt, stmt


def test_group_page_shows_its_login_account_or_a_link_form():
    from app.routers.lessons_admin import render_groups
    users = [{"id": 40, "username": "רפרנט מגזר ביצוע", "job_title": None}]
    linked = render_groups([{"id": "r1", "name": "רפרנט מגזר ביצוע", "login_user_id": 40,
                             "login_username": "רפרנט מגזר ביצוע"}], {}, users)
    assert "חשבון כניסה: <b>רפרנט מגזר ביצוע</b>" in linked and 'value="unlink"' in linked
    unlinked = render_groups([{"id": "r1", "name": "רפרנט", "login_user_id": None}], {}, users)
    assert 'value="link"' in unlinked and 'action="/lessons/api/_groups/login"' in unlinked


@pytest.mark.asyncio
async def test_group_login_is_shan_admin_only(monkeypatch):
    from app.database import get_db_session
    from app.routers import lessons_admin

    async def no_db():
        yield None

    async def not_admin(request, session):
        return None

    monkeypatch.setattr(lessons_admin, "_shan_admin", not_admin)
    app.dependency_overrides[get_db_session] = no_db
    try:
        async with _client() as c:
            r = await c.post("/lessons/api/_groups/login",
                             data={"profile_id": "r1", "action": "link", "shan_user_id": "40"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 403


def test_a_linked_referent_keeps_its_login_across_reimport():
    from pathlib import Path
    src = Path("app/services/lessons_import.py").read_text(encoding="utf-8")
    assert "WHERE (role = 'referent' AND shan_user_id IS NULL) OR name = ANY(:n)" in src


def test_a_referent_login_account_signs_in_as_the_referent():
    rows = [{"id": "s40", "role": "viewer", "is_login": True},
            {"id": "u1773149388077", "role": "referent", "is_login": True}]
    picked = la.pick_profile(rows)
    assert picked["id"] == "u1773149388077"
    ident = la.Identity(shan_user_id=40, profile=picked)
    assert ident.role == "referent" and not ident.is_viewer
    assert la.check_request("POST", "/rpc/apply_lesson_workflow_event", [],
                            {"p_actor_id": "u1773149388077", "p_actor_role": "referent"}, ident) is None


# ------------------------------------------------------------------ nightly notifications cleanup

def test_cleanup_deletes_only_dated_old_lessons_notifications():
    from app.services import lessons_maintenance as lm
    sql = lm.CLEANUP_SQL
    assert sql.startswith("DELETE FROM lessons.notifications WHERE ")
    assert "public." not in sql
    # The row that broke Lovable's job for months ("לפני שעה") is filtered out
    # before any cast, so it can never abort the delete again.
    assert "\"time\" ~ '^\\d{4}-\\d{2}-\\d{2}'" in sql
    assert "interval '30 days'" in sql


def test_cleanup_runs_at_the_next_0030_utc():
    import datetime as dt

    from app.services.lessons_maintenance import seconds_until
    assert seconds_until(dt.datetime(2026, 10, 10, 19, 30)) == 5 * 3600
    assert seconds_until(dt.datetime(2026, 10, 10, 0, 10)) == 20 * 60
    assert seconds_until(dt.datetime(2026, 10, 10, 0, 30)) == 24 * 3600


@pytest.mark.asyncio
async def test_cleanup_failure_never_raises():
    from app.services.lessons_maintenance import cleanup_notifications

    class Boom:
        def begin(self):
            raise RuntimeError("db down")

    assert await cleanup_notifications(Boom()) is None


def test_startup_schedules_the_nightly_cleanup():
    from pathlib import Path
    src = Path("app/main.py").read_text(encoding="utf-8")
    assert "_lessons_nightly_task = asyncio.create_task(_lessons_nightly(engine))" in src


def test_admin_menu_links_to_the_lessons_admin_pages_for_admins_only():
    from pathlib import Path
    nav = Path("app/templates/_navbar.html").read_text(encoding="utf-8")
    guard = nav.index("{% if _u and _u.is_admin %}")
    for href in ('href="/lessons/api/_groups"', 'href="/lessons/api/_import"'):
        pos = nav.index(href)
        assert guard < pos < nav.index("{% endif %}", guard), href


@pytest.mark.asyncio
async def test_groups_page_without_session_redirects_to_login():
    async with _client() as c:
        r = await c.get("/lessons/api/_groups", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/lessons/api/_groups"


# ------------------------------------------------------------------ AI functions (lessons_ai)

def test_ai_runs_on_the_shan_ai_router_with_its_own_label():
    from app.services.llm_router import USAGE_LABELS
    assert ai.USAGE in USAGE_LABELS


def test_parse_json_survives_fences_and_prose():
    assert ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai.parse_json('הנה התשובה: {"a": [1, 2]} בהצלחה') == {"a": [1, 2]}
    assert ai.parse_json("not json") == {} and ai.parse_json("[1,2]") == {}


def test_clip_lines_says_what_it_left_out():
    out = ai.clip_lines(["x" * 10] * 5, 25)
    assert out.count("x" * 10) == 2 and "עוד 3 שורות" in out


def test_quality_scores_match_the_lovable_maths():
    rows = [{"lesson_id": 1, "is_relevant": True, "is_implemented": True, "responded_at": "t"},
            {"lesson_id": 1, "is_relevant": False, "is_implemented": None, "responded_at": "t"},
            {"lesson_id": 1, "is_relevant": None, "is_implemented": None, "responded_at": "t"},
            {"lesson_id": 1, "is_relevant": True, "is_implemented": False, "responded_at": None}]
    q = ai.quality_scores([1, 2], rows)
    assert q[1]["responses"] == 2 and q[1]["avg"] == 1.0 and q[1]["rate"] == 0.5
    assert q[1]["shrunk"] == (1.0 * 2 + 1.0 * 3) / 5
    assert q[2] == {"responses": 0, "avg": None, "rate": None, "shrunk": 1.0}


def test_summary_prompt_keeps_the_lovable_format_and_scope():
    body = {"role": "project_manager", "userName": "משה", "lessons": [{"title": "t", "project": "p"}],
            "projects": [{"name": "p", "equipment": ["שנאי"]}], "userProjects": [{"name": "p", "equipment": []}]}
    system, user = ai.build_summarize(body, [{"preference_key": "length", "preference_value": "קצר"}], [])
    assert "🎯 **Top 3 עדיפויות**" in system and "מנהל פרויקט בשם משה" in system
    assert "• length: קצר" in system and "הפרויקטים שאני (משה) מנהל" in user
    admin_system, _ = ai.build_summarize({**body, "role": "admin"}, [], [])
    assert "עבור מנהל המערכת" in admin_system


def test_contexts_stay_inside_the_groq_minute_budget():
    lessons = [{"id": i, "title": "ל" * 80, "project": "פ" * 30, "projectName": "פ" * 30, "category": "ק", "stage": "ש",
                "risk": "high", "status": "approved", "equipment": ["שנאי"] * 5, "description": "ת" * 200} for i in range(400)]
    projects = [{"name": "פ" * 30, "stage": "ש", "type": "t", "station": "s", "equipment": ["שנאי"] * 5} for _ in range(200)]
    s1, u1 = ai.build_summarize({"role": "admin", "lessons": lessons, "projects": projects}, [], [])
    s2, u2 = ai.build_analyze({"allLessons": lessons, "projectLessons": lessons[:50], "referents": []}, [], [],
                              ai.quality_scores([l["id"] for l in lessons], []))
    s3, u3 = ai.build_review({"type": "pre_submit", "lesson": {}, "existingLessons": lessons})
    for system, user in ((s1, u1), (s2, u2), (s3, u3)):
        # 2 chars/token, conservative (insight_ai); + max_tokens must fit 8,000/min.
        assert (len(system) + len(user)) / 2 + 1800 < 8000, len(system) + len(user)


def test_review_types_and_normalised_shapes():
    assert ai.build_review({"type": "bogus"}) is None
    for kind in ("pre_submit", "post_approve", "re_analyze"):
        system, _user = ai.build_review({"type": kind, "lesson": {"title": "t", "stage": "תכנון"}})
        assert "JSON" in system
    pre = ai.normalize_review("pre_submit", {"quality_score": "14", "improvements": ["a"]})
    assert pre["quality_score"] == 10 and pre["improvements"] == ["a"] and pre["strengths"] == []
    dist = ai.normalize_review("post_approve", {"recommended_projects": [{"project_name": "p", "priority": "urgent"}]})
    assert dist["recommended_projects"][0]["priority"] == "medium"
    assert set(dist) == {"recommended_projects", "recommended_referents", "implementation_steps", "attention_points", "summary"}


def test_analyze_drops_ids_the_model_invented():
    reply = {"project_analysis": {"summary": "s"},
             "relevant_lessons": [{"lesson_id": 5, "priority": "high"}, {"lesson_id": 999}, {"lesson_id": "x"}],
             "relevant_referents": [{"referent_id": "r1"}, {"referent_id": "ghost"}]}
    out = ai.normalize_analyze(reply, {5}, {"r1"})
    assert [l["lesson_id"] for l in out["relevant_lessons"]] == [5]
    assert [r["referent_id"] for r in out["relevant_referents"]] == ["r1"]
    assert out["project_analysis"]["strengths"] == []


def test_classify_keeps_only_real_items():
    pub, per = ai.normalize_classify({"public_items": [{"text": "חסר סיכון"}, {"text": "  "}],
                                      "personal_items": [{"key": "length", "value": "קצר"}, {"key": "x"}]})
    assert pub == [{"text": "חסר סיכון", "context_type": "general", "context_value": None}]
    assert per == [{"key": "length", "value": "קצר"}]


def test_sse_body_is_what_the_dashboard_parses():
    import json as _j
    lines = [ln for ln in ai.sse_body("שלום").split("\n") if ln]
    assert _j.loads(lines[0][6:])["choices"][0]["delta"]["content"] == "שלום"
    assert lines[-1] == "data: [DONE]"


@pytest.fixture
def ai_stub(monkeypatch):
    calls = []

    async def fake_ask(system, user, *, json_mode, max_tokens=1500):
        calls.append({"system": system, "user": user, "json_mode": json_mode})
        return calls[-1].get("reply") or ('{"suggestions": [{"name": "בטיחות", "description": "d"}]}' if json_mode else "סיכום")

    async def no_prefs(session, user_id):
        calls.append({"prefs_for": user_id})
        return [], []

    monkeypatch.setattr(ai, "ask", fake_ask)
    monkeypatch.setattr(gw, "_prefs_and_insights", no_prefs)
    gw._ai_hits.clear()
    return calls


@pytest.fixture
def no_db_session():
    from app.database import get_db_session

    async def none():
        yield None
    app.dependency_overrides[get_db_session] = none
    yield
    app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_suggest_categories_end_to_end(as_user, ai_stub, no_db_session):
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/suggest-categories", json={"existingCategories": ["א"]})
    assert r.status_code == 200 and r.json() == {"suggestions": [{"name": "בטיחות", "description": "d"}]}


@pytest.mark.asyncio
async def test_summary_streams_sse_and_reads_only_own_prefs(as_user, ai_stub, no_db_session):
    as_user(_ident(groups=("r1",)))
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/summarize-lessons",
                         json={"role": "project_manager", "userId": "u1", "lessons": [], "projects": []})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in r.text
    # u1 is someone else: the caller's own preferences are used instead.
    assert {"prefs_for": "u2"} in ai_stub


@pytest.mark.asyncio
async def test_ai_down_is_a_friendly_503(as_user, monkeypatch, no_db_session):
    async def down(*a, **k):
        raise ai.AIUnavailable("quota")
    monkeypatch.setattr(ai, "ask", down)
    gw._ai_hits.clear()
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/review-lesson", json={"type": "pre_submit", "lesson": {}})
    assert r.status_code == 503 and "עמוס" in r.json()["error"]


@pytest.mark.asyncio
async def test_ai_is_rate_limited_per_user(as_user, ai_stub, no_db_session):
    as_user(_ident())
    async with _client() as c:
        codes = [(await c.post("/lessons/api/functions/v1/suggest-categories", json={})).status_code
                 for _ in range(gw.AI_PER_MINUTE + 1)]
    assert codes[:-1] == [200] * gw.AI_PER_MINUTE and codes[-1] == 429


@pytest.mark.asyncio
async def test_feedback_cannot_be_filed_as_someone_else(as_user, ai_stub, no_db_session):
    as_user(_ident())
    async with _client() as c:
        r = await c.post("/lessons/api/functions/v1/classify-ai-feedback",
                         json={"feedback_text": "x", "user_id": "u1", "user_name": "ירון"})
    assert r.status_code == 403
