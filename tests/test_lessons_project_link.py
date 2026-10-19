"""PLAN-lessons-projects.md L1 — master file ↔ lessons project rules. Pure, no DB."""
import pytest

from app.services import lessons_project_link as lpl
from app.services import stage_sectors as ss
from app.services.lessons_import import NO_LOGIN_NAMES

# ------------------------------------------------------------------ maps

def test_every_file_stage_maps_to_a_lessons_stage():
    """Every stage the file uses (stage_sectors) plus הסתיים has lessons stages."""
    for stage in [*ss.STAGE_SECTORS, *ss.CLOSED_STAGES]:
        idx = lpl.stage_indexes_for(stage)
        assert idx and all(0 <= i <= 9 for i in idx), stage


@pytest.mark.parametrize("stage,expected", [
    ("לקראת ביצוע", [4]),                 # LP-D8
    ("הסכם- אגירת אנרגיה", [5, 6]),       # LP-D8
    ("עבודה אזרחית והרכבות", [5, 6]),
    ("הרכבה חשמלית ובדיקות", [6, 7]),
    (" הסתיים ", [9]),
    ("קבלת  היתר", [3]),
])
def test_stage_map_decisions(stage, expected):
    assert lpl.stage_indexes_for(stage) == expected


@pytest.mark.parametrize("stage", [None, "", "שלב חדש שלא ראינו", float("nan")])
def test_unknown_stage_is_none_never_a_guess(stage):
    assert lpl.stage_indexes_for(stage) is None


@pytest.mark.parametrize("value,expected", [
    ("הקמה", "new_build"), ("הרחבה", "expansion"), ("שוש", "maintenance"),
    ('שו"ש', "maintenance"), ("שו״ש", "maintenance"), ("ניידות", "mobility"),
    (" הקמה ", "new_build"), ("אחר", None), (None, None),
])
def test_project_type_map(value, expected):
    assert lpl.project_type_for(value) == expected


@pytest.mark.parametrize("level,expected", [
    ("escalate", "high"), ("week", "high"), ("follow", "medium"), ("fyi", "low"), (None, "low"),
])
def test_risk_follows_the_handling_level(level, expected):
    assert lpl.risk_for(level) == expected


def test_placeholder_manager_is_never_a_login():
    assert lpl.PLACEHOLDER_PM_NAME in NO_LOGIN_NAMES


# ------------------------------------------------------------------ manager chain

PROFILES = {
    7: {"id": "u17", "role": "project_manager"},
    8: {"id": "s8", "role": "viewer"},
    9: {"id": "s9", "role": "admin"},
}
ALIASES = {"משה כהן": 7, "דנה לוי": 8, "יוסי": None, "גלי": 9, "רון": 42}


@pytest.mark.parametrize("name,expected", [
    ("משה כהן", ("u17", None)),
    (" משה כהן ", ("u17", None)),
    ("גלי", ("s9", None)),
    ("דנה לוי", (None, "not_pm")),
    ("יוסי", (None, "alias_unlinked")),        # alias row exists, nobody confirmed
    ("לא ידוע", (None, "alias_unlinked")),     # never seen
    ("רון", (None, "no_profile")),
    ("", (None, "no_name")),
    (None, (None, "no_name")),
])
def test_manager_chain_is_confirmed_links_only(name, expected):
    assert lpl.manager_profile_for(name, ALIASES, PROFILES) == expected


# ------------------------------------------------------------------ fields / plan

MASTER = {"identifier": "P-1", "name": " תחנה א ", "manager": "משה כהן", "stage": "תכנון", "project_type": "הרחבה"}


def test_fields_for_owns_the_file_fields():
    f = lpl.fields_for(MASTER, "follow", "u17")
    assert f == {"name": "תחנה א", "stage_indexes": [2], "project_type": "expansion",
                 "risk": "medium", "manager_id": "u17", "shan_active": True}


def test_fields_for_never_guesses_a_missing_stage_or_type():
    # 2026-10-19: no unfounded defaults — an unknown type no longer becomes "הקמה"
    assert lpl.fields_for({**MASTER, "project_type": "אחר"}, None, None) is None
    assert lpl.fields_for({**MASTER, "project_type": None}, None, None) is None
    assert lpl.fields_for({**MASTER, "stage": "שלב מוזר"}, None, "u17") is None
    f = lpl.fields_for({**MASTER, "name": None}, None, None)
    assert f["risk"] == "low" and f["manager_id"] == lpl.PLACEHOLDER_PM_ID and f["name"] == "P-1"


def test_an_admin_completion_fills_only_what_is_missing():
    m = {**MASTER, "stage": "שלב מוזר", "project_type": "אחר"}
    assert lpl.missing_fields(m) == ["stage", "project_type"]
    assert lpl.missing_fields(m, {"stage_indexes": [4], "project_type": None}) == ["project_type"]
    f = lpl.fields_for(m, None, None, {"stage_indexes": [4], "project_type": "mobility"})
    assert f["stage_indexes"] == [4] and f["project_type"] == "mobility"
    # a value the file gives always wins over a completion
    f = lpl.fields_for(MASTER, None, None, {"stage_indexes": [9], "project_type": "mobility"})
    assert f["stage_indexes"] == [2] and f["project_type"] == "expansion"


def test_creation_plan_skips_linked_and_reports_problems():
    masters = [
        MASTER,
        {**MASTER, "identifier": "P-2", "manager": "יוסי"},
        {**MASTER, "identifier": "P-3", "manager": "יוסי", "stage": "שלב מוזר"},
        {**MASTER, "identifier": "P-4", "manager": "", "project_type": "משהו"},
        {**MASTER, "identifier": "P-5"},
    ]
    plan = lpl.creation_plan(masters, {"P-5"}, {"P-1": "escalate"}, ALIASES, PROFILES)
    assert [c["identifier"] for c in plan["create"]] == ["P-1", "P-2"]
    assert plan["create"][0]["risk"] == "high" and plan["create"][0]["manager_id"] == "u17"
    # P-3 (stage) and P-4 (type) wait for the admin, each with exactly what is missing
    assert [(h["identifier"], h["missing"]) for h in plan["held"]] == [("P-3", ["stage"]), ("P-4", ["project_type"])]
    # held projects are not created, so their managers are not counted
    assert plan["managers_unlinked"] == [{"name": "יוסי", "problem": "alias_unlinked", "projects": 1}]
    assert plan["no_manager"] == 1
    # the completion releases P-4
    plan = lpl.creation_plan(masters, {"P-5"}, {}, ALIASES, PROFILES, {"P-4": {"project_type": "maintenance"}})
    assert "P-4" in [c["identifier"] for c in plan["create"]]


# ------------------------------------------------------------------ suggestions

CANDIDATES = [
    {"identifier": "1", "name": "תחנת משנה חדרה מזרח"},
    {"identifier": "2", "name": "תחנת משנה עכו"},
    {"identifier": "3", "name": "חדרה מזרח - הרחבה"},
]


def test_suggest_ranks_by_name_and_limits():
    out = lpl.suggest("חדרה מזרח", CANDIDATES, limit=2)
    assert [o["identifier"] for o in out] == ["1", "3"]
    assert all(0 < o["score"] <= 100 for o in out)


def test_suggest_empty_name_suggests_nothing():
    assert lpl.suggest("", CANDIDATES) == []
    assert lpl.suggest("חדרה", []) == []


# ------------------------------------------------------------------ guards

def test_link_problem():
    p = {"id": 1, "shan_project_identifier": None}
    ids = {"A", "B"}
    assert lpl.link_problem(p, "A", ids, None) is None
    assert lpl.link_problem({**p, "shan_project_identifier": "A"}, "A", ids, None) is None
    assert lpl.link_problem(None, "A", ids, None)
    assert "לא נמצא" in lpl.link_problem(p, "Z", ids, None)
    assert "כבר מקושר" in lpl.link_problem(p, "A", ids, "פרויקט אחר")
    assert "נתק" in lpl.link_problem({**p, "shan_project_identifier": "B"}, "A", ids, None)


def test_merge_problem():
    a = {"id": 1, "shan_project_identifier": None}
    b = {"id": 2, "shan_project_identifier": "X"}
    assert lpl.merge_problem(a, b) is None
    assert lpl.merge_problem(b, a) is None                       # the link moves to the target
    assert lpl.merge_problem({**b, "id": 3}, b) is None          # same file project
    assert lpl.merge_problem(a, a)
    assert lpl.merge_problem(None, b)
    assert "שונים" in lpl.merge_problem({**b, "shan_project_identifier": "Y", "id": 3}, b)


def test_undecided_is_neither_linked_nor_local():
    rows = [
        {"id": 1, "shan_project_identifier": "A", "shan_link_decision": None},
        {"id": 2, "shan_project_identifier": None, "shan_link_decision": "local"},
        {"id": 3, "shan_project_identifier": None, "shan_link_decision": None},
    ]
    assert [r["id"] for r in lpl.undecided(rows)] == [3]


# ------------------------------------------------------------------ routing

def test_project_links_route_is_registered_before_the_gateway():
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/lessons/api/_project_links" in paths
    gw = next(i for i, p in enumerate(paths) if p.startswith("/lessons/api/{"))
    assert paths.index("/lessons/api/_project_links") < gw


@pytest.mark.parametrize("a,b,expected", [
    ("משה כהן", "כהן משה", True),
    ("משה כהן", "משה לוי", False),          # a shared first name is not the same person
    ("סולי", "סולי מזרחי", True),             # a one-word name
    ("", "משה", False), (None, None, False),
])
def test_same_manager(a, b, expected):
    assert lpl.same_manager(a, b) is expected


def test_identifier_in_the_name_is_first_and_recommended():
    cands = [*CANDIDATES, {"identifier": "WBM-00986", "name": "איתן - הקמה", "manager": None}]
    out = lpl.suggest("WBM-00986", cands)
    assert out[0]["identifier"] == "WBM-00986" and out[0]["id_match"] and out[0]["recommended"]
    assert out[0]["score"] == 100


def test_short_identifier_never_matches_a_number_in_the_name():
    out = lpl.suggest("תחנה 161", [{"identifier": "161", "name": "משהו אחר", "manager": None}])
    assert not out or not out[0]["id_match"]


def test_same_manager_ranks_up_and_recommends():
    """The real screen (2026-10-17): 'אוניברסיטה' scored 40 against its own project."""
    cands = [
        {"identifier": "A", "name": "אוניברסיטת תל אביב-הקמת תחמ״ש פנימית 161", "manager": "סולי מזרחי"},
        {"identifier": "B", "name": "בשור - התקנת מתקן אגירת אנרגיה", "manager": "דנה לוי"},
        {"identifier": "C", "name": "תל אור - 2 מתקני אגירה", "manager": "לופטון כהן"},
    ]
    out = lpl.suggest("אוניברסיטה", cands, manager="סולי מזרחי")
    assert out[0]["identifier"] == "A" and out[0]["manager_match"] and out[0]["recommended"]
    assert not any(o["recommended"] for o in out[1:])


def test_two_projects_of_the_same_manager_are_not_a_recommendation():
    cands = [
        {"identifier": "A", "name": "אוניברסיטת תל אביב-הקמה", "manager": "סולי מזרחי"},
        {"identifier": "B", "name": "אוניברסיטת חיפה-הרחבה", "manager": "סולי מזרחי"},
    ]
    out = lpl.suggest("אוניברסיטה", cands, manager="סולי מזרחי")
    assert not out[0]["recommended"]


def test_a_similar_name_alone_is_not_a_recommendation():
    cands = [{"identifier": "A", "name": "חדרה מזרח", "manager": None},
             {"identifier": "B", "name": "חדרה מזרח הרחבה", "manager": None}]
    out = lpl.suggest("חדרה מזרח", cands)
    assert out[0]["score"] == out[1]["score"] == 100      # token_set: both contain the name
    assert not out[0]["recommended"]                      # no margin → no recommendation


# ------------------------------------------------------------------ manager check

@pytest.mark.parametrize("fm,resolved,problem,current,kept,expected", [
    ("משה כהן", "u17", None, "u17", None, "match"),
    ("משה כהן", "u17", None, "u3", None, "differs"),
    ("משה כהן", None, "alias_unlinked", "u3", None, "unresolved"),
    ("משה כהן", None, "not_pm", "u3", None, "unresolved"),
    ("משה כהן", "u17", None, "u3", "משה כהן", "kept"),
    (" משה כהן ", None, "alias_unlinked", "u3", "משה כהן", "kept"),
    ("דנה לוי", "u9", None, "u3", "משה כהן", "differs"),   # the file named someone else → ask again
    ("", None, "no_name", "u3", None, "no_name"),
    (None, None, "no_name", "u3", "משה כהן", "no_name"),
    ("משה כהן", "u17", None, "u17", "משה כהן", "match"),   # match wins over a stale keep
])
def test_manager_check(fm, resolved, problem, current, kept, expected):
    assert lpl.manager_check(fm, resolved, problem, current, kept) == expected


# ------------------------------------------------------------------ spelling + search

@pytest.mark.parametrize("a,b", [
    ('תחמ"ש גבעת אורן', "תחמש גבעת אורן"),
    ("תחמ״ש גבעת אורן", "תחמש גבעת אורן"),
    ("קריית גליל", "קרית גליל"),
    ("ק' גליל", "ק גליל"),
])
def test_norm_name_reads_both_spellings_as_one(a, b):
    assert lpl.norm_name(a) == lpl.norm_name(b)


def test_gershayim_no_longer_split_a_word():
    out = lpl.suggest("הוספת שנאי רביעי תחמש גבעת אורן", [
        {"identifier": "A", "name": 'גבעת אורן - הוספת שנאי 4 בתחמ"ש', "manager": None},
        {"identifier": "B", "name": 'חיפה מזרח - הוספת שנאי 3 תחמ"ש', "manager": None},
    ])
    assert out[0]["identifier"] == "A"
    out = lpl.suggest("קרית גליל", [
        {"identifier": "A", "name": "קריית גליל - הקמה", "manager": None},
        {"identifier": "B", "name": "קרית גת - הקמה", "manager": None},
    ])
    assert out[0]["identifier"] == "A" and out[0]["score"] == 100


@pytest.mark.parametrize("row,expected", [
    ({"is_active": False, "linked_to": None}, "inactive"),
    ({"is_active": False, "linked_to": 4}, "inactive"),
    ({"is_active": True, "linked_to": 4}, "linked"),
    ({"is_active": True, "linked_to": None}, "free"),
])
def test_search_reason(row, expected):
    assert lpl.search_reason(row) == expected


def test_search_route_is_registered_before_the_gateway():
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    gw = next(i for i, p in enumerate(paths) if p.startswith("/lessons/api/{"))
    assert paths.index("/lessons/api/_project_links/search") < gw


# Shaped like the real master file (made-up places — the repo is public and the
# file is inside information): generic words used to bury the place name
# (2026-10-17), and a Hebrew prefix letter hid the place ("קרית הגליל").
REAL = [
    {"identifier": "WBE-001", "name": 'גבעת אורן - התקנת שנאי 524 161/24 ק"ו, 50 מגוו"א בשדה 10 בתחמ"ש גבעת אורן', "manager": None},
    {"identifier": "WBE-002", "name": 'נחל צבי-הוספת שנאי רביעי 524 50 מגוו"א ומסדר מ"ג (מכולה)', "manager": None},
    {"identifier": "WBE-003", "name": 'כפר דגן-החלפת 2 שנאים 33 ל-2 שנאים 50, הוספת שנאי רביעי 50 מגוו"א', "manager": None},
    {"identifier": "WBE-004", "name": 'רמת אלה-הוספת שנאי רביעי ( 524) 50 מגוו"א', "manager": None},
    {"identifier": "WBE-005", "name": "עין שקד – הוספת שנאי רביעי  36KV 50 MVA", "manager": None},
    {"identifier": "WBE-006", "name": "קרית הגליל-הת' 2 שנאים 161/24ק'50 מגוו\"א כ\"א וסוללות קבלים", "manager": None},
    {"identifier": "WBE-007", "name": 'גליל – התקנת שנאי שישי (525) 50 מגוו"א.', "manager": None},
    {"identifier": "WBE-008", "name": "גליל-הקמת שני שדות 1 ו-13", "manager": None},
]


@pytest.mark.parametrize("name,expected", [
    ("הוספת שנאי רביעי תחמש גבעת אורן", "WBE-001"),
    ("קרית גליל", "WBE-006"),
])
def test_the_place_name_outweighs_generic_words(name, expected):
    out = lpl.suggest(name, REAL)
    assert out[0]["identifier"] == expected
    assert out[0]["recommended"]


def test_hebrew_prefix_letter_still_matches_the_word():
    idf = lpl.idf_weights([c["name"] for c in REAL])
    assert lpl.weighted_score("גליל", "הגליל", idf) > 90


def test_a_project_linked_elsewhere_is_shown_but_never_recommended():
    """2026-10-17: the right file project was hidden because another lessons
    project (a duplicate) held it — show it, offer a merge, never recommend a link."""
    cands = [{**REAL[0], "linked_to": 7, "linked_name": "תחמש גבעת אורן"}, *REAL[1:]]
    out = lpl.suggest("הוספת שנאי רביעי תחמש גבעת אורן", cands)
    assert out[0]["identifier"] == "WBE-001" and out[0]["linked_to"] == 7
    assert not out[0]["recommended"]
    assert not any(o["recommended"] for o in out)


# ------------------------------------------------------------------ L3 sync plan

def _linked(**kw):
    base = {"id": 1, "name": "תחנה א", "stage_indexes": [2], "project_type": "expansion", "risk": "medium",
            "manager_id": "u17", "shan_project_identifier": "P-1", "shan_manager_kept": None, "shan_active": True}
    return {**base, **kw}


def _plan(linked, masters=None, levels=None):
    masters = {"P-1": MASTER} if masters is None else masters
    return lpl.sync_plan(linked, masters, levels or {"P-1": "follow"}, ALIASES, PROFILES)


def test_sync_nothing_changes_when_the_module_already_agrees():
    assert _plan([_linked()]) == []


def test_sync_applies_the_file_fields():
    out = _plan([_linked(name="שם ישן", stage_indexes=[0], risk="low")], levels={"P-1": "escalate"})
    assert out == [{"id": 1, "fields": {"name": "תחנה א", "stage_indexes": [2], "risk": "high"},
                    "manager": None, "active": True}]


def test_sync_unknown_stage_or_type_keeps_the_module_value():
    m = {**MASTER, "stage": "שלב מוזר", "project_type": "משהו"}
    out = _plan([_linked(stage_indexes=[7], project_type="maintenance")], masters={"P-1": m})
    assert out == []


def test_sync_never_moves_a_real_manager_without_the_admin():
    # 2026-10-19: the file names someone else → nothing moves; it waits in the work list
    assert _plan([_linked(manager_id="u3")]) == []
    assert _plan([_linked(manager_id="u3", shan_manager_kept="משה כהן")]) == []


def test_sync_fills_only_the_placeholder_through_the_confirmed_chain():
    out = _plan([_linked(manager_id=lpl.PLACEHOLDER_PM_ID)])
    assert out[0]["manager"] == "u17"
    # an unresolved name never fills it
    m = {**MASTER, "manager": "יוסי"}
    assert _plan([_linked(manager_id=lpl.PLACEHOLDER_PM_ID)], masters={"P-1": m}) == []


def test_sync_leaves_every_risk_when_the_engine_failed():
    # levels=None: the engine is down — "low" is never written for it
    assert lpl.sync_plan([_linked(risk="high")], {"P-1": MASTER}, None, ALIASES, PROFILES) == []
    # …while the other fields still follow the file
    out = lpl.sync_plan([_linked(name="ישן", risk="high")], {"P-1": MASTER}, None, ALIASES, PROFILES)
    assert out[0]["fields"] == {"name": "תחנה א"}


# ------------------------------------------------------------------ the work list (2026-10-19)

def _row(**kw):
    base = {"identifier": "P-1", "decision": None, "manager_id": "u17", "sync_error": None,
            "manager_check": {"status": "match"}}
    return {**base, **kw}


def test_a_linked_project_with_nothing_open_is_handled():
    assert lpl.link_issues(_row()) == []
    assert lpl.link_issues(_row(identifier=None, decision="local", manager_check=None)) == []
    # a kept manager or an empty file cell is a decision, not a problem
    assert lpl.link_issues(_row(manager_check={"status": "kept"})) == []


@pytest.mark.parametrize(("row", "issues"), [
    ({"identifier": None, "manager_check": None}, ["undecided"]),
    ({"manager_check": {"status": "differs"}}, ["manager_differs"]),
    ({"manager_check": {"status": "unresolved"}}, ["manager_unresolved"]),
    ({"manager_id": lpl.PLACEHOLDER_PM_ID, "manager_check": {"status": "no_name"}}, ["manager_placeholder"]),
    ({"sync_error": "boom"}, ["sync_error"]),
])
def test_link_issues(row, issues):
    assert lpl.link_issues(_row(**row)) == issues


def test_station_site_and_equipment_never_keep_a_project_in_the_work_list():
    # decision א (2026-10-19): the PM completes those in "השלמת נתונים"
    assert lpl.link_issues(_row(site=None, station_type_set=False, equipment_ids=[])) == []


def _summary(**kw):
    base = {"pending": 0, "held": [], "managers_open": 0, "to_create": 0, "engine_ok": True}
    return {**base, **kw}


def test_all_clear_only_when_nothing_at_all_is_open():
    assert lpl.all_clear(_summary())
    for k, v in [("pending", 1), ("held", [{}]), ("managers_open", 2), ("to_create", 3), ("engine_ok", False)]:
        assert not lpl.all_clear(_summary(**{k: v})), k


def test_sync_report_has_the_five_categories():
    rows = [_row(issues=[]), _row(issues=["manager_differs"]), _row(issues=["sync_error"]),
            _row(identifier=None, issues=["undecided"])]
    r = lpl.sync_report([{"identifier": "P-9", "name": "x"}], [{"id": 3, "error": "e"}], rows,
                        [{"identifier": "P-7", "name": "y", "missing": ["stage"]}],
                        {"updated": 2, "managers": 1, "inactive": 0}, 0, True)
    assert r["created"] == [{"identifier": "P-9", "name": "x"}]
    assert r["linked_ok"] == 1 and r["awaiting_manager"] == 1
    assert r["awaiting_data"] == [{"identifier": "P-7", "name": "y", "missing": ["stage"]}]
    assert r["failed"] == [{"id": 3, "error": "e"}]


def test_change_note_says_what_changed():
    assert "סיכון: high" in lpl.change_note({"risk": "high"}, None, True)
    assert "לא נמחק" in lpl.change_note({}, None, False)
    assert "טרם שויך" in lpl.change_note({}, "u17", True)


def test_sync_marks_a_project_gone_from_the_file_inactive_once():
    assert _plan([_linked()], masters={}) == [{"id": 1, "fields": {}, "manager": None, "active": False}]
    assert _plan([_linked(shan_active=False)], masters={}) == []


def test_sync_reactivates_a_project_back_in_the_file():
    assert _plan([_linked(shan_active=False)]) == [{"id": 1, "fields": {}, "manager": None, "active": True}]


def test_both_upload_paths_call_the_lessons_sync():
    import pathlib
    for f in ("app/routers/projects.py", "app/services/knowledge_service.py"):
        assert "sync_after_upload()" in pathlib.Path(f).read_text(encoding="utf-8"), f


# ------------------------------------------------------------------ L2: every manager name

USERS = [{"id": 7, "username": "משה כהן"}, {"id": 8, "username": "דנה לוי"}, {"id": 9, "username": "גלי ברק"},
         {"id": 11, "username": "ישראל ישראלי"}]


@pytest.mark.parametrize("name,expected", [
    ("כהן, משה", 7),          # the file writes 'last, first'
    ("לוי דנה", 8),
    ("ישראלי ישראל", 11),
    ("מישהו אחר לגמרי", None),
])
def test_manager_hint(name, expected):
    assert lpl.manager_hint(name, USERS) == expected


def test_manager_rows_problems_first_and_reserved_name_skipped():
    counts = {"משה כהן": 5, "יוסי": 3, "טרם הוקצאה": 9, "דנה לוי": 1, "  ": 2}
    rows = lpl.manager_rows(counts, ALIASES, PROFILES, USERS)
    assert [r["name"] for r in rows] == ["יוסי", "דנה לוי", "משה כהן"]
    by = {r["name"]: r for r in rows}
    assert by["משה כהן"]["problem"] is None and by["משה כהן"]["username"] == "משה כהן"
    assert by["משה כהן"]["hint_user_id"] is None           # already confirmed — no hint
    assert by["יוסי"]["problem"] == "alias_unlinked"
    assert by["דנה לוי"]["problem"] == "not_pm" and by["דנה לוי"]["user_id"] == 8
