"""Login page: user search + "remember me on this device" (2026-10-15).

The remembered value is the user id only — a convenience that pre-selects a name.
It must never hold a password and never skip the password check.
"""

import re
from pathlib import Path

import jinja2

TEMPLATE = Path("app/templates/login.html")


class _User:
    def __init__(self, id_: int, username: str, job_title: str | None = None) -> None:
        self.id, self.username, self.job_title = id_, username, job_title


def _render() -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader("app/templates"))
    users = [_User(1, "ירון ליב", "מנהל אגף"), _User(2, "דנה כהן")]
    return env.get_template("login.html").render(request=None, users=users, role_labels={},
                                                 error=None, next_url=None)


def test_page_has_search_remember_and_switch_user():
    html = _render()
    assert 'id="user-search"' in html and 'id="user-select"' in html
    assert 'id="remember-me"' in html and "זכור אותי במכשיר זה" in html
    assert 'id="switch-user"' in html and "החלפת משתמש" in html
    # The password is still a required field — remembering a name never logs anyone in.
    assert re.search(r'<input type="password" name="password" required', html)
    assert '<option value="1">' in html and '<option value="2">' in html


def test_only_the_user_id_is_stored_locally():
    src = TEMPLATE.read_text(encoding="utf-8")
    sets = re.findall(r"localStorage\.setItem\(([^)]*)\)", src)
    assert sets == ["KEY, v"], sets
    assert "store.set(select.value)" in src
    assert "password" not in "".join(re.findall(r"store\.set\([^)]*\)", src))
    assert "sessionStorage" not in src and "document.cookie" not in src


def test_a_stale_remembered_id_is_forgotten():
    src = TEMPLATE.read_text(encoding="utf-8")
    # Only an id present in the server-rendered list (exists + has a role) is pre-selected.
    assert "all.find(u => u.value === savedId)" in src
    assert "store.clear();   // the user no longer exists" in src
