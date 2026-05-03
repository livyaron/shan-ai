"""Eval-loop scheduler: nightly auto-run + abbrev sync to source code.

Two jobs:
  03:00 UTC — run_cycle(n_probes=20)            (nightly Q&A self-audit)
  03:30 UTC — sync_abbrevs_to_code()            (mirror DB abbrevs into knowledge_service.py)

Single-line install in app/main.py: `eval_cron.start_scheduler()`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import async_session_maker
from app.models import QuerySynonym

logger = logging.getLogger(__name__)

_KS_PATH = Path(__file__).resolve().parent / "knowledge_service.py"
_BEGIN = "# BEGIN_AUTOGEN_ABBREVS"
_END = "# END_AUTOGEN_ABBREVS"

_scheduler: AsyncIOScheduler | None = None


async def _nightly_eval_run() -> None:
    """Wrapper so we can import run_cycle lazily (avoids circular import at boot)."""
    try:
        from app.routers.eval_loop import run_cycle
        run_id = await run_cycle(n_probes=20, seed_failures=True, triggered_by_user_id=None)
        logger.info(f"eval_cron: nightly run completed, eval_run_id={run_id}")
    except Exception as e:
        logger.exception(f"eval_cron: nightly run failed: {e}")


async def sync_abbrevs_to_code() -> None:
    """Rewrite the HEBREW_ABBREVS literal block between sentinel comments.

    Reads the QuerySynonym row keyed `__hebrew_abbrevs__`, parses 'k=v' entries,
    and writes the merged block back. Static fallback entries inside the file
    (those present before the loop ever ran) are preserved by re-reading and
    merging — DB additions win on conflict.
    """
    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(QuerySynonym).where(QuerySynonym.original == "__hebrew_abbrevs__")
            )).scalar_one_or_none()
            db_pairs: dict[str, str] = {}
            if row and row.synonyms:
                for entry in row.synonyms:
                    if isinstance(entry, str) and "=" in entry:
                        k, v = entry.split("=", 1)
                        db_pairs[k.strip()] = v.strip()

        if not _KS_PATH.exists():
            logger.warning(f"sync_abbrevs_to_code: knowledge_service.py not found at {_KS_PATH}")
            return

        text = _KS_PATH.read_text(encoding="utf-8")

        # Extract current static block to preserve human-curated entries.
        m = re.search(rf"{re.escape(_BEGIN)}.*?{re.escape(_END)}", text, re.DOTALL)
        if not m:
            logger.warning("sync_abbrevs_to_code: sentinel comments not found — skipping write")
            return

        # Parse existing static dict body to harvest its current entries.
        static_pairs = _parse_static_dict(text)
        merged = {**static_pairs, **db_pairs}

        new_block = _render_block(merged)
        new_text = text[:m.start()] + new_block + text[m.end():]
        if new_text == text:
            logger.info("sync_abbrevs_to_code: no changes")
            return

        _KS_PATH.write_text(new_text, encoding="utf-8")
        logger.info(f"sync_abbrevs_to_code: wrote {len(merged)} entries to {_KS_PATH.name}")

    except Exception as e:
        logger.exception(f"sync_abbrevs_to_code failed: {e}")


def _parse_static_dict(file_text: str) -> dict[str, str]:
    """Pull the current contents of the autogen block as {key: value}."""
    m = re.search(rf"{re.escape(_BEGIN)}(.*?){re.escape(_END)}", file_text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    pairs: dict[str, str] = {}
    # Match lines like:  'foo': 'bar',    or   "foo": "bar",
    for k, v in re.findall(r"""['"]([^'"\n]+)['"]\s*:\s*['"]([^'"\n]+)['"]""", body):
        pairs[k] = v
    return pairs


def _render_block(pairs: dict[str, str]) -> str:
    """Render the autogen sentinel block from a merged dict."""
    lines = [_BEGIN, "HEBREW_ABBREVS = {"]
    for k, v in pairs.items():
        # Use double-quoted Python literal — escape any double-quotes inside.
        ke = k.replace('\\', '\\\\').replace('"', '\\"')
        ve = v.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'    "{ke}": "{ve}",')
    lines.append("}")
    lines.append(_END)
    return "\n".join(lines)


def start_scheduler() -> None:
    """Idempotent: install the two cron jobs on the running asyncio loop."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.add_job(_nightly_eval_run, "cron", hour=3, minute=0, id="eval_nightly")
        _scheduler.add_job(sync_abbrevs_to_code, "cron", hour=3, minute=30, id="abbrev_sync")
        _scheduler.start()
        logger.info("eval_cron: scheduler started — eval at 03:00 UTC, abbrev sync at 03:30 UTC")
    except Exception as e:
        logger.exception(f"eval_cron: failed to start scheduler: {e}")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
