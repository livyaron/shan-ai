"""The Thursday weekly report (2026-11-12). No database.

Its first scheduled run in weeks failed to parse for nearly every user (the
reasoning model spent the token budget thinking; the JSON came back cut), and
each of them was sent "⚠️ שגיאה בייצור הדוח" as their weekly report.
"""
import inspect

from app.services import weekly_report_service as wr


def test_a_fallback_report_is_recognised():
    assert wr.report_failed(dict(wr._FALLBACK_SECTIONS)) is True
    good = {"prologue": "שבוע טוב", "decisions": "x", "summary": None, "delta": None}
    assert wr.report_failed(good) is False
    # the model answered but left the prologue empty → prologue falls back, the rest is real
    partial = {**wr._FALLBACK_SECTIONS, "decisions": "3 החלטות השבוע"}
    assert wr.report_failed(partial) is False


def test_the_cron_does_not_send_a_failed_report():
    src = inspect.getsource(wr.send_weekly_reports_cron)
    assert src.index("if report_failed(sections):") < src.index("await send_report_to_user(")
    assert "continue" in src[src.index("if report_failed(sections):"):src.index("await send_report_to_user(")]


def test_the_model_is_asked_for_json_without_spending_the_budget_on_reasoning():
    src = inspect.getsource(wr.generate_report_for_user)
    call = src[src.index('"weekly_report"'):src.index("cleaned = raw_response.strip()")]
    assert 'reasoning_effort="low"' in call and "json_mode=True" in call
