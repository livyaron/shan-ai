"""חסמים — episodes and the page's analysis over a small synthetic history. No DB."""
from datetime import date
from pathlib import Path

import pandas as pd

from app.services import blockers as bl
from app.services import insight_access as ia
from app.services import insight_triage as it
from app.services import pattern_service as ps
from app.services import stage_sectors as ss

D1, D2, D3, D4 = date(2026, 3, 25), date(2026, 5, 20), date(2026, 8, 12), date(2026, 11, 11)


def _snap(pid, d, stage, risks, handle, fc=date(2027, 6, 30)):
    return {"project_id": pid, "snapshot_date": d, "stage": stage, "fc": fc, "dev": date(2027, 6, 30),
            "risks": risks, "to_handle": handle, "finish_date_text": None}


def _frames() -> ps.Frames:
    snaps = [
        # P1: blocked through the whole history (its real age is unknown), the target slipped 6 months meanwhile
        _snap(1, D1, "תכנון", "ממתינים להיתר בנייה", "חסם לטיפול מנהל מגזר תכנון", date(2027, 1, 1)),
        _snap(1, D2, "תכנון", "ממתינים להיתר בנייה", "חסם לטיפול מנהל מגזר תכנון", date(2027, 1, 1)),
        _snap(1, D3, "תכנון", "ממתינים להיתר בנייה — הוגש ערר", "חסם לטיפול מנהל מגזר תכנון", date(2027, 7, 2)),
        _snap(1, D4, "תכנון", "ממתינים להיתר בנייה — הוגש ערר", 'חסם לטיפול סמנכ"ל', date(2027, 7, 2)),
        # P2: a blocker that opened at D2 and was closed at D4 (past)
        _snap(2, D1, "עבודה אזרחית", None, None), _snap(2, D2, "עבודה אזרחית", "קבלן לא עלה לשטח", None),
        _snap(2, D3, "עבודה אזרחית", "קבלן לא עלה לשטח", None), _snap(2, D4, "עבודה אזרחית", None, None),
        # P3: opened at the latest report, no handler
        _snap(3, D1, "בדיקות", None, None), _snap(3, D2, "בדיקות", None, None),
        _snap(3, D3, "בדיקות", None, None), _snap(3, D4, "בדיקות", "מחסור בבודקים", None),
        # P4: finished — its risk cell is not a blocker
        *[_snap(4, d, "הסתיים", "נשאר מלל ישן", None) for d in (D1, D2, D3, D4)],
    ]
    projects = [{"project_id": i, "identifier": f"P-{i}", "name": f"תחנה {i}", "manager": f"מנהל {i}",
                 "project_type": "הרחבה", "is_active": True} for i in (1, 2, 3, 4)]
    return ps.Frames(pd.DataFrame(snaps), pd.DataFrame(projects),
                     pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))


FRAMES = _frames()
P = ps._plain(ps.compute_patterns(FRAMES))
DATA = bl.analyse(FRAMES, P)


def test_an_episode_is_a_run_of_filled_risk_cells():
    eps, rdates = bl.episodes(FRAMES)
    by = {e["id"]: e for e in eps}
    assert set(by) == {"P-1", "P-2", "P-3"} and rdates == [D1, D2, D3, D4]          # the finished project has none
    assert (by["P-1"]["open"], by["P-1"]["opened"], by["P-1"]["lower_bound"], by["P-1"]["reports"]) == (True, D1, True, 4)
    assert (by["P-2"]["open"], by["P-2"]["opened"], by["P-2"]["closed"]) == (False, D2, D4)
    assert by["P-2"]["weeks"] == round((D4 - D2).days / 7)
    assert (by["P-3"]["open"], by["P-3"]["weeks"], by["P-3"]["lower_bound"]) == (True, 0, False)


def test_owner_text_change_slip_and_topics():
    e = next(x for x in bl.episodes(FRAMES)[0] if x["id"] == "P-1")
    assert e["owner"] == 'סמנכ"ל' and e["owners"] == ["מנהל מגזר תכנון", 'סמנכ"ל'] and e["top"] is True
    assert e["changes"] == 1                                                          # the text was rewritten once
    assert e["slip_months"] == 6.0 and "רישוי/היתרים/סטטוטוריקה" in e["topics"]
    assert bl.owner_of(None) == bl.NO_OWNER and bl.owner_of("אחר") == "אחר"


def test_now_history_and_flow():
    assert DATA["now"]["open"] == 2 and DATA["now"]["projects"] == 2
    assert (DATA["now"]["unowned"], DATA["now"]["top"], DATA["now"]["chronic"]) == (1, 1, 1)
    assert DATA["history"] == {"closed": 1, "median_weeks_closed": 25.0, "ever": 3}
    flow = {f["report"]: f for f in DATA["flow"]}
    assert (flow["2026-03-25"]["opened"], flow["2026-03-25"]["open"]) == (0, 1)       # already there when history starts
    assert (flow["2026-05-20"]["opened"], flow["2026-05-20"]["open"]) == (1, 2)
    assert (flow["2026-11-11"]["opened"], flow["2026-11-11"]["closed"], flow["2026-11-11"]["open"]) == (1, 1, 2)


def test_every_number_is_the_number_of_rows_behind_it():
    assert sum(DATA["ages"].values()) == len(DATA["open"]) == DATA["now"]["open"]
    for label, n in DATA["ages"].items():
        assert n == sum(1 for e in DATA["open"] if e["age"] == label)
    for r in DATA["by_owner"]:
        assert r["open"] == sum(1 for e in DATA["open"] if e["owner"] == r["owner"])
    for r in DATA["by_topic"]:
        assert r["open"] == sum(1 for e in DATA["open"]
                                if r["topic"] in e["topics"] or (r["topic"] == "ללא נושא מזוהה" and not e["topics"]))
    assert sum(DATA["now"]["levels"].values()) == DATA["now"]["open"]
    assert DATA["order"]["ages"] == list(DATA["ages"])


def test_the_order_is_a_rule_and_the_escalated_one_leads_its_level():
    ranks = [e["rank"] for e in DATA["open"]]
    assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))
    order = {lv: i for i, lv in enumerate(it.ORDER)}
    levels = [order[e["level"]] for e in DATA["open"]]
    assert levels == sorted(levels)                                                   # never a lower level above a higher one


def test_the_owner_filter_and_the_owner_chips():
    assert {r["owner"] for r in DATA["owners"]} == {'סמנכ"ל', bl.NO_OWNER}
    only = bl.analyse(FRAMES, P, owner=bl.NO_OWNER)
    assert [e["id"] for e in only["open"]] == ["P-3"] and only["now"]["open"] == 1
    assert {r["owner"] for r in only["owners"]} == {'סמנכ"ל', bl.NO_OWNER}             # the chips still offer everything


def test_a_viewer_sees_their_projects_and_the_blockers_assigned_to_their_role():
    planning = ia.Scope(sector=ss.PLANNING)
    sees = lambda sectors, manager, owner: ia.sees_blocker(planning, sectors, manager, owner)   # noqa: E731
    seen = {e["id"] for e in bl.analyse(FRAMES, P, sees)["open"]}
    assert "P-1" in seen and "P-3" not in seen                                        # P-3 is in testing, nobody's handler
    execution = ia.Scope(sector=ss.EXECUTION)
    assert ia.owns_blocker(execution, "מנהל מגזר ביצוע") and not ia.owns_blocker(execution, "מנהל מגזר תכנון")
    assert not ia.owns_blocker(ia.Scope(managers=frozenset({"מנהל 1"})), "מנהל מגזר תכנון")   # a PM is not a role
    pm = ia.Scope(managers=frozenset({"מנהל 3"}))
    mine = bl.analyse(FRAMES, P, lambda s, m, o: ia.sees_blocker(pm, s, m, o))
    assert [e["id"] for e in mine["open"]] == ["P-3"] and mine["history"]["ever"] == 1


def test_the_ai_reads_only_the_viewers_top_blockers_and_gets_no_person():
    ctx = bl.ai_context(DATA)
    assert "[P-1]" in ctx and "[P-3]" in ctx and "[P-2]" not in ctx                    # a closed blocker is not sent
    assert "מנהל 1" not in ctx and "מנהל 3" not in ctx                                 # project managers' names stay home
    assert '"' not in ctx.replace('סמנכ"ל', "")                                        # straight quotes break the JSON reply
    assert bl.ai_kind("all", None) != bl.ai_kind("all", bl.NO_OWNER) and len(bl.ai_kind("planning", "x")) < 60


def test_empty_history():
    empty = ps.Frames(pd.DataFrame(columns=["project_id", "snapshot_date", "stage", "fc", "dev", "risks", "to_handle",
                                            "finish_date_text"]),
                      pd.DataFrame(columns=["project_id", "identifier", "name", "manager", "project_type", "is_active"]),
                      pd.DataFrame(columns=["project_id", "week_date", "text", "text_hash"]))
    assert bl.analyse(empty, {"projects": []}) == {"reports": 0}


def test_the_page_is_gated_scoped_and_reachable_from_the_dashboard():
    root = Path(bl.__file__).resolve().parents[2]
    route = (root / "app/routers/projects.py").read_text(encoding="utf-8")
    body = route[route.index("async def _blockers_data"):route.index('@router.post("/blockers/ai")')]
    assert "scope.allowed" in body and "insight_access.sees_blocker" in body and "frames_and_patterns" in body
    page = (root / "app/templates/project_blockers.html").read_text(encoding="utf-8")
    assert "innerHTML" not in page and "{{ data|tojson }}" in page and "prefers-reduced-motion" in page
    for scene in ("open", "map", "time", "owners", "old", "queue", "sum", "ai"):
        assert f'data-scene="{scene}"' in page and f"BUILD.{scene} = " in page
    dash = (root / "app/templates/dashboard.html").read_text(encoding="utf-8")
    assert 'href="/dashboard/projects/blockers"' in dash and 'href="/dashboard/projects/delays"' in dash


def test_the_ai_reply_is_validated_and_a_failed_call_stores_nothing(monkeypatch):
    import asyncio
    from app.services import insight_ai
    stored = []

    async def fake_ask(system, ctx, kind):
        return {"headline": "יש 999 חסמים", "findings": [{"title": "x", "projects": ["P-1", "P-77"]}]}, "test"

    async def fake_store(session, kind, as_of, payload, provider):
        stored.append((kind, payload))

    monkeypatch.setattr(insight_ai, "_ask", fake_ask)
    monkeypatch.setattr(insight_ai, "_store", fake_store)
    out = asyncio.run(bl.recommend(None, DATA, "blockers:all:x"))
    assert out["payload"]["headline"] == "יש [?] חסמים"                              # a number the data never had
    assert out["payload"]["findings"][0]["projects"] == ["P-1"]                       # a project outside the list is dropped
    assert stored and stored[0][0] == "blockers:all:x"

    async def boom(system, ctx, kind):
        raise RuntimeError("quota")

    monkeypatch.setattr(insight_ai, "_ask", boom)
    stored.clear()
    assert asyncio.run(bl.recommend(None, DATA, "k")) is None and not stored
