# Project Report Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix inflated risk scores, harden LLM narrative tone, and add prologue/trends/epilogue pages to the 10-page portfolio report.

**Architecture:** Two files only. `project_learning_service.py` gets scoring fixes. `project_report_service.py` gets data gathering additions, a rewritten LLM prompt, and three new HTML pages injected into `_render_html()`.

**Tech Stack:** Python 3.11, SQLAlchemy async, Groq (llama-3.3-70b), inline SVG for charts.

---

## File Map

| File | Changes |
|---|---|
| `app/services/project_learning_service.py` | `_overdue_pts()` formula; `compute_risk_score()` signature + missing-data penalties |
| `app/services/project_report_service.py` | `gather_report_data()` adds trends + delta + epilogue inputs; `_REPORT_PROMPT` rewritten; `generate_report_html()` passes 8 LLM keys; `_render_html()` adds pages 0, 3, 12 + SVG helper |

---

## Task 1: Steepen overdue scoring curve (max 30 → 40 pts)

**Files:**
- Modify: `app/services/project_learning_service.py:64-74`

- [ ] **Step 1: Replace `_overdue_pts()` body**

Current formula caps at 30 pts with log base 60. Replace with steeper curve capping at 40:

```python
def _overdue_pts(estimated_finish_date: Optional[date], today: date) -> int:
    """0-40: log-scaled overdue (steeper curve) or urgency for imminent deadlines."""
    if not estimated_finish_date:
        return 0
    days_diff = (today - estimated_finish_date).days  # positive = overdue
    if days_diff > 0:
        return min(int(35 * math.log(1 + days_diff) / math.log(50)), 40)
    days_until = -days_diff
    if days_until < 14:
        return max(0, int(15 - (days_until / 14 * 5)))
    return 0
```

Calibration check:
- 7 days overdue  → ~18 pts (was ~10)
- 30 days overdue → ~31 pts (was ~20)
- 90 days overdue → ~40 pts (was ~30, capped)

- [ ] **Step 2: Commit**

```bash
git add app/services/project_learning_service.py
git commit -m "fix(risk): steepen overdue curve, raise max to 40 pts"
```

---

## Task 2: Add missing-data penalties to `compute_risk_score()`

**Files:**
- Modify: `app/services/project_learning_service.py:120-179`

- [ ] **Step 1: Add `weekly_report` parameter to signature**

Replace line 120-129:

```python
def compute_risk_score(
    stage: Optional[str],
    estimated_finish_date: Optional[date],
    dev_plan_date: Optional[date],
    risks: Optional[str],
    to_handle: Optional[str],
    last_updated: Optional[datetime],
    weekly_report: Optional[str] = None,
    prior_finish_dates: Optional[list] = None,
    today: Optional[date] = None,
) -> dict:
    """
    Compute delay risk score (0-100) + breakdown.
    Returns: {score, reliable, breakdown, main_reason, days_overdue}
    """
```

- [ ] **Step 2: Add missing-data penalties after existing scoring (line 150)**

Replace:
```python
    score = min(schedule_pts + kw + handle + stale, 100)
```

With:
```python
    # Missing-data penalties: absence of data is suspicious, not clean
    missing = 0
    if not risks or len(risks.strip()) < 10:
        missing += 12
    if not to_handle or len(to_handle.strip()) < 10:
        missing += 8
    if not weekly_report or len(weekly_report.strip()) < 20:
        missing += 5

    score = min(schedule_pts + kw + handle + stale + missing, 100)
```

- [ ] **Step 3: Pass `weekly_report` in `save_snapshot()` call (line 226)**

In `save_snapshot()`, update the `compute_risk_score` call to pass `project.weekly_report`:

```python
    result = compute_risk_score(
        stage=project.stage,
        estimated_finish_date=project.estimated_finish_date,
        dev_plan_date=project.dev_plan_date,
        risks=project.risks,
        to_handle=project.to_handle,
        last_updated=project.last_updated,
        weekly_report=project.weekly_report,
        prior_finish_dates=prior_finish_dates,
        today=today,
    )
```

- [ ] **Step 4: Commit**

```bash
git add app/services/project_learning_service.py
git commit -m "fix(risk): add missing-data penalties (+12 no risks, +8 no to_handle, +5 no weekly_report)"
```

---

## Task 3: Add trends query + delta to `gather_report_data()`

**Files:**
- Modify: `app/services/project_report_service.py:28-193`

- [ ] **Step 1: Add `text` import at top of file**

Add to the existing imports block:

```python
from sqlalchemy import select, func, text
```

(replace the existing `from sqlalchemy import select, func`)

- [ ] **Step 2: Pass `weekly_report` to `compute_risk_score()` in the project loop (line ~96)**

In the `for proj in all_projects:` loop, update the call:

```python
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
```

- [ ] **Step 3: Add trends aggregation query after the existing avg_risk query (after line ~51)**

Insert after `avg_risk = round(float(avg_risk_row or 0))`:

```python
    # Portfolio trends: last 52 snapshot dates with avg risk + delayed count
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

    # Build ascending-chronological list for chart rendering
    trends_list = [
        {
            "date":          str(r[0]),
            "avg_risk":      int(r[1] or 0),
            "delayed_count": int(r[2] or 0),
            "at_risk_count": int(r[3] or 0),
        }
        for r in reversed(trends_raw)
    ]

    # Weekly delta: compare most recent two snapshot dates
    weekly_delta = {"avg_risk": 0, "delayed_count": 0, "at_risk_count": 0, "new_in_risk_zone": []}
    if len(trends_list) >= 2:
        cur = trends_list[-1]
        prv = trends_list[-2]
        weekly_delta = {
            "avg_risk":      cur["avg_risk"] - prv["avg_risk"],
            "delayed_count": cur["delayed_count"] - prv["delayed_count"],
            "at_risk_count": cur["at_risk_count"] - prv["at_risk_count"],
            "cur_avg_risk":   cur["avg_risk"],
            "prv_avg_risk":   prv["avg_risk"],
            "cur_delayed":    cur["delayed_count"],
            "prv_delayed":    prv["delayed_count"],
            "cur_at_risk":    cur["at_risk_count"],
            "prv_at_risk":    prv["at_risk_count"],
        }
```

- [ ] **Step 4: Add `trends` and `weekly_delta` to the return dict (after `"by_type_detail"` key)**

```python
        "trends":        trends_list,
        "weekly_delta":  weekly_delta,
```

- [ ] **Step 5: Compute epilogue inputs — rising-trend projects (add after the project loop)**

After `for rows in by_type_detail.values(): rows.sort(...)`, add:

```python
    # Epilogue: projects with rising risk trend (last 3 snapshots all increasing)
    rising_trend = []
    for r in risk_rows:
        sp = r.get("sparkline", [])
        if len(sp) >= 3 and sp[-3] < sp[-2] < sp[-1]:
            rising_trend.append({"name": r["name"], "risk_score": r["risk_score"], "main_reason": r.get("main_reason", "")})

    # Epilogue: at-risk projects finishing within 14 days
    finishing_soon_atrisk = [
        r for r in (finishing_30[:20])
        if r["risk_score"] >= 50 and r.get("estimated_finish_date") and
        (date.fromisoformat(r["estimated_finish_date"]) - today).days <= 14
    ]
```

- [ ] **Step 6: Add epilogue data to return dict**

```python
        "epilogue_data": {
            "rising_trend":          rising_trend[:5],
            "entering_risk_zone":    [r for r in risk_rows[:10] if r.get("entering_risk_zone")],
            "finishing_soon_atrisk": finishing_soon_atrisk[:5],
        },
```

- [ ] **Step 7: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(report): add trends query, weekly delta, epilogue inputs to gather_report_data"
```

---

## Task 4: Rewrite LLM prompt + update `generate_report_html()`

**Files:**
- Modify: `app/services/project_report_service.py:196-254`

- [ ] **Step 1: Replace `_REPORT_PROMPT`**

Replace the entire `_REPORT_PROMPT` string (lines 196-212):

```python
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
```

- [ ] **Step 2: Update `generate_report_html()` to pass delta + epilogue data to LLM**

Replace the `prompt = _REPORT_PROMPT.format(...)` block (lines 217-234) with:

```python
    wd = data.get("weekly_delta", {})
    ep = data.get("epilogue_data", {})

    prompt = _REPORT_PROMPT.format(
        data_json=_json.dumps({
            "executive_summary": data["executive_summary"],
            "weekly_delta": {
                "avg_risk_change":     wd.get("avg_risk", 0),
                "delayed_change":      wd.get("delayed_count", 0),
                "at_risk_change":      wd.get("at_risk_count", 0),
                "this_week_avg_risk":  wd.get("cur_avg_risk", data["executive_summary"]["avg_risk_score"]),
                "last_week_avg_risk":  wd.get("prv_avg_risk", 0),
            },
            "top_risk_projects": [
                {"name": r["name"], "risk_score": r["risk_score"], "main_reason": r.get("main_reason", "")}
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
                "rising_trend":       ep.get("rising_trend", []),
                "entering_risk_zone": [{"name": r["name"], "risk_score": r["risk_score"]} for r in ep.get("entering_risk_zone", [])],
                "finishing_at_risk":  [{"name": r["name"], "estimated_finish_date": r.get("estimated_finish_date")} for r in ep.get("finishing_soon_atrisk", [])],
            },
        }, ensure_ascii=False)
    )
```

- [ ] **Step 3: Update the fallback `narratives` dict to include new keys**

Replace the `narratives = {k: "" for k in (...)}` fallback:

```python
        narratives = {k: "" for k in (
            "prologue_narrative", "executive_narrative", "portfolio_narrative",
            "risk_narrative", "action_narrative",
            "finishing_narrative", "delay_narrative", "epilogue_narrative",
        )}
```

- [ ] **Step 4: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(report): rewrite LLM prompt (critical voice), add prologue+epilogue narrative keys"
```

---

## Task 5: Add SVG chart helper + prologue page to `_render_html()`

**Files:**
- Modify: `app/services/project_report_service.py:289-625`

- [ ] **Step 1: Add `_svg_linechart()` helper after `_score_color()`**

Insert after `def _score_color(score: int) -> str:` block (after line 269):

```python
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
```

- [ ] **Step 2: Extract prologue + trends variables at the top of `_render_html()`**

After the existing variable assignments (after `btd = data.get("by_type_detail", {})` at line ~301), add:

```python
    wd   = data.get("weekly_delta", {})
    tr   = data.get("trends", [])
    epd  = data.get("epilogue_data", {})

    # Trend chart data
    risk_history    = [t["avg_risk"]      for t in tr]
    delayed_history = [t["delayed_count"] for t in tr]
    date_labels     = [t["date"][5:]      for t in tr]  # MM-DD

    # Weekly delta helpers
    def _delta_html(val: int) -> str:
        if val > 0:
            return f'<span style="color:#ef4444;">↑ +{val}</span>'
        if val < 0:
            return f'<span style="color:#10b981;">↓ {val}</span>'
        return '<span style="color:#64748b;">— ללא שינוי</span>'
```

- [ ] **Step 3: Add prologue page before PAGE 1 in the HTML f-string**

In the `return f"""..."""` block, insert before `<!-- PAGE 1: EXECUTIVE SUMMARY -->`:

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(report): add prologue page 0 with delta KPIs and SVG chart helper"
```

---

## Task 6: Add trends page (page 3) to `_render_html()`

**Files:**
- Modify: `app/services/project_report_service.py` — inside `_render_html()` HTML f-string

- [ ] **Step 1: Insert trends page between PAGE 2 and PAGE 3 (risk register)**

After the closing `</div>` of `<!-- PAGE 2: PORTFOLIO HEALTH -->` and before `<!-- PAGE 3: RISK REGISTER -->`, insert:

```python
<!-- PAGE 3: TRENDS -->
<div class="page">
  <div class="page-header">
    <div class="page-title">📈 מגמות — שינויים לאורך זמן</div>
    <div class="page-meta">עמוד 3 | {meta["generated_at"]}</div>
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
```

- [ ] **Step 2: Update page numbers in existing pages**

All existing pages currently say "עמוד 3", "עמוד 4" etc. With the new prologue (page 0) and trends (page 3) inserted, the existing pages shift:
- Old page 3 (Risk Register) → page 4
- Old page 4 (Action Items) → page 5
- … continuing through old page 10 → page 11

Update the `page-meta` divs in all 10 existing pages to `עמוד X מתוך 12` with the correct shifted numbers.

Page number mapping (old → new):
- Executive Summary: `עמוד 1` → `עמוד 1 מתוך 12`
- Portfolio Health: `עמוד 2` → `עמוד 2 מתוך 12`
- Risk Register: `עמוד 3` → `עמוד 4 מתוך 12`
- Action Items: `עמוד 4` → `עמוד 5 מתוך 12`
- Finishing Soon: `עמוד 5` → `עמוד 6 מתוך 12`
- Delayed Deep-Dive: `עמוד 6` → `עמוד 7 מתוך 12`
- By-Type Analysis: `עמוד 7` → `עמוד 8 מתוך 12`
- To-Handle Items: `עמוד 8` → `עמוד 9 מתוך 12`
- Risk Forecast: `עמוד 9` → `עמוד 10 מתוך 12`
- Data Quality: `עמוד 10` → `עמוד 11 מתוך 12`

- [ ] **Step 3: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(report): add trends page (page 3) with weekly delta table and 52-week SVG charts"
```

---

## Task 7: Add epilogue page (page 12)

**Files:**
- Modify: `app/services/project_report_service.py` — end of `_render_html()` HTML f-string

- [ ] **Step 1: Insert epilogue page before `</body>`**

Replace the closing footer `</div>\n\n</body>\n</html>` at the end of the Data Quality page with:

```python
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
      f'<div style="display:flex;justify-content:space-between;padding:8px 12px;border-bottom:1px solid rgba(26,45,71,.5);font-size:.85rem;">'
      f'<span><strong>{r["name"]}</strong></span>'
      f'<span style="color:#a78bfa;">📈 מגמה עולה</span>'
      f'<span style="color:{_score_color(r["risk_score"])};font-weight:700;">{r["risk_score"]}</span>'
      f'</div>'
      for r in epd.get("rising_trend", [])
  ) or "<div style='color:var(--text-2);padding:8px;'>אין פרויקטים במגמת עלייה</div>"}
</div>

</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add app/services/project_report_service.py
git commit -m "feat(report): add epilogue page 12 with rising-trend projects"
```

---

## Task 8: Deploy

- [ ] **Step 1: Push to GitHub**

```bash
git push origin master
```

- [ ] **Step 2: Trigger Railway redeploy**

```bash
TOKEN="$RAILWAY_API_TOKEN"
SVC_ID="a2df9c28-03eb-456a-a3e1-ae3355a96376"
ENV_ID="1bfcc433-4657-45bb-961c-c99c07bd9c21"
curl -s -X POST "https://backboard.railway.app/graphql/v2" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { serviceInstanceDeploy(serviceId: \\\"$SVC_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}"
```

Expected: `{"data":{"serviceInstanceDeploy":true}}`

- [ ] **Step 3: Verify**

1. Upload a project master XLSX — confirm PDF arrives on Telegram
2. Open PDF — confirm prologue (page 0) shows specific project names + delta KPIs
3. Page 3 shows trends table + SVG charts with actual data
4. Page 12 shows epilogue with rising-trend project names
5. Pick a project with empty `risks` field — confirm its risk score is higher than before
6. Pick a project 60+ days overdue — confirm score is near-critical (≥40 pts from overdue alone)
7. AI narrative text names problems directly, not optimistically
