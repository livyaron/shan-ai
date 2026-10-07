"""PLAN.md P5 — handling levels (rules) and the AI reading's guards. No DB, no model."""
import re
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.il_format import template_env
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
    env = template_env()
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
    html = template_env().get_template("project_page.html").render(
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


# ── Dates read DD/MM/YYYY on every patterns page (user instruction 2026-10-07) ──

def _visible(html: str) -> str:
    """What a person reads: no <script> data, no URLs (those stay ISO on purpose)."""
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    return re.sub(r'(href|action)="[^"]*"', "", html)


def test_no_iso_date_is_shown_on_the_insights_page():
    rows = {ai.DIVISION: {**AREA, "payload": {**AREA["payload"], "headline": "הגל של 2026-07-08 הזיז הכל"}}}
    html = _visible(_render(ia.Scope(admin=True), rows))
    assert not re.search(r"\d{4}-\d{2}-\d{2}", html), re.search(r".{40}\d{4}-\d{2}-\d{2}.{20}", html)
    assert "08/07/2026" in html and "16/09/2026" in html


def test_no_iso_date_is_shown_on_the_project_page_and_the_chart_data_stays_iso():
    from app.services import project_chart as pc
    frames = _frames()
    p = pt._plain(pt.compute_patterns(frames))
    h = pt._plain(pt.project_history(frames, p, "P-1"))
    payload = {"headline": "נדחה ב-2026-07-08", "story": "", "_checks": {}}
    html = template_env().get_template("project_page.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/p/P-1")),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        h=h, project=None, delay=pc.delay_story(h), matrix=pc.risk_matrix(h),
        risk_changes=pc.risk_column_changes(h), sector_labels=ss.SECTORS,
        triage=it.triage_all(p)["P-1"], levels=it.LEVELS,
        ai={"payload": payload, "provider": "Groq", "created_at": None}, ai_error=False)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", _visible(html))
    assert '"date": "2026-' in html                 # the charts' own data is untouched


def test_il_format_filters():
    from datetime import date, datetime
    from app.services import il_format as f
    assert f.il_date("2026-10-06") == "06/10/2026" and f.il_date(date(2026, 1, 2)) == "02/01/2026"
    assert f.il_date(None) == "—" and f.il_date("מלל") == "מלל"
    assert f.il_dates("מ-2026-03-25 עד 2026-09-16") == "מ-25/03/2026 עד 16/09/2026"
    assert f.il_dates("WBM-2026-0001x") == "WBM-2026-0001x"            # not a date
    assert f.il_datetime(datetime(2026, 10, 7, 12, 14)) == "07/10/2026 15:14"   # UTC → Israel (IDT)


def test_no_iso_date_in_the_event_drill_or_the_data_health_table():
    p = {**P, "health": {"snapshot_dates": [{"date": "2026-10-06", "rows": 261, "report_date": "2026-10-06"}],
                         "snapshot_rows": 261, "weekly_rows": 10, "weekly_range": ["2026-01-14", "2026-10-06"],
                         "snapshot_indexes": []}}
    v = ia.view_for(ia.Scope(admin=True), p)
    w = P["metrics"]["update_waves"]["value"][0]
    html = template_env().get_template("project_insights.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/dashboard/projects/insights"), query_params={}),
        current_user=SimpleNamespace(is_admin=True, username="u", role=None, id=1),
        p=p, view=v, sector_labels=ss.SECTORS, drill=ia.drill_for(v, p, "wave", w["from"], "any"),
        **page_ctx())
    assert not re.search(r"\d{4}-\d{2}-\d{2}", _visible(html))
    assert "14/01/2026" in html


# ── Fitting Groq's per-minute token limit (413 on 2026-10-07) ────────────

def _big_portfolio(n=185):
    projects = [{**_row(identifier=f"WBM-{i:05d}", name="תחנת משנה עם שם ארוך במיוחד " + str(i),
                        manager=f"מנהל {i % 25}", stage="עבודה אזרחית והרכבות", sectors=["supervision", "execution"],
                        fc="2026-01-01", dev="2025-06-01", forecast_moved_months=8.0, forecast_moved=True,
                        baseline_moved=True, weeks_in_stage=30, stuck=True, late=True, slip_months=7.0,
                        stale=True, escalation="חסם לטיפול מנהל מחלקה", stage_gap=1)} for i in range(n)]
    p = {"as_of": "2026-10-06", "report_dates": ["x"] * 8, "projects": projects, "events": [],
         "sectors": [], "league": [], "metrics": {}}
    weekly = {x["identifier"]: [("2026-10-06", "א" * 900), ("2026-09-23", "ב" * 900)] for x in projects}
    return p, weekly


def test_every_request_fits_the_per_minute_token_limit():
    for kind, system in (("area", ai.AREA_SYSTEM), ("project", ai.PROJECT_SYSTEM)):
        prompt_tokens = (len(system) + ai.CONTEXT_CHARS[kind] + 200) / ai.CHARS_PER_TOKEN
        assert prompt_tokens + ai.MAX_TOKENS[kind] < ai.GROQ_TPM, kind


def test_a_large_portfolio_is_packed_into_the_budget_and_says_so():
    p, weekly = _big_portfolio()
    tri = it.triage_all(p)
    aliases = ai.pm_aliases(p)
    for ctx in (ai.division_context(p, tri, weekly, aliases),
                ai.sector_context(p, tri, weekly, aliases, ss.EXECUTION)):
        assert len(ctx) <= ai.CONTEXT_CHARS["area"] + 100
        assert "הושמטו" in ctx                         # the model is told the list is cut
        assert "[WBM-00000]" in ctx                     # the worst project survives the cut


def test_pack_drops_lowest_priority_last_lines_first_and_keeps_order():
    items = [(ai.MUST, "head"), (ai.HOT, "h1"), (ai.WEEK2, "w-old"), (ai.HOT, "h2"), (ai.FOLLOW, "f1"), (ai.FOLLOW, "f2")]
    out = ai._pack(items, budget=len("head\nh1\nh2\nf1\n"))
    assert out.splitlines()[:4] == ["head", "h1", "h2", "f1"] and "הושמטו 2" in out
    assert ai._pack(items, budget=10_000) == "\n".join(t for _, t in items)


@pytest.mark.asyncio
async def test_a_failed_call_is_retried_a_minute_later(monkeypatch):
    calls, slept = [], []

    async def flaky(system, context, kind):
        calls.append(kind)
        if len(calls) < 3:
            raise RuntimeError("413 Request too large")
        return {"headline": "ok"}, "Groq"

    async def no_sleep(s):
        slept.append(s)

    monkeypatch.setattr(ai, "_ask", flaky)
    monkeypatch.setattr(ai, "_sleep", no_sleep)
    assert await ai._ask_retrying("s", "c", "area") == ({"headline": "ok"}, "Groq")
    assert slept == [ai.TPM_WAIT_SECONDS] * 2
    calls.clear()
    monkeypatch.setattr(ai, "_ask", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        await ai._ask_retrying("s", "c", "area", attempts=1)
