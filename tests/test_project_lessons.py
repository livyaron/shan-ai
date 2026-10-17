"""Lessons on the project page (project_lessons) — no database."""
import asyncio
import re
from types import SimpleNamespace

from app.services import pattern_service as pt
from app.services import project_chart as pc
from app.services import project_lessons as pl
from app.services import stage_sectors as ss
from app.services.il_format import template_env
from tests.test_pattern_service import _frames


def _l(i, **kw):
    return {"id": i, "title": f"לקח {i}", "stage": "תכנון", "category": "בטיחות", "risk": "high",
            "status": "approved", "workflow_status": "אושר והופץ", "date": "2026-09-01", "event_date": None, **kw}


def test_shape_hides_drafts_and_labels_answers():
    out = pl.shape([{"id": 7, "name": "תחנה"}],
                   [_l(1), _l(2, status="draft"), _l(1)],
                   [_l(3, is_relevant=True, is_implemented=True, priority="high", reason="דומה"),
                    _l(4, is_relevant=False, is_implemented=None, priority="low", reason=""),
                    _l(5, status="draft", is_relevant=None, is_implemented=None, priority="medium", reason="")])
    assert out["ok"] and out["linked"]
    assert [l["id"] for l in out["written"]] == [1]                 # draft hidden, duplicate once
    assert [l["answer"] for l in out["applied"]] == ["יושם", "לא רלוונטי"]
    assert out["written"][0]["url"] == "/lessons/lessons/1"
    assert out["projects"][0]["url"] == "/lessons/projects/7"
    assert out["written"][0]["risk_label"] == "גבוה"


def test_answer_label():
    assert pl.answer_label(None, None) == "ממתין לתשובה"
    assert pl.answer_label(True, False) == "רלוונטי — טרם יושם"


def test_unlinked_project_is_not_linked():
    assert pl.shape([], [], []) == {"ok": True, "linked": False, "projects": [], "written": [], "applied": []}


def test_failure_never_raises():
    class Boom:
        def begin_nested(self):
            raise RuntimeError("no schema")
    assert asyncio.run(pl.for_project(Boom(), "P-1")) == {"ok": False}


def _render(lessons):
    h = pt._plain(pt.project_history(_frames(), pt._plain(pt.compute_patterns(_frames())), "P-1"))
    return template_env().get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels=ss.SECTORS, lessons=lessons)


def test_page_renders_lessons():
    html = _render(pl.shape([{"id": 7, "name": "תחנה"}], [_l(1)],
                            [_l(3, is_relevant=None, is_implemented=None, priority="high", reason="דומה")]))
    for s in ("📚 לקחים", "1 נכתבו", "1 הופצו", 'href="/lessons/lessons/1"', "ממתין לתשובה", "01/09/2026"):
        assert s in html
    assert "2026-09-01" not in html


def test_page_states_unlinked_and_down():
    assert "עוד לא קושר" in _render(pl.shape([], [], []))
    assert "לא זמינה" in _render({"ok": False})
