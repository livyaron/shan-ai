/* Project page charts — drawn from the JSON the server embeds (project_chart.py).
 * Rules followed (dataviz guide): one y axis, 2px lines, >=8px markers with a
 * 2px surface ring, hairline solid grid, a legend plus sparing end labels, a
 * crosshair that snaps to the nearest report with ONE tooltip for every
 * series, hit areas far larger than the marks, keyboard parity, and every
 * string from the data inserted with textContent — never parsed as HTML. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const SURFACE = "#0f1826", GRID = "#1a2d47", ZERO = "#2e4466", AXIS = "#94a3b8", MUTED = "#64748b", INK = "#e2e8f0";
  const tip = document.getElementById("tip");

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(tag, cls, text, parent) {           // HTML node, text only
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    if (parent) parent.appendChild(n);
    return n;
  }
  function readJSON(id) {
    const s = document.getElementById(id);
    try { return s ? JSON.parse(s.textContent) : null; } catch (e) { return null; }
  }
  function ord(iso) { return Date.parse(iso + "T00:00:00Z") / 86400000; }
  function fmtDate(iso) { return iso ? iso.split("-").reverse().join("/") : "—"; }
  function fmtM(v) { return v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + v.toFixed(1) + " ח׳"; }

  function showTip(clientX, clientY) {
    tip.style.display = "block";
    const pad = 14, r = tip.getBoundingClientRect();
    let x = clientX + pad, y = clientY + pad;
    if (x + r.width > window.innerWidth - 8) x = clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = Math.max(8, clientY - r.height - pad);
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { tip.style.display = "none"; }

  /* ── How the delay develops ─────────────────────────────────────────── */
  function drawDelay() {
    const data = readJSON("delay-data"), box = document.getElementById("delay-chart");
    if (!data || !box) return;
    box.textContent = "";
    const W = Math.max(340, box.clientWidth), H = 340;
    const L = 52, R = W < 560 ? 16 : 96, T = 26, B = 42;
    const pts = data.points, series = data.series;
    const x0 = ord(pts[0].date), x1 = ord(pts[pts.length - 1].date);
    const px = d => L + (ord(d) - x0) / Math.max(1, x1 - x0) * (W - L - R);
    const py = v => T + (data.y_max - v) / Math.max(0.1, data.y_max - data.y_min) * (H - T - B);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": "התפתחות האיחור בחודשים לאורך הדוחות" }, box);

    // grid + y ticks (months), zero line a step stronger
    data.ticks.forEach(t => {
      el("line", { x1: L, x2: W - R, y1: py(t), y2: py(t), stroke: t === 0 ? ZERO : GRID, "stroke-width": 1 }, svg);
      const lab = el("text", { x: L - 8, y: py(t) + 4, "text-anchor": "end", "font-size": 11, fill: AXIS }, svg);
      lab.textContent = (t > 0 ? "+" : "") + t;
    });
    const unit = el("text", { x: L - 8, y: T - 10, "text-anchor": "end", "font-size": 11, fill: MUTED }, svg);
    unit.textContent = "חודשים";

    // stage changes: a hairline + a small marker on top; the name is in the tooltip
    pts.forEach(p => {
      if (!p.stage_changed) return;
      el("line", { x1: px(p.date), x2: px(p.date), y1: T, y2: H - B, stroke: "#5b6b82", "stroke-width": 1 }, svg);
      el("rect", { x: px(p.date) - 3, y: T - 3, width: 6, height: 6, fill: "#5b6b82", transform: `rotate(45 ${px(p.date)} ${T})` }, svg);
    });

    // x labels, newest always shown, older skipped when they would collide
    let lastX = Infinity;
    for (let i = pts.length - 1; i >= 0; i--) {
      const x = px(pts[i].date);
      if (lastX - x < 40) continue;
      const t = el("text", { x, y: H - B + 18, "text-anchor": "middle", "font-size": 10, fill: MUTED }, svg);
      t.textContent = fmtDate(pts[i].date).slice(0, 5);
      lastX = x;
    }

    // lines (broken where a report has no value) and markers
    const dots = {};
    series.forEach(s => {
      let seg = [];
      const flush = () => { if (seg.length > 1) el("polyline", { points: seg.join(" "), fill: "none", stroke: s.color,
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg); seg = []; };
      pts.forEach(p => { const v = p[s.key]; if (v === null || v === undefined) flush(); else seg.push(`${px(p.date)},${py(v)}`); });
      flush();
      dots[s.key] = pts.map(p => {
        const v = p[s.key];
        if (v === null || v === undefined) return null;
        const jumped = s.key === "fc_slip" && p.moves.some(m => m.kind === "forecast");
        return el("circle", { cx: px(p.date), cy: py(v), r: jumped ? 5.5 : 4, fill: s.color, stroke: SURFACE, "stroke-width": 2 }, svg);
      });
    });

    // end labels — only where they don't collide (legend + tooltip carry the rest)
    if (R > 60) {
      const placed = [];
      series.forEach(s => {
        let last = null;
        for (let i = pts.length - 1; i >= 0; i--) if (pts[i][s.key] !== null && pts[i][s.key] !== undefined) { last = pts[i]; break; }
        if (!last) return;
        const y = py(last[s.key]);
        if (placed.some(q => Math.abs(q - y) < 14)) return;
        placed.push(y);
        // Short name + value; the full name is in the legend and the tooltip.
        // RTL text: anchor "end" is its left edge, so the label runs rightward from the point.
        const t = el("text", { x: px(last.date) + 10, y: y + 4, "font-size": 11, fill: INK, direction: "rtl",
          "text-anchor": "end" }, svg);
        t.textContent = `${s.short} ${fmtM(last[s.key])}`;
      });
    }

    // crosshair + one hit column per report (the whole plot height is the target)
    const cross = el("line", { y1: T, y2: H - B, stroke: AXIS, "stroke-width": 1, opacity: 0 }, svg);
    let active = -1;
    function select(i, clientX, clientY) {
      if (i < 0 || i >= pts.length) return;
      if (active >= 0) series.forEach(s => { const d = dots[s.key][active]; if (d) d.setAttribute("r", d.dataset.r || d.getAttribute("r")); });
      active = i;
      const p = pts[i], x = px(p.date);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("opacity", 0.8);
      series.forEach(s => { const d = dots[s.key][i]; if (d) { d.dataset.r = d.dataset.r || d.getAttribute("r"); d.setAttribute("r", 6.5); } });
      fillDelayTip(p, series);
      if (clientX === undefined) {
        const r = box.getBoundingClientRect();
        clientX = r.left + x * r.width / W; clientY = r.top + T * r.height / H + 20;
      }
      showTip(clientX, clientY);
    }
    const mids = pts.map((p, i) => i === 0 ? L : (px(pts[i - 1].date) + px(p.date)) / 2);
    pts.forEach((p, i) => {
      const x0c = mids[i], x1c = i === pts.length - 1 ? W - R + 10 : mids[i + 1];
      const hit = el("rect", { x: x0c, y: T, width: Math.max(1, x1c - x0c), height: H - T - B, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", e => select(i, e.clientX, e.clientY));
      hit.addEventListener("pointerdown", e => select(i, e.clientX, e.clientY));
    });
    svg.addEventListener("pointerleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
    box.onkeydown = e => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        select(active < 0 ? pts.length - 1 : active + (e.key === "ArrowRight" ? 1 : -1));
      } else if (e.key === "Escape") { cross.setAttribute("opacity", 0); hideTip(); }
    };
    box.onblur = () => { cross.setAttribute("opacity", 0); hideTip(); };
  }

  function fillDelayTip(p, series) {
    tip.textContent = "";
    h("div", "hd", `דוח ${fmtDate(p.date)}`, tip);
    const st = h("div", "", null, tip);
    h("span", "", `שלב: ${p.stage}`, st);
    if (p.stage_changed) h("span", "pill", "שלב חדש", st);
    series.forEach(s => {
      const row = h("div", "row", null, tip);
      const k = h("span", "key", null, row); k.style.background = s.color;
      h("b", "", fmtM(p[s.key]), row);
      h("span", "", s.label, row);
    });
    const tg = h("div", "sec", null, tip);
    h("div", "", `יעד חשמול: ${p.fc ? fmtDate(p.fc) : (p.fc_text ? "מלל — " + p.fc_text.slice(0, 60) : "—")}`, tg);
    h("div", "", `יעד תכנית פיתוח: ${fmtDate(p.dev)}`, tg);
    if (p.moves.length) {
      const mv = h("div", "sec", null, tip);
      h("div", "", "זז מאז הדוח הקודם:", mv);
      p.moves.forEach(m => h("div", "", `• ${m.kind === "forecast" ? "יעד החשמול" : "תכנית הפיתוח"} נדחה ב-${m.months} ח׳`, mv));
    }
    if (p.topics.length) {
      const tp = h("div", "sec", null, tip);
      h("div", "", "נושאים בדיווח השבוע:", tp);
      p.topics.forEach(t => h("span", "pill", t, tp));
    }
    if (p.risk_changed) {
      const rc = h("div", "sec", null, tip);
      h("div", "", "⚠ עמודת הסיכונים השתנתה בדוח הזה:", rc);
      h("div", "", p.risk_text || "— (ריק)", rc);
    }
    if (p.week_text) {
      const wk = h("div", "sec", null, tip);
      h("div", "", `דיווח שבועי ${fmtDate(p.week)}${p.week_repeat ? " · 🔁 זהה לשבוע הקודם" : ""}:`, wk);
      h("div", "", p.week_text, wk);
    }
  }

  /* ── Risks over time: topic × week ───────────────────────────────────── */
  function drawMatrix() {
    const data = readJSON("matrix-data"), box = document.getElementById("risk-matrix");
    if (!data || !box) return;
    box.textContent = "";
    const W = Math.max(340, box.clientWidth), n = data.weeks.length;
    const labelW = W < 560 ? 118 : 190, cellH = 22, gap = 2, top = 4, xLab = 22;
    const gridW = W - labelW - 10, cw = gridW / n;
    const H = top + data.rows.length * (cellH + gap) + xLab;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "נושאי סיכון לפי שבוע" }, box);
    const HIT = "#3987e5", REPEAT = "#64748b", MISS = "#172338";
    const gapWeeks = data.missing_before || data.weeks.map(() => 0);

    data.rows.forEach((row, r) => {
      const y = top + r * (cellH + gap);
      const lab = el("text", { x: W - 4, y: y + cellH / 2 + 4, "text-anchor": "end", "font-size": 11.5, fill: row.kind === "repeat" ? MUTED : INK }, svg);
      lab.textContent = `${row.label} (${row.weeks})`;
      row.cells.forEach((c, i) => {
        const cell = el("rect", { x: i * cw + gap / 2, y, width: Math.max(1, cw - gap), height: cellH, rx: 2,
          fill: c.hit ? (row.kind === "repeat" ? REPEAT : HIT) : MISS }, svg);
        const over = e => {
          cell.setAttribute("stroke", INK); cell.setAttribute("stroke-width", 1.5);
          tip.textContent = "";
          h("div", "hd", `שבוע ${fmtDate(data.weeks[i])}`, tip);
          if (gapWeeks[i]) h("div", "", `לפני שבוע זה חסרים בקובץ ${gapWeeks[i]} שבועות`, tip);
          h("div", "", row.label, tip);
          const body = h("div", "sec", null, tip);
          if (row.kind === "repeat") h("div", "", c.hit ? "🔁 המלל זהה לשבוע הקודם" : "מלל חדש השבוע", body);
          else if (c.hit) h("div", "", c.quote, body);
          else h("div", "", "לא הוזכר השבוע", body);
          showTip(e.clientX, e.clientY);
        };
        cell.addEventListener("pointermove", over);
        cell.addEventListener("pointerdown", over);
        cell.addEventListener("pointerleave", () => { cell.removeAttribute("stroke"); hideTip(); });
      });
    });
    // holes in the weekly history: a dashed divider where weeks are missing
    gapWeeks.forEach((k, i) => {
      if (!k) return;
      const x = i * cw;
      el("line", { x1: x, x2: x, y1: top - 2, y2: H - xLab + 2, stroke: "#e0b341", "stroke-width": 2,
        "stroke-dasharray": "3 3" }, svg);
    });
    // week labels, newest always shown
    let lastX = Infinity;
    for (let i = n - 1; i >= 0; i--) {
      const x = i * cw + cw / 2;
      if (lastX - x < 46) continue;
      const t = el("text", { x, y: H - 6, "text-anchor": "middle", "font-size": 10, fill: MUTED }, svg);
      t.textContent = fmtDate(data.weeks[i]).slice(0, 5);
      lastX = x;
    }
  }

  function drawAll() { drawDelay(); drawMatrix(); }
  drawAll();
  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawAll, 150); });
})();
