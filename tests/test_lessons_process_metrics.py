"""Lessons process metrics — pure `compute` over synthetic rows. No DB."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import lessons_process_metrics as m

NOW = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)
D = lambda days: NOW - timedelta(days=days)   # noqa: E731


def ev(lesson_id, days_ago, status_after, event_type="status_change", id_=0):
    return {"id": id_, "lesson_id": lesson_id, "event_type": event_type, "status_after": status_after, "created_at": D(days_ago)}


def lesson(id_, workflow_status, created_by="u1", project_id=1, stage="ביצוע", category="בטיחות"):
    return {"id": id_, "workflow_status": workflow_status, "status": "new", "stage": stage, "category": category,
            "project_id": project_id, "created_by": created_by}


LESSONS = [lesson(1, "אושר והופץ"), lesson(2, "בטיפול רפרנט", created_by="u2", project_id=2), lesson(3, "חדש", project_id=None)]
EVENTS = [
    ev(1, 40, "חדש", "lesson_created"), ev(1, 30, "בטיפול רפרנט"), ev(1, 24, "ממתין לאישור מנהל מערכת"),
    ev(1, 20, "אושר והופץ", "distributed"),
    ev(2, 10, "חדש", "lesson_created"), ev(2, 8, "בטיפול רפרנט"), ev(2, 8, "בטיפול רפרנט", "referent_response"),
    ev(3, 3, "חדש", "lesson_created"),
]
IMPLS = [
    {"lesson_id": 1, "project_id": 1, "is_relevant": True, "is_implemented": True, "responded_at": D(15)},
    {"lesson_id": 1, "project_id": 2, "is_relevant": False, "is_implemented": None, "responded_at": D(18)},
    {"lesson_id": 1, "project_id": 3, "is_relevant": None, "is_implemented": None, "responded_at": None},
]
PROJECTS = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]


def result():
    return m.compute(LESSONS, EVENTS, IMPLS, PROJECTS, NOW)


def test_time_waiting_at_each_step():
    w = result()["wait_days_per_step"]
    assert w["חדש"] == {"n": 2, "median": 6.0, "p90": 10.0, "max": 10.0}     # 10 days and 2 days
    assert w["בטיפול רפרנט"]["median"] == 6.0
    assert w["ממתין לאישור מנהל מערכת"]["median"] == 4.0
    assert "אושר והופץ" not in w                                             # a final status is not a wait


def test_lessons_sitting_at_a_step_right_now():
    o = result()["open_now_days_at_step"]
    assert o["בטיפול רפרנט"] == {"n": 1, "median": 8.0, "p90": 8.0, "max": 8.0}   # a repeated status does not restart the clock
    assert o["חדש"]["max"] == 3.0


def test_created_to_distributed():
    assert result()["created_to_distributed_days"] == {"n": 1, "median": 20.0, "p90": 20.0, "max": 20.0}


def test_what_happens_after_distribution():
    a = result()["after_distribution"]
    assert (a["sent"], a["answered"], a["answer_rate_pct"]) == (3, 2, 66.7)
    assert (a["relevant_implemented"], a["relevant_not_implemented"], a["not_relevant"]) == (1, 0, 1)
    assert a["days_to_answer"]["median"] == 3.5                               # 5 days and 2 days after distribution
    assert a["unanswered_over_30_days"] == 0
    assert (a["projects_targeted"], a["projects_that_never_answered"]) == (3, 1)
    assert (a["lessons_sent"], a["lessons_nobody_answered"]) == (1, 0)


def test_an_unanswered_lesson_goes_stale_after_30_days():
    old = [dict(e, created_at=e["created_at"] - timedelta(days=20)) for e in EVENTS]
    assert m.compute(LESSONS, old, IMPLS, PROJECTS, NOW)["after_distribution"]["unanswered_over_30_days"] == 1


def test_who_writes():
    w = result()["who_writes"]
    assert w == {"writers": 2, "top_3_writers_share_pct": 100.0, "projects_in_module": 4, "projects_with_a_lesson": 2}


def test_empty_module_does_not_divide_by_zero():
    r = m.compute([], [], [], [], NOW)
    assert r["after_distribution"]["answer_rate_pct"] is None and r["created_to_distributed_days"] == {"n": 0}


def test_the_result_carries_no_names_or_lesson_text():
    """It is written to the log — numbers and the module's own step/stage/category names only."""
    import json
    blob = json.dumps(m.compute(LESSONS, EVENTS, IMPLS, PROJECTS, NOW), ensure_ascii=False)
    assert "u1" not in blob and "u2" not in blob
    src = Path(m.__file__).read_text(encoding="utf-8")
    load = src[src.index("async def load_rows"):src.index("async def snapshot")]
    for column in ("title", "description", "recommendation", "actor_name", "target_user_name", "note"):
        assert column not in load, column
    assert "INSERT" not in src and "UPDATE " not in src and "DELETE" not in src
