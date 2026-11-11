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
- **כניסה (2026-11-11, `app/routers/login.py`):** רשימת המשתמשים גלויה בכוונה, ולכן (1) 5 סיסמאות שגויות נועלות את המשתמש ל-15 דקות (`MAX_FAILURES`/`LOCK_SECONDS`, בזיכרון); (2) **הסיסמה הראשונית (`auth.DEFAULT_PASSWORD`) לעולם לא פותחת session** — היא רק שולחת קוד בן 6 ספרות לטלגרם של המשתמש (`start_password_setup`), והקוד + סיסמה אישית (≥8) ב-`/login/new-password` הם שמכניסים. אין קישור טלגרם → מנהל מערכת מאפס סיסמה. גם `/login/app` (אפליקציית הלקחים) מחזיר 403 עם `change_password`. ניהול משתמשים (create / set-role / delete / regen-code) — מנהל מערכת בלבד, בצד השרת; משתמש לא משנה את התפקיד של עצמו ב-edit. טסטים: `test_login_security.py`, `test_security_guards.py`.
