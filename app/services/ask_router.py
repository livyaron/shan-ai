"""Single entry point for answering a user question.

Used by the /dashboard/ask web router, the Telegram polling handler, and the
per-question repair loop. Eval = production from this module on.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from app.services.knowledge_service import normalize_hebrew


@dataclass
class AnswerResult:
    answer: str
    sources_used: list[dict]
    log_id: Optional[int]
    path: str          # "correction_pin" | "decision" | "project_tools" | "rag"
    intent: Optional[str]
    param: Optional[str]


def _normalize_q_hash(question: str) -> str:
    """sha256 of Hebrew-normalized question. Used as a hash key for pin/override lookups."""
    return hashlib.sha256(normalize_hebrew(question.strip()).encode("utf-8")).hexdigest()
