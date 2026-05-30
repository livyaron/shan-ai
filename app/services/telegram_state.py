"""Shared in-memory state for Telegram bot handlers.

Module-level globals — Python's import cache guarantees handle_message
and handle_callback always reference the same dict objects.
"""

# { telegram_id (int): decision_id (int) }  — waiting for rejection reason
_awaiting_rejection_note: dict[int, int] = {}

# { telegram_id (int): decision_id (int) }  — waiting for irrelevance reason
_awaiting_irrelevant_reason: dict[int, int] = {}

# { telegram_id (int): distribution_id (int) }  — waiting for rejection reason on a distribution
_awaiting_dist_rejection: dict[int, int] = {}

# { telegram_id (int): original_text (str) }  — waiting for clarification on an UNCLEAR message
_awaiting_clarification: dict[int, str] = {}

# { telegram_id (int): file_id (int) }  — waiting for master-file confirmation after upload
_awaiting_master_confirm: dict[int, int] = {}

# { telegram_id (int): original_text (str) }  — bot wasn't sure; waiting for user to confirm if it's a decision
_awaiting_decision_confirm: dict[int, str] = {}

# { telegram_id (int): original_text (str) }  — confirmed decision; waiting for "needs manager approval?" answer
_awaiting_mgr_approval_confirm: dict[int, str] = {}

# { telegram_id (int): edit state dict }  — in-progress RACI inline edit session
# value: { decision_id, items: [{user_id, role, name}], all_users: [{id, name}], is_critical, parsed }
_raci_edit_state: dict[int, dict] = {}

# { telegram_id (int): {"text": str, "result": dict, "user_has_manager": bool} }
# Pending decision preview — user must approve or dismiss before commit
_awaiting_decision_preview: dict[int, dict] = {}

# { telegram_id (int): filter state dict }  — active custom-filter session
# value: { "owner": str, "type": str|None, "status": str|None, "date_days": int, "page": int }
_decisions_menu_state: dict[int, dict] = {}

# { telegram_id (int): filter state dict }  — active projects custom-filter session
# value: { "stage": str|None, "type": str|None, "mgr": str|None, "th": str|None, "date": str|None }
_projects_menu_state: dict[int, dict] = {}

# { telegram_id (int): (shortcut_key, page) }  — origin for back-nav from detail card
# shortcut_key is one of: "late"|"handle"|"quarter"|"all"|"active"|"cf"
_projects_detail_origin: dict[int, tuple[str, int]] = {}

# { telegram_id (int): [candidate_identifier, ...] }  — waiting for user to pick one project
_awaiting_disambiguation: dict[int, list] = {}

# { telegram_id (int): (decision_id, back_page) }  — rated via feedback menu, awaiting text note
_awaiting_fb_menu_text: dict[int, tuple[int, int]] = {}


from collections import deque
import time as _time

_CONTEXT_MAXLEN = 5
_CONTEXT_TTL = 1800.0   # 30 minutes
_CONTEXT_INJECT = 3     # how many entries to inject into prompts

# { telegram_id (int): deque of {"role": str, "content": str, "ts": float} }
_conversation_context: dict[int, deque] = {}


def get_context(telegram_id: int) -> list[dict]:
    """Return last _CONTEXT_INJECT entries; clear and return [] if TTL expired."""
    if telegram_id not in _conversation_context:
        return []
    dq = _conversation_context[telegram_id]
    if not dq:
        return []
    if _time.time() - dq[-1]["ts"] > _CONTEXT_TTL:
        del _conversation_context[telegram_id]
        return []
    return list(dq)[-_CONTEXT_INJECT:]


def append_context(telegram_id: int, role: str, content: str) -> None:
    """Append one exchange entry; creates the deque on first call."""
    if telegram_id not in _conversation_context:
        _conversation_context[telegram_id] = deque(maxlen=_CONTEXT_MAXLEN)
    _conversation_context[telegram_id].append({
        "role": role,
        "content": content[:500],
        "ts": _time.time(),
    })


def clear_context(telegram_id: int) -> None:
    """Remove all context for this user."""
    _conversation_context.pop(telegram_id, None)


# { telegram_id (int): [user_id, ...] }  — subordinate list for team-report selection
_awaiting_team_report: dict[int, list[int]] = {}
