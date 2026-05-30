"""Project portfolio report service — data gathering, HTML generation."""
import json as _json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Project, ProjectSnapshot, Decision,
    ProjectReport, RoleEnum, DecisionTypeEnum, DecisionStatusEnum,
)
from app.services.project_learning_service import get_overview_stats, get_risk_table
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


async def gather_report_data(user: User, session: AsyncSession) -> dict:
    """Assemble all data needed to render the report and video."""
    overview = await get_overview_stats(session)
    risk_rows = await get_risk_table(session)

    totals      = overview.get("totals", {})
    type_counts = overview.get("type_counts", {})

    # RAG status per type
    rag_by_type = {}
    for t, counts in type_counts.items():
        if counts.get("at_risk", 0) > 0:
            rag_by_type[t] = "RED"
        elif counts.get("delayed", 0) > 0:
            rag_by_type[t] = "AMBER"
        else:
            rag_by_type[t] = "GREEN"

    # Avg risk score
    avg_risk_row = (await session.execute(
        select(func.avg(ProjectSnapshot.risk_score))
        .where(ProjectSnapshot.is_active == True, ProjectSnapshot.risk_score.isnot(None))
    )).scalar()
    avg_risk = round(float(avg_risk_row or 0))

    # Decisions last 30 days
    since30 = datetime.utcnow() - timedelta(days=30)
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
    total_dec    = dec_row[0] or 0
    approval_rate = round((dec_row[2] / total_dec * 100) if total_dec else 0)

    # Action items from high-risk projects
    delayed = [r for r in risk_rows if r.get("risk_score", 0) >= 60][:5]
    action_items = [
        {
            "item":        f"טיפול בפרויקט {r['name']} — ציון סיכון {r['risk_score']}",
            "owner":       r.get("stage", "—"),
            "priority":    "HIGH" if r["risk_score"] >= 80 else "MEDIUM",
            "main_reason": r.get("main_reason", ""),
        }
        for r in delayed
    ]

    return {
        "meta": {
            "generated_at": datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
            "username":     user.username or "—",
            "role":         user.role.value if user.role else "—",
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
    es   = data["executive_summary"]
    ph   = data["portfolio_health"]
    rr   = data["risk_register"]
    ai   = data["action_items"]

    rag_rows = "".join(
        f'<tr><td>{t}</td><td>{counts.get("active",0)}</td>'
        f'<td style="color:#f59e0b;">{counts.get("delayed",0)}</td>'
        f'<td style="color:#ef4444;">{counts.get("at_risk",0)}</td>'
        f'<td>{_rag_badge(es["rag_by_type"].get(t,"GREEN"))}</td></tr>'
        for t, counts in ph["type_counts"].items()
    )

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

    ai_rows = "".join(
        f'<tr>'
        f'<td style="color:{"#ef4444" if a["priority"]=="HIGH" else "#f59e0b"};">'
        f'{"⚠️ גבוה" if a["priority"]=="HIGH" else "🟡 בינוני"}</td>'
        f'<td>{a["item"]}</td>'
        f'<td style="color:#64748b;">{a.get("owner","")}</td>'
        f'</tr>'
        for a in ai
    )

    trend_bars = ""
    if ph["delay_trend"]:
        max_cnt = max(1, max(w["count"] for w in ph["delay_trend"]))
        trend_bars = "".join(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:3px;flex:1;">'
            f'<div style="width:100%;height:{max(4,round(w["count"]/max_cnt*60))}px;'
            f'background:#ef4444;border-radius:3px 3px 0 0;"></div>'
            f'<span style="font-size:.6rem;color:var(--text-2);">{w["week"][5:]}</span></div>'
            for w in ph["delay_trend"]
        )
    else:
        trend_bars = "<span style='color:var(--text-2);margin:auto;'>אין נתוני מגמה עדיין</span>"

    stage_badges = "".join(
        f'<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 14px;font-size:.85rem;">'
        f'<span style="color:var(--cyan);font-weight:700;">{cnt}</span> {stage}</div>'
        for stage, cnt in ph["stage_distribution"].items()
    ) or "<span style='color:var(--text-2);'>אין נתונים</span>"

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
</style>
</head>
<body>

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

<div class="page">
  <div class="page-header">
    <div class="page-title">🏗️ בריאות תיק הפרויקטים</div>
    <div class="page-meta">{meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("portfolio_narrative","")}</div>
  <h3>מגמת איחורים — שבועות אחרונים</h3>
  <div style="display:flex;gap:6px;align-items:flex-end;height:80px;margin:12px 0 20px;background:var(--bg);padding:12px;border-radius:8px;">{trend_bars}</div>
  <h3>התפלגות שלבים</h3>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">{stage_badges}</div>
  <div style="margin-top:20px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:.82rem;color:var(--text-2);">
    🔢 סה"כ החלטות (30 ימים): <strong style="color:var(--text);">{es["decisions_30d"]}</strong> &nbsp;|&nbsp;
    ⚠️ קריטיות ממתינות: <strong style="color:#ef4444;">{es["critical_pending"]}</strong>
  </div>
</div>

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
