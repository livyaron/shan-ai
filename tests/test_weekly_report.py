"""Tests for weekly report v2 — ReportHistory model, service API, cron skip."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Task 1 ──────────────────────────────────────────────────────────────────

def test_report_history_model_importable():
    from app.models import ReportHistory
    row = ReportHistory(user_id=1, sections={"prologue": "hi"}, sent_via="telegram")
    assert row.user_id == 1
    assert row.sections["prologue"] == "hi"
    assert row.raw_data is None  # default
