"""Deep AI reading of the patterns page (PLAN.md P5).

Three zoom levels — division, sector, project. The model is handed the
engine's numbers and the handling level (insight_triage) and writes only the
prose: what is going on, the likely root cause with quotes, the action, the
questions to ask and what to watch. It never computes or ranks anything.

Guards, all enforced after the reply (`validate`), so a model that ignores the
prompt still cannot put a fabricated fact on the page:
- a number not present in the context is replaced by "[?]" and counted;
- a quote not found verbatim in the weekly texts is dropped;
- a project id not in the context is dropped.
PM names never leave the system: the context carries "מנה״פ #n" and the
names are put back only when the reply is rendered (`restore_names`).

Context builders and `validate` are pure (tested without a DB or a model);
`generate_*` do the I/O and swallow their own errors — a failed analysis is a
missing panel, never a broken page.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date

from app.services import insight_triage as it
from app.services import stage_sectors as ss

logger = logging.getLogger(__name__)

USAGE = "pattern_analysis"
AREA_SECTORS = (ss.PLANNING, ss.SUPERVISION, ss.EXECUTION, ss.PM_DEPT)
DIVISION = "division"

# Size. The Groq account (on_demand tier) allows 8,000 tokens PER MINUTE, and
# Groq counts prompt + max_tokens against it — a 9,137-token request was
# refused outright on 2026-10-07 (413) and the whole analysis fell through to
# a failing fallback. So every context is packed into a character budget
# (`_pack`): Hebrew runs at no less than ~2 chars/token, so 7,000 chars of
# context + ~2,200 of system prompt + the reply cap stays under 8,000.
GROQ_TPM = 8000             # the account's per-minute limit (Groq on_demand tier)
CHARS_PER_TOKEN = 2.0       # conservative for Hebrew; real text tokenizes denser
CONTEXT_CHARS = {"area": 7000, "project": 7000}
MAX_TOKENS = {"area": 2400, "project": 2200}
MAX_HOT_PROJECTS = 15       # 🔴+🟠 projects written out in full per area
MAX_FOLLOW_PROJECTS = 25    # 🟡 projects listed with their reasons only
AREA_WEEKS = 2              # newest weekly texts per hot project (area levels)
PROJECT_WEEKS = 8           # newest weekly texts for a project deep-dive
TEXT_CHARS = 220            # per weekly text, area levels
PROJECT_TEXT_CHARS = 450    # per weekly text, project level
TPM_WAIT_SECONDS = 65       # one Groq per-minute window, plus slack
ATTEMPTS = 3                # per analysis, a TPM window apart

_NUM = re.compile(r"\d+(?:\.\d+)?")


def sector_kind(key: str) -> str:
    return f"sector:{key}"


def project_kind(identifier: str) -> str:
    return f"project:{identifier}"


# ── Pure helpers ───────────────────────────────────────────────────────────

def _clean(t: object, limit: int | None = None) -> str:
    """One line, gershayim instead of straight quotes (JSON safety, CLAUDE.md §5)."""
    s = " ".join(str(t or "").split()).replace('"', "״")
    return s if limit is None or len(s) <= limit else s[:limit].rstrip() + "…"


def pm_aliases(p: dict) -> dict[str, str]:
    """PM name → "מנה״פ #n", stable by name order within one engine output."""
    names = sorted({x["manager"] for x in p.get("projects", []) if x.get("manager")})
    return {n: f"מנה״פ #{i}" for i, n in enumerate(names, 1)}


def restore_names(obj: object, aliases: dict[str, str]) -> object:
    """Put the PM names back into a validated reply, longest alias first
    (#12 before #1)."""
    back = sorted(((a, n) for n, a in aliases.items()), key=lambda x: -len(x[0]))
    if isinstance(obj, str):
        for a, n in back:
            obj = obj.replace(a, n)
        return obj
    if isinstance(obj, list):
        return [restore_names(v, aliases) for v in obj]
    if isinstance(obj, dict):
        return {k: restore_names(v, aliases) for k, v in obj.items()}
    return obj


def _norm_num(tok: str) -> str:
    try:
        return repr(float(tok))
    except ValueError:
        return tok


def _nums(text: str) -> set[str]:
    return {_norm_num(t) for t in _NUM.findall(text)}


def _quote_norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", t or "").split())


def _level_counts(projects: list[dict], tri: dict) -> str:
    counts = {lv: 0 for lv in it.ORDER}
    for x in projects:
        if x["identifier"] in tri:
            counts[tri[x["identifier"]]["level"]] += 1
    return " | ".join(f"{it.LEVELS[lv]['icon']} {it.LEVELS[lv]['label']}: {counts[lv]}" for lv in it.ORDER)


def _project_line(x: dict, tri: dict, aliases: dict) -> str:
    t = tri.get(x["identifier"], {"level": it.FYI, "reasons": []})
    lv = it.LEVELS[t["level"]]
    fc = x.get("fc") or ("מלל: " + _clean(x.get("fc_text"), 60) if x.get("fc_text") else "—")
    reasons = "; ".join(_clean(r["text"]) for r in t["reasons"]) or "—"
    return (f"[{x['identifier']}] {_clean(x.get('name'), 60)} | {lv['icon']} {lv['label']} | שלב: {x['stage']} | "
            f"{aliases.get(x.get('manager'), 'ללא מנה״פ')} | יעד מסתמן: {fc} | ת.פיתוח: {x.get('dev') or '—'} | "
            f"סיבות: {reasons}")


# Line priorities for `_pack`: lower survives longer when the budget is tight.
MUST, HOT, WEEK1, FOLLOW, WEEK2 = 0, 1, 2, 3, 4


def _pack(items: list[tuple[int, str]], budget: int) -> str:
    """Join (priority, line) pairs into at most `budget` chars, keeping the
    order. When it does not fit, whole lines go — lowest priority first, the
    last of them first — and the context says how many, so the model knows
    the list is not complete."""
    keep = [True] * len(items)
    total = sum(len(t) + 1 for _, t in items)
    dropped = 0
    for prio in sorted({p for p, _ in items if p != MUST}, reverse=True):
        for i in range(len(items) - 1, -1, -1):
            if total <= budget:
                break
            if keep[i] and items[i][0] == prio:
                keep[i] = False
                total -= len(items[i][1]) + 1
                dropped += 1
    lines = [t for (_, t), k in zip(items, keep) if k]
    if dropped:
        lines.append(f"(הושמטו {dropped} שורות מחוסר מקום — הרשימות למעלה אינן מלאות.)")
    return "\n".join(lines)


def _hot_block(projects: list[dict], tri: dict, aliases: dict,
               weekly: dict[str, list[tuple[str, str]]]) -> list[tuple[int, str]]:
    order = {lv: i for i, lv in enumerate(it.ORDER)}
    ranked = sorted(projects, key=lambda x: (order[tri.get(x["identifier"], {"level": it.FYI})["level"]],
                                             -(x.get("forecast_moved_months") or 0)))
    hot = [x for x in ranked if tri.get(x["identifier"], {}).get("level") in (it.ESCALATE, it.WEEK)]
    follow = [x for x in ranked if tri.get(x["identifier"], {}).get("level") == it.FOLLOW]
    items = [(MUST, f"פרויקטים בדרגת הסלמה / טיפול מנהל אגף ({len(hot)}, מוצגים עד {MAX_HOT_PROJECTS}):")]
    for x in hot[:MAX_HOT_PROJECTS]:
        items.append((HOT, "- " + _project_line(x, tri, aliases)))
        if x.get("escalation"):
            items.append((HOT, f"  עמודת לטיפול: {_clean(x['escalation'], 120)}"))
        for n, (week, text) in enumerate(weekly.get(x["identifier"], [])[:AREA_WEEKS]):
            items.append((WEEK1 if n == 0 else WEEK2, f"  דיווח {week}: {_clean(text, TEXT_CHARS)}"))
    items.append((MUST, f"פרויקטים במעקב ({len(follow)}, מוצגים עד {MAX_FOLLOW_PROJECTS}):"))
    items += [(FOLLOW, "- " + _project_line(x, tri, aliases)) for x in follow[:MAX_FOLLOW_PROJECTS]]
    return items


def _sector_row(s: dict) -> str:
    pct = lambda v: "—" if v is None else f"{v}%"   # noqa: E731
    return (f"{s['label']}: {s['n']} פרויקטים | מאחרים מול תכנית: {pct(s.get('late_pct'))} | "
            f"נדחו מאז התאריך הראשון: {pct(s.get('drift_pct'))} | בסיס זז: {s.get('baseline_moved')} | "
            f"תקועים: {s.get('stuck')}")


def division_context(p: dict, tri: dict, weekly: dict, aliases: dict) -> str:
    m = p.get("metrics", {})
    lines = [f"נכון לדוח {p.get('as_of')} · {len(p.get('report_dates', []))} דוחות בהיסטוריה · "
             f"{len(p.get('projects', []))} פרויקטים פעילים.",
             "דרגות טיפול (לפי כללים קבועים): " + _level_counts(p.get("projects", []), tri), "",
             "לפי מגזר (שלב נוכחי; עבודה אזרחית והרכבות נספר בשני מגזרים — אין לסכם):"]
    lines += ["- " + _sector_row(s) for s in p.get("sectors", [])]
    att = m.get("slip_attribution", {}).get("value", {})
    lines += ["", "איפה נוצרו דחיות יעד החשמול (מגזר שהחזיק את הפרויקט ברגע הדחייה):"]
    lines += [f"- {ss.SECTORS.get(k, k)}: {v['events']} אירועים, {v['months']} חודשים" for k, v in att.items() if v.get("events")]
    waves = m.get("update_waves", {}).get("value", [])
    if waves:
        w = waves[-1]
        lines += ["", f"הגל האחרון ({w['from']} עד {w['to']}): {w['forecast_later']} יעדים נדחו, "
                      f"{w['baseline_later']} תכניות פיתוח נדחו, {w['both']} שניהם, מתוך {w['n']}."]
    league = [r for r in p.get("league", []) if r.get("manager") in aliases]
    if league:
        lines += ["", "מנהלי פרויקטים (מאחרים / נדחו / בסיס זז / תקועים מתוך n):"]
        lines += [f"- {aliases[r['manager']]}: n={r['n']} | {r.get('late_pct')}% / {r.get('drift_pct')}% / "
                  f"{r.get('baseline_moved')} / {r.get('stuck')}" + ("" if r.get("rank") else " (מדגם קטן)")
                  for r in league]
    topics = sorted(m.get("risk_categories", {}).get("value", []), key=lambda r: -r["projects"])[:6]
    if topics:
        lines += ["", "נושאים שכותבים עליהם (פרויקטים): " + " | ".join(f"{r['category']}: {r['projects']}" for r in topics)]
    items = [(MUST, ln) for ln in lines] + [(MUST, "")] + _hot_block(p.get("projects", []), tri, aliases, weekly)
    return _pack(items, CONTEXT_CHARS["area"])


def sector_context(p: dict, tri: dict, weekly: dict, aliases: dict, sector: str) -> str:
    projects = [x for x in p.get("projects", []) if sector in x.get("sectors", [])]
    row = next((s for s in p.get("sectors", []) if s["sector"] == sector), None)
    att = p.get("metrics", {}).get("slip_attribution", {}).get("value", {}).get(sector)
    lines = [f"מגזר: {ss.SECTORS.get(sector, sector)} · נכון לדוח {p.get('as_of')} · {len(projects)} פרויקטים בשלבי המגזר.",
             "דרגות טיפול (לפי כללים קבועים): " + _level_counts(projects, tri)]
    if row:
        lines.append(_sector_row(row))
    if att and att.get("events"):
        lines.append(f"דחיות יעד שנוצרו כשהפרויקט היה במגזר: {att['events']} אירועים, {att['months']} חודשים.")
    stages: dict[str, int] = {}
    for x in projects:
        stages[x["stage"]] = stages.get(x["stage"], 0) + 1
    lines.append("תמהיל שלבים: " + " | ".join(f"{k}: {v}" for k, v in sorted(stages.items(), key=lambda kv: -kv[1])))
    items = [(MUST, ln) for ln in lines] + [(MUST, "")] + _hot_block(projects, tri, aliases, weekly)
    return _pack(items, CONTEXT_CHARS["area"])


def project_context(h: dict, triage: dict, aliases: dict) -> str:
    row = h.get("row") or {}
    lv = it.LEVELS[triage["level"]]
    lines = [f"[{h['identifier']}] {_clean(h.get('name'), 80)} · סוג: {_clean(h.get('project_type'))} · "
             f"{aliases.get(h.get('manager'), 'ללא מנה״פ')} · נכון לדוח {h.get('as_of')}",
             f"דרגת טיפול (לפי כללים): {lv['icon']} {lv['label']} — בעלים: {lv['owner']}",
             "סיבות: " + ("; ".join(_clean(r["text"]) for r in triage["reasons"]) or "—")]
    if row:
        lines.append(f"שלב: {row.get('stage')} | יעד מסתמן: {row.get('fc') or _clean(row.get('fc_text'), 80) or '—'} | "
                     f"ת.פיתוח: {row.get('dev') or '—'} | שבועות בשלב: {row.get('weeks_in_stage')}"
                     + (" (לפחות)" if row.get("weeks_lower_bound") else ""))
    lines += ["", "ציר דוחות (תאריך | שלב | יעד מסתמן | ת.פיתוח | לטיפול):"]
    for t in h.get("timeline", [])[-12:]:
        lines.append(f"- {t['date']} | {t['stage']} | {t.get('fc') or _clean(t.get('fc_text'), 40) or '—'} | "
                     f"{t.get('dev') or '—'} | {_clean(t.get('to_handle'), 80) or '—'}")
    if h.get("events"):
        lines += ["", "תזוזות תאריכים:"]
        lines += [f"- {'יעד חשמול' if e['kind'] == 'forecast' else 'תכנית פיתוח'}: {e['from']} עד {e['to']}, "
                  f"{e['months']} חודשים, בשלב {e['stage']}" for e in h["events"]]
    if h.get("flags"):
        lines += ["", "קריאת סיכון מחושבת (כללים):"] + [f"- {_clean(f['text'], 240)}" for f in h["flags"]]
    if h.get("peers"):
        pe = h["peers"]
        lines.append(f"השוואה ל-{pe['n']} פרויקטים באותו שלב: חציון שבועות בשלב {pe.get('median_weeks')}, "
                     f"חציון דחייה {pe.get('median_moved_months')} חודשים.")
    if h.get("topics"):
        lines += ["", "נושאים שעלו בדיווחים:"]
        lines += [f"- {t['category']} ({t['weeks']} דיווחים)" for t in h["topics"]]
    cur = h["timeline"][-1] if h.get("timeline") else {}
    if cur.get("risks"):
        lines.append(f"עמודת הסיכונים: {_clean(cur['risks'], 300)}")
    lines += ["", f"הדיווחים השבועיים (החדש ראשון, עד {PROJECT_WEEKS}):"]
    items = [(MUST, ln) for ln in lines]
    # Older reports give way first: priority grows with age.
    items += [(HOT + n, f"- {w['week']}: {_clean(w['text'], PROJECT_TEXT_CHARS)}"
                        + (" (זהה לקודם)" if w.get("same_as_before") else ""))
              for n, w in enumerate(h.get("weekly", [])[:PROJECT_WEEKS])]
    return _pack(items, CONTEXT_CHARS["project"])


_RULES = """כללים מחייבים:
1. השתמש רק בעובדות ובמספרים שמופיעים בנתונים. אסור לחשב, להמציא או לעגל מספרים. אם חסר מידע — כתוב שחסר.
2. דרגת הטיפול של כל פרויקט כבר נקבעה בכללים קבועים. אל תשנה אותה ואל תמציא דרגה.
3. זהה פרויקט רק לפי המזהה בסוגריים המרובעים, בדיוק כפי שהוא כתוב.
4. ציטוט חייב להיות מועתק מילה במילה מדיווח שבועי שבנתונים.
5. חפש עומק: סיבת שורש משותפת לכמה פרויקטים (אותו גורם חיצוני, אותו שלב, אותו מנה״פ), סתירה בין המלל לתאריכים, תכנית פיתוח שזזה יחד עם היעד (הסתרת איחור), דיווח שחוזר על עצמו. אל תחזור על המספר — הסבר מה הוא אומר ומה עושים.
6. פעולה מומלצת = מי, מה, ועד מתי במילים (השבוע, עד הדוח הבא, תוך חודש) — לא תאריך. ברמת פירוט שמנהל יכול לבצע מחר בבוקר. הבעלים לפי דרגת הטיפול.
7. עברית בלבד. בלי markdown. בלי מרכאות כפולות רגילות בתוך טקסט — השתמש בגרשיים ״ או בגרש ׳.
8. החזר אובייקט JSON תקין בלבד."""

AREA_SYSTEM = """אתה אנליסט בכיר לאגף פרויקטים של תחנות משנה בחברת חשמל. אתה מקבל פלט של מנוע דפוסים מחושב (בלי מודל שפה) ודרגות טיפול שנקבעו בכללים, ומנסח ניתוח עומק למנהל.
""" + _RULES + """
מבנה ה-JSON:
{"headline": "משפט אחד — הדבר החשוב ביותר",
 "situation": "3-5 משפטים: תמונת המצב והמגמה",
 "findings": [{"title": "כותרת קצרה", "analysis": "2-4 משפטים: מה קורה, למה (סיבת שורש סבירה), ומה המשמעות", "projects": ["מזהה"], "action": "פעולה מומלצת", "owner": "בעלים"}],
 "patterns": ["דפוס רוחבי שחוזר בכמה פרויקטים"],
 "questions": ["שאלה חדה לשאול בישיבת הסטטוס"],
 "watch": "מה לבדוק בדוח של השבוע הבא"}
3 עד 7 ממצאים, מהחמור לקל. 0 עד 4 דפוסים. 2 עד 5 שאלות."""

PROJECT_SYSTEM = """אתה אנליסט בכיר לאגף פרויקטים של תחנות משנה בחברת חשמל. אתה מקבל את כל ההיסטוריה של פרויקט אחד (מחושבת, בלי מודל שפה) ואת דרגת הטיפול שנקבעה בכללים, ומנסח צלילת עומק.
""" + _RULES + """
מבנה ה-JSON:
{"headline": "משפט אחד — מצב הפרויקט",
 "story": "4-6 משפטים כרונולוגיים: מה קרה לפרויקט לאורך הדוחות",
 "root_cause": "סיבת השורש הסבירה לעיכוב או לסיכון, ומה מחזק אותה בנתונים",
 "evidence": [{"week": "YYYY-MM-DD", "quote": "ציטוט מדויק מהדיווח"}],
 "action": "הפעולה המומלצת",
 "owner": "בעלים",
 "questions": ["שאלה לשאול את מנה״פ"],
 "watch": "מה לבדוק בשבוע הבא"}
1 עד 4 ציטוטים. 2 עד 4 שאלות."""


def _walk_strings(obj: object, fn) -> object:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: (v if k in ("projects", "week") else _walk_strings(v, fn)) for k, v in obj.items()}
    return obj


def validate(reply: dict, context: str, allowed_ids: set[str], quote_texts: list[str]) -> dict:
    """Make the reply safe to show. Returns a new dict with `_checks`."""
    allowed_nums = _nums(context)
    checks = {"numbers_replaced": 0, "quotes_dropped": 0, "projects_dropped": 0}

    def fix_numbers(s: str) -> str:
        def sub(m: re.Match) -> str:
            if _norm_num(m.group(0)) in allowed_nums:
                return m.group(0)
            checks["numbers_replaced"] += 1
            return "[?]"
        return _NUM.sub(sub, s)

    out = _walk_strings(reply if isinstance(reply, dict) else {}, fix_numbers)
    for f in out.get("findings", []) if isinstance(out.get("findings"), list) else []:
        ids = f.get("projects") if isinstance(f.get("projects"), list) else []
        keep = [str(i) for i in ids if str(i) in allowed_ids]
        checks["projects_dropped"] += len(ids) - len(keep)
        f["projects"] = keep
    if isinstance(out.get("evidence"), list):
        corpus = _quote_norm(" ".join(quote_texts))
        kept = []
        for e in out["evidence"]:
            q = _quote_norm(str(e.get("quote", "")) if isinstance(e, dict) else "")
            if q and len(q) >= 8 and q in corpus:
                kept.append(e)
            else:
                checks["quotes_dropped"] += 1
        out["evidence"] = kept
    out["_checks"] = checks
    return out


def decorate(payload: dict, tri: dict) -> dict:
    """Attach the rule-based level to every finding (worst of its projects)."""
    for f in payload.get("findings", []) or []:
        levels = [tri[i]["level"] for i in f.get("projects", []) if i in tri]
        f["level"] = min(levels, key=it.ORDER.index) if levels else None
    return payload


# ── I/O ────────────────────────────────────────────────────────────────────

_lock = asyncio.Lock()
STATUS: dict = {"running": False, "last_error": None, "done": []}
AUTOSTART_BACKOFF_SECONDS = 1800   # a page view re-tries a failed run at most this often
_last_autostart: dict[str, float] = {}


def claim_autostart(as_of: str | None) -> bool:
    """True once per report date per backoff window — a page view may kick a
    missing analysis, but a failing model (quota) is not re-asked on every view."""
    import time
    if not as_of or STATUS["running"]:
        return False
    now = time.monotonic()
    if now - _last_autostart.get(as_of, -AUTOSTART_BACKOFF_SECONDS) < AUTOSTART_BACKOFF_SECONDS:
        return False
    _last_autostart[as_of] = now
    return True


async def _ask(system: str, context: str, kind: str) -> tuple[dict, str]:
    from app.services.claude_service import _extract_json
    from app.services.llm_router import get_last_llm_meta, llm_chat
    raw = await llm_chat(USAGE, [{"role": "system", "content": system},
                                 {"role": "user", "content": context}],
                         max_tokens=MAX_TOKENS[kind], temperature=0.2, json_mode=True,
                         reasoning_effort="low")
    provider, _ = get_last_llm_meta()
    reply = _extract_json(raw)
    if not isinstance(reply, dict):
        raise ValueError("reply is not a JSON object")
    return reply, provider


_sleep = asyncio.sleep   # swapped out in tests


async def _ask_retrying(system: str, context: str, kind: str, attempts: int = ATTEMPTS) -> tuple[dict, str]:
    """`_ask`, retried a per-minute window apart: a rate limit, a fallback
    timeout or a reply cut mid-JSON are all worth one more try a minute later."""
    for n in range(attempts):
        try:
            return await _ask(system, context, kind)
        except Exception as e:
            if n == attempts - 1:
                raise
            logger.warning(f"insight_ai: {kind} attempt {n + 1} failed ({type(e).__name__}: {str(e)[:160]}) "
                           f"— retrying in {TPM_WAIT_SECONDS}s")
            await _sleep(TPM_WAIT_SECONDS)
    raise RuntimeError("unreachable")


async def _store(session, kind: str, as_of: date, payload: dict, provider: str) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models import PatternAIAnalysis
    stmt = pg_insert(PatternAIAnalysis).values(kind=kind, as_of=as_of, payload=payload, provider=provider)
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_pattern_ai_kind_as_of",
        set_={"payload": payload, "provider": provider, "created_at": stmt.excluded.created_at}))
    await session.commit()


async def load(session, kinds: list[str], as_of: str | None) -> dict[str, dict]:
    """kind → {payload, provider, created_at} for this report date. Never raises."""
    if not kinds or not as_of:
        return {}
    try:
        from sqlalchemy import select
        from app.models import PatternAIAnalysis
        rows = (await session.execute(select(PatternAIAnalysis).where(
            PatternAIAnalysis.kind.in_(kinds), PatternAIAnalysis.as_of == date.fromisoformat(as_of)))).scalars().all()
        return {r.kind: {"payload": r.payload, "provider": r.provider, "created_at": r.created_at} for r in rows}
    except Exception as e:   # a missing panel, never a broken page
        logger.warning(f"insight_ai.load failed: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return {}


def weekly_by_identifier(frames) -> dict[str, list[tuple[str, str]]]:
    """identifier → [(week, text)] newest first."""
    if frames.weekly.empty:
        return {}
    ids = dict(zip(frames.projects["project_id"], frames.projects["identifier"]))
    out: dict[str, list[tuple[str, str]]] = {}
    for r in frames.weekly.sort_values("week_date", ascending=False).itertuples():
        ident = ids.get(r.project_id)
        if ident is not None:
            out.setdefault(ident, []).append((r.week_date.isoformat(), r.text))
    return out


async def generate_areas(force: bool = False) -> dict:
    """Division + every sector for the newest report date. Sequential (token
    budget), single-flight (a second call while one runs is a no-op)."""
    if _lock.locked():
        return {"status": "running"}
    async with _lock:
        STATUS.update(running=True, last_error=None, done=[])
        try:
            from app.database import async_session_maker
            from app.services import pattern_service as ps
            async with async_session_maker() as session:
                frames = await ps.load_frames(session)
                p = ps._plain(ps.compute_patterns(frames))
                if not p.get("as_of"):
                    return {"status": "no_data"}
                tri = it.triage_all(p)
                weekly = weekly_by_identifier(frames)
                aliases = pm_aliases(p)
                have = {} if force else await load(session, [DIVISION] + [sector_kind(k) for k in AREA_SECTORS], p["as_of"])
                jobs = [(DIVISION, lambda: division_context(p, tri, weekly, aliases), p.get("projects", []))]
                for k in AREA_SECTORS:
                    jobs.append((sector_kind(k), (lambda k=k: sector_context(p, tri, weekly, aliases, k)),
                                 [x for x in p.get("projects", []) if k in x.get("sectors", [])]))
                pending = [j for j in jobs if j[0] not in have and j[2]]
                for i, (kind, build, projects) in enumerate(pending):
                    if i:
                        # Each call takes most of a minute's token budget.
                        await _sleep(TPM_WAIT_SECONDS)
                    try:
                        ctx = build()
                        reply, provider = await _ask_retrying(AREA_SYSTEM, ctx, "area")
                        payload = decorate(validate(reply, ctx, {x["identifier"] for x in projects}, []), tri)
                        payload["_aliases"] = aliases
                        await _store(session, kind, date.fromisoformat(p["as_of"]), payload, provider)
                        STATUS["done"].append(kind)
                    except Exception as e:
                        logger.warning(f"insight_ai: {kind} failed: {type(e).__name__}: {e}")
                        STATUS["last_error"] = f"{kind}: {type(e).__name__}"
                        await session.rollback()
            return {"status": "ok", "done": list(STATUS["done"])}
        except Exception as e:
            logger.error(f"insight_ai.generate_areas failed: {e}", exc_info=True)
            STATUS["last_error"] = type(e).__name__
            return {"status": "error"}
        finally:
            STATUS["running"] = False


async def generate_project(session, frames, p: dict, identifier: str) -> dict | None:
    """Level 3 for one project, stored for this report date. None on failure."""
    from app.services import pattern_service as ps
    h = ps._plain(ps.project_history(frames, p, identifier) or {})
    if not h or not p.get("as_of"):
        return None
    tri = it.triage_all(p)
    triage = tri.get(identifier, {"level": it.FYI, "reasons": []})
    aliases = pm_aliases(p)
    if h.get("manager") and h["manager"] not in aliases:
        aliases[h["manager"]] = f"מנה״פ #{len(aliases) + 1}"
    ctx = project_context(h, triage, aliases)
    try:
        reply, provider = await _ask(PROJECT_SYSTEM, ctx, "project")
    except Exception as e:
        logger.warning(f"insight_ai: project {identifier} failed: {type(e).__name__}: {e}")
        return None
    payload = validate(reply, ctx, {identifier}, [w["text"] for w in h.get("weekly", [])])
    payload["level"] = triage["level"]
    payload["reasons"] = triage["reasons"]
    payload["_aliases"] = aliases
    try:
        await _store(session, project_kind(identifier), date.fromisoformat(p["as_of"]), payload, provider)
    except Exception as e:
        logger.warning(f"insight_ai: storing project {identifier} failed: {e}")
        await session.rollback()
    return {"payload": payload, "provider": provider, "created_at": None}


def for_display(row: dict | None) -> dict | None:
    """The stored reply with PM names restored, ready for the template."""
    if not row:
        return None
    payload = dict(row["payload"])
    aliases = payload.pop("_aliases", {}) or {}
    return {**row, "payload": restore_names(payload, aliases)}
