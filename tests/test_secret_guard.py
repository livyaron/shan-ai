"""The Claude Code secret guard (.claude/hooks/secret_guard.py) blocks a commit or
push that would publish a credential to this public repo.

Every fake secret is assembled at runtime: a literal one in this file would be
exactly what the guard exists to stop.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "secret_guard.py"
_spec = importlib.util.spec_from_file_location("secret_guard", GUARD)
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

GOOGLE = "AIza" + "B" * 35
TELEGRAM = "7123456789:" + "A" * 35
GROQ = "gsk_" + "c" * 48
REAL_DB = "postgresql://shan_user:" + "s3cret_pw" + "@interchange.proxy." + "rlwy.net:15720/shan_ai"


def kinds(line: str) -> list[str]:
    return [k for k, _ in sg.scan_line(line)]


def test_provider_keys_are_caught():
    assert kinds(f"key = '{GOOGLE}'") == ["Google API key"]
    assert kinds(f"url = 'https://api.telegram.org/bot{TELEGRAM}/x'") == ["Telegram bot token"]
    assert kinds(f"GROQ = '{GROQ}'") == ["Groq API key"]


def test_a_railway_db_url_with_a_password_is_caught():
    assert kinds(f"psql \"{REAL_DB}\" -c 'select 1'") == ["Database URL with password"]


def test_placeholders_and_code_references_pass():
    for line in (
        "postgresql+asyncpg://shan_user:@localhost:5432/shan_ai",
        "postgresql+asyncpg://shan_user:${POSTGRES_PASSWORD}@postgres:5432/shan_ai",
        "postgresql://user:pass@host:port/db",
        "postgresql://shan_user:your_db_password_here@localhost:5432/shan_ai",
        "postgres://u:<LESSONS_PGRST_PASSWORD>@host/db",
        "JWT_SECRET_KEY=change-me-random-hex",
        "GROQ_API_KEY = settings.groq_api_key",
        "TELEGRAM_BOT_TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN')",
        'psql "$RAILWAY_DATABASE_URL" -c "select 1"',
    ):
        assert kinds(line) == [], line


def test_the_secret_is_never_echoed():
    [(kind, preview)] = list(sg.scan_line(GOOGLE))
    assert GOOGLE not in preview and preview.startswith("AIza")


def test_allow_marker_silences_a_line():
    assert kinds(f"{GOOGLE}  # {sg.ALLOW_MARKER}") == []


def test_only_added_lines_of_a_diff_count():
    diff = (
        "+++ b/app/x.py\n@@ -1,2 +1,2 @@\n"
        f"-old = '{GOOGLE}'\n"
        " context\n"
        f"+new = '{GROQ}'\n"
    )
    [hit] = sg.scan_diff(diff)
    assert hit.where == "app/x.py:2" and hit.kind == "Groq API key"


def test_env_files_are_caught_but_examples_are_not():
    assert [h.where for h in sg.secret_file_hits([".env", "a/.env.local", ".env.example"])] == [
        ".env",
        "a/.env.local",
    ]


def _run_hook(event: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_commit_of_a_staged_secret_is_blocked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text(f"KEY = '{GOOGLE}'\n")
    _git(repo, "add", "app.py")
    res = _run_hook({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "git commit -m x"}})
    assert res.returncode == 2
    assert "app.py:1" in res.stderr and GOOGLE not in res.stderr


def test_add_and_commit_in_one_command_is_scanned(tmp_path):
    repo = _repo(tmp_path)
    (repo / "notes.md").write_text(f"psql \"{REAL_DB}\"\n")
    cmd = "git add -A && git commit -m notes"
    res = _run_hook({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}})
    assert res.returncode == 2 and "notes.md:1" in res.stderr


def test_push_scans_commits_not_on_a_remote(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text(f"T = '{TELEGRAM}'\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "x")
    res = _run_hook({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "git push -u origin b"}})
    assert res.returncode == 2 and "Telegram bot token" in res.stderr


def test_clean_commit_and_other_commands_pass(tmp_path):
    repo = _repo(tmp_path)
    (repo / "ok.py").write_text("x = 1\n")
    _git(repo, "add", "ok.py")
    for cmd in ("git commit -m ok", "ls -la", "grep -r AIza ."):
        res = _run_hook({"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}})
        assert res.returncode == 0, (cmd, res.stderr)


def test_github_mcp_file_writes_are_scanned():
    event = {
        "tool_name": "mcp__github__push_files",
        "tool_input": {"files": [{"path": "cfg.py", "content": f"K = '{GROQ}'\n"}]},
    }
    res = _run_hook(event)
    assert res.returncode == 2 and "cfg.py:1" in res.stderr


def test_a_broken_event_never_blocks():
    res = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True)
    assert res.returncode == 0 and "skipped" in res.stderr
