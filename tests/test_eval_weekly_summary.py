from app.services.eval_cron import format_eval_summary


def test_format_eval_summary_with_delta():
    cur = {"n_probes": 50, "n_pass": 40, "started_at": "08/06"}
    prev = {"n_probes": 50, "n_pass": 45}
    msg = format_eval_summary(cur, prev, newly_failing=["מתי חישמול תחנה X?"])
    assert "80%" in msg
    assert "90%" in msg
    assert "מתי חישמול תחנה X?" in msg
    assert msg.startswith("‏")


def test_format_eval_summary_no_previous():
    msg = format_eval_summary({"n_probes": 10, "n_pass": 7, "started_at": "08/06"}, None, [])
    assert "70%" in msg
