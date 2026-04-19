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
