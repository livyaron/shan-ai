"""חדר מבצעים wall — the hourly line (quote + one AI sentence about the board).

DB-free: the LLM leg is monkeypatched and every cache call is made with
session=None, which the day-cache treats as "no cache" rather than an error.
"""
import datetime

import pytest

from app.services import war_room_motto as motto

IL = datetime.timezone(datetime.timedelta(hours=3))


def _at(hour, day=4):
    return datetime.datetime(2026, 9, day, hour, 30, tzinfo=IL)


@pytest.fixture(autouse=True)
def _clean_cache():
    motto._cache.clear()
    motto._inflight.clear()
    yield
    motto._cache.clear()
    motto._inflight.clear()


# --------------------------------------------------------------------------
# the quotes themselves
# --------------------------------------------------------------------------

def test_quotes_are_curated_not_generated():
    """The wall must never show a quote a model invented — this list is the source."""
    assert motto.STOIC and motto.WRY
    texts = [q[0] for q in motto.QUOTES]
    assert len(texts) == len(set(texts))
    for text, author, kind in motto.QUOTES:
        assert text.strip() and author.strip()
        assert kind in ("stoic", "wry")


def test_the_same_hour_always_shows_the_same_quote():
    """Two renders inside one hour must match, or the screen looks like it glitches."""
    assert motto.pick_quote(_at(9)) == motto.pick_quote(
        datetime.datetime(2026, 9, 4, 9, 59, tzinfo=IL))


def test_the_quote_changes_between_hours():
    assert motto.pick_quote(_at(9)) != motto.pick_quote(_at(10))


def test_humor_rotates_in_on_schedule():
    """Dry humour every HUMOR_EVERY hours — the screen should not preach all day."""
    wry_hours = [h for h in range(24) if motto.pick_quote(_at(h))[2] == "wry"]
    assert wry_hours == [h for h in range(24) if h % motto.HUMOR_EVERY == 1]
    assert 4 <= len(wry_hours) <= 8


def test_a_full_day_spreads_across_the_shelf():
    """A day must not land on the same two lines over and over."""
    picked = {motto.pick_quote(_at(h))[0] for h in range(24)}
    assert len(picked) >= 8


# --------------------------------------------------------------------------
# the connecting line
# --------------------------------------------------------------------------

def test_fallback_line_puts_lateness_first():
    line = motto.fallback_line({"overdue": 4, "silent": 9, "do_now": 7})
    assert line.startswith("4 משימות באיחור")


def test_fallback_line_falls_through_to_silence_then_load():
    assert "בלי דיווח" in motto.fallback_line({"overdue": 0, "silent": 3, "do_now": 7})
    assert "דחופות" in motto.fallback_line({"overdue": 0, "silent": 0, "do_now": 7})


def test_fallback_line_on_a_clean_board_is_not_empty():
    assert motto.fallback_line({}).strip()


# --------------------------------------------------------------------------
# building and caching
# --------------------------------------------------------------------------

async def test_build_uses_the_model_sentence_but_keeps_our_quote(monkeypatch):
    """The model writes the connecting sentence only — the quote stays curated,
    even when the model answers with a fabricated one of its own."""
    from app.services import llm_router

    async def fake(*a, **k):
        return "«ציטוט מומצא» — אריסטו. סוגרים היום את שתי המשימות באיחור."
    monkeypatch.setattr(llm_router, "llm_chat", fake)

    m = await motto.build(None, {"overdue": 2}, _at(9))
    assert (m["quote"], m["author"], m["kind"]) == motto.pick_quote(_at(9))
    assert "אריסטו" in m["line"]          # the model's sentence is kept as written
    assert m["quote"] != "ציטוט מומצא"    # but it never becomes the quote


async def test_build_caches_the_hour(monkeypatch):
    from app.services import llm_router
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        return "עשר משימות באיחור אצל שני אחראים — סוגרים שתיים לפני סוף היום."
    monkeypatch.setattr(llm_router, "llm_chat", fake)

    await motto.build(None, {"overdue": 1}, _at(9))
    again = await motto.get_motto(None, {"overdue": 1}, _at(9))
    assert again["line"] == "עשר משימות באיחור אצל שני אחראים — סוגרים שתיים לפני סוף היום."
    assert len(calls) == 1


async def test_a_new_hour_is_a_new_entry(monkeypatch):
    from app.services import llm_router

    async def fake(*a, **k):
        return "עשר משימות באיחור — נסגור שתיים מהן לפני סוף היום."
    monkeypatch.setattr(llm_router, "llm_chat", fake)

    await motto.build(None, {}, _at(9))
    assert motto.cache_key(_at(10)) not in motto._cache
    assert len(motto._cache) == 1          # the cache never grows past the hour


async def test_llm_failure_serves_the_computed_line_and_is_not_cached(monkeypatch):
    from app.services import llm_router

    async def boom(*a, **k):
        raise RuntimeError("groq down")
    monkeypatch.setattr(llm_router, "llm_chat", boom)

    m = await motto.build(None, {"overdue": 3}, _at(9))
    assert m["line"] == motto.fallback_line({"overdue": 3})
    # A transient outage must not pin the fallback for the rest of the hour.
    assert motto.cache_key(_at(9)) not in motto._cache


async def test_an_empty_model_answer_also_falls_back(monkeypatch):
    from app.services import llm_router

    async def empty(*a, **k):
        return "   "
    monkeypatch.setattr(llm_router, "llm_chat", empty)

    m = await motto.build(None, {"silent": 2}, _at(9))
    assert "בלי דיווח" in m["line"]


async def test_a_cache_miss_never_waits_for_the_model(monkeypatch):
    """The render path returns immediately and fills the cache in the background."""
    spawned = []
    monkeypatch.setattr(motto, "_spawn", lambda f, s, n: spawned.append(n))

    m = await motto.get_motto(None, {"overdue": 5}, _at(9))
    assert m["line"] == motto.fallback_line({"overdue": 5})
    assert m["quote"] == motto.pick_quote(_at(9))[0]
    assert spawned == [_at(9)]


async def test_model_bullets_and_quote_marks_are_stripped(monkeypatch):
    from app.services import llm_router

    async def messy(*a, **k):
        return '- "סוגרים היום שתי משימות באיחור ומדווחים סטטוס על השאר"'
    monkeypatch.setattr(llm_router, "llm_chat", messy)

    m = await motto.build(None, {}, _at(9))
    assert m["line"] == "סוגרים היום שתי משימות באיחור ומדווחים סטטוס על השאר"


# --------------------------------------------------------------------------
# what the model sends back is not always a sentence
# --------------------------------------------------------------------------

async def test_a_stub_answer_is_refused_and_not_cached(monkeypatch):
    """The bug the room actually saw: a two-letter stub of a sentence on the wall.

    It happens when the answering provider spends the token budget on a reasoning
    preamble and the sentence itself is cut off mid-word. A stub is not a sentence
    — the computed line is better — and it must not be cached for the hour.
    """
    from app.services import llm_router

    async def stub(*a, **k):
        return "עם"
    monkeypatch.setattr(llm_router, "llm_chat", stub)

    m = await motto.build(None, {"overdue": 7}, _at(9))
    assert m["line"] == motto.fallback_line({"overdue": 7})
    assert motto.cache_key(_at(9)) not in motto._cache


async def test_a_reasoning_preamble_is_dropped(monkeypatch):
    """A provider that leaks its thinking must not put English on the wall."""
    from app.services import llm_router

    async def chatty(*a, **k):
        return ("Okay, let me think about this. The board has ten late missions.\n"
                "I should keep it short and practical.\n"
                "עשר משימות באיחור — בוחרים שתיים וסוגרים אותן לפני סוף היום.")
    monkeypatch.setattr(llm_router, "llm_chat", chatty)

    m = await motto.build(None, {"overdue": 10}, _at(9))
    assert m["line"] == "עשר משימות באיחור — בוחרים שתיים וסוגרים אותן לפני סוף היום."
    assert "Okay" not in m["line"]


def test_pick_sentence_takes_the_hebrew_line():
    assert motto._pick_sentence("Reasoning here\nמשפט בעברית שהוא התשובה") == \
        "משפט בעברית שהוא התשובה"
    assert motto._pick_sentence("only english") == ""
    assert motto._pick_sentence("") == ""


async def test_the_hourly_call_gets_a_real_token_budget(monkeypatch):
    """A tight ceiling is what let a reasoning preamble eat the answer —
    gemma_client.py carries the same warning."""
    from app.services import llm_router
    seen = {}

    async def fake(*a, **k):
        seen.update(k)
        return "עשר משימות באיחור — סוגרים שתיים לפני סוף היום ומדווחים על השאר."
    monkeypatch.setattr(llm_router, "llm_chat", fake)

    await motto.build(None, {}, _at(9))
    assert seen["max_tokens"] >= 500
    assert motto.MAX_TOKENS >= 500


async def test_a_stub_already_in_the_cache_is_not_served(monkeypatch):
    """A bad line written before the guard existed must not hold the hour."""
    class _Row:
        text = "המכשול שבדרך הופך להיות הדרך.|מרקוס אורליוס|עם"

    async def fake_get(session, kind):
        return _Row()
    from app.services import missions_report_service as mrs
    monkeypatch.setattr(mrs, "day_cache_get", fake_get)
    monkeypatch.setattr(motto, "_spawn", lambda f, s, n: None)

    m = await motto.get_motto(None, {"overdue": 2}, _at(9))
    assert m["line"] == motto.fallback_line({"overdue": 2})
