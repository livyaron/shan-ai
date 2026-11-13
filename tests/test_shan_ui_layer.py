"""The shared motion layer (static/css/shan_ui.css + static/js/shan_ui.js).

It is decoration: these tests hold the promises that keep it harmless — it is
loaded once from the navbar, never inside the lessons app, never fetches or
writes text, never leaves content hidden, and switches off for reduced motion.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static/css/shan_ui.css").read_text(encoding="utf-8")
JS = (ROOT / "static/js/shan_ui.js").read_text(encoding="utf-8")
NAV = (ROOT / "app/templates/_navbar.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "app/templates/login.html").read_text(encoding="utf-8")


def test_layer_is_loaded_from_the_navbar_and_not_in_the_lessons_app():
    assert NAV.count("/static/css/shan_ui.css") == 1 and NAV.count("/static/js/shan_ui.js") == 1
    guard = NAV.index("{% if not _path.startswith('/lessons') %}")
    assert guard < NAV.index("/static/css/shan_ui.css") < NAV.index("{% endif %}", guard)


def test_script_is_decoration_only():
    for banned in ("fetch(", "XMLHttpRequest", "innerHTML", "textContent", "localStorage", "document.write"):
        assert banned not in JS, banned


def test_nothing_is_left_hidden():
    """Entry animations use `backwards` fill only — an element's resting style is the page's own."""
    for rule in re.findall(r"animation:\s*shanRise[^;]*;", CSS):
        assert "backwards" in rule and "forwards" not in rule and "both" not in rule
    body = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", CSS)
    body = re.sub(r"\.shan-nav-(ink|progress)[^{]*\{[^}]*\}", "", body)     # the layer's own two markers
    body = re.sub(r"\.shan-nav::after[^{]*\{[^}]*\}", "", body)
    assert not re.search(r"opacity:\s*0\s*[;}]", body)


def test_cards_get_no_lasting_transform():
    """A transform that stays on a card re-anchors a fixed modal inside it."""
    for sel, decl in re.findall(r"([^{}]*\.card[^{}]*)\{([^}]*)\}", CSS):
        if "@" in sel:
            continue
        assert "transform" not in decl.replace("text-transform", ""), sel.strip()


def test_reduced_motion_switches_it_off():
    block = CSS[CSS.index("@media (prefers-reduced-motion: reduce)"):]
    assert "navigation: none" in block and "animation: none" in block
    assert "prefers-reduced-motion" in JS and "prefers-reduced-motion" in LOGIN


def test_tables_follow_the_dark_page():
    assert "--bs-table-bg: transparent" in CSS and "--bs-table-color: inherit" in CSS


def test_login_shows_the_emblem_unclipped_and_no_stale_model_name():
    assert "logo-emblem" in LOGIN and "object-fit: cover" not in LOGIN
    assert "llama" not in LOGIN and "UTC+3" not in LOGIN
    assert 'id="sld"' in LOGIN and 'aria-hidden="true"' in LOGIN
