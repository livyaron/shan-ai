"""חדר מבצעים wall — the hourly line: a curated quote plus one AI sentence that
ties it to what the board actually looks like right now.

Three hard rules shape this module:

1. **The quote is never generated.** Models invent plausible-sounding Stoics, and
   a fabricated Marcus Aurelius on a wall screen in front of 500 engineers is a
   lie with a font size. `war_room_quotes.QUOTES` is a curated library; the
   model only writes the one sentence connecting a REAL quote to REAL numbers.
2. **No quote repeats until the whole shelf has been shown.** A wall screen
   that recycles its library every day becomes wallpaper — see `pick_quote`.
3. **The screen never waits for Groq.** A cache miss returns the computed line
   immediately and fills the cache in the background, so the wall renders at the
   same speed whether the LLM answers in 200ms, in 8s, or never.
4. **The two lines take turns.** The quote holds for the hour, but the sentence
   under it alternates between the AI one and the computed one every
   SWAP_MINUTES — the strip is the only thing on the wall that can change
   inside an hour, so it should.

One LLM call per clock hour at most, and only while somebody is actually looking
at the wall.
"""

import asyncio
import datetime
import logging
import random
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import missions_menu_service as oms
from app.services import war_room_quotes as _quotes

logger = logging.getLogger(__name__)

QUOTES = _quotes.QUOTES
STOIC = [q for q in QUOTES if q[2] == "stoic"]
WRY = [q for q in QUOTES if q[2] == "wry"]

# Every Nth hour gets the dry shelf instead of the Stoic one.
HUMOR_EVERY = 4

# A sentence shorter than this is not a sentence — it is what is left of one.
# See _pick_sentence: the wall would rather print its own computed line than a
# two-letter stub of the model's.
MIN_LINE_CHARS = 25
MIN_LINE_WORDS = 4

# Budget for the hourly call. NOT a tight one: the fallback provider (gemma) leaks
# a chain-of-thought preamble before its answer, so a small ceiling is spent on
# the reasoning and the actual sentence gets cut off mid-word — which is exactly
# how a three-letter stub reached the wall. gemma_client.py carries the same
# warning. One call per hour: the extra tokens cost nothing worth counting.
MAX_TOKENS = 700

_cache: dict[str, dict] = {}      # "YYYY-MM-DD HH" -> motto dict
_inflight: set[str] = set()       # keys currently being built in the background


def cache_key(now: datetime.datetime) -> str:
    """One entry per clock hour — this is also the DB cache's kind suffix."""
    return now.strftime("%Y-%m-%d %H")


# Which hours land on which shelf. Derived from HUMOR_EVERY rather than spelled
# out, so changing that constant moves the schedule and the rotation together.
_WRY_HOURS = [h for h in range(24) if h % HUMOR_EVERY == 1]
_STOIC_HOURS = [h for h in range(24) if h % HUMOR_EVERY != 1]
_WRY_RANK = {h: i for i, h in enumerate(_WRY_HOURS)}
_STOIC_RANK = {h: i for i, h in enumerate(_STOIC_HOURS)}


def _slot(now: datetime.datetime) -> tuple[list, str, int]:
    """(shelf, shelf key, slot number) for this hour.

    The slot number counts that shelf's showings since day zero, so it advances
    by exactly one every time the shelf comes up — which is what lets the
    rotation below hand out each quote once and only once.
    """
    hour = now.hour
    if WRY and hour in _WRY_RANK:
        return WRY, "wry", now.toordinal() * len(_WRY_HOURS) + _WRY_RANK[hour]
    return STOIC, "stoic", now.toordinal() * len(_STOIC_HOURS) + _STOIC_RANK[hour]


@lru_cache(maxsize=8)
def _cycle_order(shelf_key: str, size: int, cycle: int) -> tuple[int, ...]:
    """A shuffled permutation of a shelf, fixed for the whole cycle.

    Seeded by (shelf, cycle) and nothing else: two containers, two browsers and
    a redeploy in the middle must all compute the same order for the same hour,
    or the wall looks like it is glitching. `random.Random(str)` hashes its seed
    with SHA-512, so this is stable across processes and PYTHONHASHSEED.
    """
    order = list(range(size))
    random.Random(f"{shelf_key}:{cycle}").shuffle(order)
    return tuple(order)


def pick_quote(now: datetime.datetime) -> tuple[str, str, str]:
    """Which quote this hour shows.

    Deterministic (two renders of the same hour must match) and exhaustive: a
    shelf is walked in a shuffled order and every quote in it is shown exactly
    once before any of them comes back. With today's library that is a full
    cycle of about eight days per shelf, versus the single day the first
    14-quote version managed.

    The one seam: the last quote of a cycle and the first of the next are drawn
    independently, so back-to-back repeats are possible at odds of 1/len(shelf).
    Closing that would mean deriving each cycle from the previous one all the
    way back to day zero — not worth it for a 0.7% chance of one repeat.
    """
    shelf, key, slot = _slot(now)
    size = len(shelf)
    return shelf[_cycle_order(key, size, slot // size)[slot % size]]


# How long each of the two lines holds the bottom strip before the other takes
# over. The hour is split into bands of this many minutes, alternating:
#
#   :00-:04 computed   :05-:09 AI   :10-:14 computed   ...
#
# Two reasons the computed line opens the hour rather than the AI one. First,
# the AI sentence for a new hour does not exist yet at :00 — get_motto returns
# immediately and fills the cache in the background — so the opening band is
# the only line there is. Second, a strip that never changes inside an hour is
# a strip people stop reading; alternating gives someone crossing the corridor
# twice a chance to see two different things.
#
# Must divide 60, or the bands drift across the hour boundary and the opening
# band stops being the computed one.
SWAP_MINUTES = 5

# The computed line, per board condition. This is what the wall shows whenever
# the LLM is unavailable — which, per the provider notes in gemma_client.py and
# llm_router.py, is not a rare event. A single hardcoded sentence per condition
# meant the room read the SAME line every time Groq had a bad afternoon, so each
# condition carries a small shelf of its own and rotates with the hour.
#
# Same register as the quotes: mostly straight, occasionally dry. Every line has
# to survive being read from four metres away by somebody who is late.
#
# EVERY SHELF MUST HOLD AN ODD NUMBER OF LINES, and more than six of them.
# The computed line takes every OTHER band, so it walks its shelf two steps at
# a time; on an even-length shelf that stride only ever reaches half the lines,
# and the six computed slots in an hour become three sentences shown twice.
# An odd length makes the stride coprime with the shelf and covers all of it.
# A test enforces both.
_FALLBACKS: dict[str, list[str]] = {
    "overdue": [
        "{n} משימות באיחור. השעה הקרובה שווה יותר מכל הסבר.",
        "{n} משימות באיחור. אף אחת מהן לא תסגור את עצמה עד הישיבה הבאה.",
        "{n} משימות באיחור. נתחיל מהוותיקה שבהן — היא כבר מכירה אותנו.",
        "{n} משימות עברו את היעד. תאריך יעד שחלף הוא החלטה שלא קיבלנו.",
        "{n} משימות באיחור. נסגור אחת היום — זו כבר מגמה.",
        "{n} משימות באיחור. או שמזיזים את היעד, או שמזיזים את המשימה.",
        "{n} משימות באיחור. הן לא נעלמות — הן רק מצטברות בשקט.",
    ],
    "silent": [
        "{n} משימות בלי דיווח. עדכון של שורה אחת חוסך ישיבה שלמה.",
        "{n} משימות שותקות. שקט בלוח אינו סימן טוב — הוא חוסר מידע.",
        "{n} משימות בלי עדכון. מי שלא מדווח בעצמו, מדווחים עליו.",
        "{n} משימות בלי דיווח. שתי דקות כתיבה חוסכות שבוע ניחושים.",
        "{n} משימות ללא סימן חיים. נעדכן סטטוס לפני שמישהו ישאל.",
        "{n} משימות בלי דיווח. אין חדשות זה לא בהכרח חדשות טובות.",
        "{n} משימות שקטות. דיווח קצר עכשיו עדיף על הסבר ארוך אחר כך.",
    ],
    "do_now": [
        "{n} משימות דחופות על השולחן. אחת בכל פעם, עד הסוף.",
        "{n} משימות ברביע דחוף וחשוב. איתן פותחים את היום, לא איתן מסיימים.",
        "{n} משימות דחופות. ריבוי דחיפויות הוא בדרך כלל תכנון שנדחה.",
        "{n} משימות דחופות. נבחר אחת ונסגור אותה — לא נפתח את כולן.",
        "{n} משימות דחופות. דחוף אינו אומר בבת אחת — הוא אומר קודם.",
        "{n} משימות דחופות. מה שלא ייסגר היום יחזור מחר גדול יותר.",
        "{n} משימות דחופות. נסמן מי אחראי על כל אחת — עכשיו, לא בישיבה.",
    ],
    "clean": [
        "הלוח נקי. זה הזמן לתכנן את השבוע הבא, לא לנוח בו.",
        "הלוח נקי. שקט כזה נמשך בדיוק עד הטלפון הבא.",
        "אין איחורים ואין שתיקות. נשתמש בשעה הזאת לתכנן, לא לכבות.",
        "הלוח נקי. עכשיו מטפלים במה שחשוב ואינו דחוף.",
        "אין איחורים על הלוח. זה מצב שמחזיקים, לא מצב שמגיע מעצמו.",
        "הלוח נקי. השעה הזאת שווה יותר מכל שעה שנרוץ בה אחר כך.",
        "אין באיחור ואין שתיקות. נשאל את עצמנו מה חסר בלוח, לא מה בו.",
    ],
}


def _band(now: datetime.datetime) -> int:
    """This minute's swap band, counted from day zero.

    Absolute rather than per-hour so the computed line's phrasing keeps moving
    across hours instead of resetting to the same sentence every :00.
    """
    return (now.toordinal() * 24 * 60 + now.hour * 60 + now.minute) // SWAP_MINUTES


def shows_computed(now: datetime.datetime) -> bool:
    """True while the strip belongs to the computed line rather than the AI one."""
    return (now.minute // SWAP_MINUTES) % 2 == 0


def fallback_line(stats: dict, now: datetime.datetime | None = None) -> str:
    """The computed connecting sentence — never empty, never needs the LLM.

    Ordered by what actually deserves the room's attention: lateness first,
    silence second, load third. Within the chosen condition the phrasing rotates
    with the swap band, so the three times it comes up in an hour are three
    different sentences, and an LLM outage does not pin one line to the wall for
    the afternoon.
    """
    now = now or datetime.datetime.now(oms._IL_TZ)
    band = _band(now)

    def _say(condition: str, n: int = 0) -> str:
        shelf = _FALLBACKS[condition]
        return shelf[band % len(shelf)].format(n=n)

    overdue = int(stats.get("overdue") or 0)
    silent = int(stats.get("silent") or 0)
    do_now = int(stats.get("do_now") or 0)
    if overdue:
        return _say("overdue", overdue)
    if silent:
        return _say("silent", silent)
    if do_now:
        return _say("do_now", do_now)
    return _say("clean")


def _hebrew_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if "\u05d0" <= c <= "\u05ea") / len(text)


def _pick_sentence(raw: str) -> str:
    """The Hebrew sentence out of whatever the model sent back.

    A provider that leaks its reasoning answers with English lines before the
    Hebrew one; flattening the whole reply would put that reasoning on the wall.
    The answer we asked for is one sentence, so it is the longest Hebrew line.
    """
    lines = [" ".join(line.split()) for line in (raw or "").splitlines()]
    hebrew = [line for line in lines if line and _hebrew_ratio(line) >= 0.3]
    if not hebrew:
        return ""
    return max(hebrew, key=len)


def _motto(quote: tuple[str, str, str], line: str) -> dict:
    text, author, kind = quote
    return {"quote": text, "author": author, "kind": kind, "line": line}


_PROMPT = """אתה כותב שורה אחת למסך חדר מבצעים באגף תשתיות חשמל.

לפניך ציטוט אמיתי ומספרים אמיתיים מלוח המשימות.
כתוב משפט אחד בעברית שמחבר בין הציטוט למצב הלוח — משפט שאדם קורא ממרחק
ארבעה מטרים ויודע מה לעשות בשעה הקרובה.

כללים מחייבים:
- משפט אחד בלבד, עד 18 מילים. בלי כותרת, בלי רשימה, בלי אימוג׳י.
- אל תצטט מחדש את הציטוט ואל תזכיר את שם הכותב.
- השתמש רק במספרים שניתנו לך. אל תמציא נתונים, שמות או משימות.
- דבר אל צוות, לא אל יחיד. בגוף ראשון רבים או בציווי.
- אל תשתמש במרכאות כפולות (") — גרש בודד (׳) בלבד.
"""


def _context(quote: tuple[str, str, str], stats: dict) -> str:
    text, author, kind = quote
    tone = ("הציטוט אירוני — מותר שהמשפט יהיה יבש ומחויך, אך לא מזלזל."
            if kind == "wry" else "הציטוט סטואי — המשפט יהיה מרוכז ומעשי.")
    return (
        f"הציטוט: {text} ({author})\n"
        f"{tone}\n\n"
        f"מצב הלוח כרגע:\n"
        f"- משימות פתוחות: {stats.get('open', 0)}\n"
        f"- באיחור: {stats.get('overdue', 0)}\n"
        f"- דחופות לביצוע עכשיו: {stats.get('do_now', 0)}\n"
        f"- ללא דיווח סטטוס: {stats.get('silent', 0)}\n"
        f"- נסגרו השבוע: {stats.get('done_week', 0)}\n"
    )


async def build(session: AsyncSession, stats: dict, now: datetime.datetime | None = None) -> dict:
    """Build this hour's motto and cache it. Falls back to the computed line."""
    now = now or datetime.datetime.now(oms._IL_TZ)
    key = cache_key(now)
    quote = pick_quote(now)

    from app.services.llm_router import llm_chat
    try:
        raw = await llm_chat(
            "wall_motto",
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": _context(quote, stats)}],
            max_tokens=MAX_TOKENS,
            temperature=0.7,
        )
    except Exception as e:
        logger.warning(f"war_room_motto: LLM failed, using the computed line: {e}")
        raw = ""

    # One line, no stray markup — the model occasionally answers with a bullet.
    # Capped here rather than in CSS: see war_room_wall.shorten for why the wall
    # never lets the browser do its own cutting.
    from app.services.war_room_wall import MOTTO_LINE_CHARS, shorten
    line = shorten(_pick_sentence(raw).lstrip("-•* ").strip('"״'), MOTTO_LINE_CHARS)
    if len(line) < MIN_LINE_CHARS or len(line.split()) < MIN_LINE_WORDS:
        # What came back is a stub, not a sentence — usually a reasoning preamble
        # that ate the token budget. Print our own line rather than the stub, and
        # do not cache it: the next hour gets a fresh try.
        logger.warning(
            f"war_room_motto: model answer unusable ({len(raw or '')} chars raw, "
            f"{len(line)} kept: {line!r}) — serving the computed line"
        )
        return _motto(quote, fallback_line(stats, now))

    motto = _motto(quote, line)
    _cache.clear()
    _cache[key] = motto
    await _db_put(session, key, motto)
    return motto


async def _db_put(session: AsyncSession, key: str, motto: dict) -> None:
    """Mirror into the day cache so a Railway redeploy does not re-pay for the hour."""
    from app.services import missions_report_service as mrs
    await mrs.day_cache_put(session, f"wall_motto_{key[-2:]}", f"{motto['quote']}|{motto['author']}|{motto['line']}")


async def _db_get(session: AsyncSession, key: str) -> dict | None:
    from app.services import missions_report_service as mrs
    row = await mrs.day_cache_get(session, f"wall_motto_{key[-2:]}")
    if row is None or not row.text or row.text.count("|") < 2:
        return None
    quote, author, line = row.text.split("|", 2)
    kind = next((k for t, a, k in QUOTES if t == quote), "stoic")
    from app.services.war_room_wall import MOTTO_LINE_CHARS, shorten
    line = shorten(line, MOTTO_LINE_CHARS)
    # A stub stored by an earlier build must not be served for the rest of the
    # hour: treat it as a miss, so the caller shows the computed line and a fresh
    # build replaces it.
    if len(line) < MIN_LINE_CHARS or len(line.split()) < MIN_LINE_WORDS:
        return None
    return {"quote": quote, "author": author, "kind": kind, "line": line}


def _present(motto: dict, stats: dict, now: datetime.datetime) -> dict:
    """Which of the two lines this minute shows.

    Only the connecting line alternates. The QUOTE stays whatever the hour
    picked: a wall that swaps its quote mid-hour reads as a broken screen, not
    as a richer one.
    """
    if shows_computed(now):
        return {**motto, "line": fallback_line(stats, now)}
    return motto


def _spawn(session_factory, stats: dict, now: datetime.datetime) -> None:
    """Fill this hour's cache without making the current render wait for it."""
    key = cache_key(now)
    if key in _inflight:
        return
    _inflight.add(key)

    async def _run():
        try:
            async with session_factory() as bg_session:
                await build(bg_session, stats, now)
        except Exception as e:
            logger.warning(f"war_room_motto: background build failed: {e}")
        finally:
            _inflight.discard(key)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:      # no loop (tests, sync callers) — nothing to spawn
        _inflight.discard(key)


async def get_motto(
    session: AsyncSession, stats: dict, now: datetime.datetime | None = None,
) -> dict:
    """This hour's line. Never raises, never blocks on the LLM, never returns empty."""
    now = now or datetime.datetime.now(oms._IL_TZ)
    key = cache_key(now)
    if key in _cache:
        return _present(_cache[key], stats, now)

    try:
        stored = await _db_get(session, key)
    except Exception as e:
        logger.warning(f"war_room_motto: cache read failed: {e}")
        stored = None
    if stored:
        _cache.clear()
        _cache[key] = stored
        return _present(stored, stats, now)

    from app.database import async_session_maker
    _spawn(async_session_maker, stats, now)
    return _motto(pick_quote(now), fallback_line(stats, now))
