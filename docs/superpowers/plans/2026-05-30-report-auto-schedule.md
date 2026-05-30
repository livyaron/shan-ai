# Report Auto-Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dashboard link to project reports, auto-generate+send reports on a per-user schedule (Telegram video + dashboard save), and give admins a control page to manage all schedules.

**Architecture:** New `ProjectReportSchedule` table (one row per user) stores enabled/day/time. `eval_cron.py` adds a job running every 15 min that checks due schedules, generates the HTML report + MP4 video, saves to `project_reports`, and sends via Telegram (text summary + video file). Admin control page at `/dashboard/report-schedule` shows all users in a table with inline schedule forms.

**Tech Stack:** FastAPI/SQLAlchemy async, APScheduler (already in use), python-telegram-bot v21+, existing `project_report_service.py` + `video_report_service.py`.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `app/models.py` | Modify | Add `ProjectReportSchedule` model |
| `app/services/project_report_service.py` | Modify | Add `auto_send_project_report()` |
| `app/services/eval_cron.py` | Modify | Add `_project_report_cron()` job every 15 min |
| `app/routers/project_reports.py` | Modify | Add schedule CRUD endpoints + "send now" |
| `app/templates/dashboard.html` | Modify | Add "📋 דוחות פרויקטים" button to CTA row + navbar link |
| `app/templates/report_schedule.html` | **Create** | Admin control page |

---

## Task 1: Dashboard Link

**Files:**
- Modify: `app/templates/dashboard.html`

- [ ] **Step 1: Add "פרויקטים — דוח" link to the CTA row**

Find the CTA row (around line 858):
```html
<div class="mb-4 d-flex align-items-center gap-3">
    <a href="/dashboard/decisions?new=1" ...>+ החלטה חדשה</a>
    <button class="btn-cmd" onclick="openDashboardAI()">◈ ניתוח AI</button>
    <a href="/dashboard/reports" class="btn btn-outline-primary btn-sm ms-2">📊 ניהול דוחות →</a>
</div>
```

Add after the "ניהול דוחות" link:

```html
        <a href="/dashboard/project-reports" class="btn btn-outline-success btn-sm ms-1">
            📋 דוחות פרויקטים →
        </a>
```

- [ ] **Step 2: Add navbar link**

Find the navbar links (around line 818-829). After the `📂 פרויקטים` link, add:

```html
        <a href="/dashboard/project-reports" class="nav-link">📋 דוחות</a>
```

- [ ] **Step 3: Restart and verify**

```bash
docker-compose restart fastapi && sleep 5 && docker logs shan-ai-api --tail 5
```

Open `http://localhost:8000/dashboard` — verify "📋 דוחות פרויקטים →" button appears in the CTA row.

- [ ] **Step 4: Commit**

```bash
git add app/templates/dashboard.html
git commit -m "feat(dashboard): add project reports link to CTA row and navbar"
```

---

## Task 2: ProjectReportSchedule Model + Migration

**Files:**
- Modify: `app/models.py`

- [ ] **Step 1: Run migration on local DB**

```bash
docker exec shan-ai-api python -c "
import asyncio
from app.database import async_session_maker
from sqlalchemy import text

async def run():
    async with async_session_maker() as s:
        await s.execute(text('''
CREATE TABLE IF NOT EXISTS project_report_schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    day_of_week INTEGER,
    hour_il INTEGER NOT NULL DEFAULT 8,
    minute_il INTEGER NOT NULL DEFAULT 0,
    last_sent_at TIMESTAMP,
    created_by_id INTEGER REFERENCES users(id)
)'''))
        await s.execute(text('CREATE INDEX IF NOT EXISTS ix_prs_user_id ON project_report_schedules(user_id)'))
        await s.commit()
        print('ok')

asyncio.run(run())
"
```

Expected: `ok`

- [ ] **Step 2: Add model to `app/models.py`**

After the `ProjectReport` class, add:

```python
class ProjectReportSchedule(Base):
    __tablename__ = "project_report_schedules"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    enabled       = Column(Boolean, default=False, nullable=False)
    day_of_week   = Column(Integer, nullable=True)   # 0=Sun … 6=Sat; None = every day
    hour_il       = Column(Integer, default=8,  nullable=False)  # Israel time
    minute_il     = Column(Integer, default=0,  nullable=False)
    last_sent_at  = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user       = relationship("User", foreign_keys=[user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
```

- [ ] **Step 3: Restart and verify**

```bash
docker-compose restart fastapi && sleep 5 && docker logs shan-ai-api --tail 5
```

Expected: `Application startup complete.`

- [ ] **Step 4: Commit**

```bash
git add app/models.py
git commit -m "feat(schedule): add ProjectReportSchedule model"
```

---

## Task 3: auto_send_project_report + Telegram video sending

**Files:**
- Modify: `app/services/project_report_service.py`

- [ ] **Step 1: Add `auto_send_project_report` function**

Append to `app/services/project_report_service.py`:

```python
async def auto_send_project_report(user, session, bot=None) -> bool:
    """
    Generate report + video for user, save to project_reports, send via Telegram.
    Returns True on success.
    """
    import os
    from app.models import ProjectReport
    from app.services.video_report_service import generate_report_video

    try:
        report_data = await gather_report_data(user, session)
        html        = await generate_report_html(report_data)

        report = ProjectReport(
            user_id=user.id,
            report_data=report_data,
            html_content=html,
        )
        session.add(report)
        await session.flush()
        report_id = report.id
        await session.commit()

        # Generate video (synchronous in this context — cron can wait)
        video_path = await generate_report_video(report_data, report_id)
        if video_path:
            report = await session.get(ProjectReport, report_id)
            if report:
                report.video_path = video_path
                await session.commit()

        # Send via Telegram
        if bot and user.telegram_id:
            await _telegram_send_report(bot, user, report_id, report_data, video_path)

        return True

    except Exception as exc:
        logger.error(f"auto_send_project_report failed for user {user.id}: {exc}")
        return False


async def _telegram_send_report(bot, user, report_id: int, data: dict, video_path: str | None) -> None:
    """Send report summary + MP4 via Telegram."""
    es = data.get("executive_summary", {})
    meta = data.get("meta", {})

    summary = (
        f"‏📋 *דוח פרויקטים* — {meta.get('generated_at', '')}\n\n"
        f"📊 פעיל: *{es.get('total_active', 0)}* | "
        f"🟡 באיחור: *{es.get('total_delayed', 0)}* | "
        f"🔴 סיכון: *{es.get('total_at_risk', 0)}*\n"
        f"ציון סיכון ממוצע: *{es.get('avg_risk_score', 0)}*\n"
        f"אחוז אישורי החלטות: *{es.get('approval_rate_pct', 0)}%*\n\n"
        f"[צפה בדוח המלא בדשבורד]"
        f"(https://easygoing-endurance-production-df54.up.railway.app/dashboard/project-reports/{report_id})"
    )

    await bot.send_message(
        chat_id=user.telegram_id,
        text=summary,
        parse_mode="Markdown",
    )

    if video_path:
        full_path = os.path.join("static", video_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "rb") as vf:
                    await bot.send_video(
                        chat_id=user.telegram_id,
                        video=vf,
                        caption=f"‏🎬 וידאו דוח פרויקטים — {meta.get('generated_at', '')}",
                    )
            except Exception as ve:
                logger.warning(f"Telegram video send failed for user {user.id}: {ve}")
```

- [ ] **Step 2: Restart and verify import**

```bash
docker-compose restart fastapi && sleep 5 && docker logs shan-ai-api --tail 5
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(schedule): add auto_send_project_report with Telegram video delivery"
```

---

## Task 4: Scheduler Job in eval_cron.py

**Files:**
- Modify: `app/services/eval_cron.py`

- [ ] **Step 1: Add `_project_report_cron` job**

Read `app/services/eval_cron.py`. In `start_scheduler()`, after the existing weekly report job, add:

```python
    sch.add_job(
        _project_report_cron,
        CronTrigger(minute="*/15"),
        id="project_report_cron",
        replace_existing=True,
    )
    logger.info("eval_cron: project_report_cron registered (every 15 min)")
```

Then add the function after `_weekly_report_run`:

```python
async def _project_report_cron() -> None:
    """Check project report schedules and send due reports (runs every 15 min)."""
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta
    from app.database import async_session_maker
    from app.models import ProjectReportSchedule, User
    from app.services.project_report_service import auto_send_project_report
    from app.services.telegram_polling import telegram_bot
    from sqlalchemy import select

    tz_il = ZoneInfo("Asia/Jerusalem")
    now_il = datetime.now(tz=tz_il)
    current_dow  = now_il.weekday()  # 0=Mon … 6=Sun (Python convention)
    # Remap to 0=Sun … 6=Sat to match our stored convention
    current_dow_sun = (current_dow + 1) % 7
    current_hour   = now_il.hour
    current_minute = now_il.minute

    async with async_session_maker() as session:
        schedules = (await session.execute(
            select(ProjectReportSchedule).where(ProjectReportSchedule.enabled == True)
        )).scalars().all()

        bot = telegram_bot.application.bot if (
            telegram_bot.application and telegram_bot.application.bot
        ) else None

        for sched in schedules:
            # Check day of week (None = every day)
            if sched.day_of_week is not None and sched.day_of_week != current_dow_sun:
                continue

            # Check hour/minute window (within 15-min slot)
            if sched.hour_il != current_hour:
                continue
            if not (sched.minute_il <= current_minute < sched.minute_il + 15):
                continue

            # Avoid double-sending within the same 30-min window
            if sched.last_sent_at:
                age = datetime.utcnow() - sched.last_sent_at
                if age < timedelta(minutes=30):
                    continue

            # Send
            user = await session.get(User, sched.user_id)
            if not user:
                continue

            logger.info(f"project_report_cron: sending report for user {user.id} ({user.username})")
            ok = await auto_send_project_report(user, session, bot)

            if ok:
                sched.last_sent_at = datetime.utcnow()
                await session.commit()
```

- [ ] **Step 2: Restart and verify job registered**

```bash
docker-compose restart fastapi && sleep 8 && docker logs shan-ai-api | grep "project_report_cron"
```

Expected: `eval_cron: project_report_cron registered (every 15 min)`

- [ ] **Step 3: Commit**

```bash
git add app/services/eval_cron.py
git commit -m "feat(schedule): add project_report_cron job every 15 min in eval_cron"
```

---

## Task 5: Admin Control Page — Endpoints + Template

**Files:**
- Modify: `app/routers/project_reports.py`
- Create: `app/templates/report_schedule.html`

- [ ] **Step 1: Add schedule endpoints to `app/routers/project_reports.py`**

Append to `app/routers/project_reports.py`:

```python
# ── Schedule management (admin only) ─────────────────────────────────────────

@router.get("/schedule", response_class=HTMLResponse)
async def report_schedule_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    if not current_user.is_admin and not getattr(current_user, "role", None) in (
        __import__("app.models", fromlist=["RoleEnum"]).RoleEnum.DIVISION_MANAGER,
        __import__("app.models", fromlist=["RoleEnum"]).RoleEnum.DEPUTY_DIVISION_MANAGER,
    ):
        raise HTTPException(status_code=403, detail="Admin only")

    from app.models import RoleEnum, ProjectReportSchedule
    from sqlalchemy import select as _sel

    users = (await session.execute(
        _sel(User).where(User.role.isnot(None), User.role != RoleEnum.VIEWER)
        .order_by(User.role, User.username)
    )).scalars().all()

    # Load existing schedules
    schedules = (await session.execute(_sel(ProjectReportSchedule))).scalars().all()
    sched_map = {s.user_id: s for s in schedules}

    rows = []
    for u in users:
        s = sched_map.get(u.id)
        rows.append({
            "id":          u.id,
            "username":    u.username,
            "role":        u.role.value if u.role else "",
            "telegram":    bool(u.telegram_id),
            "enabled":     s.enabled if s else False,
            "day_of_week": s.day_of_week if s else None,   # None = every day
            "hour_il":     s.hour_il if s else 8,
            "minute_il":   s.minute_il if s else 0,
            "last_sent":   s.last_sent_at.strftime("%d/%m/%Y %H:%M") if (s and s.last_sent_at) else "—",
        })

    return templates.TemplateResponse("report_schedule.html", {
        "request": request,
        "current_user": current_user,
        "rows": rows,
    })


@router.post("/schedule/save", response_class=HTMLResponse)
async def report_schedule_save(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.models import RoleEnum, ProjectReportSchedule
    from sqlalchemy import select as _sel

    if not current_user.is_admin and getattr(current_user, "role", None) not in (
        RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER,
    ):
        raise HTTPException(status_code=403, detail="Admin only")

    form = await request.form()

    # Collect user IDs that have "enabled" checkbox checked
    enabled_ids = {int(k.split("_")[1]) for k, v in form.items()
                   if k.startswith("enabled_") and v == "on"}

    # Collect all user_ids mentioned in the form
    all_ids = {int(k.split("_", 1)[1]) for k in form.keys()
               if k.startswith(("enabled_", "dow_", "hour_", "minute_"))}

    existing = {s.user_id: s for s in (await session.execute(
        _sel(ProjectReportSchedule)
    )).scalars().all()}

    for uid in all_ids:
        try:
            dow_raw = form.get(f"dow_{uid}", "")
            dow = int(dow_raw) if dow_raw != "" else None
            hour   = int(form.get(f"hour_{uid}", 8))
            minute = int(form.get(f"minute_{uid}", 0))
        except (ValueError, TypeError):
            continue

        if uid in existing:
            s = existing[uid]
            s.enabled     = (uid in enabled_ids)
            s.day_of_week = dow
            s.hour_il     = max(0, min(23, hour))
            s.minute_il   = max(0, min(59, minute))
        else:
            s = ProjectReportSchedule(
                user_id       = uid,
                enabled       = (uid in enabled_ids),
                day_of_week   = dow,
                hour_il       = max(0, min(23, hour)),
                minute_il     = max(0, min(59, minute)),
                created_by_id = current_user.id,
            )
            session.add(s)

    await session.commit()
    return RedirectResponse("/dashboard/project-reports/schedule", status_code=302)


@router.post("/schedule/send-now/{user_id}", response_class=HTMLResponse)
async def report_schedule_send_now(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.models import RoleEnum
    if not current_user.is_admin and getattr(current_user, "role", None) not in (
        RoleEnum.DIVISION_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER,
    ):
        raise HTTPException(status_code=403, detail="Admin only")

    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    from app.services.project_report_service import auto_send_project_report
    from app.services.telegram_polling import telegram_bot
    bot = (telegram_bot.application.bot
           if telegram_bot.application and telegram_bot.application.bot else None)

    asyncio.create_task(auto_send_project_report(target, session, bot))
    return RedirectResponse("/dashboard/project-reports/schedule", status_code=302)
```

Also add `select` import at the top of the file if not already there (it's already imported via `from sqlalchemy import select, desc`).

- [ ] **Step 2: Create `app/templates/report_schedule.html`**

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shan-AI — תזמון דוחות פרויקטים</title>
  <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { --bg-deep:#070b12; --bg-surface:#0c1220; --bg-card:#0f1826; --border:#1a2d47; --cyan:#00d4ff; --text-1:#e2e8f0; --text-2:#64748b; --green:#10b981; }
    body { background:var(--bg-deep); color:var(--text-1); font-family:'Heebo',sans-serif; min-height:100vh; }
    .navbar { background:var(--bg-surface); border-bottom:1px solid var(--border); padding:12px 24px; }
    .page-title { color:var(--cyan); font-size:1.3rem; font-weight:700; }
    .card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; }
    .table { color:var(--text-1); } .table th { color:var(--text-2); border-color:var(--border); font-size:.78rem; text-transform:uppercase; } .table td { border-color:var(--border); vertical-align:middle; }
    .btn-cyan { background:var(--cyan); color:#000; font-weight:600; border:none; border-radius:6px; padding:6px 18px; cursor:pointer; }
    .btn-outline-dim { border:1px solid var(--border); color:var(--text-2); background:transparent; border-radius:6px; padding:5px 14px; text-decoration:none; display:inline-block; }
    .btn-outline-dim:hover { color:var(--text-1); border-color:var(--cyan); }
    select, input[type=number] { background:var(--bg-surface); color:var(--text-1); border:1px solid var(--border); border-radius:5px; padding:3px 8px; font-family:'Heebo',sans-serif; font-size:.82rem; }
    .form-check-input { width:1.1em; height:1.1em; cursor:pointer; }
    .badge-role { font-size:.68rem; padding:2px 7px; border-radius:4px; background:rgba(0,212,255,.1); color:var(--cyan); }
    .badge-no-tg { font-size:.68rem; padding:2px 7px; border-radius:4px; background:rgba(239,68,68,.1); color:#ef4444; }
  </style>
</head>
<body>
<nav class="navbar d-flex justify-content-between align-items-center">
  <span class="page-title">⏰ תזמון דוחות פרויקטים אוטומטיים</span>
  <div class="d-flex gap-2 align-items-center">
    <span style="color:var(--text-2);font-size:.85rem;">{{ current_user.username }}</span>
    <a href="/dashboard/project-reports" class="btn-outline-dim">← דוחות</a>
    <a href="/dashboard" class="btn-outline-dim">לוח בקרה</a>
    <a href="/logout" class="btn-outline-dim">יציאה</a>
  </div>
</nav>

<div class="container-fluid px-4 py-4">

  <div style="background:rgba(0,212,255,.06);border:1px solid rgba(0,212,255,.2);border-radius:8px;padding:10px 16px;margin-bottom:16px;font-size:.82rem;color:var(--cyan);">
    ⏰ הדוח נשלח אוטומטית בטלגרם (טקסט + וידאו) ונשמר בדשבורד. הזמן בשעון ישראל.
    בדיקה כל 15 דקות — הגדר שעות עגולות (דקה = 0) לדיוק מירבי.
  </div>

  <form method="post" action="/dashboard/project-reports/schedule/save">
  <div class="card p-3">
    <table class="table table-hover mb-3">
      <thead>
        <tr>
          <th>משתמש</th><th>תפקיד</th><th>טלגרם</th>
          <th>פעיל</th><th>יום בשבוע</th><th>שעה (ישראל)</th>
          <th>נשלח לאחרונה</th><th>שלח עכשיו</th>
        </tr>
      </thead>
      <tbody>
      {% for r in rows %}
      <tr>
        <td><strong>{{ r.username }}</strong></td>
        <td><span class="badge-role">{{ r.role }}</span></td>
        <td>
          {% if r.telegram %}
            <span style="color:var(--green);font-size:.8rem;">✅</span>
          {% else %}
            <span class="badge-no-tg">ללא טלגרם</span>
          {% endif %}
        </td>
        <td>
          <input type="checkbox" class="form-check-input" name="enabled_{{ r.id }}"
                 {% if r.enabled %}checked{% endif %}>
        </td>
        <td>
          <select name="dow_{{ r.id }}" style="min-width:90px;">
            <option value="" {% if r.day_of_week is none %}selected{% endif %}>כל יום</option>
            <option value="0" {% if r.day_of_week == 0 %}selected{% endif %}>ראשון</option>
            <option value="1" {% if r.day_of_week == 1 %}selected{% endif %}>שני</option>
            <option value="2" {% if r.day_of_week == 2 %}selected{% endif %}>שלישי</option>
            <option value="3" {% if r.day_of_week == 3 %}selected{% endif %}>רביעי</option>
            <option value="4" {% if r.day_of_week == 4 %}selected{% endif %}>חמישי</option>
            <option value="5" {% if r.day_of_week == 5 %}selected{% endif %}>שישי</option>
            <option value="6" {% if r.day_of_week == 6 %}selected{% endif %}>שבת</option>
          </select>
        </td>
        <td>
          <input type="number" name="hour_{{ r.id }}"   value="{{ r.hour_il }}"   min="0" max="23" style="width:52px;"> :
          <input type="number" name="minute_{{ r.id }}" value="{{ r.minute_il }}" min="0" max="59" style="width:52px;">
        </td>
        <td style="font-size:.78rem;color:var(--text-2);">{{ r.last_sent }}</td>
        <td>
          <form method="post" action="/dashboard/project-reports/schedule/send-now/{{ r.id }}" style="margin:0;" onsubmit="return confirm('לשלוח דוח עכשיו ל-{{ r.username }}?');">
            <button type="submit" class="btn-outline-dim" style="font-size:.78rem;padding:3px 10px;">▶ שלח</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    <div class="d-flex justify-content-end">
      <button type="submit" class="btn-cyan">💾 שמור הגדרות</button>
    </div>
  </div>
  </form>
</div>
</body>
</html>
```

- [ ] **Step 3: Restart and verify schedule page accessible**

```bash
docker-compose restart fastapi && sleep 8 && docker logs shan-ai-api --tail 5
```

Open `http://localhost:8000/dashboard/project-reports/schedule` (logged in as admin).
Expected: schedule table with all users, enable checkboxes, day/time selectors.

- [ ] **Step 4: Test saving a schedule**

On the schedule page:
1. Enable a user, set day = "כל יום", hour = 8, minute = 0
2. Click "שמור הגדרות" → verify redirects back and settings are saved

- [ ] **Step 5: Commit**

```bash
git add app/routers/project_reports.py app/templates/report_schedule.html
git commit -m "feat(schedule): add admin report-schedule page with save + send-now endpoints"
```

---

## Task 6: Also add navbar link + "⏰ תזמון" button on project-reports list page

**Files:**
- Modify: `app/templates/project_reports.html`

- [ ] **Step 1: Add schedule link to the project reports navbar**

In `project_reports.html`, find the navbar `d-flex gap-2` div. Add after the generate button:

```html
    <a href="/dashboard/project-reports/schedule" class="btn-outline-dim">⏰ תזמון אוטומטי</a>
```

- [ ] **Step 2: Commit**

```bash
git add app/templates/project_reports.html
git commit -m "feat(schedule): add schedule link to project reports list page"
```

---

## Task 7: Railway Migration + Deploy

- [ ] **Step 1: Run migration on Railway DB**

```bash
docker exec shan-ai-api python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

URL = 'postgresql+asyncpg://shan_user:shan_secure_pass_2025@interchange.proxy.rlwy.net:15720/shan_ai'

async def run():
    e = create_async_engine(URL)
    async with e.begin() as c:
        await c.execute(text('''
CREATE TABLE IF NOT EXISTS project_report_schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    day_of_week INTEGER,
    hour_il INTEGER NOT NULL DEFAULT 8,
    minute_il INTEGER NOT NULL DEFAULT 0,
    last_sent_at TIMESTAMP,
    created_by_id INTEGER REFERENCES users(id)
)'''))
        await c.execute(text('CREATE INDEX IF NOT EXISTS ix_prs_user_id ON project_report_schedules(user_id)'))
    await e.dispose()
    print('railway ok')

asyncio.run(run())
"
```

Expected: `railway ok`

- [ ] **Step 2: Push + redeploy**

```bash
git push origin master

TOKEN="62eb95f1-6f66-46f2-8d0f-23a4908fa298"
SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"
ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { serviceInstanceDeploy(serviceId: \\\"$SVC_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}"
```

Expected: `{"data":{"serviceInstanceDeploy":true}}`

- [ ] **Step 3: Smoke test on Railway**

1. Navigate to `https://easygoing-endurance-production-df54.up.railway.app/dashboard` → verify "📋 דוחות פרויקטים →" button visible
2. Navigate to `/dashboard/project-reports/schedule` → verify schedule table loads
3. Enable one user + save → verify settings persist
4. Click "▶ שלח" on a user with Telegram → verify report generated + video sent

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "chore: smoke-tested report auto-schedule on Railway" --allow-empty
```

---

## Verification Checklist

- [ ] "📋 דוחות פרויקטים →" button in main dashboard CTA row
- [ ] "📋 דוחות" navbar link in all dashboard pages
- [ ] `/dashboard/project-reports/schedule` loads with all users
- [ ] Save schedule persists to DB (`project_report_schedules` table)
- [ ] "שלח עכשיו" triggers report generation + Telegram message + video
- [ ] `eval_cron` logs show `project_report_cron registered` on startup
- [ ] Scheduler fires at correct time and sends report
- [ ] `last_sent_at` updated after successful send
- [ ] Day-of-week "כל יום" (None) sends every day at configured time
- [ ] Double-send protection: 30-min window check prevents duplicate sends
