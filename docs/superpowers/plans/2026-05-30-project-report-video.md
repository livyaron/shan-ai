# Project Report + Video + NotebookLM MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a 4-section HTML project portfolio report from live project/snapshot data, produce a narrated 720p MP4 slide video, and configure NotebookLM MCP locally so Claude can send reports to NotebookLM for Audio Overview generation.

**Architecture:** New `ProjectReport` model stores HTML + video path. `project_report_service.py` gathers role-scoped data from existing `ProjectSnapshot`/`Project`/`Decision` tables, calls Groq to write narrative text, then wraps in a pre-defined HTML template. `video_report_service.py` renders 6 Pillow slides (Hebrew RTL via `python-bidi`), generates Hebrew gTTS audio per slide, and assembles into MP4 via moviepy. New `/dashboard/project-reports` page lists and generates reports.

**Tech Stack:** FastAPI/SQLAlchemy async, Groq (llama-3.3-70b), moviepy 1.0.3, Pillow ≥10, gTTS, python-bidi, numpy, PostgreSQL. Static files served from `static/` at `/static`.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `app/models.py` | Modify | Add `ProjectReport` model |
| `app/services/project_report_service.py` | **Create** | Data gathering + Groq narrative + HTML assembly |
| `app/services/video_report_service.py` | **Create** | Pillow slides + gTTS audio + moviepy MP4 |
| `app/routers/project_reports.py` | **Create** | 4 endpoints: list, generate, detail, delete |
| `app/main.py` | Modify | Include new router |
| `app/templates/project_reports.html` | **Create** | Report list page |
| `app/templates/project_report_detail.html` | **Create** | Report detail: HTML viewer + MP4 player |
| `static/project_reports/.gitkeep` | **Create** | Persist directory in git |
| `requirements.txt` | Modify | Add moviepy, gTTS, python-bidi |
| `Dockerfile` | Modify | Add ffmpeg + Hebrew fonts + libsm6 |

---

## Task 1: ProjectReport Model + DB Migration

**Files:**
- Modify: `app/models.py`
- Run: SQL migration against local DB

- [ ] **Step 1: Run migration on local DB**

```bash
docker exec shan-ai-api python -c "
import asyncio
from app.database import async_session_maker
from sqlalchemy import text

async def run():
    async with async_session_maker() as s:
        await s.execute(text('''
CREATE TABLE IF NOT EXISTS project_reports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    report_data JSONB,
    html_content TEXT,
    video_path VARCHAR(500),
    notebooklm_url VARCHAR(500)
)
'''))
        await s.execute(text('CREATE INDEX IF NOT EXISTS ix_project_reports_user_id ON project_reports(user_id)'))
        await s.execute(text('CREATE INDEX IF NOT EXISTS ix_project_reports_generated_at ON project_reports(generated_at)'))
        await s.commit()
        print('migration ok')

asyncio.run(run())
"
```

Expected: `migration ok`

- [ ] **Step 2: Add `ProjectReport` to `app/models.py`**

After the `ProjectSnapshot` class, add:

```python
class ProjectReport(Base):
    __tablename__ = "project_reports"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    generated_at  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    report_data   = Column(JSON, nullable=True)
    html_content  = Column(Text, nullable=True)
    video_path    = Column(String(500), nullable=True)
    notebooklm_url = Column(String(500), nullable=True)

    user = relationship("User")
```

`JSON` is already imported (used elsewhere in models). If not present, add `JSON` to the SQLAlchemy import line.

- [ ] **Step 3: Restart and verify**

```bash
docker-compose restart fastapi && sleep 5 && docker logs shan-ai-api --tail 5
```

Expected: `Application startup complete.`

- [ ] **Step 4: Commit**

```bash
git add app/models.py
git commit -m "feat(reports): add ProjectReport model"
```

---

## Task 2: project_report_service.py — gather_report_data

**Files:**
- Create: `app/services/project_report_service.py`
- Test: `tests/test_project_report_service.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_project_report_service.py`:

```python
"""Tests for project_report_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.project_report_service import gather_report_data
from app.models import RoleEnum


@pytest.mark.asyncio
async def test_gather_report_data_returns_required_keys():
    user = MagicMock()
    user.id = 1
    user.username = "test"
    user.role = RoleEnum.DIVISION_MANAGER

    session = AsyncMock()
    # Mock get_overview_stats and get_risk_table
    with patch("app.services.project_report_service.get_overview_stats") as mock_ov, \
         patch("app.services.project_report_service.get_risk_table") as mock_rt:
        mock_ov.return_value = {
            "totals": {"active": 10, "delayed": 2, "at_risk": 1, "entering_next_week": 0},
            "type_counts": {},
            "delay_trend": [],
            "stage_distribution": {},
        }
        mock_rt.return_value = []

        result = await gather_report_data(user, session)

    assert "executive_summary" in result
    assert "portfolio_health" in result
    assert "risk_register" in result
    assert "meta" in result
    assert result["meta"]["username"] == "test"


@pytest.mark.asyncio
async def test_gather_report_data_limits_risk_register():
    user = MagicMock()
    user.id = 1
    user.username = "x"
    user.role = RoleEnum.DIVISION_MANAGER

    session = AsyncMock()
    big_risk_table = [{"project_id": i, "name": f"p{i}", "risk_score": 90 - i} for i in range(20)]

    with patch("app.services.project_report_service.get_overview_stats") as mock_ov, \
         patch("app.services.project_report_service.get_risk_table") as mock_rt:
        mock_ov.return_value = {"totals": {}, "type_counts": {}, "delay_trend": [], "stage_distribution": {}}
        mock_rt.return_value = big_risk_table

        result = await gather_report_data(user, session)

    assert len(result["risk_register"]) <= 10
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-api pytest tests/test_project_report_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.project_report_service'`

- [ ] **Step 3: Create `app/services/project_report_service.py`** (data gathering portion)

```python
"""Project portfolio report service — data gathering, HTML generation."""
import json
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Project, ProjectSnapshot, Decision, ProjectReport, RoleEnum
from app.services.project_learning_service import get_overview_stats, get_risk_table

logger = logging.getLogger(__name__)


async def gather_report_data(user: User, session: AsyncSession) -> dict:
    """Assemble all data needed to render the report and video."""
    overview = await get_overview_stats(session)
    risk_rows = await get_risk_table(session)

    totals = overview.get("totals", {})
    type_counts = overview.get("type_counts", {})

    # RAG status per type: RED if at_risk>0, AMBER if delayed>0, else GREEN
    rag_by_type = {}
    for t, counts in type_counts.items():
        if counts.get("at_risk", 0) > 0:
            rag_by_type[t] = "RED"
        elif counts.get("delayed", 0) > 0:
            rag_by_type[t] = "AMBER"
        else:
            rag_by_type[t] = "GREEN"

    # Avg risk score across all projects with snapshots
    avg_risk_rows = (await session.execute(
        select(func.avg(ProjectSnapshot.risk_score))
        .where(ProjectSnapshot.is_active == True, ProjectSnapshot.risk_score.isnot(None))
    )).scalar()
    avg_risk = round(float(avg_risk_rows or 0))

    # Decisions last 30 days
    from datetime import timedelta
    since30 = datetime.utcnow() - timedelta(days=30)
    from app.models import DecisionTypeEnum, DecisionStatusEnum
    from sqlalchemy import or_

    dec_stmt = select(
        func.count(Decision.id).label("total"),
        func.count(Decision.id).filter(
            Decision.type.in_([DecisionTypeEnum.CRITICAL, DecisionTypeEnum.UNCERTAIN]),
            Decision.status == DecisionStatusEnum.PENDING,
        ).label("critical_pending"),
        func.count(Decision.id).filter(
            Decision.status == DecisionStatusEnum.APPROVED
        ).label("approved"),
    ).where(Decision.created_at >= since30, Decision.is_relevant == True)

    if user.role == RoleEnum.PROJECT_MANAGER:
        dec_stmt = dec_stmt.where(Decision.submitter_id == user.id)

    dec_row = (await session.execute(dec_stmt)).one()
    total_dec = dec_row[0] or 0
    approval_rate = round((dec_row[2] / total_dec * 100) if total_dec else 0)

    # Top delayed projects (for action items)
    delayed = [r for r in risk_rows if r.get("risk_score", 0) >= 60][:5]
    action_items = [
        {
            "item": f"טיפול בפרויקט {r['name']} — ציון סיכון {r['risk_score']}",
            "owner": r.get("stage", "—"),
            "priority": "HIGH" if r["risk_score"] >= 80 else "MEDIUM",
            "main_reason": r.get("main_reason", ""),
        }
        for r in delayed
    ]

    return {
        "meta": {
            "generated_at": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
            "username": user.username or "—",
            "role": user.role.value if user.role else "—",
        },
        "executive_summary": {
            "total_active":       totals.get("active", 0),
            "total_delayed":      totals.get("delayed", 0),
            "total_at_risk":      totals.get("at_risk", 0),
            "entering_next_week": totals.get("entering_next_week", 0),
            "avg_risk_score":     avg_risk,
            "rag_by_type":        rag_by_type,
            "decisions_30d":      total_dec,
            "critical_pending":   dec_row[1] or 0,
            "approval_rate_pct":  approval_rate,
        },
        "portfolio_health": {
            "type_counts":        type_counts,
            "delay_trend":        overview.get("delay_trend", []),
            "stage_distribution": overview.get("stage_distribution", {}),
        },
        "risk_register": risk_rows[:10],
        "action_items":  action_items,
    }
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
docker exec shan-ai-api pytest tests/test_project_report_service.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/project_report_service.py tests/test_project_report_service.py
git commit -m "feat(reports): add gather_report_data"
```

---

## Task 3: project_report_service.py — Groq Narrative + HTML

**Files:**
- Modify: `app/services/project_report_service.py`
- Test: `tests/test_project_report_service.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_project_report_service.py`:

```python
import pytest
from unittest.mock import patch
from app.services.project_report_service import generate_report_html


@pytest.mark.asyncio
async def test_generate_report_html_returns_html_string():
    sample_data = {
        "meta": {"generated_at": "30/05/2026 12:00", "username": "test", "role": "division_manager"},
        "executive_summary": {
            "total_active": 10, "total_delayed": 2, "total_at_risk": 1,
            "entering_next_week": 0, "avg_risk_score": 45,
            "rag_by_type": {"הקמה": "RED", "הרחבה": "GREEN"},
            "decisions_30d": 8, "critical_pending": 1, "approval_rate_pct": 75,
        },
        "portfolio_health": {"type_counts": {}, "delay_trend": [], "stage_distribution": {}},
        "risk_register": [{"name": "פרויקט א", "identifier": "P001", "type": "הקמה",
                           "stage": "ביצוע", "risk_score": 85, "main_reason": "איחור"}],
        "action_items": [{"item": "טיפול בפרויקט א", "owner": "מנהל", "priority": "HIGH", "main_reason": ""}],
    }

    with patch("app.services.project_report_service.llm_chat") as mock_llm:
        mock_llm.return_value = json.dumps({
            "executive_narrative": "המצב הכולל דורש תשומת לב.",
            "portfolio_narrative": "פרויקטי הקמה מובילים בסיכון.",
            "risk_narrative": "פרויקט א נמצא בסיכון גבוה.",
            "action_narrative": "יש לטפל בפרויקט א בדחיפות.",
        })
        html = await generate_report_html(sample_data)

    assert html.startswith("<!DOCTYPE html")
    assert "דוח פרויקטים" in html
    assert "המצב הכולל" in html
    assert "פרויקט א" in html
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
docker exec shan-ai-api pytest tests/test_project_report_service.py::test_generate_report_html_returns_html_string -v
```

Expected: `ImportError` for `generate_report_html`

- [ ] **Step 3: Add `generate_report_html` and `_REPORT_PROMPT` to `project_report_service.py`**

Append to `app/services/project_report_service.py`:

```python
import json as _json

_REPORT_PROMPT = """\
כתוב בעברית שוטפת ומקצועית. אתה כותב דוח פרויקטים לתשתיות חשמל.

נתוני קלט:
{data_json}

הנחיות: כתוב פסקה קצרה (3-4 משפטים) לכל אחד מ-4 המפתחות הבאים.
אל תוסיף מפתחות נוספים. החזר JSON בלבד, ללא טקסט לפני ואחרי.

{{
  "executive_narrative": "...",   
  "portfolio_narrative": "...",  
  "risk_narrative": "...",       
  "action_narrative": "..."      
}}"""


async def generate_report_html(data: dict) -> str:
    """Call Groq for narrative text, then wrap in full HTML template."""
    from app.services.llm_router import llm_chat

    prompt = _REPORT_PROMPT.format(
        data_json=_json.dumps({
            "executive_summary": data["executive_summary"],
            "top_risk_projects": [
                {"name": r["name"], "risk_score": r["risk_score"], "main_reason": r.get("main_reason", "")}
                for r in data["risk_register"][:5]
            ],
            "action_items_count": len(data["action_items"]),
        }, ensure_ascii=False)
    )

    raw = await llm_chat(usage="project_report", messages=[{"role": "user", "content": prompt}])

    try:
        # Strip markdown fences if present
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        narratives = _json.loads(clean)
    except Exception:
        logger.warning("project_report: LLM JSON parse failed, using empty narratives")
        narratives = {k: "" for k in ("executive_narrative", "portfolio_narrative", "risk_narrative", "action_narrative")}

    return _render_html(data, narratives)


def _rag_badge(status: str) -> str:
    colors = {"RED": "#ef4444", "AMBER": "#f59e0b", "GREEN": "#10b981"}
    labels = {"RED": "🔴 סיכון", "AMBER": "🟡 איחור", "GREEN": "🟢 תקין"}
    c = colors.get(status, "#64748b")
    return f'<span style="color:{c};font-weight:700;">{labels.get(status, status)}</span>'


def _render_html(data: dict, narratives: dict) -> str:
    meta = data["meta"]
    es = data["executive_summary"]
    ph = data["portfolio_health"]
    rr = data["risk_register"]
    ai = data["action_items"]

    # RAG type table rows
    rag_rows = "".join(
        f'<tr><td>{t}</td><td>{counts.get("active",0)}</td>'
        f'<td style="color:#f59e0b;">{counts.get("delayed",0)}</td>'
        f'<td style="color:#ef4444;">{counts.get("at_risk",0)}</td>'
        f'<td>{_rag_badge(es["rag_by_type"].get(t,"GREEN"))}</td></tr>'
        for t, counts in ph["type_counts"].items()
    )

    # Risk register rows
    risk_rows = "".join(
        f'<tr>'
        f'<td><strong>{r["name"]}</strong> <span style="color:#64748b;font-size:.8rem;">{r["identifier"]}</span></td>'
        f'<td>{r.get("type","")}</td>'
        f'<td>{r.get("stage","")}</td>'
        f'<td style="color:{"#ef4444" if r["risk_score"]>=70 else "#f59e0b" if r["risk_score"]>=40 else "#10b981"};">'
        f'<strong>{r["risk_score"]}</strong></td>'
        f'<td style="color:#94a3b8;font-size:.8rem;">{r.get("main_reason","")}</td>'
        f'</tr>'
        for r in rr
    )

    # Action items rows
    ai_rows = "".join(
        f'<tr>'
        f'<td style="color:{"#ef4444" if a["priority"]=="HIGH" else "#f59e0b"};">'
        f'{"⚠️ גבוה" if a["priority"]=="HIGH" else "🟡 בינוני"}</td>'
        f'<td>{a["item"]}</td>'
        f'<td style="color:#64748b;">{a.get("owner","")}</td>'
        f'</tr>'
        for a in ai
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>דוח פרויקטים — {meta["generated_at"]}</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{--bg:#070b12;--bg-c:#0f1826;--border:#1a2d47;--cyan:#00d4ff;--text:#e2e8f0;--text-2:#64748b;--red:#ef4444;--amber:#f59e0b;--green:#10b981;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);color:var(--text);font-family:'Heebo',sans-serif;direction:rtl;padding:24px;}}
  .page{{max-width:900px;margin:0 auto 40px;padding:32px;background:var(--bg-c);border:1px solid var(--border);border-radius:12px;page-break-after:always;}}
  .page-header{{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid var(--cyan);padding-bottom:12px;margin-bottom:20px;}}
  .page-title{{color:var(--cyan);font-size:1.3rem;font-weight:700;}}
  .page-meta{{color:var(--text-2);font-size:.82rem;}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;}}
  .kpi{{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center;}}
  .kpi-val{{font-size:2rem;font-weight:700;color:var(--cyan);}}
  .kpi-val.warn{{color:var(--amber);}} .kpi-val.danger{{color:var(--red);}}
  .kpi-label{{font-size:.75rem;color:var(--text-2);margin-top:4px;}}
  .narrative{{color:#cbd5e1;line-height:1.8;font-size:.92rem;margin-bottom:20px;background:var(--bg);border-right:3px solid var(--cyan);padding:12px 16px;border-radius:4px;}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem;}}
  th{{color:var(--text-2);border-bottom:1px solid var(--border);padding:7px 8px;text-align:right;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;}}
  td{{border-bottom:1px solid rgba(26,45,71,.5);padding:8px;}}
  tr:hover td{{background:rgba(0,212,255,.04);}}
  h3{{color:var(--cyan);font-size:1rem;margin:16px 0 10px;}}
  @media print{{body{{background:#fff;color:#000;}} .page{{background:#fff;border:1px solid #ccc;color:#000;}}}}
</style>
</head>
<body>

<!-- PAGE 1: EXECUTIVE SUMMARY -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📊 דוח פרויקטים — סיכום מנהלים</div>
    <div class="page-meta">נוצר: {meta["generated_at"]} | {meta["username"]} | {meta["role"]}</div>
  </div>
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">{es["total_active"]}</div><div class="kpi-label">פרויקטים פעילים</div></div>
    <div class="kpi"><div class="kpi-val warn">{es["total_delayed"]}</div><div class="kpi-label">באיחור</div></div>
    <div class="kpi"><div class="kpi-val danger">{es["total_at_risk"]}</div><div class="kpi-label">סיכון גבוה (≥70)</div></div>
    <div class="kpi"><div class="kpi-val" style="color:#a78bfa;">{es["entering_next_week"]}</div><div class="kpi-label">חיזוי — נכנסים לסיכון</div></div>
    <div class="kpi"><div class="kpi-val">{es["avg_risk_score"]}</div><div class="kpi-label">ציון סיכון ממוצע</div></div>
    <div class="kpi"><div class="kpi-val">{es["approval_rate_pct"]}%</div><div class="kpi-label">אחוז אישורי החלטות</div></div>
  </div>
  <div class="narrative">{narratives.get("executive_narrative","")}</div>
  <h3>סטטוס לפי סוג פרויקט</h3>
  <table>
    <thead><tr><th>סוג</th><th>פעיל</th><th>באיחור</th><th>סיכון</th><th>RAG</th></tr></thead>
    <tbody>{rag_rows}</tbody>
  </table>
</div>

<!-- PAGE 2: PORTFOLIO HEALTH -->
<div class="page">
  <div class="page-header">
    <div class="page-title">🏗️ בריאות תיק הפרויקטים</div>
    <div class="page-meta">{meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("portfolio_narrative","")}</div>
  <h3>מגמת איחורים — שבועות אחרונים</h3>
  <div style="display:flex;gap:6px;align-items:flex-end;height:80px;margin:12px 0 20px;background:var(--bg);padding:12px;border-radius:8px;">
    {"".join(
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:3px;flex:1;">'
        f'<div style="width:100%;height:{max(4,round(w["count"]/max(1,max(x["count"] for x in ph["delay_trend"]))*60))}px;'
        f'background:#ef4444;border-radius:3px 3px 0 0;"></div>'
        f'<span style="font-size:.6rem;color:var(--text-2);">{w["week"][5:]}</span></div>'
        for w in ph["delay_trend"]
    ) if ph["delay_trend"] else "<span style='color:var(--text-2);margin:auto;'>אין נתוני מגמה עדיין</span>"}
  </div>
  <h3>התפלגות שלבים</h3>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
    {"".join(
        f'<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 14px;font-size:.85rem;">'
        f'<span style="color:var(--cyan);font-weight:700;">{cnt}</span> {stage}</div>'
        for stage, cnt in ph["stage_distribution"].items()
    ) or "<span style='color:var(--text-2);'>אין נתונים</span>"}
  </div>
  <div style="margin-top:20px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:.82rem;color:var(--text-2);">
    🔢 סה"כ החלטות (30 ימים): <strong style="color:var(--text);">{es["decisions_30d"]}</strong> &nbsp;|&nbsp;
    ⚠️ קריטיות ממתינות: <strong style="color:#ef4444;">{es["critical_pending"]}</strong>
  </div>
</div>

<!-- PAGE 3: RISK REGISTER -->
<div class="page">
  <div class="page-header">
    <div class="page-title">⚠️ רישום סיכונים — 10 פרויקטים מובילים</div>
    <div class="page-meta">{meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("risk_narrative","")}</div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>שלב</th><th>ציון</th><th>סיבה עיקרית</th></tr></thead>
    <tbody>{risk_rows}</tbody>
  </table>
</div>

<!-- PAGE 4: ACTION ITEMS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">✅ פעולות נדרשות</div>
    <div class="page-meta">{meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("action_narrative","")}</div>
  <table>
    <thead><tr><th>עדיפות</th><th>פעולה</th><th>בעלים</th></tr></thead>
    <tbody>{ai_rows}</tbody>
  </table>
  <div style="margin-top:24px;padding:14px;background:var(--bg);border:1px solid rgba(0,212,255,.2);border-radius:8px;font-size:.8rem;color:var(--text-2);text-align:center;">
    דוח זה נוצר אוטומטית על ידי Shan-AI | {meta["generated_at"]}
  </div>
</div>

</body>
</html>"""
```

- [ ] **Step 4: Run tests**

```bash
docker exec shan-ai-api pytest tests/test_project_report_service.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/project_report_service.py tests/test_project_report_service.py
git commit -m "feat(reports): add generate_report_html with Groq narrative and HTML template"
```

---

## Task 4: video_report_service.py — Slides + Audio + MP4

**Files:**
- Create: `app/services/video_report_service.py`

This task has no unit tests (video generation requires ffmpeg + fonts at runtime). Verified by smoke test in Task 8.

- [ ] **Step 1: Create `app/services/video_report_service.py`**

```python
"""Video report service — generate MP4 slide video from report data."""
import os
import logging
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# 1280×720 dark theme matching dashboard
_W, _H = 1280, 720
_BG    = (7,  11, 18)
_BG_C  = (12, 18, 32)
_CYAN  = (0, 212, 255)
_TEXT  = (226, 232, 240)
_TEXT2 = (100, 116, 139)
_RED   = (239, 68,  68)
_AMBER = (245, 158, 11)
_GREEN = (16,  185, 129)

_NOTO_PATH = "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf"
_FALLBACK  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _get_font(size: int):
    from PIL import ImageFont
    path = _NOTO_PATH if os.path.exists(_NOTO_PATH) else _FALLBACK
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _rtl(text: str) -> str:
    """Apply bidi algorithm so PIL renders Hebrew RTL correctly."""
    try:
        from bidi.algorithm import get_display
        return get_display(text)
    except Exception:
        return text


def _draw_text_ra(draw, x: int, y: int, text: str, font, fill):
    """Draw text right-aligned at (x, y)."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x - w, y), text, font=font, fill=fill)
    except AttributeError:
        # older Pillow fallback
        w, _ = draw.textsize(text, font=font)
        draw.text((x - w, y), text, font=font, fill=fill)


def _draw_text_center(draw, x: int, y: int, text: str, font, fill):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x - w // 2, y), text, font=font, fill=fill)
    except AttributeError:
        w, _ = draw.textsize(text, font=font)
        draw.text((x - w // 2, y), text, font=font, fill=fill)


def _make_slide(title: str, lines: list[str], slide_n: int, total: int,
                accent_color=None) -> "numpy.ndarray":
    """Render one slide as numpy array (H×W×3)."""
    import numpy as np
    from PIL import Image, ImageDraw

    accent = accent_color or _CYAN
    img = Image.new("RGB", (_W, _H), _BG)
    draw = ImageDraw.Draw(img)

    # Header bar
    draw.rectangle([0, 0, _W, 90], fill=_BG_C)
    draw.rectangle([0, 88, _W, 93], fill=accent)

    # Logo / brand (left side of header)
    font_brand = _get_font(20)
    draw.text((40, 32), "Shan-AI", font=font_brand, fill=_CYAN)

    # Title (center of header, RTL)
    font_title = _get_font(38)
    _draw_text_center(draw, _W // 2, 24, _rtl(title), font_title, accent)

    # Content lines
    font_body = _get_font(26)
    y = 120
    for line in lines:
        if line.startswith("---"):
            draw.rectangle([60, y + 10, _W - 60, y + 12], fill=_BG_C)
            y += 30
            continue
        col = _TEXT
        if line.startswith("🔴"):
            col = _RED
        elif line.startswith("🟡"):
            col = _AMBER
        elif line.startswith("🟢"):
            col = _GREEN
        _draw_text_ra(draw, _W - 60, y, _rtl(line), font_body, col)
        y += 46

    # Footer
    draw.rectangle([0, _H - 50, _W, _H], fill=_BG_C)
    font_small = _get_font(18)
    _draw_text_center(draw, _W // 2, _H - 36, f"{slide_n} / {total}", font_small, _TEXT2)
    _draw_text_ra(draw, _W - 40, _H - 36, _rtl("שן-AI • מודיעין תפעולי"), font_small, _CYAN)

    return np.array(img)


def _make_audio(text: str, lang: str = "he") -> Optional[str]:
    """Generate TTS MP3, return temp file path. Returns None on failure."""
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang=lang, slow=False)
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        tts.save(path)
        return path
    except Exception as e:
        logger.warning(f"gTTS failed: {e}")
        return None


def _slides_from_data(data: dict) -> list[tuple[str, list[str], str]]:
    """
    Returns list of (title, content_lines, narration_text) per slide.
    6 slides total.
    """
    meta = data["meta"]
    es   = data["executive_summary"]
    ph   = data["portfolio_health"]
    rr   = data["risk_register"][:5]
    ai   = data["action_items"][:3]

    slides = []

    # Slide 1: Title
    slides.append((
        "דוח פרויקטים שבועי",
        [
            f"נוצר: {meta['generated_at']}",
            f"עבור: {meta['username']} — {meta['role']}",
            "---",
            f"סה\"כ פרויקטים פעילים: {es['total_active']}",
            f"🔴 באיחור: {es['total_delayed']}   ⚠️ סיכון גבוה: {es['total_at_risk']}",
        ],
        f"ברוכים הבאים לדוח הפרויקטים השבועי. נוצר ב-{meta['generated_at']}. "
        f"יש לנו {es['total_active']} פרויקטים פעילים, מהם {es['total_delayed']} באיחור ו-{es['total_at_risk']} בסיכון גבוה.",
    ))

    # Slide 2: Executive Summary
    rag_lines = [f"{t}: {'🔴 סיכון' if s=='RED' else '🟡 איחור' if s=='AMBER' else '🟢 תקין'}"
                 for t, s in es["rag_by_type"].items()]
    slides.append((
        "סיכום מנהלים",
        [
            f"ציון סיכון ממוצע: {es['avg_risk_score']}",
            f"החלטות (30 ימים): {es['decisions_30d']}  |  אחוז אישורים: {es['approval_rate_pct']}%",
            "---",
            *rag_lines[:4],
        ],
        f"ציון הסיכון הממוצע עומד על {es['avg_risk_score']}. "
        f"אחוז אישורי ההחלטות עומד על {es['approval_rate_pct']} אחוז. "
        f"{'יש ' + str(es['critical_pending']) + ' החלטות קריטיות הממתינות לאישור.' if es['critical_pending'] else 'אין החלטות קריטיות ממתינות.'}",
    ))

    # Slide 3: Portfolio Health
    type_lines = []
    for t, counts in list(ph["type_counts"].items())[:4]:
        type_lines.append(f"{t}: {counts.get('active',0)} פעיל, {counts.get('delayed',0)} באיחור")
    slides.append((
        "בריאות תיק הפרויקטים",
        type_lines or ["אין נתוני סוגים עדיין"],
        "סקירת תיק הפרויקטים לפי סוגים. " +
        " ".join(f"{t} — {c.get('active',0)} פרויקטים פעילים." for t, c in list(ph["type_counts"].items())[:3]),
    ))

    # Slide 4: Risk Register
    risk_lines = [
        f"{'🔴' if r['risk_score']>=70 else '🟡'} {r['name']} — ציון {r['risk_score']}"
        for r in rr
    ] or ["אין פרויקטים בסיכון גבוה"]
    slides.append((
        "רישום סיכונים",
        risk_lines,
        "הפרויקטים בסיכון הגבוה ביותר: " +
        ", ".join(f"{r['name']} עם ציון {r['risk_score']}" for r in rr[:3]) + ".",
    ))

    # Slide 5: Action Items
    action_lines = [
        f"{'⚠️' if a['priority']=='HIGH' else '🟡'} {a['item'][:60]}"
        for a in ai
    ] or ["אין פעולות נדרשות דחופות"]
    slides.append((
        "פעולות נדרשות",
        action_lines,
        "פעולות מומלצות לשבוע הבא: " +
        ". ".join(a["item"][:80] for a in ai[:3]) + ".",
    ))

    # Slide 6: Closing
    slides.append((
        "סיכום",
        [
            "נקודות עיקריות:",
            f"• {es['total_active']} פרויקטים פעילים",
            f"• {es['total_delayed']} פרויקטים באיחור — דורשים תשומת לב",
            f"• {es['total_at_risk']} פרויקטים בסיכון גבוה",
            "---",
            "Shan-AI — מודיעין תפעולי לתשתיות חשמל",
        ],
        f"לסיכום: תיק הפרויקטים מכיל {es['total_active']} פרויקטים פעילים. "
        f"יש לטפל ב-{es['total_delayed']} פרויקטים באיחור ו-{es['total_at_risk']} בסיכון גבוה. "
        "תודה על הצפייה.",
    ))

    return slides


async def generate_report_video(data: dict, report_id: int) -> Optional[str]:
    """
    Generate a 720p MP4 slide video for the given report_data.
    Returns relative path like 'project_reports/42.mp4', or None on failure.
    """
    try:
        from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips
    except ImportError:
        logger.error("moviepy not installed — video generation skipped")
        return None

    os.makedirs("static/project_reports", exist_ok=True)
    out_path = f"static/project_reports/{report_id}.mp4"

    slides_def = _slides_from_data(data)
    total = len(slides_def)
    slide_duration = 9  # seconds per slide
    clips = []
    tmp_audio_files = []

    try:
        for i, (title, lines, narration) in enumerate(slides_def, 1):
            # Render slide image
            arr = _make_slide(title, lines, i, total)
            img_clip = ImageClip(arr, duration=slide_duration)

            # Generate TTS audio
            audio_path = _make_audio(narration)
            if audio_path:
                tmp_audio_files.append(audio_path)
                try:
                    audio_clip = AudioFileClip(audio_path)
                    # Trim audio to slide duration (don't extend)
                    if audio_clip.duration > slide_duration:
                        audio_clip = audio_clip.subclip(0, slide_duration)
                    img_clip = img_clip.set_audio(audio_clip)
                except Exception as ae:
                    logger.warning(f"audio attach failed for slide {i}: {ae}")

            clips.append(img_clip)

        # Concatenate and write
        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(
            out_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,
            verbose=False,
        )
        return f"project_reports/{report_id}.mp4"

    except Exception as e:
        logger.error(f"video generation failed: {e}")
        return None

    finally:
        for p in tmp_audio_files:
            try:
                os.unlink(p)
            except Exception:
                pass
```

- [ ] **Step 2: Commit**

```bash
git add app/services/video_report_service.py
git commit -m "feat(reports): add video_report_service with Pillow slides + gTTS + moviepy"
```

---

## Task 5: Router + Endpoints

**Files:**
- Create: `app/routers/project_reports.py`

- [ ] **Step 1: Create `app/routers/project_reports.py`**

```python
"""Project report endpoints."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import ProjectReport, User
from app.routers.login import get_current_user
from app.templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/project-reports", tags=["project-reports"])


@router.get("", response_class=HTMLResponse)
async def project_reports_list(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    reports = (await session.execute(
        select(ProjectReport)
        .where(ProjectReport.user_id == current_user.id)
        .order_by(desc(ProjectReport.generated_at))
        .limit(20)
    )).scalars().all()

    return templates.TemplateResponse("project_reports.html", {
        "request": request,
        "current_user": current_user,
        "reports": [
            {
                "id": r.id,
                "generated_at": r.generated_at.strftime("%d/%m/%Y %H:%M"),
                "has_video": bool(r.video_path),
                "notebooklm_url": r.notebooklm_url,
            }
            for r in reports
        ],
    })


@router.post("/generate", response_class=HTMLResponse)
async def project_reports_generate(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    from app.services.project_report_service import gather_report_data, generate_report_html

    report_data = await gather_report_data(current_user, session)
    html = await generate_report_html(report_data)

    report = ProjectReport(
        user_id=current_user.id,
        report_data=report_data,
        html_content=html,
    )
    session.add(report)
    await session.flush()  # get the id before video generation
    await session.commit()

    # Generate video in background (non-blocking)
    report_id = report.id
    asyncio.create_task(_generate_video_background(report_id, report_data))

    return RedirectResponse(f"/dashboard/project-reports/{report_id}", status_code=302)


async def _generate_video_background(report_id: int, report_data: dict) -> None:
    """Background task: generate video and update DB with path."""
    from app.database import async_session_maker
    from app.services.video_report_service import generate_report_video

    video_path = await generate_report_video(report_data, report_id)
    if video_path:
        async with async_session_maker() as s:
            report = await s.get(ProjectReport, report_id)
            if report:
                report.video_path = video_path
                await s.commit()
        logger.info(f"Video saved: {video_path}")


@router.get("/{report_id}", response_class=HTMLResponse)
async def project_report_detail(
    report_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    report = await session.scalar(
        select(ProjectReport).where(
            ProjectReport.id == report_id,
            ProjectReport.user_id == current_user.id,
        )
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return templates.TemplateResponse("project_report_detail.html", {
        "request": request,
        "current_user": current_user,
        "report": {
            "id":            report.id,
            "generated_at":  report.generated_at.strftime("%d/%m/%Y %H:%M"),
            "html_content":  report.html_content or "",
            "video_path":    report.video_path,
            "notebooklm_url": report.notebooklm_url,
        },
    })


@router.post("/{report_id}/delete", response_class=HTMLResponse)
async def project_report_delete(
    report_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    import os
    report = await session.scalar(
        select(ProjectReport).where(
            ProjectReport.id == report_id,
            ProjectReport.user_id == current_user.id,
        )
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.video_path:
        full = os.path.join("static", report.video_path)
        try:
            os.unlink(full)
        except FileNotFoundError:
            pass

    await session.delete(report)
    await session.commit()
    return RedirectResponse("/dashboard/project-reports", status_code=302)
```

- [ ] **Step 2: Check `templates_config`**

Check if `app/templates_config.py` or equivalent exists:

```bash
grep -rn "templates = Jinja2Templates\|from.*templates" app/routers/dashboard.py | head -5
```

If the templates object is imported from a shared module, use the same import. If it's `from fastapi.templating import Jinja2Templates` with `templates = Jinja2Templates(directory="app/templates")`, use the same pattern in the new router.

Adjust the `templates` import in `project_reports.py` to match the existing pattern.

- [ ] **Step 3: Commit**

```bash
git add app/routers/project_reports.py
git commit -m "feat(reports): add project_reports router with generate, list, detail, delete"
```

---

## Task 6: Templates

**Files:**
- Create: `app/templates/project_reports.html`
- Create: `app/templates/project_report_detail.html`

- [ ] **Step 1: Create `app/templates/project_reports.html`**

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shan-AI — דוחות פרויקטים</title>
  <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { --bg-deep:#070b12; --bg-surface:#0c1220; --bg-card:#0f1826; --border:#1a2d47; --cyan:#00d4ff; --text-1:#e2e8f0; --text-2:#64748b; }
    body { background:var(--bg-deep); color:var(--text-1); font-family:'Heebo',sans-serif; min-height:100vh; }
    .navbar { background:var(--bg-surface); border-bottom:1px solid var(--border); padding:12px 24px; }
    .page-title { color:var(--cyan); font-size:1.3rem; font-weight:700; }
    .card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; }
    .table { color:var(--text-1); } .table th { color:var(--text-2); border-color:var(--border); font-size:.8rem; text-transform:uppercase; } .table td { border-color:var(--border); vertical-align:middle; }
    .btn-cyan { background:var(--cyan); color:#000; font-weight:600; border:none; border-radius:6px; padding:7px 20px; }
    .btn-outline-dim { border:1px solid var(--border); color:var(--text-2); background:transparent; border-radius:6px; padding:6px 16px; }
    .btn-outline-dim:hover { color:var(--text-1); border-color:var(--cyan); }
    .badge-video { background:rgba(0,212,255,.12); color:var(--cyan); font-size:.72rem; padding:2px 8px; border-radius:4px; }
  </style>
</head>
<body>
<nav class="navbar d-flex justify-content-between align-items-center">
  <span class="page-title">📋 דוחות פרויקטים</span>
  <div class="d-flex gap-2 align-items-center">
    <span style="color:var(--text-2);font-size:.85rem;">{{ current_user.username }}</span>
    <form method="post" action="/dashboard/project-reports/generate" style="margin:0;">
      <button type="submit" class="btn-cyan">🔄 צור דוח חדש</button>
    </form>
    <a href="/dashboard" class="btn-outline-dim">← לוח בקרה</a>
    <a href="/logout" class="btn-outline-dim">יציאה</a>
  </div>
</nav>

<div class="container-fluid px-4 py-4">
  <div class="card p-3">
    {% if reports %}
    <table class="table table-hover mb-0">
      <thead><tr><th>תאריך יצירה</th><th>וידאו</th><th>NotebookLM</th><th>פעולות</th></tr></thead>
      <tbody>
      {% for r in reports %}
      <tr>
        <td><strong>{{ r.generated_at }}</strong></td>
        <td>
          {% if r.has_video %}<span class="badge-video">🎬 מוכן</span>
          {% else %}<span style="color:var(--text-2);font-size:.8rem;">בהכנה...</span>{% endif %}
        </td>
        <td>
          {% if r.notebooklm_url %}
          <a href="{{ r.notebooklm_url }}" target="_blank" style="color:var(--cyan);font-size:.82rem;">🎙️ פתח</a>
          {% else %}<span style="color:var(--text-2);font-size:.8rem;">—</span>{% endif %}
        </td>
        <td class="d-flex gap-2">
          <a href="/dashboard/project-reports/{{ r.id }}" class="btn-outline-dim">👁 צפה</a>
          <form method="post" action="/dashboard/project-reports/{{ r.id }}/delete"
                onsubmit="return confirm('למחוק דוח זה?');" style="margin:0;">
            <button type="submit" class="btn-outline-dim" style="color:#ef4444;">🗑</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div style="text-align:center;padding:60px 0;color:var(--text-2);">
      <p style="font-size:1.1rem;">אין דוחות עדיין.</p>
      <form method="post" action="/dashboard/project-reports/generate" style="margin-top:16px;">
        <button type="submit" class="btn-cyan">🔄 צור דוח ראשון</button>
      </form>
    </div>
    {% endif %}
  </div>
</div>
</body>
</html>
```

- [ ] **Step 2: Create `app/templates/project_report_detail.html`**

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shan-AI — דוח פרויקטים {{ report.generated_at }}</title>
  <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { --bg-deep:#070b12; --bg-surface:#0c1220; --bg-card:#0f1826; --border:#1a2d47; --cyan:#00d4ff; --text-1:#e2e8f0; --text-2:#64748b; }
    body { background:var(--bg-deep); color:var(--text-1); font-family:'Heebo',sans-serif; min-height:100vh; }
    .navbar { background:var(--bg-surface); border-bottom:1px solid var(--border); padding:12px 24px; }
    .page-title { color:var(--cyan); font-size:1.2rem; font-weight:700; }
    .btn-outline-dim { border:1px solid var(--border); color:var(--text-2); background:transparent; border-radius:6px; padding:6px 16px; text-decoration:none; }
    .btn-outline-dim:hover { color:var(--text-1); border-color:var(--cyan); }
    .section-card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; margin-bottom:16px; }
    .section-header { padding:12px 16px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; font-weight:600; color:var(--cyan); }
    .section-body { padding:0; border-top:1px solid var(--border); }
    iframe { width:100%; border:none; background:#070b12; }
    video { width:100%; border-radius:8px; background:#000; }
    .video-card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px; }
    .spinner { display:inline-block; width:16px; height:16px; border:2px solid rgba(0,212,255,.3); border-top-color:var(--cyan); border-radius:50%; animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
  </style>
</head>
<body>
<nav class="navbar d-flex justify-content-between align-items-center">
  <span class="page-title">📋 דוח {{ report.generated_at }}</span>
  <div class="d-flex gap-2 align-items-center">
    <span style="color:var(--text-2);font-size:.85rem;">{{ current_user.username }}</span>
    <a href="/dashboard/project-reports" class="btn-outline-dim">← רשימת דוחות</a>
    <a href="/logout" class="btn-outline-dim">יציאה</a>
  </div>
</nav>

<div class="container-fluid px-4 py-4">

  <!-- VIDEO SECTION -->
  <div class="video-card">
    <div style="font-size:.9rem;font-weight:600;color:var(--cyan);margin-bottom:12px;">🎬 וידאו הדוח</div>
    {% if report.video_path %}
    <video controls preload="metadata">
      <source src="/static/{{ report.video_path }}" type="video/mp4">
      הדפדפן שלך אינו תומך בהפעלת וידאו.
    </video>
    {% else %}
    <div style="color:var(--text-2);padding:20px 0;" id="video-pending">
      <span class="spinner"></span> &nbsp; הוידאו נמצא בהכנה — רענן בעוד מספר דקות.
    </div>
    <script>
      // Auto-refresh every 15s until video is ready
      setTimeout(() => location.reload(), 15000);
    </script>
    {% endif %}

    {% if report.notebooklm_url %}
    <div style="margin-top:12px;font-size:.82rem;">
      🎙️ <a href="{{ report.notebooklm_url }}" target="_blank" style="color:var(--cyan);">
        פתח Audio Overview ב-NotebookLM
      </a>
    </div>
    {% endif %}
  </div>

  <!-- HTML REPORT (iframe) -->
  <div class="section-card">
    <div class="section-header" onclick="toggleReport()">
      <span>📄 דוח HTML מלא</span>
      <span id="rep-arr">▼</span>
    </div>
    <div class="section-body" id="rep-body">
      <iframe id="rep-frame" srcdoc="{{ report.html_content | e }}" style="height:1200px;"></iframe>
    </div>
  </div>

</div>

<script>
function toggleReport() {
  const b = document.getElementById('rep-body');
  const a = document.getElementById('rep-arr');
  b.style.display = b.style.display === 'none' ? '' : 'none';
  a.textContent = b.style.display === 'none' ? '▶' : '▼';
}
</script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/project_reports.html app/templates/project_report_detail.html
git commit -m "feat(reports): add project report list and detail templates"
```

---

## Task 7: Infrastructure — Dockerfile + requirements + static dir + main.py

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Modify: `app/main.py`
- Create: `static/project_reports/.gitkeep`

- [ ] **Step 1: Add dependencies to `requirements.txt`**

Add these lines to `requirements.txt` (after the existing Pillow line):

```
moviepy==1.0.3
gTTS>=2.5.0
python-bidi>=0.4.2
numpy>=1.24.0
```

- [ ] **Step 2: Update `Dockerfile`**

Replace the existing `RUN apt-get` block with:

```dockerfile
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    ffmpeg \
    fonts-noto-unhinted \
    libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*
```

`ffmpeg` is required by moviepy. `fonts-noto-unhinted` provides Hebrew font. `libsm6 libxext6 libxrender-dev` are required by OpenCV (indirect moviepy dep on some systems).

- [ ] **Step 3: Create static directory placeholder**

```bash
mkdir -p static/project_reports
echo "" > static/project_reports/.gitkeep
git add static/project_reports/.gitkeep
```

- [ ] **Step 4: Register router in `app/main.py`**

Find where other routers are included (grep for `include_router`). Add after the last existing `include_router` call:

```python
from app.routers.project_reports import router as project_reports_router
app.include_router(project_reports_router)
```

Also check the templates import pattern used in other routers (e.g., `from app.templates_config import templates` or `templates = Jinja2Templates(directory="app/templates")`). In `project_reports.py` Step 2 of Task 5, update the `templates` import to match the project's actual pattern.

To check:
```bash
grep -n "Jinja2Templates\|templates" app/routers/dashboard.py | head -5
```

- [ ] **Step 5: Rebuild Docker locally**

```bash
docker-compose down && docker-compose up --build -d && sleep 15 && docker logs shan-ai-api --tail 20
```

Expected: `Application startup complete.` (build will take longer due to new packages)

- [ ] **Step 6: Smoke test report generation**

Open `http://localhost:8000/dashboard/project-reports` in browser (logged in).

Click "🔄 צור דוח חדש":
- Should redirect to the detail page
- HTML report should render in the iframe
- Video section shows "בהכנה..." until video is ready (auto-refreshes)
- After ~2 minutes, reload — video should appear and be playable

- [ ] **Step 7: Commit**

```bash
git add requirements.txt Dockerfile app/main.py static/project_reports/.gitkeep
git commit -m "feat(reports): wire router, update Dockerfile with ffmpeg+fonts, add static dir"
```

---

## Task 8: Railway DB Migration + Deploy

- [ ] **Step 1: Run migration on Railway DB**

```bash
TOKEN="$RAILWAY_API_TOKEN"
# Use the existing Railway Postgres connection
docker exec shan-ai-api python -c "
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

RAILWAY_URL = '$RAILWAY_DATABASE_URL'

async def run():
    engine = create_async_engine(RAILWAY_URL)
    async with engine.begin() as conn:
        await conn.execute(text('''
CREATE TABLE IF NOT EXISTS project_reports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    report_data JSONB,
    html_content TEXT,
    video_path VARCHAR(500),
    notebooklm_url VARCHAR(500)
)'''))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_project_reports_user_id ON project_reports(user_id)'))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_project_reports_generated_at ON project_reports(generated_at)'))
    await engine.dispose()
    print('railway migration ok')

asyncio.run(run())
"
```

Expected: `railway migration ok`

- [ ] **Step 2: Push and redeploy**

```bash
git push origin master

TOKEN="$RAILWAY_API_TOKEN"
SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"
ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { serviceInstanceDeploy(serviceId: \\\"$SVC_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}"
```

Expected: `{"data":{"serviceInstanceDeploy":true}}`

Note: Railway build will be slower (~5 min) due to ffmpeg + fonts installation.

- [ ] **Step 3: Verify Railway app**

Navigate to `https://easygoing-endurance-production-df54.up.railway.app/dashboard/project-reports`.

Click "צור דוח חדש" → verify HTML report renders. Wait ~3 min → verify video appears.

**Important:** Railway has an ephemeral filesystem. MP4 files will be lost on each redeploy. To persist videos across deploys, either:
a) Add a Railway Volume (persistent disk) and change `static/project_reports/` to mount there
b) Accept that videos are regenerated after each deploy (reports are in DB, just re-generate video on request)

For now, option (b) is acceptable — the report HTML is always in DB.

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "chore: smoke-tested project report + video on Railway" --allow-empty
```

---

## Task 9: NotebookLM MCP Local Setup

This is a local Claude Code configuration — not Railway. Run these steps in your local terminal.

- [ ] **Step 1: Install Node.js (if not present)**

Check: `node --version`. If not installed, install from https://nodejs.org (LTS version).

- [ ] **Step 2: Install the NotebookLM MCP server**

```bash
npx -y @roomi/notebooklm-mcp --version
```

If that doesn't work, try the alternative:

```bash
npm install -g notebooklm-mcp
```

- [ ] **Step 3: Configure in Claude Code settings**

Open `C:\Users\livya\.claude\settings.json` and add to the `mcpServers` section:

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "npx",
      "args": ["-y", "@roomi/notebooklm-mcp"],
      "env": {}
    }
  }
}
```

If `mcpServers` section doesn't exist, add it at the top level.

- [ ] **Step 4: Restart Claude Code and authenticate**

Restart Claude Code. On first use, the MCP server will open a Chrome browser window asking you to log in to your Google account. Log in once — cookies are persisted.

- [ ] **Step 5: Test the MCP**

In a new Claude Code session, say:

> "Use the notebooklm MCP to create a new notebook called 'Test' and add this text as a source: 'Hello world project report test'"

If the MCP is working, Claude will create the notebook automatically.

- [ ] **Step 6: Wire notebooklm_url into the report**

Once the MCP is confirmed working, Claude Code can:
1. Generate a report via `POST /dashboard/project-reports/generate`
2. Read the report HTML
3. Use MCP to create a NotebookLM notebook with the report text as a source
4. Request an Audio Overview
5. Get the notebook URL
6. Call `PATCH /dashboard/project-reports/{id}/notebooklm_url` to save the URL

This last step is a manual Claude-Code workflow — not a fully automated server feature — since NotebookLM requires browser auth.

---

## Verification Checklist

- [ ] `project_reports` table exists in local and Railway DB
- [ ] `/dashboard/project-reports` page loads with "צור דוח חדש" button
- [ ] Generating a report produces a 4-section HTML with all sections populated
- [ ] HTML renders in iframe on detail page
- [ ] Video generates (~2 min) and plays in the `<video>` element
- [ ] Video has Hebrew audio narration per slide
- [ ] Detail page auto-refreshes until video is ready
- [ ] Delete button removes report + video file
- [ ] Railway deploy succeeds (longer build due to ffmpeg)
- [ ] NotebookLM MCP authenticates and can create notebooks
