"""Tests for the ◈ ניתוח AI panels: SQL-side rollups and the short-TTL cache.

The panels used to load every matching Decision row into Python just to count
it, and cached nothing — so each press cost a growing scan plus a fresh Groq
call. These cover both halves of that fix.
"""

import json

import pytest
from sqlalchemy import select, func, or_, exists

from app.models import (
    Decision, DecisionDistribution, DecisionTypeEnum, DecisionStatusEnum,
)
from app.routers import dashboard as dash
from app.services import analysis_cache


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class _RecordingSession:
    """Fake AsyncSession: records the statements it is given, returns canned rows."""

    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.results.pop(0))

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self.results.pop(0)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def scalars(self):
        return self

    # scalars().all()
    def __iter__(self):
        return iter(self._rows)


# ── The rollup counts in SQL, not in Python ────────────────────────────────

async def test_decision_rollup_aggregates_in_sql():
    session = _RecordingSession([
        [(DecisionTypeEnum.CRITICAL, 3), (DecisionTypeEnum.NORMAL, 5)],
        [(DecisionStatusEnum.PENDING, 2), (DecisionStatusEnum.APPROVED, 6)],
        [(4.25, 4)],
    ])
    rollup = await dash._decision_rollup(session, Decision.submitter_id == 7)

    assert rollup["total"] == 8
    assert rollup["type_counts"] == {"critical": 3, "normal": 5}
    assert rollup["status_counts"] == {"pending": 2, "approved": 6}
    assert rollup["avg_feedback"] == 4.2      # Decimal → rounded float
    assert rollup["n_feedback"] == 4

    # Three grouped/aggregate queries — no "SELECT decisions.*" anywhere.
    assert len(session.statements) == 3
    for stmt in session.statements:
        sql = _sql(stmt).lower()
        assert "count(" in sql or "avg(" in sql
        assert "decisions.embedding" not in sql, "must not hydrate whole rows"


async def test_decision_rollup_handles_an_empty_set():
    session = _RecordingSession([[], [], [(None, 0)]])
    rollup = await dash._decision_rollup(session, Decision.submitter_id == 7)
    assert rollup == {
        "total": 0, "type_counts": {}, "status_counts": {},
        "avg_feedback": None, "n_feedback": 0,
    }


async def test_recent_decision_lines_uses_a_limit():
    session = _RecordingSession([
        [(DecisionTypeEnum.CRITICAL, DecisionStatusEnum.APPROVED, "החלפת שנאי")],
    ])
    lines = await dash._recent_decision_lines(
        session, Decision.submitter_id == 7, 6, with_status=True
    )
    assert lines == ["[CRITICAL][approved] החלפת שנאי"]
    sql = _sql(session.statements[0]).lower()
    assert "limit 6" in sql
    assert "order by decisions.created_at desc" in sql


async def test_recent_decision_lines_without_status_tag():
    session = _RecordingSession([
        [(DecisionTypeEnum.NORMAL, DecisionStatusEnum.PENDING, "בדיקת ממסר")],
    ])
    lines = await dash._recent_decision_lines(
        session, Decision.submitter_id == 7, 5, with_status=False
    )
    assert lines == ["[NORMAL] בדיקת ממסר"]


async def test_recent_feedback_notes_are_newest_first_and_capped():
    session = _RecordingSession([["הערה חדשה", "הערה ישנה"]])
    notes = await dash._recent_feedback_notes(session, Decision.submitter_id == 7, 4)
    assert notes == ["הערה חדשה", "הערה ישנה"]
    sql = _sql(session.statements[0]).lower()
    assert "limit 4" in sql
    assert "order by decisions.created_at desc" in sql


def test_involved_filter_still_compiles_as_a_correlated_exists():
    """The dashboard panel counts decisions the user submitted OR received."""
    uid = 7
    dist_sent = exists(
        select(DecisionDistribution.id).where(
            DecisionDistribution.decision_id == Decision.id,
            DecisionDistribution.user_id == uid,
        )
    )
    stmt = (select(func.count()).select_from(Decision)
            .where(or_(Decision.submitter_id == uid, dist_sent)))
    sql = _sql(stmt).lower()
    assert "exists" in sql and "decision_distributions" in sql
    assert "count(" in sql


# ── The model's JSON, fence and all ────────────────────────────────────────

def test_parse_ai_json_plain():
    assert dash._parse_ai_json('{"sections": []}') == {"sections": []}


def test_parse_ai_json_tolerates_a_markdown_fence():
    raw = '```json\n{"sections": [{"title": "א"}]}\n```'
    assert dash._parse_ai_json(raw)["sections"][0]["title"] == "א"


def test_parse_ai_json_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        dash._parse_ai_json("not json at all")


# ── Repeat presses are served from cache ───────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_panel_cache():
    analysis_cache.invalidate()
    yield
    analysis_cache.invalidate()


async def test_panel_is_built_once_and_then_served_from_cache():
    builds = []

    async def _build():
        builds.append(1)
        return {"sections": [{"title": "א"}]}

    first = await dash._cached_ai_panel("dashboard:7", False, _build)
    second = await dash._cached_ai_panel("dashboard:7", False, _build)

    assert len(builds) == 1, "a second press must not re-call the LLM"
    assert json.loads(first.body)["cached"] is False
    assert json.loads(second.body)["cached"] is True
    assert json.loads(second.body)["sections"] == [{"title": "א"}]
    assert json.loads(second.body)["generated_at"]


async def test_refresh_rebuilds_the_panel():
    builds = []

    async def _build():
        builds.append(1)
        return {"sections": []}

    await dash._cached_ai_panel("dashboard:7", False, _build)
    await dash._cached_ai_panel("dashboard:7", True, _build)
    assert len(builds) == 2


async def test_panels_are_keyed_separately():
    builds = []

    async def _build():
        builds.append(1)
        return {"sections": []}

    await dash._cached_ai_panel("user:1", False, _build)
    await dash._cached_ai_panel("user:2", False, _build)
    assert len(builds) == 2
    assert set(analysis_cache.stats()["keys"]) == {"user:1", "user:2"}


async def test_a_failed_build_is_not_cached():
    """A transient Groq outage must not pin an error for the whole TTL."""
    async def _boom():
        raise RuntimeError("groq down")

    with pytest.raises(RuntimeError):
        await dash._cached_ai_panel("decision:5", False, _boom)
    assert analysis_cache.get("decision:5") is None


# ── The cache itself ───────────────────────────────────────────────────────

def test_cache_entries_expire(monkeypatch):
    import time as _time
    now = [1000.0]
    monkeypatch.setattr(_time, "time", lambda: now[0])
    monkeypatch.setattr(analysis_cache, "time", _time)

    analysis_cache.put("k", {"a": 1})
    assert analysis_cache.get("k") == {"a": 1}

    now[0] += analysis_cache.TTL_SECONDS + 1
    assert analysis_cache.get("k") is None


def test_cache_is_bounded():
    for i in range(analysis_cache.MAX_ENTRIES + 25):
        analysis_cache.put(f"k{i}", {"i": i})
    assert len(analysis_cache.stats()["keys"]) <= analysis_cache.MAX_ENTRIES
    # The newest entry always survives the eviction pass.
    assert analysis_cache.get(f"k{analysis_cache.MAX_ENTRIES + 24}") is not None
