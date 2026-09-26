"""Geometry for the project page's target-date chart. Pure — the template only
draws what this returns.

One y axis (a date), two series over report dates: the electrification target
(יעד חשמול מסתמן) and the development-plan target (יעד תכנית פיתוח). A gap
between them is lateness; both climbing together is a baseline that moves with
the forecast. Colours are the reference palette's first two categorical slots,
dark steps, validated against the page surface (#0f1826): all checks pass.
"""
from __future__ import annotations

from datetime import date

W, H = 760, 280
PAD_L, PAD_R, PAD_T, PAD_B = 70, 110, 24, 40   # right pad holds the direct labels

SERIES = (
    {"key": "fc", "label": "יעד חשמול מסתמן", "color": "#3987e5"},
    {"key": "dev", "label": "יעד תכנית פיתוח", "color": "#d95926"},
)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def timeline_chart(timeline: list[dict]) -> dict | None:
    """None when there is nothing to draw (fewer than two dated reports)."""
    rows = [r for r in timeline if r.get("fc") or r.get("dev")]
    if len(rows) < 2:
        return None
    xs = [_d(r["date"]).toordinal() for r in rows]
    ys = [_d(r[k]).toordinal() for r in rows for k in ("fc", "dev") if r.get(k)]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if y1 - y0 < 60:            # a flat line still gets a readable band
        y0, y1 = y0 - 30, y1 + 30
    pad = (y1 - y0) * 0.08
    y0, y1 = y0 - pad, y1 + pad

    def px(o: int) -> float:
        return round(PAD_L + (o - x0) / max(1, x1 - x0) * (W - PAD_L - PAD_R), 1)

    def py(o: float) -> float:
        return round(PAD_T + (y1 - o) / (y1 - y0) * (H - PAD_T - PAD_B), 1)

    series = []
    for s in SERIES:
        pts = [{"x": px(_d(r["date"]).toordinal()), "y": py(_d(r[s["key"]]).toordinal()),
                "report": r["date"], "value": r[s["key"]]} for r in rows if r.get(s["key"])]
        if pts:
            series.append({**s, "points": pts, "path": " ".join(f"{p['x']},{p['y']}" for p in pts)})

    # Label the last point of each series; nudge apart when they would collide.
    ends = sorted(((srs["points"][-1]["y"], i) for i, srs in enumerate(series)))
    for j in range(1, len(ends)):
        if ends[j][0] - ends[j - 1][0] < 14:
            ends[j] = (ends[j - 1][0] + 14, ends[j][1])
    for y, i in ends:
        series[i]["label_y"] = y

    ticks = []
    first = date.fromordinal(int(y0))
    m = date(first.year, first.month, 1)
    step = max(1, round((y1 - y0) / 30.44 / 5))     # ~5 month ticks
    while m.toordinal() <= y1:
        if m.toordinal() >= y0:
            ticks.append({"y": py(m.toordinal()), "label": m.strftime("%m/%Y")})
        mo = m.month - 1 + step
        m = date(m.year + mo // 12, mo % 12 + 1, 1)

    return {
        "w": W, "h": H, "plot_left": PAD_L, "plot_right": W - PAD_R, "plot_bottom": H - PAD_B,
        "series": series, "y_ticks": ticks,
        "x_labels": _spaced_labels(rows, px),
    }


MIN_LABEL_GAP = 38   # px between x-axis labels; weekly reports sit ~10px apart


def _spaced_labels(rows: list[dict], px) -> list[dict]:
    """Report dates on the x axis, newest always shown, older ones skipped
    when they would collide. Every point keeps its hover title regardless."""
    labels, last_x = [], None
    for r in reversed(rows):
        x = px(_d(r["date"]).toordinal())
        if last_x is None or last_x - x >= MIN_LABEL_GAP:
            labels.append({"x": x, "label": _d(r["date"]).strftime("%d/%m")})
            last_x = x
    return labels[::-1]
