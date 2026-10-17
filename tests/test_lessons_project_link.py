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


def test_fields_for_defaults_and_unknown_stage():
    f = lpl.fields_for({**MASTER, "project_type": "אחר", "name": None}, None, None)
    assert f["project_type"] == lpl.DEFAULT_TYPE and f["risk"] == "low"
    assert f["manager_id"] == lpl.PLACEHOLDER_PM_ID and f["name"] == "P-1"
    assert lpl.fields_for({**MASTER, "stage": "שלב מוזר"}, None, "u17") is None


def test_creation_plan_skips_linked_and_reports_problems():
    masters = [
        MASTER,
        {**MASTER, "identifier": "P-2", "manager": "יוסי"},
        {**MASTER, "identifier": "P-3", "manager": "יוסי", "stage": "שלב מוזר"},
        {**MASTER, "identifier": "P-4", "manager": "", "project_type": "משהו"},
        {**MASTER, "identifier": "P-5"},
    ]
    plan = lpl.creation_plan(masters, {"P-5"}, {"P-1": "escalate"}, ALIASES, PROFILES)
    assert [c["identifier"] for c in plan["create"]] == ["P-1", "P-2", "P-4"]
    assert plan["create"][0]["risk"] == "high" and plan["create"][0]["manager_id"] == "u17"
    assert [u["identifier"] for u in plan["unknown_stage"]] == ["P-3"]
    # P-3 is not created, so its manager is not counted; an empty name is "no manager", not a link to make
    assert plan["managers_unlinked"] == [{"name": "יוסי", "problem": "alias_unlinked", "projects": 1}]
    assert plan["no_manager"] == 2
    assert plan["type_unknown"] == ["משהו"]


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
