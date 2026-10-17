"""Optional mission/decision → project link (project_links) — no database."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from app.models import Decision, DecisionStatusEnum, DecisionTypeEnum, Mission, Project
from app.services import decisions_menu_service as dms
from app.services import missions_menu_service as oms
from app.services import project_link_telegram as plt
from app.services import project_links as pl

PROJECTS = [
    {"id": 1, "identifier": "WBE-087", "name": "איתן-החל' שנאים ומסדרים 4-שלבים", "manager": "אלטון קובי"},
    {"id": 2, "identifier": "WBE-100", "name": "הוספת שנאי רביעי חדרה", "manager": "כהן משה"},
    {"id": 3, "identifier": "WBE-101", "name": "הוספת שנאי רביעי עפולה", "manager": "לוי דנה"},
    {"id": 4, "identifier": "WBE-102", "name": "הוספת שנאי רביעי כרמיאל", "manager": "לוי דנה"},
]


def test_identifier_in_text_wins():
    out = pl.suggest("להזמין מפסק לפרויקט wbe-101 עד סוף החודש", PROJECTS)
    assert out[0]["id"] == 3 and out[0]["reason"] == "id" and out[0]["score"] == 100


def test_rare_place_word_beats_common_words():
    out = pl.suggest("חסר שנאי באתר עפולה, צריך לתאם עם הקבלן", PROJECTS)
    assert out and out[0]["id"] == 3 and out[0]["reason"] == "name"


def test_no_suggestion_for_unrelated_text():
    assert pl.suggest("לקבוע ישיבת צוות שבועית", PROJECTS) == []
    assert pl.suggest("", PROJECTS) == []


def test_manager_bonus_only_reorders():
    text = "הוספת שנאי רביעי"     # equally close to three projects
    plain = pl.suggest(text, PROJECTS)
    mine = pl.suggest(text, PROJECTS, manager_names={"לוי דנה"})
    assert {r["id"] for r in plain} == {r["id"] for r in mine}
    assert mine[0]["id"] in (3, 4)
    assert all(r["score"] <= 100 for r in mine)       # shown score is the name score


def test_search_by_id_and_name():
    assert [p["id"] for p in pl.search("WBE-087", PROJECTS)] == [1]
    assert [p["id"] for p in pl.search("wbe087", PROJECTS)] == [1]
    assert {p["id"] for p in pl.search("שנאי רביעי", PROJECTS)} == {2, 3, 4}
    assert pl.search("", PROJECTS) == []


def test_label():
    assert pl.label(PROJECTS[0]) == "WBE-087 — איתן-החל' שנאים ומסדרים 4-שלבים"
    assert pl.label(None) == ""
    assert pl.label(Project(project_identifier="X-1", name=None)) == "X-1"


def test_telegram_callbacks_fit_and_clear_only_when_linked():
    kb = plt.picker_keyboard("d", 123456, PROJECTS, linked=False, back=plt.back_cb("d", 123456, "recent", 12))
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert all(len(c.encode()) <= 64 for c in cbs)
    assert "pl:s:d:123456:1" in cbs and not any(c.endswith(":0") and c.startswith("pl:s") for c in cbs)
    assert cbs[-1] == "dm:d:123456:recent:12"
    kb = plt.picker_keyboard("m", 9, [], linked=True, back=plt.back_cb("m", 9))
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "pl:s:m:9:0" in cbs and cbs[-1] == "om:d:9:my:0"


def _mission(**kw):
    m = Mission(id=5, title="משימה", status="open", is_urgent=False, is_important=False, owner_id=1, **kw)
    m.updates, m.due_changes = [], []
    return m


def test_mission_card_shows_project_and_button():
    m = _mission()
    m.project = Project(project_identifier="WBE-087", name="איתן")
    assert "🏗 <b>פרויקט:</b> WBE-087 — איתן" in oms.build_mission_card(m)
    cbs = [b.callback_data for row in oms.build_mission_card_keyboard(m, "qdo", 2).inline_keyboard for b in row]
    assert "pl:o:m:5:qdo:2" in cbs


def test_decision_card_link_button_needs_right():
    d = Decision(id=7, type=DecisionTypeEnum.NORMAL, status=DecisionStatusEnum.PENDING, summary="s", is_relevant=True)
    rows = lambda can: [b.callback_data for r in dms.build_decision_card_keyboard(d, "my", 0, can_link=can).inline_keyboard for b in r]
    assert "pl:o:d:7:my:0" in rows(True) and not any(c.startswith("pl:") for c in rows(False))
    d.project = Project(project_identifier="WBE-087", name="איתן")
    assert "WBE-087" in dms.format_decision_card(d)


def test_for_project_never_raises():
    class Boom:
        def begin_nested(self):
            raise RuntimeError("db down")
    assert asyncio.run(pl.for_project(Boom(), "P-1", SimpleNamespace(is_admin=True, id=1))) == {"ok": False}


def test_picker_uses_text_content_only():
    src = Path("app/templates/_project_picker.html").read_text(encoding="utf-8")
    assert "innerHTML" not in src


def test_every_board_layout_offers_the_link():
    for name in ("war_room.html", "war_room_focus.html", "war_room_table.html"):
        src = Path("app/templates", name).read_text(encoding="utf-8")
        assert '_war_room_project_field.html' in src and 'data-proj="{{ m.id }}"' in src, name


def test_project_page_renders_linked_items():
    from app.services import pattern_service as pt
    from app.services import project_chart as pc
    from app.services import stage_sectors as ss
    from app.services.il_format import template_env
    from tests.test_pattern_service import _frames
    h = pt._plain(pt.project_history(_frames(), pt._plain(pt.compute_patterns(_frames())), "P-1"))
    links = {"ok": True,
             "missions": [{"id": 1, "title": "להזמין שנאי", "owner": "יוסי", "active": True, "status": "פתוחה",
                           "due": "2026-11-01", "overdue": False}],
             "decisions": [{"id": 9, "summary": "לאשר קבלן", "submitter": "דנה", "type": "✅", "status": "⏳ ממתין",
                            "date": "2026-10-01", "relevant": True}]}
    html = template_env().get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels=ss.SECTORS, links=links)
    for s in ("משימות והחלטות מקושרות", "1 משימות", "1 החלטות", "להזמין שנאי", "#9", "01/11/2026"):
        assert s in html
    assert not re.search(r">[^<]*\d{4}-\d{2}-\d{2}[^<]*<", html.split('id="delay-data"')[0])
