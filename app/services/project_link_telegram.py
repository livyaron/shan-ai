"""Telegram side of the optional mission/decision → project link (project_links).

Callbacks (all well inside Telegram's 64-byte limit):
  pl:o:{k}:{id}:{origin}:{page}  open the picker (suggestions)   k = m | d
  pl:q:{k}:{id}                  ask for a typed search
  pl:s:{k}:{id}:{project_id}     link (0 = clear)
Pure keyboard/text builders — the handler lives in telegram_polling.
"""
from __future__ import annotations

import html as _html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

KINDS = {"m": "משימה", "d": "החלטה"}
BUTTON_NAME_CHARS = 32


def back_cb(kind: str, obj_id: int, origin: str = "my", page: int | str = 0) -> str:
    """The card the picker returns to."""
    return f"{'om' if kind == 'm' else 'dm'}:d:{obj_id}:{origin}:{page}"


def _button(kind: str, obj_id: int, p: dict[str, Any]) -> InlineKeyboardButton:
    name = (p.get("name") or "")[:BUTTON_NAME_CHARS]
    return InlineKeyboardButton(f"🏗 {p['identifier']}{' · ' + name if name else ''}",
                                callback_data=f"pl:s:{kind}:{obj_id}:{p['id']}")


def picker_keyboard(kind: str, obj_id: int, projects: list[dict[str, Any]], linked: bool,
                    back: str) -> InlineKeyboardMarkup:
    rows = [[_button(kind, obj_id, p)] for p in projects]
    rows.append([InlineKeyboardButton("🔎 חיפוש לפי מזהה / שם", callback_data=f"pl:q:{kind}:{obj_id}")])
    if linked:
        rows.append([InlineKeyboardButton("🚫 הסר קישור", callback_data=f"pl:s:{kind}:{obj_id}:0")])
    rows.append([InlineKeyboardButton("🔙 חזרה לכרטיס", callback_data=back)])
    return InlineKeyboardMarkup(rows)


def picker_text(kind: str, obj_id: int, current: str, suggestions: list[dict[str, Any]]) -> str:
    lines = [f"‏🏗 <b>קישור {KINDS[kind]} #{obj_id} לפרויקט</b> (לא חובה)",
             f"מקושר כעת: <b>{_html.escape(current)}</b>" if current else "לא מקושר לפרויקט."]
    if suggestions:
        lines.append("\nהצעות לפי הטקסט — שום דבר לא מקושר עד שתבחר:")
        for s in suggestions:
            why = "המזהה מופיע בטקסט" if s.get("reason") == "id" else f"התאמת שם {s.get('score')}%"
            lines.append(f"• {_html.escape(s['identifier'])} — {_html.escape(s.get('name') or '')} <i>({why})</i>")
    else:
        lines.append("\nאין הצעה מתאימה — אפשר לחפש לפי מזהה או שם.")
    return "\n".join(lines)


def search_text(q: str, n: int) -> str:
    return (f"‏🔎 תוצאות עבור <b>{_html.escape(q)}</b>: {n}" if n
            else f"‏🔎 לא נמצא פרויקט עבור <b>{_html.escape(q)}</b>. נסה מזהה (למשל WBE-087) או מילה מהשם.")


def done_text(kind: str, obj_id: int, label: str) -> str:
    return (f"‏✅ {KINDS[kind]} #{obj_id} קושרה ל־<b>{_html.escape(label)}</b>" if label
            else f"‏✅ הקישור לפרויקט הוסר מ{KINDS[kind]} #{obj_id}.")


def back_keyboard(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 חזרה לכרטיס", callback_data=back)]])
