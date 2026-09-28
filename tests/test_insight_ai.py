"""PLAN.md P5 — handling levels (rules) and the AI reading's guards. No DB, no model."""
import re
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import insight_access as ia
from app.services import insight_ai as ai
from app.services import insight_triage as it
from app.services import pattern_service as pt
from app.services import stage_sectors as ss
from tests.test_pattern_service import D1, D2, D3, _frames, page_ctx

P = pt._plain(pt.compute_patterns(_frames(extra_projects=4)))
AS_OF = P["as_of"]


def _row(**kw):
    base = {"identifier": "X", "name": "n", "manager": "m", "stage": "תכנון", "sectors": ["planning"],
            "fc": None, "fc_text": None, "dev": None, "forecast_moved_months": None, "forecast_moved": None,
            "baseline_moved": None, "weeks_in_stage": 0, "weeks_lower_bound": False, "stuck": False,
            "late": False, "slip_months": None, "undated": False, "stale": False, "frozen": False,
            "escalation": None, "escalation_top": False, "stage_gap": 0}
    return {**base, **kw}


# ── Handling levels: the rules the user approved (2026-09-28) ───────────

@pytest.mark.parametrize("row,moves,level", [
    (_row(), 0, it.FYI),
    (_row(frozen=True), 0, it.ESCALATE),
    (_row(escalation='חסם לטיפול סמנכ"ל', escalation_top=True), 0, it.ESCALATE),
    (_row(fc="2026-01-01", forecast_moved_months=6.0, forecast_moved=True), 1, it.ESCALATE),
    (_row(forecast_moved_months=2.0, forecast_moved=True), 3, it.ESCALATE),
    (_row(fc="2026-01-01"), 0, it.WEEK),
    (_row(fc="2099-01-01", forecast_moved_months=6.0, forecast_moved=True), 1, it.WEEK),
    (_row(forecast_moved=True, baseline_moved=True, forecast_moved_months=1.0), 1, it.WEEK),
    (_row(escalation="חסם לטיפול מנהל מחלקה"), 0, it.WEEK),
    (_row(weeks_in_stage=24, stuck=True), 0, it.WEEK),
    (_row(weeks_in_stage=12, stuck=True), 0, it.FOLLOW),
    (_row(late=True, slip_months=2.0), 0, it.FOLLOW),
    (_row(undated=True), 0, it.FOLLOW),
    (_row(stale=True), 0, it.FOLLOW),
    (_row(stage_gap=1), 0, it.FOLLOW),
    (_row(forecast_moved=True, forecast_moved_months=1.0), 1, it.FOLLOW),
])
def test_each_rule_sets_its_level(row, moves, level):
    t = it.triage_project(row, moves, "2026-09-16")
    assert t["level"] == level
    assert (t["reasons"] == []) is (level == it.FYI)


def test_highest_level_wins_and_every_reason_is_kept_worst_first():
    t = it.triage_project(_row(frozen=True, stale=True, fc="2026-01-01"), 0, "2026-09-16")
    assert t["level"] == it.ESCALATE
    assert [r["level"] for r in t["reasons"]] == [it.ESCALATE, it.WEEK, it.FOLLOW]


def test_engine_rows_carry_the_rule_flags_that_escalate():
    f = _frames()
    snaps = f.snaps.copy()
    last = (snaps["project_id"] == 2) & (snaps["snapshot_date"] == D3)
    snaps.loc[last, "to_handle"] = 'חסם לטיפול סמנכ"ל'
    weekly = pd.concat([f.weekly, pd.DataFrame([{"project_id": 4, "week_date": D3 + timedelta(days=1),
                                                  "text": "הפרויקט הוקפא עד להודעה חדשה", "text_hash": "z"}])])
    p = pt._plain(pt.compute_patterns(pt.Frames(snaps, f.projects, weekly)))
    rows = {x["identifier"]: x for x in p["projects"]}
    assert rows["P-2"]["escalation_top"] and rows["P-4"]["frozen"]
    tri = it.triage_all(p)
    assert tri["P-2"]["level"] == tri["P-4"]["level"] == it.ESCALATE


def test_board_holds_only_the_viewers_projects_and_the_triage_drill_matches_it():
    for scope in (ia.Scope(admin=True), ia.Scope(sector=ss.EXECUTION), ia.Scope(managers=frozenset({"מנהל ב"}))):
        v = ia.view_for(scope, P)
        on_board = {x["identifier"] for rows in v["triage"].values() for x in rows}
        assert on_board == {x["identifier"] for x in v["projects"]}
        for lv in it.ORDER:
            assert len(ia.drill_for(v, P, "triage", lv)["rows"]) == len(v["triage"][lv])
    assert ia.drill_for(ia.view_for(ia.Scope(admin=True), P), P, "triage", "nonsense") is None


# ── Who reads which analysis ─────────────────────────────────────────────

def test_ai_levels_follow_the_scope():
    full = [ai.DIVISION] + [ai.sector_kind(k) for k in ai.AREA_SECTORS]
    assert ia.ai_kinds_for(ia.Scope(admin=True)) == full
    assert ia.ai_kinds_for(ia.Scope(sector=ss.PM_DEPT)) == full
    assert ia.ai_kinds_for(ia.Scope(sector=ss.EXECUTION)) == [ai.sector_kind(ss.EXECUTION)]
    assert ia.ai_kinds_for(ia.Scope(managers=frozenset({"מנהל א"}))) == []   # PM: level 3 only


def test_sector_context_holds_only_that_sectors_projects():
    tri = it.triage_all(P)
    ctx = ai.sector_context(P, tri, {}, ai.pm_aliases(P), ss.EXECUTION)
    inside = {x["identifier"] for x in P["projects"] if ss.EXECUTION in x["sectors"]}
    for x in P["projects"]:
        assert (f"[{x['identifier']}]" in ctx) is (x["identifier"] in inside and tri[x["identifier"]]["level"] != it.FYI)


def test_pm_names_never_reach_the_model():
    tri = it.triage_all(P)
    aliases = ai.pm_aliases(P)
    frames = _frames(extra_projects=4)
    weekly = ai.weekly_by_identifier(frames)
    texts = [ai.division_context(P, tri, weekly, aliases)]
    texts += [ai.sector_context(P, tri, weekly, aliases, k) for k in ai.AREA_SECTORS]
    h = pt._plain(pt.project_history(frames, P, "P-1"))
    texts.append(ai.project_context(h, tri["P-1"], aliases))
    for t in texts:
        assert not re.search(r"מנהל [אב](?![א-ת])", t)      # "מנהל אגף" is a level label, not a name
        assert '"' not in t                               # gershayim only (JSON safety)
    assert "מנה״פ #1" in texts[0]


def test_restore_names_puts_the_longest_alias_back_first():
    aliases = {f"שם {i}": f"מנה״פ #{i}" for i in range(1, 13)}
    assert ai.restore_names({"a": ["מנה״פ #12 ו-מנה״פ #1"]}, aliases) == {"a": ["שם 12 ו-שם 1"]}


# ── The guards on the reply ──────────────────────────────────────────────

def test_validate_replaces_unknown_numbers_and_drops_foreign_ids_and_fake_quotes():
    ctx = "[P-1] נדחה 12 חודשים ב-2026-07-08, 3 פעמים"
    reply = {"headline": "נדחה 12 חודשים, 40% מהפרויקטים",
             "findings": [{"title": "t", "analysis": "3 פעמים מאז 2026-07-08", "projects": ["P-1", "P-99"]}],
             "evidence": [{"week": "2026-07-08", "quote": "ממתינים להיתר מרמ״י"},
                          {"week": "2026-07-08", "quote": "משפט שלא נכתב מעולם בדיווח"}]}
    out = ai.validate(reply, ctx, {"P-1"}, ["עדיין ממתינים להיתר מרמ״י, צפי לחודש הבא"])
    assert out["headline"] == "נדחה 12 חודשים, [?]% מהפרויקטים"
    assert out["findings"][0]["analysis"] == "3 פעמים מאז 2026-07-08"
    assert out["findings"][0]["projects"] == ["P-1"]
    assert [e["quote"] for e in out["evidence"]] == ["ממתינים להיתר מרמ״י"]
    assert out["_checks"] == {"numbers_replaced": 1, "quotes_dropped": 1, "projects_dropped": 1}


def test_validate_survives_a_malformed_reply():
    out = ai.validate({"findings": "not a list", "evidence": [None]}, "", set(), [])
    assert out["_checks"]["quotes_dropped"] == 1


def test_decorate_takes_the_worst_level_of_a_findings_projects():
    tri = {"A": {"level": it.FOLLOW}, "B": {"level": it.ESCALATE}}
    out = ai.decorate({"findings": [{"projects": ["A", "B"]}, {"projects": []}]}, tri)
    assert [f["level"] for f in out["findings"]] == [it.ESCALATE, None]


# ── Rendering ────────────────────────────────────────────────────────────

AREA = {"payload": {"headline": "כותרת", "situation": "מצב",
                    "findings": [{"title": "ממצא", "analysis": "ניתוח", "projects": ["P-2"], "action": "לזמן ישיבה",
                                  "owner": "מנהל האגף", "level": it.WEEK}],
                    "patterns": ["דפוס"], "questions": ["שאלה?"], "watch": "לבדוק",
                    "_checks": {"numbers_replaced": 2, "quotes_dropped": 0, "projects_dropped": 0}},
        "provider": "Groq", "created_at": None}


def _render(scope, ai_rows, running=False):
    env = Environment(loader=FileSystemLoader("app/templates"))
    return env.get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights"), query_params={}),
        current_user=SimpleNamespace(is_admin=scope.admin, username="u", role=None, id=1),
        p=P, view=ia.view_for(scope, P), sector_labels=ss.SECTORS, **page_ctx(ai_rows, running))


def test_insights_page_draws_the_board_and_the_analyses_the_viewer_may_read():
    html = _render(ia.Scope(admin=True), {ai.DIVISION: AREA})
    assert "ניתוח AI מעמיק" in html and "רמה 1 · תמונת אגף" in html and "לזמן ישיבה" in html
    assert "2 מספרים שלא הופיעו בנתונים" in html and "רענן ניתוח AI" in html
    assert "drill=triage" in html
    pm = _render(ia.Scope(managers=frozenset({"מנהל ב"})), {})
    assert "רמה 1" not in pm and "רמה 2" not in pm and "רענן ניתוח AI" not in pm
    assert "🔴 הסלמה" in pm                                  # the rule board is for everyone
    sector = _render(ia.Scope(sector=ss.EXECUTION), {}, running=True)
    assert "רמה 2 · " + ss.SECTORS[ss.EXECUTION] in sector and "הניתוח בהכנה" in sector
    assert "רמה 1" not in sector


def test_project_page_draws_level_three():
    from app.services import project_chart as pc
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt._plain(pt.project_history(frames, p, "P-1"))
    payload = {"headline": "כותרת", "story": "סיפור", "root_cause": "שורש",
               "evidence": [{"week": "2026-09-16", "quote": "ציטוט"}], "action": "פעולה", "owner": "בעלים",
               "questions": ["שאלה"], "watch": "מעקב", "_checks": {"numbers_replaced": 0, "quotes_dropped": 1}}
    html = Environment(loader=FileSystemLoader("app/templates")).get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels=ss.SECTORS,
        triage=it.triage_all(p)["P-1"], levels=it.LEVELS,
        ai={"payload": payload, "provider": "Groq", "created_at": None}, ai_error=False)
    for s in ("צלילת AI לפרויקט", "סיבת שורש סבירה", "שורש", "ציטוט", "1 ציטוטים לא אומתו", "נתח מחדש"):
        assert s in html
    assert "innerHTML" not in html


def test_contexts_quote_the_newest_weekly_texts():
    weekly = ai.weekly_by_identifier(_frames())
    assert weekly["P-1"][0] == ((D3).isoformat(), "שבוע 4")
    assert D1 < D2   # the fixture's report dates, newest last


def test_a_page_view_kicks_a_missing_analysis_once_per_backoff_window():
    ai._last_autostart.clear()
    assert ai.claim_autostart("2026-09-16") is True
    assert ai.claim_autostart("2026-09-16") is False      # a failing model is not re-asked per view
    assert ai.claim_autostart("2026-09-23") is True       # a new report date is a new analysis
    assert ai.claim_autostart(None) is False
