"""Short-TTL in-process cache for the ◈ ניתוח AI dashboard panels.

Every press of an AI-analysis button used to be a fresh Groq call, so opening
the same panel twice cost two LLM round-trips and two multi-second waits. These
panels are cheap to rebuild once the queries behind them are bounded, and they
go stale as the data moves, so a short TTL is the right shape here — unlike the
חדר מבצעים day-cache in missions_report_service, which is a once-a-day artifact
shared by everyone and therefore persisted to Postgres.

Process-local on purpose: a lost cache costs one rebuild, nothing more.
"""

import logging
import time

logger = logging.getLogger(__name__)

TTL_SECONDS = 900          # 15 minutes — long enough to cover a browsing session
MAX_ENTRIES = 200          # hard ceiling; the panels are small dicts

_cache: dict[str, tuple[float, dict]] = {}


def _evict() -> None:
    """Drop expired entries, then the oldest ones if still over the ceiling."""
    now = time.time()
    for key in [k for k, (ts, _) in _cache.items() if now - ts >= TTL_SECONDS]:
        _cache.pop(key, None)
    if len(_cache) > MAX_ENTRIES:
        for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[: len(_cache) - MAX_ENTRIES]:
            _cache.pop(key, None)


def get(key: str) -> dict | None:
    """Fresh cached payload, or None. Expired entries are dropped on the way out."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts >= TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def put(key: str, value: dict) -> dict:
    # Insert first, then evict: the entry just written is the newest, so it is
    # never the one trimmed, and the ceiling holds exactly.
    _cache[key] = (time.time(), value)
    _evict()
    return value


def invalidate(key: str | None = None) -> None:
    """Drop one entry, or everything when key is None."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


def stats() -> dict:
    """Diagnostics: how many panels are warm right now."""
    now = time.time()
    return {
        "entries": len(_cache),
        "ttl_seconds": TTL_SECONDS,
        "keys": sorted(k for k, (ts, _) in _cache.items() if now - ts < TTL_SECONDS),
    }
