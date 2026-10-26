---
paths:
  - "app/services/job_titles.py"
  - "app/templates/users.html"
  - "app/templates/profile.html"
  - "tests/test_job_titles.py"
  - "tests/test_users_admin_guard.py"
---
# Users: job titles

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

- **תואר מנהל/ת פרויקט ניטרלי (user 2026-10-20):** `app/services/job_titles.neutral_title` is the ONLY rule — "מנהל פרויקט"/"מנהלת פרויקט" (and …פרויקטים) are stored as "מנהל/ת פרויקט(ים)" on every save (users page create/edit, personal profile) and rewritten once at startup (`app/main.py`, idempotent). Any other title is kept as typed. A test fails if a save path stores `job_title` raw.
