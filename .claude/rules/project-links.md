---
paths:
  - "app/services/project_links.py"
  - "app/services/project_link_telegram.py"
  - "app/templates/_project_picker.html"
  - "app/templates/_war_room_project_field.html"
  - "app/templates/decisions.html"
  - "tests/test_project_links.py"
---
# Linking missions / decisions to a project

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

- **קישור משימות/החלטות לפרויקט (2026-10-17, optional):** `missions.project_id` / `decisions.project_id` → `projects.id` ON DELETE SET NULL, ALTERed at startup (`app/main.py`); `project` relationships are `lazy="selectin"` so a Telegram card can read them after the session closes. `app/services/project_links.py` is the ONLY place a link is suggested (`suggest`: identifier in the text → name via `lessons_project_link.weighted_score` → bonus for the confirmed מנה"פ of the owner/submitter; **rules, no model**), searched, and read back (`for_project` — missions = the board everyone sees; decisions = the decisions-page rule, admin all). **A suggestion is never applied by itself** (user decision): web = `_project_picker.html` (war room create form + 🏗 on every board layout card, decisions page 🏗 for admin/submitter) → `POST /dashboard/war-room/{id}/project`, `POST /dashboard/decisions/{id}/project`; Telegram = `pl:` callbacks (`project_link_telegram.py`, state `_project_link_state`; any other button ends a pending typed search). The project page draws `for_project` (🔗 card). Picker options = ACTIVE projects only.
