"""חדר מבצעים wall — the hourly line: a curated quote plus one AI sentence that
ties it to what the board actually looks like right now.

Two hard rules shape this module:

1. **The quote is never generated.** Models invent plausible-sounding Stoics, and
   a fabricated Marcus Aurelius on a wall screen in front of 500 engineers is a
   lie with a font size. The quotes below are a curated list; the model only
   writes the one sentence that connects a REAL quote to REAL board numbers.
2. **The screen never waits for Groq.** A cache miss returns the computed line
   immediately and fills the cache in the background, so the wall renders at the
   same speed whether the LLM answers in 200ms, in 8s, or never.

One LLM call per clock hour at most, and only while somebody is actually looking
at the wall.
"""

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import missions_menu_service as oms

logger = logging.getLogger(__name__)

# (quote, attribution, kind). kind "wry" is the dry-humour shelf — it rotates in
# every HUMOR_EVERY hours so the screen does not preach all day long.
#
# Attributions are deliberately author-only: naming a chapter and verse we are
# not certain of would be exactly the fabrication this module exists to avoid.
QUOTES: list[tuple[str, str, str]] = [
    ("המכשול שבדרך הופך להיות הדרך.", "מרקוס אורליוס", "stoic"),
    ("אל תבזבז עוד זמן בוויכוח על מה שאדם טוב צריך להיות. היה כזה.", "מרקוס אורליוס", "stoic"),
    ("עשה כל מעשה בחייך כאילו הוא האחרון.", "מרקוס אורליוס", "stoic"),
    ("לא האירועים מטרידים את האדם, אלא דעתו על האירועים.", "אפיקטטוס", "stoic"),
    ("קבע תחילה מה ברצונך להיות, ואז עשה את הנדרש.", "אפיקטטוס", "stoic"),
    ("אנו סובלים יותר בדמיון מאשר במציאות.", "סנקה", "stoic"),
    ("לספינה שאינה יודעת לאיזה נמל היא חותרת, שום רוח אינה טובה.", "סנקה", "stoic"),
    ("אין הזמן קצר — אנחנו מבזבזים ממנו הרבה.", "סנקה", "stoic"),
    ("אל תסביר את הפילוסופיה שלך. גלם אותה.", "אפיקטטוס", "stoic"),
    ("שום תוכנית קרב אינה שורדת את המפגש הראשון עם האויב.", "הלמוט פון מולטקה", "wry"),
    ("אם משהו יכול להשתבש — הוא ישתבש.", "חוק מרפי", "wry"),
    ("עבודה מתרחבת כך שתמלא את כל הזמן שהוקצב לה.", "חוק פרקינסון", "wry"),
    ("תשעים האחוזים הראשונים לוקחים תשעים אחוז מהזמן. עשרת האחוזים הנותרים — את השאר.",
     "חוק 90-90 (טום קרגיל)", "wry"),
    ("אין דבר קבוע יותר מפתרון זמני.", "פתגם הנדסי", "wry"),
]

STOIC = [q for q in QUOTES if q[2] == "stoic"]
WRY = [q for q in QUOTES if q[2] == "wry"]

# Every Nth hour gets the dry shelf instead of the Stoic one.
HUMOR_EVERY = 4

_cache: dict[str, dict] = {}      # "YYYY-MM-DD HH" -> motto dict
_inflight: set[str] = set()       # keys currently being built in the background


def cache_key(now: datetime.datetime) -> str:
    """One entry per clock hour — this is also the DB cache's kind suffix."""
    return now.strftime("%Y-%m-%d %H")


def pick_quote(now: datetime.datetime) -> tuple[str, str, str]:
    """Which quote this hour shows. Deterministic: two renders of the same hour
    must show the same line, or the wall looks like it is glitching."""
    hour = now.hour
    shelf = WRY if (hour % HUMOR_EVERY == 1 and WRY) else STOIC
    index = (now.toordinal() * 24 + hour) % len(shelf)
    return shelf[index]


def fallback_line(stats: dict) -> str:
    """The connecting sentence when the LLM is unavailable — computed, never empty.

    Ordered by what actually deserves the room's attention: lateness first,
    silence second, load third.
    """
    overdue = int(stats.get("overdue") or 0)
    silent = int(stats.get("silent") or 0)
    do_now = int(stats.get("do_now") or 0)
    if overdue:
        return f"{overdue} משימות באיחור. השעה הקרובה שווה יותר מכל הסבר."
    if silent:
        return f"{silent} משימות בלי דיווח. עדכון של שורה אחת חוסך ישיבה שלמה."
    if do_now:
        return f"{do_now} משימות דחופות על השולחן. אחת בכל פעם, עד הסוף."
    return "הלוח נקי. זה הזמן לתכנן את השבוע הבא, לא לנוח בו."


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
            max_tokens=120,
            temperature=0.7,
        )
    except Exception as e:
        logger.warning(f"war_room_motto: LLM failed, using the computed line: {e}")
        raw = ""

    # One line, no stray markup — the model occasionally answers with a bullet.
    line = " ".join((raw or "").split()).lstrip("-•* ").strip('"״')
    if not line:
        # Not cached: a transient outage must not pin the fallback for the hour.
        return _motto(quote, fallback_line(stats))

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
    return {"quote": quote, "author": author, "kind": kind, "line": line}


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
        return _cache[key]

    try:
        stored = await _db_get(session, key)
    except Exception as e:
        logger.warning(f"war_room_motto: cache read failed: {e}")
        stored = None
    if stored:
        _cache.clear()
        _cache[key] = stored
        return stored

    from app.database import async_session_maker
    _spawn(async_session_maker, stats, now)
    return _motto(pick_quote(now), fallback_line(stats))
