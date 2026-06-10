"""Project portfolio report service — data gathering, HTML generation."""
import asyncio
import json as _json
import logging
from datetime import datetime, timedelta, date
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Project, ProjectSnapshot, Decision,
    ProjectReport, RoleEnum, DecisionTypeEnum, DecisionStatusEnum,
)
from app.services.project_learning_service import (
    get_overview_stats, get_risk_table, compute_risk_score,
)
from app.services.llm_router import llm_chat

logger = logging.getLogger(__name__)


def generate_pdf(html_content: str) -> bytes:
    from weasyprint import HTML, CSS
    return HTML(string=html_content, base_url=None).write_pdf(
        stylesheets=[CSS(string="@page { size: A4; margin: 10mm; }")]
    )


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

    # Portfolio trends: last 52 snapshot dates aggregated
    trends_raw = (await session.execute(text("""
        SELECT snapshot_date,
               ROUND(AVG(risk_score))::int AS avg_risk,
               COUNT(*) FILTER (WHERE days_overdue > 0) AS delayed_count,
               COUNT(*) FILTER (WHERE risk_score >= 70) AS at_risk_count
        FROM project_snapshots
        WHERE is_active = TRUE AND risk_score IS NOT NULL
        GROUP BY snapshot_date
        ORDER BY snapshot_date DESC
        LIMIT 52
    """))).all()

    trends_list = [
        {
            "date":          str(r[0]),
            "avg_risk":      int(r[1] or 0),
            "delayed_count": int(r[2] or 0),
            "at_risk_count": int(r[3] or 0),
        }
        for r in reversed(trends_raw)
    ]

    weekly_delta: dict = {"avg_risk": 0, "delayed_count": 0, "at_risk_count": 0}
    if len(trends_list) >= 2:
        cur = trends_list[-1]
        prv = trends_list[-2]
        weekly_delta = {
            "avg_risk":      cur["avg_risk"] - prv["avg_risk"],
            "delayed_count": cur["delayed_count"] - prv["delayed_count"],
            "at_risk_count": cur["at_risk_count"] - prv["at_risk_count"],
            "cur_avg_risk":  cur["avg_risk"],
            "prv_avg_risk":  prv["avg_risk"],
            "cur_delayed":   cur["delayed_count"],
            "prv_delayed":   prv["delayed_count"],
            "cur_at_risk":   cur["at_risk_count"],
            "prv_at_risk":   prv["at_risk_count"],
        }

    # Decisions last 30 days
    since30 = datetime.utcnow() - timedelta(days=30)

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

    # ── Extended data for new report pages ───────────────────────────────────
    today = date.today()
    all_projects = (await session.execute(
        select(Project).where(Project.is_active == True)
    )).scalars().all()

    finishing_30, finishing_60, finishing_90 = [], [], []
    delayed_detail = []
    stale_projects = []
    to_handle_items = []
    by_type_detail: dict = {}

    for proj in all_projects:
        score_res = compute_risk_score(
            stage=proj.stage,
            estimated_finish_date=proj.estimated_finish_date,
            dev_plan_date=proj.dev_plan_date,
            risks=proj.risks,
            to_handle=proj.to_handle,
            last_updated=proj.last_updated,
            weekly_report=proj.weekly_report,
            today=today,
        )
        score = score_res["score"]
        days_over = score_res["days_overdue"] or 0

        ptype = proj.project_type or "אחר"
        if ptype not in by_type_detail:
            by_type_detail[ptype] = []

        row = {
            "id":                  proj.id,
            "name":                proj.name or proj.project_identifier,
            "identifier":          proj.project_identifier,
            "type":                ptype,
            "stage":               proj.stage or "—",
            "manager":             proj.manager or "—",
            "risk_score":          score,
            "days_overdue":        days_over,
            "main_reason":         score_res["main_reason"],
            "estimated_finish_date": str(proj.estimated_finish_date) if proj.estimated_finish_date else None,
            "weekly_report_brief": proj.weekly_report_brief or "",
        }

        if proj.estimated_finish_date:
            days_until = (proj.estimated_finish_date - today).days
            if 0 <= days_until <= 30:
                finishing_30.append(row)
            elif 31 <= days_until <= 60:
                finishing_60.append(row)
            elif 61 <= days_until <= 90:
                finishing_90.append(row)

        if days_over > 0:
            delayed_detail.append(row)

        if proj.last_updated:
            days_stale = (datetime.utcnow() - proj.last_updated).days
            if days_stale > 14:
                stale_projects.append({**row, "days_stale": days_stale})

        if score >= 50 and proj.to_handle:
            for item in proj.to_handle.splitlines():
                item = item.strip()
                if item:
                    to_handle_items.append({
                        "project":    proj.name or proj.project_identifier,
                        "type":       ptype,
                        "item":       item,
                        "risk_score": score,
                    })

        if score > 0:
            by_type_detail[ptype].append(row)

    delayed_detail.sort(key=lambda r: r["days_overdue"], reverse=True)
    stale_projects.sort(key=lambda r: r["days_stale"], reverse=True)
    to_handle_items.sort(key=lambda r: r["risk_score"], reverse=True)
    for rows in by_type_detail.values():
        rows.sort(key=lambda r: r["risk_score"], reverse=True)

    # Epilogue: projects with rising risk trend (last 3 snapshots all increasing)
    rising_trend = []
    for r in risk_rows:
        sp = r.get("sparkline", [])
        if len(sp) >= 3 and sp[-3] < sp[-2] < sp[-1]:
            rising_trend.append({
                "name":        r["name"],
                "risk_score":  r["risk_score"],
                "main_reason": r.get("main_reason", ""),
            })

    # Epilogue: at-risk projects finishing within 14 days
    finishing_soon_atrisk = [
        r for r in finishing_30[:20]
        if r["risk_score"] >= 50 and r.get("estimated_finish_date") and
        (date.fromisoformat(r["estimated_finish_date"]) - today).days <= 14
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
        "risk_register":   risk_rows[:10],
        "action_items":    action_items,
        "finishing_30":    finishing_30[:20],
        "finishing_60":    finishing_60[:20],
        "finishing_90":    finishing_90[:20],
        "delayed_detail":  delayed_detail[:25],
        "stale_projects":  stale_projects[:20],
        "to_handle_items": to_handle_items[:30],
        "by_type_detail":  by_type_detail,
        "trends":          trends_list,
        "weekly_delta":    weekly_delta,
        "epilogue_data": {
            "rising_trend":          rising_trend[:5],
            "entering_risk_zone":    [r for r in risk_rows[:10] if r.get("entering_risk_zone")],
            "finishing_soon_atrisk": finishing_soon_atrisk[:5],
        },
    }


_REPORT_PROMPT = """\
אתה אנליסט תשתיות חשמל. המשימה שלך: לזהות מה לא בסדר, לא לתאר מה עובד.
אל תרכך בעיות. אם מגמה שלילית — אמור זאת ישירות.
אם הנתונים נראים טובים מדי — ציין זאת כסיכון בפני עצמו.
כתוב בעברית. החזר JSON בלבד, ללא טקסט לפני ואחרי.

נתוני קלט:
{data_json}

כתוב ערך לכל אחד מ-8 המפתחות הבאים. prologue ו-epilogue: 2-3 משפטים ישירים וקריטיים. שאר המפתחות: פסקה קצרה (3-4 משפטים).

{{
  "prologue_narrative": "...",
  "executive_narrative": "...",
  "portfolio_narrative": "...",
  "risk_narrative": "...",
  "action_narrative": "...",
  "finishing_narrative": "...",
  "delay_narrative": "...",
  "epilogue_narrative": "..."
}}"""


async def generate_report_html(data: dict) -> str:
    """Call Groq for narrative text, then wrap in full HTML template."""
    wd = data.get("weekly_delta", {})
    ep = data.get("epilogue_data", {})

    prompt = _REPORT_PROMPT.format(
        data_json=_json.dumps({
            "executive_summary": data["executive_summary"],
            "weekly_delta": {
                "avg_risk_change":    wd.get("avg_risk", 0),
                "delayed_change":     wd.get("delayed_count", 0),
                "at_risk_change":     wd.get("at_risk_count", 0),
                "this_week_avg_risk": wd.get("cur_avg_risk", data["executive_summary"]["avg_risk_score"]),
                "last_week_avg_risk": wd.get("prv_avg_risk", 0),
            },
            "top_risk_projects": [
                {
                    "name":               r["name"],
                    "risk_score":         r["risk_score"],
                    "main_reason":        r.get("main_reason", ""),
                    "weekly_brief":       r.get("weekly_report_brief", ""),
                }
                for r in data["risk_register"][:5]
            ],
            "action_items_count":  len(data["action_items"]),
            "finishing_30d_count": len(data.get("finishing_30", [])),
            "finishing_60d_count": len(data.get("finishing_60", [])),
            "delayed_count":       len(data.get("delayed_detail", [])),
            "stale_count":         len(data.get("stale_projects", [])),
            "top_delayed": [
                {"name": r["name"], "days_overdue": r["days_overdue"], "main_reason": r["main_reason"]}
                for r in data.get("delayed_detail", [])[:5]
            ],
            "epilogue": {
                "rising_trend":      ep.get("rising_trend", []),
                "entering_risk_zone": [{"name": r["name"], "risk_score": r["risk_score"]} for r in ep.get("entering_risk_zone", [])],
                "finishing_at_risk":  [{"name": r["name"], "estimated_finish_date": r.get("estimated_finish_date")} for r in ep.get("finishing_soon_atrisk", [])],
            },
        }, ensure_ascii=False)
    )

    raw = await llm_chat(usage="project_report", messages=[{"role": "user", "content": prompt}])

    try:
        clean = raw.strip()
        # Strip markdown fences (```json ... ``` or ``` ... ```)
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean.rsplit("\n", 1)[0]
        clean = clean.strip()
        # Fallback: extract first { ... } block in case LLM added preamble text
        if not clean.startswith("{"):
            start = clean.find("{")
            end   = clean.rfind("}")
            if start != -1 and end != -1:
                clean = clean[start:end + 1]
        narratives = _json.loads(clean)
        logger.info(f"project_report: LLM narratives parsed OK, keys={list(narratives.keys())}")
    except Exception as exc:
        logger.warning(f"project_report: LLM JSON parse failed ({exc}), raw[:200]={raw[:200]!r}")
        narratives = {k: "" for k in (
            "prologue_narrative", "executive_narrative", "portfolio_narrative",
            "risk_narrative", "action_narrative",
            "finishing_narrative", "delay_narrative", "epilogue_narrative",
        )}

    return _render_html(data, narratives)


def _rag_badge(status: str) -> str:
    colors = {"RED": "#ef4444", "AMBER": "#f59e0b", "GREEN": "#10b981"}
    labels = {"RED": "🔴 סיכון", "AMBER": "🟡 איחור", "GREEN": "🟢 תקין"}
    c = colors.get(status, "#64748b")
    return f'<span style="color:{c};font-weight:700;">{labels.get(status, status)}</span>'


def _score_color(score: int) -> str:
    if score >= 70:
        return "#ef4444"
    if score >= 40:
        return "#f59e0b"
    return "#10b981"


def _svg_linechart(values: list, color: str, max_val: int = 100, width: int = 820, height: int = 90) -> str:
    """Render a simple SVG polyline chart for trend data."""
    if not values:
        return "<span style='color:#64748b;font-size:.8rem;'>אין נתונים היסטוריים</span>"
    n = len(values)
    pad = 8
    effective_w = width - pad * 2
    effective_h = height - pad * 2
    mv = max(max_val, max(values) if values else 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + int(i / max(n - 1, 1) * effective_w)
        y = pad + effective_h - int(v / mv * effective_h)
        pts.append(f"{x},{y}")
    return (
        f'<svg width="{width}" height="{height}" style="display:block;overflow:visible;">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _project_row(r: dict, show_type: bool = True) -> str:
    sc = r.get("risk_score", 0)
    cols = (
        f'<td><strong>{r.get("name","")}</strong>'
        f'<span style="color:#64748b;font-size:.75rem;margin-right:6px;">{r.get("identifier","")}</span></td>'
    )
    if show_type:
        cols += f'<td style="color:#94a3b8;">{r.get("type","")}</td>'
    cols += (
        f'<td style="color:#94a3b8;">{r.get("stage","—")}</td>'
        f'<td style="color:#94a3b8;">{r.get("manager","—")}</td>'
        f'<td style="color:{_score_color(sc)};font-weight:700;">{sc}</td>'
        f'<td style="color:#94a3b8;font-size:.8rem;">{r.get("main_reason","")}</td>'
    )
    return f"<tr>{cols}</tr>"


def _render_html(data: dict, narratives: dict) -> str:
    meta = data["meta"]
    es   = data["executive_summary"]
    ph   = data["portfolio_health"]
    rr   = data["risk_register"]
    ai   = data["action_items"]
    f30  = data.get("finishing_30", [])
    f60  = data.get("finishing_60", [])
    f90  = data.get("finishing_90", [])
    dd   = data.get("delayed_detail", [])
    sp   = data.get("stale_projects", [])
    thi  = data.get("to_handle_items", [])
    btd  = data.get("by_type_detail", {})
    wd   = data.get("weekly_delta", {})
    tr   = data.get("trends", [])
    epd  = data.get("epilogue_data", {})

    risk_history    = [t["avg_risk"]      for t in tr]
    delayed_history = [t["delayed_count"] for t in tr]
    date_labels     = [t["date"][5:]      for t in tr]

    def _delta_html(val: int) -> str:
        if val > 0:
            return f'<span style="color:#ef4444;">↑ +{val}</span>'
        if val < 0:
            return f'<span style="color:#10b981;">↓ {val}</span>'
        return '<span style="color:#64748b;">— ללא שינוי</span>'

    rag_rows = "".join(
        f'<tr><td>{t}</td><td>{counts.get("active",0)}</td>'
        f'<td style="color:#f59e0b;">{counts.get("delayed",0)}</td>'
        f'<td style="color:#ef4444;">{counts.get("at_risk",0)}</td>'
        f'<td>{_rag_badge(es["rag_by_type"].get(t,"GREEN"))}</td></tr>'
        for t, counts in ph["type_counts"].items()
    )

    risk_rows_html = "".join(
        f'<tr>'
        f'<td><strong>{r.get("name","")}</strong>'
        f'<span style="color:#64748b;font-size:.8rem;margin-right:4px;">{r.get("identifier","")}</span></td>'
        f'<td>{r.get("type","")}</td>'
        f'<td>{r.get("stage","")}</td>'
        f'<td style="color:{_score_color(r["risk_score"])};"><strong>{r["risk_score"]}</strong></td>'
        f'<td style="color:#94a3b8;font-size:.8rem;">{r.get("main_reason","")}</td>'
        f'<td style="color:#cbd5e1;font-size:.78rem;max-width:220px;">{r.get("weekly_report_brief","")}</td>'
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

    # Page 5: finishing soon
    def _finishing_section(rows, label, color):
        if not rows:
            return f'<div style="color:var(--text-2);padding:8px 0;">אין פרויקטים {label}</div>'
        hdr = f'<h3 style="color:{color};">{label} ({len(rows)})</h3>'
        trs = "".join(
            f'<tr><td><strong>{r["name"]}</strong>'
            f'<span style="color:#64748b;font-size:.75rem;margin-right:4px;">{r["identifier"]}</span></td>'
            f'<td style="color:#94a3b8;">{r["type"]}</td>'
            f'<td style="color:#94a3b8;">{r["stage"]}</td>'
            f'<td style="color:#94a3b8;">{r["manager"]}</td>'
            f'<td style="color:{color};font-weight:700;">{r["estimated_finish_date"] or "—"}</td>'
            f'<td style="color:{_score_color(r["risk_score"])}">{r["risk_score"]}</td>'
            f'</tr>'
            for r in rows
        )
        return (
            hdr +
            '<table><thead><tr>'
            '<th>פרויקט</th><th>סוג</th><th>שלב</th><th>מנהל</th><th>תאריך סיום</th><th>ציון סיכון</th>'
            f'</tr></thead><tbody>{trs}</tbody></table>'
        )

    finishing_html = (
        _finishing_section(f30, "מסתיימים תוך 30 יום", "#ef4444") +
        _finishing_section(f60, "מסתיימים תוך 31-60 יום", "#f59e0b") +
        _finishing_section(f90, "מסתיימים תוך 61-90 יום", "#10b981")
    )
    finishing_count = len(f30) + len(f60) + len(f90)

    # Page 6: delayed deep-dive
    delayed_rows_html = "".join(
        f'<tr>'
        f'<td><strong>{r["name"]}</strong>'
        f'<span style="color:#64748b;font-size:.75rem;margin-right:4px;">{r["identifier"]}</span></td>'
        f'<td style="color:#94a3b8;">{r["type"]}</td>'
        f'<td style="color:#94a3b8;">{r["stage"]}</td>'
        f'<td style="color:#94a3b8;">{r["manager"]}</td>'
        f'<td style="color:#ef4444;font-weight:700;">{r["days_overdue"]}</td>'
        f'<td style="color:{_score_color(r["risk_score"])};font-weight:700;">{r["risk_score"]}</td>'
        f'<td style="color:#94a3b8;font-size:.8rem;">{r["main_reason"]}</td>'
        f'</tr>'
        for r in dd
    ) or "<tr><td colspan='7' style='color:var(--text-2);text-align:center;'>אין פרויקטים באיחור</td></tr>"

    # Page 7: by-type detail
    by_type_html = ""
    for ptype, rows in btd.items():
        if not rows:
            continue
        top = rows[:5]
        trs = "".join(_project_row(r, show_type=False) for r in top)
        by_type_html += (
            f'<h3>{ptype} — {len(rows)} פרויקטים פעילים</h3>'
            f'<table><thead><tr><th>פרויקט</th><th>שלב</th><th>מנהל</th><th>ציון</th><th>סיבה</th></tr></thead>'
            f'<tbody>{trs}</tbody></table>'
        )

    # Page 8: to-handle items from high-risk projects
    thi_rows = "".join(
        f'<tr>'
        f'<td><strong>{t["project"]}</strong></td>'
        f'<td style="color:#94a3b8;">{t["type"]}</td>'
        f'<td style="color:{_score_color(t["risk_score"])};font-weight:700;">{t["risk_score"]}</td>'
        f'<td>{t["item"]}</td>'
        f'</tr>'
        for t in thi
    ) or "<tr><td colspan='4' style='color:var(--text-2);text-align:center;'>אין פריטים לטיפול</td></tr>"

    # Page 9: risk forecast (entering risk zone)
    forecast_rows = "".join(
        f'<tr>'
        f'<td><strong>{r["name"]}</strong>'
        f'<span style="color:#64748b;font-size:.75rem;margin-right:4px;">{r["identifier"]}</span></td>'
        f'<td style="color:#94a3b8;">{r["type"]}</td>'
        f'<td style="color:#94a3b8;">{r["stage"]}</td>'
        f'<td style="color:#a78bfa;font-weight:700;">{r["risk_score"]}</td>'
        f'<td style="color:#94a3b8;font-size:.8rem;">{r["main_reason"]}</td>'
        f'</tr>'
        for r in rr if r.get("entering_risk_zone")
    ) or "<tr><td colspan='5' style='color:var(--text-2);text-align:center;'>אין פרויקטים בתחזית כניסה לסיכון</td></tr>"

    # Page 10: stale/data-quality
    stale_rows = "".join(
        f'<tr>'
        f'<td><strong>{r["name"]}</strong>'
        f'<span style="color:#64748b;font-size:.75rem;margin-right:4px;">{r["identifier"]}</span></td>'
        f'<td style="color:#94a3b8;">{r["type"]}</td>'
        f'<td style="color:#94a3b8;">{r["stage"]}</td>'
        f'<td style="color:#94a3b8;">{r["manager"]}</td>'
        f'<td style="color:#f59e0b;font-weight:700;">{r["days_stale"]}</td>'
        f'<td style="color:{_score_color(r["risk_score"])}">{r["risk_score"]}</td>'
        f'</tr>'
        for r in sp
    ) or "<tr><td colspan='6' style='color:var(--text-2);text-align:center;'>כל הפרויקטים עודכנו לאחרונה</td></tr>"

    CSS = """
  :root{--bg:#070b12;--bg-c:#0f1826;--border:#1a2d47;--cyan:#00d4ff;--text:#e2e8f0;--text-2:#64748b;--red:#ef4444;--amber:#f59e0b;--green:#10b981;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:'Heebo',Arial,'David',sans-serif;direction:rtl;text-align:right;padding:24px;}
  .page{max-width:940px;margin:0 auto 40px;padding:32px;background:var(--bg-c);border:1px solid var(--border);border-radius:12px;page-break-after:always;}
  .page-header{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid var(--cyan);padding-bottom:12px;margin-bottom:20px;}
  .page-title{color:var(--cyan);font-size:1.3rem;font-weight:700;}
  .page-meta{color:var(--text-2);font-size:.82rem;}
  .kpi-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;}
  .kpi{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center;}
  .kpi-val{font-size:2rem;font-weight:700;color:var(--cyan);}
  .kpi-val.warn{color:var(--amber);}.kpi-val.danger{color:var(--red);}
  .kpi-label{font-size:.75rem;color:var(--text-2);margin-top:4px;}
  .narrative{color:#cbd5e1;line-height:1.8;font-size:.92rem;margin-bottom:20px;background:var(--bg);border-right:3px solid var(--cyan);padding:12px 16px;border-radius:4px;}
  table{width:100%;border-collapse:collapse;font-size:.85rem;}
  th{color:var(--text-2);border-bottom:1px solid var(--border);padding:7px 8px;text-align:right;font-size:.75rem;letter-spacing:.04em;}
  td{border-bottom:1px solid rgba(26,45,71,.5);padding:8px;text-align:right;}
  tr:hover td{background:rgba(0,212,255,.04);}
  h3{color:var(--cyan);font-size:1rem;margin:16px 0 10px;}
  .badge-grid{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;}
  .badge{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 14px;font-size:.85rem;}
"""

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>דוח פרויקטים — {meta["generated_at"]}</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>

<!-- PAGE 0: PROLOGUE -->
<div class="page" style="border-color:rgba(239,68,68,.4);background:rgba(239,68,68,.04);">
  <div class="page-header" style="border-color:#ef4444;">
    <div class="page-title" style="color:#ef4444;">🔴 תמצית מנהלים — {meta["generated_at"]}</div>
    <div class="page-meta">{meta["username"]} | {meta["role"]}</div>
  </div>
  <div style="font-size:1.05rem;line-height:2;color:#e2e8f0;padding:16px 20px;border-right:4px solid #ef4444;background:rgba(239,68,68,.06);border-radius:4px;">
    {narratives.get("prologue_narrative","—")}
  </div>
  <div style="display:flex;gap:16px;margin-top:20px;flex-wrap:wrap;">
    <div class="kpi" style="flex:1;min-width:160px;">
      <div class="kpi-val danger">{es["avg_risk_score"]}</div>
      <div class="kpi-label">ציון סיכון ממוצע {_delta_html(wd.get("avg_risk",0))}</div>
    </div>
    <div class="kpi" style="flex:1;min-width:160px;">
      <div class="kpi-val warn">{es["total_delayed"]}</div>
      <div class="kpi-label">באיחור {_delta_html(wd.get("delayed_count",0))}</div>
    </div>
    <div class="kpi" style="flex:1;min-width:160px;">
      <div class="kpi-val danger">{es["total_at_risk"]}</div>
      <div class="kpi-label">סיכון גבוה (≥70) {_delta_html(wd.get("at_risk_count",0))}</div>
    </div>
  </div>
</div>

<!-- PAGE 1: EXECUTIVE SUMMARY -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📊 דוח פרויקטים — סיכום מנהלים</div>
    <div class="page-meta">עמוד 1 מתוך 12 | {meta["generated_at"]} | {meta["username"]} | {meta["role"]}</div>
  </div>
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">{es["total_active"]}</div><div class="kpi-label">פרויקטים פעילים</div></div>
    <div class="kpi"><div class="kpi-val warn">{es["total_delayed"]}</div><div class="kpi-label">באיחור</div></div>
    <div class="kpi"><div class="kpi-val danger">{es["total_at_risk"]}</div><div class="kpi-label">סיכון גבוה (≥70)</div></div>
    <div class="kpi"><div class="kpi-val" style="color:#a78bfa;">{es["entering_next_week"]}</div><div class="kpi-label">צפויים להיכנס לסיכון</div></div>
    <div class="kpi"><div class="kpi-val">{es["avg_risk_score"]}</div><div class="kpi-label">ציון סיכון ממוצע</div></div>
    <div class="kpi"><div class="kpi-val">{es["approval_rate_pct"]}%</div><div class="kpi-label">אחוז אישורי החלטות (30י׳)</div></div>
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
    <div class="page-meta">עמוד 2 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("portfolio_narrative","")}</div>
  <h3>מגמת איחורים — שבועות אחרונים</h3>
  <div style="display:flex;gap:6px;align-items:flex-end;height:80px;margin:12px 0 20px;background:var(--bg);padding:12px;border-radius:8px;">{trend_bars}</div>
  <h3>התפלגות שלבים</h3>
  <div class="badge-grid">{stage_badges}</div>
  <div style="margin-top:20px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:.82rem;color:var(--text-2);">
    🔢 סה"כ החלטות (30 ימים): <strong style="color:var(--text);">{es["decisions_30d"]}</strong> &nbsp;|&nbsp;
    ⚠️ קריטיות ממתינות: <strong style="color:#ef4444;">{es["critical_pending"]}</strong>
  </div>
</div>

<!-- PAGE 3: TRENDS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📈 מגמות — שינויים לאורך זמן</div>
    <div class="page-meta">עמוד 3 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <h3>שינוי שבועי (השבוע מול שבוע שעבר)</h3>
  <table style="margin-bottom:24px;">
    <thead><tr><th>מדד</th><th>שבוע שעבר</th><th>השבוע</th><th>שינוי</th></tr></thead>
    <tbody>
      <tr>
        <td>ציון סיכון ממוצע</td>
        <td>{wd.get("prv_avg_risk","—")}</td>
        <td><strong>{wd.get("cur_avg_risk", es["avg_risk_score"])}</strong></td>
        <td>{_delta_html(wd.get("avg_risk",0))}</td>
      </tr>
      <tr>
        <td>פרויקטים באיחור</td>
        <td>{wd.get("prv_delayed","—")}</td>
        <td><strong>{wd.get("cur_delayed", es["total_delayed"])}</strong></td>
        <td>{_delta_html(wd.get("delayed_count",0))}</td>
      </tr>
      <tr>
        <td>פרויקטים בסיכון גבוה (≥70)</td>
        <td>{wd.get("prv_at_risk","—")}</td>
        <td><strong>{wd.get("cur_at_risk", es["total_at_risk"])}</strong></td>
        <td>{_delta_html(wd.get("at_risk_count",0))}</td>
      </tr>
    </tbody>
  </table>
  <h3>ציון סיכון ממוצע — היסטוריה מלאה</h3>
  <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:20px;">
    {_svg_linechart(risk_history, "#ef4444", 100)}
    <div style="display:flex;justify-content:space-between;font-size:.65rem;color:var(--text-2);margin-top:4px;">
      <span>{date_labels[0] if date_labels else ""}</span>
      <span>{date_labels[-1] if date_labels else ""}</span>
    </div>
  </div>
  <h3>מספר פרויקטים באיחור — היסטוריה מלאה</h3>
  <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;">
    {_svg_linechart(delayed_history, "#f59e0b", max(delayed_history) if delayed_history else 10)}
    <div style="display:flex;justify-content:space-between;font-size:.65rem;color:var(--text-2);margin-top:4px;">
      <span>{date_labels[0] if date_labels else ""}</span>
      <span>{date_labels[-1] if date_labels else ""}</span>
    </div>
  </div>
</div>

<!-- PAGE 4: RISK REGISTER -->
<div class="page">
  <div class="page-header">
    <div class="page-title">⚠️ רישום סיכונים — 10 פרויקטים מובילים</div>
    <div class="page-meta">עמוד 4 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("risk_narrative","")}</div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>שלב</th><th>ציון</th><th>סיבה עיקרית</th><th>עדכון שבועי</th></tr></thead>
    <tbody>{risk_rows_html}</tbody>
  </table>
</div>

<!-- PAGE 5: ACTION ITEMS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">✅ פעולות נדרשות</div>
    <div class="page-meta">עמוד 5 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("action_narrative","")}</div>
  <table>
    <thead><tr><th>עדיפות</th><th>פעולה</th><th>שלב</th></tr></thead>
    <tbody>{ai_rows}</tbody>
  </table>
</div>

<!-- PAGE 6: FINISHING SOON -->
<div class="page">
  <div class="page-header">
    <div class="page-title">🏁 פרויקטים המסתיימים ב-90 הימים הקרובים ({finishing_count})</div>
    <div class="page-meta">עמוד 6 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("finishing_narrative","")}</div>
  {finishing_html}
</div>

<!-- PAGE 7: DELAYED DEEP-DIVE -->
<div class="page">
  <div class="page-header">
    <div class="page-title">🔴 פרויקטים באיחור — ניתוח מעמיק ({len(dd)})</div>
    <div class="page-meta">עמוד 7 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div class="narrative">{narratives.get("delay_narrative","")}</div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>שלב</th><th>מנהל</th><th>ימי איחור</th><th>ציון</th><th>סיבה</th></tr></thead>
    <tbody>{delayed_rows_html}</tbody>
  </table>
</div>

<!-- PAGE 8: BY-TYPE ANALYSIS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📂 ניתוח לפי סוג פרויקט</div>
    <div class="page-meta">עמוד 8 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  {by_type_html or '<div style="color:var(--text-2);">אין נתונים</div>'}
</div>

<!-- PAGE 9: TO-HANDLE ITEMS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📋 פריטים לטיפול מפרויקטים בסיכון (ציון ≥50)</div>
    <div class="page-meta">עמוד 9 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>ציון</th><th>פריט לטיפול</th></tr></thead>
    <tbody>{thi_rows}</tbody>
  </table>
</div>

<!-- PAGE 10: RISK FORECAST -->
<div class="page">
  <div class="page-header">
    <div class="page-title">🔮 תחזית סיכונים — צפויים להיכנס לאזור סיכון</div>
    <div class="page-meta">עמוד 10 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div style="padding:10px 14px;background:rgba(167,139,250,.08);border:1px solid rgba(167,139,250,.25);border-radius:6px;font-size:.82rem;color:#a78bfa;margin-bottom:16px;">
    פרויקטים אלה נמצאים כיום מתחת לסף סיכון גבוה (70), אך מגמת הציון צפויה להחצות את הסף בשבוע הקרוב.
  </div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>שלב</th><th>ציון נוכחי</th><th>סיבה</th></tr></thead>
    <tbody>{forecast_rows}</tbody>
  </table>
</div>

<!-- PAGE 11: DATA QUALITY / STALE -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📅 איכות נתונים — פרויקטים לא מעודכנים (&gt;14 ימים)</div>
    <div class="page-meta">עמוד 11 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div style="padding:10px 14px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.25);border-radius:6px;font-size:.82rem;color:#f59e0b;margin-bottom:16px;">
    פרויקטים שלא עודכנו מעל 14 ימים — הציון שלהם עלול להיות לא מדויק. נדרש עדכון.
  </div>
  <table>
    <thead><tr><th>פרויקט</th><th>סוג</th><th>שלב</th><th>מנהל</th><th>ימים ללא עדכון</th><th>ציון</th></tr></thead>
    <tbody>{stale_rows}</tbody>
  </table>
  <div style="margin-top:32px;padding:14px;background:var(--bg);border:1px solid rgba(0,212,255,.2);border-radius:8px;font-size:.8rem;color:var(--text-2);text-align:center;">
    דוח זה נוצר אוטומטית על ידי Shan-AI | {meta["generated_at"]}
  </div>
</div>

<!-- PAGE 12: EPILOGUE -->
<div class="page" style="border-color:rgba(167,139,250,.3);background:rgba(167,139,250,.03);">
  <div class="page-header" style="border-color:#a78bfa;">
    <div class="page-title" style="color:#a78bfa;">🔮 מה לעקוב בשבוע הבא</div>
    <div class="page-meta">עמוד 12 מתוך 12 | {meta["generated_at"]}</div>
  </div>
  <div style="font-size:.95rem;line-height:2;color:#e2e8f0;padding:16px 20px;border-right:4px solid #a78bfa;background:rgba(167,139,250,.06);border-radius:4px;margin-bottom:20px;">
    {narratives.get("epilogue_narrative","—")}
  </div>
  {"".join(
      f'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid rgba(26,45,71,.5);font-size:.85rem;">'
      f'<span><strong>{r["name"]}</strong></span>'
      f'<span style="color:#a78bfa;font-size:.8rem;">📈 מגמה עולה</span>'
      f'<span style="color:{_score_color(r["risk_score"])};font-weight:700;">{r["risk_score"]}</span>'
      f'</div>'
      for r in epd.get("rising_trend", [])
  ) or "<div style='color:var(--text-2);padding:8px;font-size:.85rem;'>אין פרויקטים במגמת עלייה מתמשכת</div>"}
</div>

</body>
</html>"""


async def auto_send_project_report(user, session, bot=None) -> bool:
    """Generate report HTML, save to DB, send Telegram notification with inline buttons."""
    from app.models import ProjectReport

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

        if bot and user.telegram_id:
            await _telegram_send_report(bot, user, report_id, report_data)

        return True

    except Exception as exc:
        logger.error(f"auto_send_project_report failed for user {user.id}: {exc}")
        return False


async def _telegram_send_report(bot, user, report_id: int, data: dict) -> None:
    """Send full report as PDF attachment via Telegram. Falls back to text on failure."""
    from io import BytesIO
    from app.database import async_session_maker
    from app.models import ProjectReport as _PR

    async with async_session_maker() as s:
        rpt = await s.get(_PR, report_id)
        html_content = rpt.html_content if rpt else None

    meta = data.get("meta", {})
    ts = meta.get("generated_at", "report").replace("/", "-").replace(" ", "_").replace(":", "")
    filename = f"דוח_פרויקטים_{ts}.pdf"
    caption = f"‏📊 *דוח פרויקטים* — {meta.get('generated_at', '')}\nהדוח המלא מצורף כקובץ PDF."

    if html_content:
        try:
            pdf_bytes = await asyncio.get_event_loop().run_in_executor(
                None, generate_pdf, html_content
            )
            await bot.send_document(
                chat_id=user.telegram_id,
                document=BytesIO(pdf_bytes),
                filename=filename,
                caption=caption,
                parse_mode="Markdown",
            )
            return
        except Exception as pdf_exc:
            logger.warning(f"_telegram_send_report: PDF failed, falling back to text: {pdf_exc}")

    # Fallback: text summary + dashboard link
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    es   = data.get("executive_summary", {})
    base = "https://easygoing-endurance-production-df54.up.railway.app"
    summary = (
        f"‏📊 *דוח פרויקטים* — {meta.get('generated_at', '')}\n\n"
        f"📌 פעיל: *{es.get('total_active', 0)}*  |  "
        f"🟡 באיחור: *{es.get('total_delayed', 0)}*  |  "
        f"🔴 סיכון: *{es.get('total_at_risk', 0)}*\n"
        f"ציון סיכון ממוצע: *{es.get('avg_risk_score', 0)}*"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📄 דוח מלא", url=f"{base}/dashboard/project-reports/{report_id}"),
    ]])
    await bot.send_message(
        chat_id=user.telegram_id,
        text=summary,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
