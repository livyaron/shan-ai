"""Agent 3 — Repair Proposer.

Takes a batch of (probe, verdict) pairs from the Judge, clusters the failures by
shared characteristics, and asks an LLM to produce ONE structured patch per cluster.
Patches are stored as RepairProposal rows with status='pending' — never applied here.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuerySynonym, RepairProposal
from app.services.llm_router import llm_chat
from app.services.eval_judge_service import Verdict
from app.services.eval_probe_service import ProbeQuestion

logger = logging.getLogger(__name__)


PATCH_TYPES = {"add_synonym", "add_abbreviation", "prompt_patch", "stop_word_remove", "field_alias"}
RISK_LEVELS = {"low", "medium", "high"}


@dataclass
class FailureItem:
    probe: ProbeQuestion
    verdict: Verdict
    log_id: int | None = None
    answer: str = ""


@dataclass
class Cluster:
    failure_type: str
    dominant_token: str | None
    items: list[FailureItem] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.items)


_HEBREW_RE = re.compile(r"[֐-׿]+(?:[\"'][֐-׿]+)?")
_TRIVIAL = {
    "מה", "כל", "של", "על", "את", "הוא", "היא", "יש", "אין",
    "פרויקט", "פרויקטים", "בפרויקט", "תאריך", "מנהל", "סטטוס",
    "כמה", "מתי", "איזה", "אילו", "מי", "האם", "תן", "הצג",
}


def _dominant_token(items: list[FailureItem]) -> str | None:
    """Most common 3+ char Hebrew token across questions, excluding trivial words."""
    counter: Counter[str] = Counter()
    for it in items:
        text = it.probe.question or ""
        for tok in _HEBREW_RE.findall(text):
            if len(tok) >= 3 and tok not in _TRIVIAL:
                counter[tok] += 1
    if not counter:
        return None
    tok, n = counter.most_common(1)[0]
    return tok if n >= 2 else None


def cluster_failures(items: list[FailureItem]) -> list[Cluster]:
    """Group FAIL/PARTIAL items by (failure_type, dominant_token). Singletons drop unless severe."""
    by_type: dict[str, list[FailureItem]] = {}
    for it in items:
        if it.verdict.verdict == "PASS":
            continue
        key = it.verdict.failure_type or "WRONG_DATA"
        by_type.setdefault(key, []).append(it)

    clusters: list[Cluster] = []
    for ft, members in by_type.items():
        # Sub-cluster by dominant token across the failure_type bucket
        token = _dominant_token(members)
        clusters.append(Cluster(failure_type=ft, dominant_token=token, items=members))

    # Sort clusters by size desc; the loop's repair budget consumes the biggest first.
    clusters.sort(key=lambda c: c.size, reverse=True)
    return clusters


_REPAIR_SYSTEM_PROMPT = """אתה מנוע תיקון אוטומטי לבוט עברית.
מקבל אשכול של שאלות שכשלו, סיווג הכשל, וקונפיגורציה נוכחית של המערכת.
מטרה: להציע תיקון מבני יחיד שעשוי לפתור את האשכול.

החזר JSON בלבד:
{
  "type": "add_synonym" | "add_abbreviation" | "prompt_patch" | "stop_word_remove" | "field_alias",
  "patch": <ראה למטה לפי הסוג>,
  "rationale": "<מה התיקון אמור לפתור>",
  "risk": "low" | "medium" | "high"
}

מבנה patch לפי סוג:
- add_synonym:       {"original": "<מונח קנוני>", "synonyms": ["<חלופה1>", "<חלופה2>", ...]}
- add_abbreviation:  {"key": "<ראשי תיבות>", "value": "<צורה מלאה>"}
- prompt_patch:      {"usage": "rag_specific" | "rag_list", "content": "<פרומפט מערכת חדש מלא בעברית>"}
- stop_word_remove:  {"tokens": ["<מילה1>", "<מילה2>"]}      # מילים שלא צריכות יותר להיות ב-stop list
- field_alias:       {"column": "<שם עמודה>", "aliases": ["<שם בעברית>", ...]}

שיקולי risk:
- low: סינונים/מילונים (synonym, abbreviation, field_alias, stop_word_remove קצר)
- medium: prompt_patch קצר שלא משנה כללי חובה ממוספרים
- high: prompt_patch ארוך, או שמשנה רשימת "כללי חובה" ממוספרים, או חורג מ-1500 תווים

הנחיות:
- תיקון אחד בלבד! לא רשימה.
- אם אין תיקון בטוח, החזר {"type": "add_synonym", "patch": {"original":"","synonyms":[]}, "rationale": "...", "risk": "low"}.
- ללא מרקדאון, JSON בלבד.
"""


async def _config_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Read current synonyms + abbreviations + stop-word drops to give the LLM context."""
    # Import the module (not bound names) so we always see the live caches —
    # _ensure_eval_caches rebinds the globals, so `from ... import name` captures stale snapshots.
    from app.services import knowledge_service as ks
    await ks._ensure_eval_caches(session)

    syn_stmt = select(QuerySynonym).where(
        ~QuerySynonym.original.in_([
            "__hebrew_abbrevs__", "__stop_word_drops__", "__global_instructions__",
        ])
    ).limit(50)
    syn_rows = (await session.execute(syn_stmt)).scalars().all()
    return {
        "current_synonyms": [{"original": r.original, "synonyms": r.synonyms} for r in syn_rows],
        "current_abbreviations": {**ks.HEBREW_ABBREVS, **(ks._DB_ABBREVS_CACHE or {})},
        "current_stop_word_drops": sorted(list(ks._DB_STOP_WORD_DROPS_CACHE or set())),
    }


def _validate_patch(data: dict) -> tuple[str, dict, str, str] | None:
    """Coerce + validate the LLM JSON. Returns (type, patch, rationale, risk) or None."""
    t = (data.get("type") or "").strip()
    if t not in PATCH_TYPES:
        return None
    patch = data.get("patch")
    if not isinstance(patch, dict):
        return None
    risk = (data.get("risk") or "medium").strip().lower()
    if risk not in RISK_LEVELS:
        risk = "medium"
    rationale = (data.get("rationale") or "")[:1000]

    # Per-type structural validation
    if t == "add_synonym":
        if not isinstance(patch.get("synonyms"), list) or not patch.get("original"):
            return None
    elif t == "add_abbreviation":
        if not patch.get("key") or not patch.get("value"):
            return None
    elif t == "prompt_patch":
        if patch.get("usage") not in ("rag_specific", "rag_list"):
            return None
        content = patch.get("content") or ""
        if len(content) < 50:
            return None
        if len(content) > 1500 or "כללי חובה" in content:
            risk = "high"
    elif t == "stop_word_remove":
        if not isinstance(patch.get("tokens"), list) or not patch["tokens"]:
            return None
    elif t == "field_alias":
        if not patch.get("column") or not isinstance(patch.get("aliases"), list):
            return None
    return t, patch, rationale, risk


async def propose_fix(
    session: AsyncSession,
    cluster: Cluster,
    eval_run_id: int | None = None,
) -> RepairProposal | None:
    """Ask the Repair LLM for ONE patch covering the cluster. Persists as pending RepairProposal."""
    config = await _config_snapshot(session)
    cluster_payload = {
        "failure_type": cluster.failure_type,
        "dominant_token": cluster.dominant_token,
        "size": cluster.size,
        "examples": [
            {
                "question": it.probe.question,
                "answer": (it.answer or "")[:300],
                "evidence": it.verdict.evidence,
                "target_project": it.probe.target_project,
                "target_field": it.probe.target_field,
            }
            for it in cluster.items[:8]
        ],
    }
    payload = {"cluster": cluster_payload, "config": config}

    try:
        raw = await llm_chat(
            "eval_repair",
            messages=[
                {"role": "system", "content": _REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=1500,
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as e:
        logger.error(f"propose_fix LLM call failed: {e}")
        return None

    valid = _validate_patch(data)
    if not valid:
        logger.info(f"Repair agent returned invalid patch for {cluster.failure_type}: {data}")
        return None
    t, patch, rationale, risk = valid

    predicted_log_ids = [it.log_id for it in cluster.items if it.log_id]

    proposal = RepairProposal(
        eval_run_id=eval_run_id,
        type=t,
        patch_json=patch,
        rationale=rationale,
        risk=risk,
        predicted_log_ids=predicted_log_ids,
        status="pending",
    )
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return proposal
