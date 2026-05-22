"""Shared in-memory state for Telegram bot handlers.

Module-level globals — Python's import cache guarantees handle_message
and handle_callback always reference the same dict objects.
"""

# { telegram_id (int): decision_id (int) }  — waiting for rejection reason
_awaiting_rejection_note: dict[int, int] = {}

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

# { telegram_id (int): original_question (str) }  — waiting for user to pick one project
_awaiting_disambiguation: dict[int, str] = {}
