# HMI Cleanup & Navigation Redesign — 2026-06-12

## Problem
- No shared layout: 20 templates each duplicate navbar markup + CSS (~11k lines total).
- Navbars drifted: only `dashboard.html` links to all pages; `reports.html`, `report_detail.html`, `report_schedule.html`, `project_reports.html`, `project_report_detail.html`, `eval.html`, `eval_curate.html` are navigation dead-ends.
- Flat 11-link navbar overflows on smaller screens; duplicate emojis (📊 used 3×).
- Junk files in templates dir: 5 `.bak` files, `‏‏login - עותק.html`.

## Design

### Shared navbar include: `app/templates/_navbar.html`
- Pure Jinja include (`{% include "_navbar.html" %}`), no base-template refactor (minimal diff per page; page CSS untouched).
- Self-contained `<style>` with `shan-nav-*` class prefix — zero collision with per-page `.navbar` styles.
- Active link auto-detected from `request.url.path` (all pages pass `request`).
- `pending_approvals` badge guarded with `is defined` (only some routes pass it).
- `current_user` guarded; `profile.html` passes `user` instead — fallback handled.

### Navigation structure (RTL)
Top-level (daily use):
- 📊 דשבורד → `/dashboard/`
- 📋 החלטות (badge) → `/dashboard/decisions`
- 📂 פרויקטים → `/dashboard/projects`
- 💬 שאל → `/dashboard/ask`
- 📁 קבצים → `/dashboard/files`

Dropdown «📈 דוחות»:
- דוחות שבועיים → `/dashboard/reports`
- דוח פרויקטים → `/dashboard/project-reports`
- תזמון דוחות → `/dashboard/project-reports/schedule`

Dropdown «⚙️ מערכת»:
- 👥 משתמשים → `/dashboard/users`
- 🎓 למידה → `/dashboard/learning`
- 📜 חוקי למידה → `/dashboard/learning/rules`
- 📊 לוגים → `/dashboard/logs`
- 🤖 מודל AI → `/dashboard/llm-config`
- 🧩 RACI בינה → `/dashboard/raci-intelligence`
- 🧪 Eval → `/dashboard/eval`

Left side: user chip (links to `/dashboard/profile`) + logout.

Dropdowns: CSS hover + focus-within (no JS dependency).

### Page work
1. 13 standard pages: replace existing `<nav>…</nav>` with include.
2. Orphan pages: add include; move page-action buttons (e.g., reports.html «צור לכולם» / «שלח לכולם») from navbar into a page-content header row.
3. Delete `.bak` templates + `login - עותק.html` (git-tracked → recoverable).

### Testing
`docker-compose restart fastapi`, then HTTP-check every dashboard route renders (no Jinja errors).

## Out of scope
- Full base-template (`{% extends %}`) refactor.
- Per-page content redesign beyond moved action buttons.
- login.html (standalone page, no navbar).
