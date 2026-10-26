#!/usr/bin/env python3
"""PreToolUse guard: block a commit/push that would publish a secret.

The repo is public, and real credentials have reached it before (a Railway DB
URL in docs/, keys in Railway logs). Claude Code runs this before every Bash
call and before the GitHub MCP file-writing tools. It acts only when the call
would publish something:

* ``git commit``  -> the lines being committed (staged; plus the working tree
  when the same command runs ``git add`` or ``commit -a``)
* ``git push``    -> every commit not yet on any remote
* ``mcp__github__push_files`` / ``create_or_update_file`` -> the file content

Only ADDED lines are read, so a secret already in history does not block every
later commit (rotate it instead). Exit 2 blocks the call and tells Claude why;
the secret itself is never echoed, only its first 4 characters.

False positive? Put ``secret-guard: allow`` on the same line.
Internal error (no git, odd repo) -> warn and let the call through: a broken
guard must not stop all work.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Iterable, Iterator, NamedTuple

ALLOW_MARKER = "secret-guard: allow"

# (name, pattern, group holding the secret). Anchored on the provider's own
# key format, so ordinary code does not match.
TOKEN_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("Telegram bot token", re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])"), 0),  # also inside .../bot<token>/
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b"), 0),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), 0),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"), 0),
    ("Resend API key", re.compile(r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9]{16,}\b"), 0),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"), 0),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), 0),
]

# postgres://user:PASSWORD@host — group 1 = password, group 2 = host
DB_URL = re.compile(r"\bpostgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:([^@\s]*)@([^:/\s]+)")
# Hosts that are real infrastructure: any password there is a leak.
REAL_DB_HOST = re.compile(r"rlwy\.net|railway\.(?:app|internal)|supabase|amazonaws|neon\.tech", re.I)
PLACEHOLDER = re.compile(
    r"^(?:|pass|password|secret|changeme|xxx+|\*+|your_\w*|<[^>]*>|\$\{?[\w.{}-]*\}?)$", re.I
)

# NAME=value lines for the secrets this project actually uses (.env style).
SECRET_NAMES = (
    r"GOOGLE_AI_API_KEY|GEMINI_API_KEY|GROQ_API_KEY|ANTHROPIC_API_KEY|TELEGRAM_BOT_TOKEN"
    r"|WEBHOOK_SECRET_TOKEN|RESEND_API_KEY|INVENTORY_API_KEY|LESSONS_SRC_KEY|JWT_SECRET_KEY"
    r"|POSTGRES_PASSWORD|LESSONS_PGRST_PASSWORD|RAILWAY_TOKEN|RAILWAY_API_TOKEN"
)
ENV_ASSIGN = re.compile(
    rf"\b(?:{SECRET_NAMES})\b\s*[=:]\s*[\"']?([A-Za-z0-9_\-+/=.@:]{{12,}})[\"']?(?!\s*\()"
)
PLACEHOLDER_WORDS = re.compile(r"change-?me|your_|example|dummy|fake|placeholder|xxxx", re.I)
CODE_REF = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")  # os.environ / settings.x

SECRET_FILES = re.compile(r"(?:^|/)\.env(?:\.[\w-]+)?$")
SAFE_FILES = re.compile(r"\.(?:example|sample|template)$")


class Hit(NamedTuple):
    where: str
    kind: str
    preview: str


def _mask(secret: str) -> str:
    return secret[:4] + "…" if len(secret) > 4 else "…"


def scan_line(line: str) -> Iterator[tuple[str, str]]:
    """Yield (kind, masked) for every secret on one line."""
    if ALLOW_MARKER in line:
        return
    for kind, pattern, group in TOKEN_PATTERNS:
        for m in pattern.finditer(line):
            yield kind, _mask(m.group(group))
    for m in DB_URL.finditer(line):
        password, host = m.group(1), m.group(2)
        if REAL_DB_HOST.search(host) and password and not password.startswith(("$", "<")):
            yield "Database URL with password", _mask(password)
        elif not PLACEHOLDER.match(password):
            yield "Database URL with password", _mask(password)
    for m in ENV_ASSIGN.finditer(line):
        value = m.group(1)
        if CODE_REF.match(value) or PLACEHOLDER.match(value) or PLACEHOLDER_WORDS.search(value):
            continue
        yield "Secret assigned in plain text", _mask(value)


def scan_diff(diff: str) -> list[Hit]:
    """Scan the added lines of a unified diff (``git diff`` / ``git log -p``)."""
    hits: list[Hit] = []
    path, lineno = "?", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+"):
            hits += [Hit(f"{path}:{lineno}", k, p) for k, p in scan_line(raw[1:])]
            lineno += 1
        elif not raw.startswith("-"):
            lineno += 1
    return hits


def scan_text(path: str, text: str) -> list[Hit]:
    return [
        Hit(f"{path}:{n}", k, p)
        for n, line in enumerate(text.splitlines(), 1)
        for k, p in scan_line(line)
    ]


def secret_file_hits(paths: Iterable[str]) -> list[Hit]:
    return [
        Hit(p, "Environment file", "the whole file")
        for p in paths
        if SECRET_FILES.search(p) and not SAFE_FILES.search(p)
    ]


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _git_cwd(command: str, base: str | None) -> str | None:
    m = re.search(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
    return m.group(1).strip("\"'") if m else base


def _segments(command: str) -> list[str]:
    return [s for s in re.split(r"&&|\|\||;|\n", command) if s.strip()]


def _is_git(segment: str, verb: str) -> bool:
    return re.search(rf"\bgit\b(?:\s+-C\s+\S+|\s+-c\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+{verb}\b", segment) is not None


def scan_commit(command: str, cwd: str | None) -> list[Hit]:
    segs = _segments(command)
    commit = next(s for s in segs if _is_git(s, "commit"))
    hits = scan_diff(_git("diff", "--cached", "-U0", cwd=cwd))
    added = _git("diff", "--cached", "--name-only", "--diff-filter=A", cwd=cwd).split()
    stages_more = any(_is_git(s, "add") for s in segs) or re.search(
        r"\s-(?:[a-zA-Z]*a[a-zA-Z]*)\b|\s--all\b", commit
    )
    if stages_more:
        hits += scan_diff(_git("diff", "-U0", cwd=cwd))
        for path in _git("ls-files", "--others", "--exclude-standard", cwd=cwd).split("\n"):
            if not path:
                continue
            added.append(path)
            try:
                with open(f"{cwd or '.'}/{path}", encoding="utf-8") as fh:
                    hits += scan_text(path, fh.read())
            except (OSError, UnicodeDecodeError):
                continue
    return hits + secret_file_hits(added)


def scan_push(cwd: str | None) -> list[Hit]:
    unpushed = ["HEAD", "--not", "--remotes"]
    hits = scan_diff(_git("log", "-p", "-U0", "--format=", *unpushed, cwd=cwd))
    added = _git("log", "--format=", "--name-only", "--diff-filter=A", *unpushed, cwd=cwd).split()
    return hits + secret_file_hits(added)


def scan_mcp(tool_input: dict) -> list[Hit]:
    files = tool_input.get("files") or [tool_input]
    hits: list[Hit] = []
    for f in files:
        path = str(f.get("path", "?"))
        hits += scan_text(path, str(f.get("content", "")))
        hits += secret_file_hits([path])
    return hits


def check(event: dict) -> list[Hit]:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool.startswith("mcp__github__"):
        return scan_mcp(tool_input)
    command = str(tool_input.get("command", ""))
    cwd = _git_cwd(command, event.get("cwd"))
    hits: list[Hit] = []
    if any(_is_git(s, "commit") for s in _segments(command)):
        hits += scan_commit(command, cwd)
    if any(_is_git(s, "push") for s in _segments(command)):
        hits += scan_push(cwd)
    return hits


def main() -> int:
    try:
        hits = check(json.load(sys.stdin))
    except Exception as exc:  # never let a guard bug stop all work
        print(f"[secret-guard] skipped, could not scan: {exc}", file=sys.stderr)
        return 0
    if not hits:
        return 0
    lines = [f"  - {h.where}: {h.kind} ({h.preview})" for h in dict.fromkeys(hits)]
    print(
        "[secret-guard] BLOCKED: this would publish a secret to the public repo:\n"
        + "\n".join(lines)
        + "\nMove it to an environment variable / Railway variable. If the line is a"
        " harmless placeholder, add 'secret-guard: allow' on it. A secret that was"
        " already pushed must be ROTATED - removing it from the file is not enough.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
