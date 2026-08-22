"""/health must answer "is my code actually deployed?".

Without a build marker, a 404 on a newly added route is indistinguishable from
a route that was never added — which is exactly the ambiguity that made a
deployed-vs-not question unanswerable.
"""

import datetime

import pytest

from app.main import app, health_check


@pytest.fixture(autouse=True)
def _clear_started_at():
    had = hasattr(app.state, "started_at")
    prev = getattr(app.state, "started_at", None)
    yield
    if had:
        app.state.started_at = prev
    elif hasattr(app.state, "started_at"):
        del app.state.started_at


async def test_health_reports_the_deployed_commit(monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod.settings, "RAILWAY_GIT_COMMIT_SHA", "df90e92abcdef1234567")
    monkeypatch.setattr(main_mod.settings, "RAILWAY_GIT_BRANCH", "master")
    monkeypatch.setattr(main_mod.settings, "RAILWAY_DEPLOYMENT_ID", "dep-123")
    app.state.started_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=90)

    body = await health_check()
    assert body["status"] == "healthy"
    assert body["commit"] == "df90e92abcde"       # short, not the full sha
    assert body["branch"] == "master"
    assert body["deployment_id"] == "dep-123"
    assert 85 <= body["uptime_seconds"] <= 95
    assert body["started_at"].endswith("Z")


async def test_health_degrades_gracefully_outside_railway(monkeypatch):
    """Locally none of the Railway vars exist — /health must still answer."""
    from app import main as main_mod

    monkeypatch.setattr(main_mod.settings, "RAILWAY_GIT_COMMIT_SHA", "")
    monkeypatch.setattr(main_mod.settings, "RAILWAY_GIT_BRANCH", "")
    monkeypatch.setattr(main_mod.settings, "RAILWAY_DEPLOYMENT_ID", "")
    if hasattr(app.state, "started_at"):
        del app.state.started_at

    body = await health_check()
    assert body["status"] == "healthy"
    assert body["commit"] == "unknown"
    assert body["started_at"] is None and body["uptime_seconds"] is None


def test_the_diagnostic_routes_are_registered():
    """These 404'd in production; prove they exist so the cause is the build, not the code."""
    paths = {r.path for r in app.routes}
    assert "/dashboard/war-room/cache-status" in paths
    assert "/dashboard/llm-health" in paths
    assert "/health" in paths


def test_cache_status_is_not_shadowed_by_the_mission_id_routes():
    """/{mission_id}/... must never swallow a literal war-room sub-path."""
    war_room = [r for r in app.routes if r.path.startswith("/dashboard/war-room")]
    literal = [r for r in war_room if "{" not in r.path]
    assert "/dashboard/war-room/cache-status" in {r.path for r in literal}
    # The parameterised siblings are POST-only, so a GET can never reach them.
    for r in war_room:
        if "{mission_id}" in r.path:
            assert "GET" not in (r.methods or set()), f"{r.path} would shadow a GET"
