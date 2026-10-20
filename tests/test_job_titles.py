"""Gender-neutral PM title (user 2026-10-20). Pure, no DB."""
import pytest

from app.services.job_titles import migration_statements, neutral_title


@pytest.mark.parametrize(("typed", "stored"), [
    ("מנהלת פרויקט", "מנהל/ת פרויקט"),
    ("מנהל פרויקט", "מנהל/ת פרויקט"),
    ("  מנהלת   פרויקט ", "מנהל/ת פרויקט"),
    ("מנהלת פרויקטים", "מנהל/ת פרויקטים"),
    ("מנהל/ת פרויקט", "מנהל/ת פרויקט"),        # already neutral — unchanged
    ("מהנדס חשמל בכיר", "מהנדס חשמל בכיר"),     # any other title is kept as typed
    ("", None),
    (None, None),
])
def test_neutral_title(typed, stored):
    assert neutral_title(typed) == stored


def test_startup_migration_rewrites_only_the_gendered_titles():
    olds = {p["old"] for _, p in migration_statements()}
    assert olds == {"מנהל פרויקט", "מנהלת פרויקט", "מנהל פרויקטים", "מנהלת פרויקטים"}
    assert all(p["new"].startswith("מנהל/ת") for _, p in migration_statements())
    assert all(sql.startswith("UPDATE users SET job_title") for sql, _ in migration_statements())


def test_every_save_path_stores_the_neutral_form():
    from pathlib import Path
    src = Path("app/routers/dashboard.py").read_text(encoding="utf-8")
    assert "job_title or None" not in src
    assert src.count("neutral_title(job_title)") == 3
