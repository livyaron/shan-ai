"""The live main dashboard: /dashboard/live serves the page's own numbers, the
7-day window is zero-filled, and the template renders with and without data.

No DB — a fake session answers every query with an empty result.
"""
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db_session
from app.main import app
from app.models import User
from app.routers.dashboard import DASHBOARD_DAYS, templates
from app.routers.login import get_current_user

TEMPLATE = Path("app/templates/dashboard.html").read_text(encoding="utf-8")
BG_JS = Path("static/js/substation_bg.js")


class _EmptyResult:
    def __iter__(self):
        return iter([])

    def all(self):
        return []

    def scalar(self):
        return 0


class _EmptySession:
    async def execute(self, *_a, **_k):
        return _EmptyResult()


@pytest.mark.asyncio
async def test_live_endpoint_returns_the_page_numbers_zero_filled():
    async def fake_user():
        return User(id=9201, username="live_u", password_hash="", is_admin=False)

    async def fake_session():
        yield _EmptySession()

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db_session] = fake_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/dashboard/live")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    d = r.json()
    # a quiet week is still seven points on the line, ending today
    assert d["daily_data"] == [0] * DASHBOARD_DAYS
    assert len(d["daily_labels"]) == DASHBOARD_DAYS
    assert d["daily_labels"][-1] == datetime.utcnow().date().strftime("%d/%m")
    assert d["daily_labels"][0] == (datetime.utcnow().date() - timedelta(days=DASHBOARD_DAYS - 1)).strftime("%d/%m")
    for key in ("total_decisions", "pending_approvals", "week_total", "type_counts",
                "status_counts", "raci_counts", "written_count", "recent_decisions", "server_time"):
        assert key in d, key
    assert "type_labels_he" not in d        # page-only presentation, not data


def _render(**over):
    ctx = dict(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/")),
        current_user=SimpleNamespace(id=1, username="u", is_admin=False, role=None, full_name="u"),
        total_decisions=0, total_users=0, avg_feedback=None, type_counts={}, status_counts={},
        daily_labels=["01/01"] * DASHBOARD_DAYS, daily_data=[0] * DASHBOARD_DAYS, week_total=0,
        recent_decisions=[], role_counts={}, pending_approvals=0, raci_counts={}, written_count=0,
        type_labels_he={"critical": "קריטי"}, status_labels_he={"pending": "ממתין"},
    )
    ctx.update(over)
    return templates.env.get_template("dashboard.html").render(ctx)


def test_template_renders_empty_and_full():
    empty = _render()
    assert 'id="substation-bg"' in empty
    assert "אין החלטות עדיין" in empty

    full = _render(
        total_decisions=10, type_counts={"critical": 3}, status_counts={"pending": 10},
        pending_approvals=2, week_total=4,
        recent_decisions=[{"id": 7, "type": "critical", "status": "pending", "summary": "<b>x</b>",
                           "username": "a", "created_at": "01/01/2026 10:00", "feedback_score": None}],
    )
    assert 'id="sb-pending-bay"' in full and "sb-bay alarm" in full      # waiting approvals trip the bay
    assert 'data-live="critical">3<' in full                              # the server value is in the HTML at rest
    assert "width:30%" in full                                             # 3 of 10 critical
    assert "<b>x</b>" not in full and "&lt;b&gt;x&lt;/b&gt;" in full      # summaries stay escaped


def test_every_live_key_is_produced_by_the_poller():
    """A data-live key the JS never fills would freeze silently on the first poll."""
    keys = set(re.findall(r'data-live="([a-zA-Z_]+)"', TEMPLATE))
    body = re.search(r"function sbLiveValues\(d\) \{(.*?)\n\}", TEMPLATE, re.DOTALL).group(1)
    produced = set(re.findall(r"\b([a-zA-Z_]+):", body))
    assert keys and keys <= produced, keys - produced


def test_background_is_served_and_decoration_only():
    assert BG_JS.exists()
    assert "/static/js/substation_bg.js" in TEMPLATE
    js = BG_JS.read_text(encoding="utf-8")
    # it is driven by the page, never the other way round: no fetches, no text on screen
    assert "fetch(" not in js and "fillText" not in js
    assert "prefers-reduced-motion" in js
