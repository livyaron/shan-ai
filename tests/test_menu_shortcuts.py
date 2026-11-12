"""A message opens a menu only when it IS the menu button (2026-11-12). No database.

"כמה פרויקטים מנהל איתן אוחיון?" got the projects menu ("סה״כ: 265 פרוייקטים
פעילים") instead of an answer, because any message containing the word did.
"""
import inspect

import pytest

from app.services.telegram_routing import menu_shortcut


@pytest.mark.parametrize("text,menu", [
    ("📁 פרוייקטים", "projects"), ("פרויקטים", "projects"), ("  פרוייקטים ", "projects"),
    ("📋 החלטות", "decisions"), ("החלטות", "decisions"),
    ("📊 דוח שלי", "my_report"), ("📊 דוח פרויקטים", "projects_report"),
    ("👥 דוח צוות", "team_report"), ("🎯 חדר מבצעים", "missions"), ("משימות", "missions"),
])
def test_the_button_opens_its_menu(text, menu):
    assert menu_shortcut(text) == menu


@pytest.mark.parametrize("text", [
    "כמה פרויקטים מנהל איתן אוחיון?",
    "כמה פרויקטים מנהל איתן אוחיון",
    "אילו פרויקטים מאחרים?",
    "מה ההחלטות שממתינות לאישור שלי?",
    "אילו החלטות התקבלו בפרויקט רעות",
    "מה המשימות בפרויקט X?",
    "תכין לי דוח צוות על השבוע האחרון",
    "פרויקטים 2026", "פרויקטים?", "", "שלום",
])
def test_a_question_that_mentions_the_word_is_not_a_menu(text):
    assert menu_shortcut(text) is None


def test_every_reply_keyboard_button_is_a_known_menu():
    from types import SimpleNamespace
    from app.models import RoleEnum
    from app.services import telegram_polling as tp
    for role in RoleEnum:
        if role == RoleEnum.VIEWER:
            continue
        kb = tp._main_reply_keyboard(SimpleNamespace(role=role, is_admin=False))
        for row in kb.keyboard:
            for button in row:
                label = getattr(button, "text", button)
                assert menu_shortcut(label) is not None, label


def test_the_handler_no_longer_matches_by_containment():
    from app.services import telegram_polling as tp
    src = inspect.getsource(type(tp.telegram_bot).handle_message)
    for word in ('"החלטות" in text', '"פרויקטים" in text', '"פרוייקטים" in text',
                 '"דוח צוות" in text', '"דוח שלי" in text'):
        assert word not in src, word
    assert src.index("_menu_shortcut(text)") < src.index("_agent.engine_on(session, user)")
