/* Station scenes — the live electrical animation inside each dashboard station.
 *
 * Decoration only, like substation_bg.js: it never fetches, never draws text
 * and never shows a number. The page mounts a <canvas data-scene="..."> and
 * feeds it a mood (load, alarm, counts) through window.StationScenes:
 *
 *   StationScenes.mount(canvas)          canvas.dataset.scene picks the scene
 *   StationScenes.set(kind, params)      params for every canvas of that kind
 *   StationScenes.burst(kind)            a one-off surge (a number just moved)
 *   StationScenes.setActive(canvas, on)  skip a canvas that is covered
 *
 *   scope  החלטות  3-phase oscilloscope; a fault spike while approvals wait
 *   breaker משימות  3-pole circuit breaker opening and closing; trips red when overdue
 *   xfmr    פרויקטים substation power transformer; pulses ride bus → windings → bus, red = late
 *   globe   לקחים   one plasma globe fed by streams from outside; touch gathers the discharge
 *   rail    the feeder down the side of a zoom, so the live picture follows the scroll
 *
 * prefers-reduced-motion: one still frame per resize, no loop.
 */
(function () {
    'use strict';
    const REDUCED = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    const TAU = Math.PI * 2;
    const rnd = (a, b) => a + Math.random() * (b - a);
    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

    const PARAMS = {
        scope: { load: .4, alarm: false },
        breaker: { level: .4, alarm: false },
        xfmr:  { count: 40, bad: 0 },
        rail:  { color: '#00d4ff', boost: 0 },
        globe: { amount: .5, charge: .5 },
    };
    const mounts = [];

    /* ── helpers ─────────────────────────────────────────────────────── */
    function glow(ctx, color, blur) { ctx.shadowColor = color; ctx.shadowBlur = blur; }
    // jagged lightning between two points (midpoint displacement)
    function bolt(x1, y1, x2, y2, rough, depth) {
        let pts = [[x1, y1], [x2, y2]];
        for (let d = 0; d < depth; d++) {
            const next = [pts[0]];
            for (let i = 1; i < pts.length; i++) {
                const [ax, ay] = pts[i - 1], [bx, by] = pts[i];
                const len = Math.hypot(bx - ax, by - ay), nx = -(by - ay) / (len || 1), ny = (bx - ax) / (len || 1);
                const off = rnd(-1, 1) * rough * len;
                next.push([(ax + bx) / 2 + nx * off, (ay + by) / 2 + ny * off], pts[i]);
            }
            pts = next;
        }
        return pts;
    }
    // ctx.roundRect is missing on older Safari; one throw would stop every scene
    function rrect(ctx, x, y, w, h, r) { if (ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h); }
    function strokePts(ctx, pts) {
        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.stroke();
    }

    /* ── החלטות: 3-phase oscilloscope ────────────────────────────────── */
    function scope(s, ctx, w, h, dt, p) {
        s.ph = (s.ph || 0) + dt * (1.6 + p.load * 2.4);
        s.kick = Math.max(0, (s.kick || 0) - dt * 1.4);
        s.fault = Math.max(0, (s.fault || 0) - dt);
        if (p.alarm && (s.nextFault = (s.nextFault ?? 2) - dt) <= 0) { s.fault = .45; s.nextFault = rnd(2.2, 4); }
        // the graticule of a scope screen
        ctx.strokeStyle = 'rgba(0,212,255,.08)'; ctx.lineWidth = 1;
        for (let x = (s.ph * 12) % 22; x < w; x += 22) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
        for (let y = h / 2 % 22; y < h; y += 22) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
        ctx.strokeStyle = 'rgba(0,212,255,.22)'; ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
        const A = h * .3 * (.35 + .65 * p.load) * (1 + s.kick * .7), lam = Math.max(120, w / 1.7);
        const phases = [['#00d4ff', 0], ['#8b9cf4', TAU / 3], ['#e6edf7', 2 * TAU / 3]];
        phases.forEach(([col, off], k) => {
            const faulty = k === 0 && s.fault > 0;
            ctx.strokeStyle = faulty ? '#ff5f6d' : col; ctx.lineWidth = k === 2 ? 1.2 : 1.8;
            glow(ctx, faulty ? '#ff5f6d' : col, faulty ? 16 : 8);
            ctx.globalAlpha = k === 2 ? .55 : .95;
            ctx.beginPath();
            for (let x = 0; x <= w; x += 3) {
                let y = h / 2 + A * Math.sin(TAU * x / lam - s.ph + off);
                if (faulty) y += Math.sin(x * .9 + s.ph * 40) * A * .9 * (s.fault / .45) * (x / w);
                x ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
            }
            ctx.stroke();
        });
        ctx.globalAlpha = 1; ctx.shadowBlur = 0;
        // the beam sweeping across the screen
        const sx = (s.ph * 60) % (w + 80) - 40, g = ctx.createLinearGradient(sx - 40, 0, sx + 6, 0);
        g.addColorStop(0, 'rgba(0,212,255,0)'); g.addColorStop(1, 'rgba(0,212,255,.22)');
        ctx.fillStyle = g; ctx.fillRect(sx - 40, 0, 46, h);
        if (s.fault > 0) { ctx.fillStyle = `rgba(255,95,109,${s.fault * .25})`; ctx.fillRect(0, 0, w, h); }
    }

    /* ── משימות: a 3-pole circuit breaker that opens and closes ──────── */
    // Current flows through the closed contacts; the moving contact pulls
    // away, a soft arc is drawn out and quenched, the load side goes dark,
    // then the breaker recloses. A busier board operates it more often;
    // overdue missions trip it (red indicator, red-tinted arc).
    function breaker(s, ctx, w, h, dt, p) {
        const col = '#ff9f43', hot = p.alarm ? '#ff5f6d' : col;
        const poles = 3, gapMax = Math.min(26, w * .05), units = Math.max(1, Math.round(w / 650));
        s.t = (s.t || 0) + dt; s.flow = (s.flow || 0) + dt;
        // state machine: closed → opening → open → closing → closed
        if (!s.phase) { s.phase = 'closed'; s.left = rnd(1.5, 3); s.gap = 0; s.arc = 0; }
        s.left -= dt;
        if (s.phase === 'closed' && s.left <= 0) { s.phase = 'opening'; s.arc = 1; }
        if (s.phase === 'opening') { s.gap = Math.min(gapMax, s.gap + dt * gapMax * 9); if (s.gap >= gapMax) { s.phase = 'open'; s.left = rnd(.5, 1.1); } }
        if (s.phase === 'open' && s.left <= 0) s.phase = 'closing';
        if (s.phase === 'closing') { s.gap = Math.max(0, s.gap - dt * gapMax * 6); if (!s.gap) { s.phase = 'closed'; s.left = rnd(1.4, 3.2) * (1.4 - p.level * .8); s.touch = .35; } }
        s.arc = Math.max(0, s.arc - dt * 3.2);              // the arc is quenched in ~0.3 s
        s.touch = Math.max(0, (s.touch || 0) - dt);
        const conducting = s.phase === 'closed' || s.arc > .05;
        for (let u = 0; u < units; u++) {
            const cx = w * (u + .5) / units, bw = Math.min(150, w / units * .42), top = h * .14, bot = h * .86;
            for (let k = 0; k < poles; k++) {
                const y = top + (bot - top) * (k + .5) / poles;
                const xL = cx - bw / 2, xR = cx + bw / 2;
                // conductors (line side always live; load side only while conducting)
                const x0 = u ? w * u / units : 0, x1 = (u + 1) * w / units;
                ctx.lineWidth = 2;
                ctx.strokeStyle = 'rgba(255,159,67,.55)'; ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(xL, y); ctx.stroke();
                ctx.strokeStyle = conducting ? 'rgba(255,159,67,.55)' : 'rgba(79,98,128,.5)'; ctx.beginPath(); ctx.moveTo(xR, y); ctx.lineTo(x1, y); ctx.stroke();
                // current: soft dashes riding the conductor
                ctx.save(); ctx.setLineDash([6, 16]); ctx.lineDashOffset = s.flow * 70; ctx.lineWidth = 2.4;
                ctx.strokeStyle = '#ffd7a8'; glow(ctx, col, 8); ctx.globalAlpha = .9;
                ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(xL, y); ctx.stroke();
                if (conducting) { ctx.beginPath(); ctx.moveTo(xR, y); ctx.lineTo(x1, y); ctx.stroke(); }
                ctx.restore();
                // the interrupter: a translucent insulating bottle
                const hh = Math.min(22, (bot - top) / poles * .7);
                ctx.fillStyle = 'rgba(20,34,54,.85)'; ctx.strokeStyle = 'rgba(143,160,184,.55)'; ctx.lineWidth = 1;
                ctx.beginPath(); rrect(ctx, xL, y - hh / 2, bw, hh, hh / 2); ctx.fill(); ctx.stroke();
                // contacts: fixed (line side) and moving (load side)
                const mid = cx - 2, fixedEnd = mid, movingStart = mid + 4 + s.gap;
                ctx.lineWidth = 4; ctx.lineCap = 'round';
                ctx.strokeStyle = '#c9d6e8'; ctx.beginPath(); ctx.moveTo(xL + 4, y); ctx.lineTo(fixedEnd, y); ctx.stroke();
                ctx.strokeStyle = '#9fb0c7'; ctx.beginPath(); ctx.moveTo(movingStart, y); ctx.lineTo(xR - 4, y); ctx.stroke();
                if (s.phase === 'closed') {                   // current through the closed contacts
                    ctx.strokeStyle = col; glow(ctx, col, 10); ctx.lineWidth = 1.6; ctx.globalAlpha = .7 + .3 * Math.sin(s.t * 18 + k);
                    ctx.beginPath(); ctx.moveTo(xL + 4, y); ctx.lineTo(xR - 4, y); ctx.stroke(); ctx.globalAlpha = 1; ctx.shadowBlur = 0;
                }
                if (s.arc > .05) {                            // a soft arc drawn out between the contacts, then quenched
                    const a = s.arc, len = movingStart - fixedEnd;
                    ctx.strokeStyle = hot; ctx.lineWidth = 2 + 4 * a; ctx.globalAlpha = .35 * a; glow(ctx, hot, 18);
                    ctx.beginPath(); ctx.moveTo(fixedEnd, y);
                    ctx.quadraticCurveTo(fixedEnd + len / 2, y - 4 * Math.sin(s.t * 40 + k) * a, movingStart, y); ctx.stroke();
                    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2; ctx.globalAlpha = .8 * a; ctx.stroke();
                    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
                }
                if (s.touch > 0) {                            // a small flash as the contacts meet
                    ctx.fillStyle = `rgba(255,215,168,${s.touch * 1.6})`; glow(ctx, col, 14);
                    ctx.beginPath(); ctx.arc(mid + 2, y, 3 + s.touch * 8, 0, TAU); ctx.fill(); ctx.shadowBlur = 0;
                }
            }
            // operating mechanism: the rod linking the three moving contacts, and its indicator
            const rodX = cx + 10 + s.gap;
            ctx.strokeStyle = 'rgba(143,160,184,.6)'; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(rodX, top + 4); ctx.lineTo(rodX, bot - 4); ctx.stroke();
            const on = s.phase === 'closed', led = p.alarm ? '#ff5f6d' : (on ? '#36e273' : '#ffc107');
            ctx.fillStyle = led; glow(ctx, led, 12);
            ctx.beginPath(); ctx.arc(cx + bw / 2 + 12, top + 2, 3.5, 0, TAU); ctx.fill(); ctx.shadowBlur = 0;
        }
    }
    breaker.burst = s => { if (s.phase === 'closed') s.left = 0; };   // operate now

    /* ── פרויקטים: a substation power transformer ────────────────────── */
    // HV busbar → droppers → HV bushings → the windings in the tank (glowing
    // with the load) → LV bushings → LV busbar. Pulses ride that path; the
    // late share of the projects rides in red.
    function xfmr(s, ctx, w, h, dt, p) {
        const n = Math.max(1, Math.round(w / 420)), busTop = h * .1, busBot = h * .94;
        s.t = (s.t || 0) + dt; s.kick = Math.max(0, (s.kick || 0) - dt);
        const units = [];
        for (let i = 0; i < n; i++) {
            const cx = w * (i + .5) / n, tw = Math.min(h * .9, w / n * .36), th = h * .34;
            units.push({ cx, tw, th, ty: h * .5 - th / 2 });
        }
        // busbars
        ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(139,156,244,.6)';
        [busTop, busBot].forEach(y => { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); });
        // the paths current takes, one per phase per transformer
        if (!s.paths || s.pw !== w || s.ph !== h) {
            s.pw = w; s.ph = h; s.paths = [];
            units.forEach(u => [-1, 0, 1].forEach(k => {
                const bx = u.cx + k * u.tw * .26;
                const pts = [[u.cx + k * u.tw * .26 - u.tw * 1.2, busTop], [bx, busTop], [bx, u.ty - h * .14], [bx, u.ty + u.th * .5],
                             [bx, u.ty + u.th + h * .1], [bx, busBot], [bx + u.tw * 1.2, busBot]];
                const seg = []; let L = 0;
                for (let j = 1; j < pts.length; j++) { const d = Math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]); seg.push(d); L += d; }
                s.paths.push({ pts, seg, L });
            }));
            s.pulses = [];
        }
        units.forEach(u => {
            const { cx, tw, th, ty } = u;
            // droppers and LV leads
            ctx.strokeStyle = 'rgba(139,156,244,.45)'; ctx.lineWidth = 1.4;
            [-1, 0, 1].forEach(k => {
                const bx = cx + k * tw * .26;
                ctx.beginPath(); ctx.moveTo(bx, busTop); ctx.lineTo(bx, ty - h * .14); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(bx, ty + th + h * .1); ctx.lineTo(bx, busBot); ctx.stroke();
            });
            // radiators on both sides
            [-1, 1].forEach(side => {
                const rx = cx + side * (tw / 2 + 4) - (side < 0 ? tw * .22 : 0);
                ctx.strokeStyle = 'rgba(143,160,184,.5)'; ctx.lineWidth = 1;
                for (let f = 0; f < 6; f++) { const x = rx + f * tw * .22 / 6; ctx.beginPath(); ctx.moveTo(x, ty + 3); ctx.lineTo(x, ty + th - 3); ctx.stroke(); }
            });
            // the tank
            const g = ctx.createLinearGradient(cx - tw / 2, 0, cx + tw / 2, 0);
            g.addColorStop(0, '#1b2a42'); g.addColorStop(.5, '#2a3d5c'); g.addColorStop(1, '#1b2a42');
            ctx.fillStyle = g; ctx.strokeStyle = 'rgba(139,156,244,.6)'; ctx.lineWidth = 1.2;
            ctx.beginPath(); rrect(ctx, cx - tw / 2, ty, tw, th, 4); ctx.fill(); ctx.stroke();
            // the windings breathe with the load (50 Hz is too fast to see — this is the envelope)
            const hum = .55 + .25 * Math.sin(s.t * 6) + .3 * s.kick;
            [-1, 0, 1].forEach(k => {
                const wx = cx + k * tw * .26;
                ctx.strokeStyle = `rgba(139,156,244,${.35 + .45 * hum})`; glow(ctx, '#8b9cf4', 10 * hum); ctx.lineWidth = 1.2;
                for (let c = 0; c < 4; c++) { ctx.beginPath(); ctx.ellipse(wx, ty + th * (.25 + c * .17), tw * .08, th * .05, 0, 0, TAU); ctx.stroke(); }
                ctx.shadowBlur = 0;
            });
            // bushings: HV tall with sheds, LV short
            [-1, 0, 1].forEach(k => {
                const bx = cx + k * tw * .26;
                for (let sh = 0; sh < 5; sh++) {
                    const y = ty - 3 - sh * (h * .14 / 5);
                    ctx.fillStyle = '#8fa0b8'; ctx.beginPath(); ctx.ellipse(bx, y, 5 - sh * .4, 1.8, 0, 0, TAU); ctx.fill();
                }
                for (let sh = 0; sh < 3; sh++) {
                    const y = ty + th + 3 + sh * (h * .1 / 3);
                    ctx.fillStyle = '#7d8fa8'; ctx.beginPath(); ctx.ellipse(bx, y, 3.6, 1.5, 0, 0, TAU); ctx.fill();
                }
            });
        });
        // pulses along the paths; the late share in red
        const want = clamp(Math.round(p.count / 4), 6, 40) + (s.kick > 0 ? 12 : 0);
        while (s.pulses.length < want) s.pulses.push({ path: (Math.random() * s.paths.length) | 0, t: Math.random(), v: rnd(.12, .22) });
        if (s.pulses.length > want) s.pulses.length = want;
        const badN = Math.round(s.pulses.length * clamp(p.bad, 0, 1));
        s.pulses.forEach((q, i) => {
            q.t += q.v * dt; if (q.t > 1) { q.t -= 1; q.path = (Math.random() * s.paths.length) | 0; }
            const P = s.paths[q.path]; let d = q.t * P.L, j = 0;
            while (j < P.seg.length - 1 && d > P.seg[j]) { d -= P.seg[j]; j++; }
            const a = P.pts[j], b = P.pts[j + 1], u = P.seg[j] ? d / P.seg[j] : 0;
            const x = a[0] + (b[0] - a[0]) * u, y = a[1] + (b[1] - a[1]) * u;
            if (j === 3) return;                                  // inside the tank: the windings carry it
            const col = i < badN ? '#ff5f6d' : '#8b9cf4';
            const gr = ctx.createRadialGradient(x, y, 0, x, y, 7);
            gr.addColorStop(0, '#fff'); gr.addColorStop(.35, col); gr.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, 7, 0, TAU); ctx.fill();
        });
    }
    xfmr.burst = s => { s.kick = 1.2; };

    /* ── לקחים: plasma globe ─────────────────────────────────────────── */
    // Like the real thing: touch the glass and the discharge gathers into one
    // bright channel to your finger, the glass glows where you touch it, and a
    // press sends a pulse through every filament.
    function globe(s, ctx, w, h, dt, p, m) {
        const count = 1;                                       // one globe, in the middle
        s.t = (s.t || 0) + dt; s.kick = Math.max(0, (s.kick || 0) - dt);
        s.f = s.f || []; s.touchA = s.touchA || [];
        for (let gi = 0; gi < count; gi++) {
            const cx = w / 2, cy = h * .52, R = Math.min(h * .44, w * .4);
            inflow(s, ctx, w, h, dt, cx, cy, R, p);
            let target = null, near = 0;
            if (m) {
                const d = Math.hypot(m.x - cx, m.y - cy);
                if (d < R * 2.4) { target = Math.atan2(m.y - cy, m.x - cx); near = clamp(1.25 - d / (R * 1.6), 0, 1); }
            }
            s.touchA[gi] = (s.touchA[gi] || 0) + ((near > .55 ? 1 : 0) - (s.touchA[gi] || 0)) * Math.min(1, dt * 8);
            const touch = s.touchA[gi];
            // glass and base
            const bg = ctx.createRadialGradient(cx, cy, R * .1, cx, cy, R);
            bg.addColorStop(0, `rgba(45,212,191,${.1 + .12 * touch})`); bg.addColorStop(1, 'rgba(10,20,35,.55)');
            ctx.fillStyle = bg; ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fill();
            ctx.strokeStyle = `rgba(45,212,191,${.35 + .4 * touch})`; ctx.lineWidth = 1; ctx.stroke();
            // charge ring: how much of the knowledge is approved
            ctx.strokeStyle = '#2dd4bf'; ctx.lineWidth = 2.5; glow(ctx, '#2dd4bf', 10);
            ctx.beginPath(); ctx.arc(cx, cy, R + 5, -Math.PI / 2, -Math.PI / 2 + TAU * clamp(p.charge, 0, 1)); ctx.stroke();
            ctx.shadowBlur = 0;
            // filaments
            const want = 4 + Math.round(clamp(p.amount, 0, 1) * 7) + (s.kick > 0 ? 4 : 0);
            const fl = s.f[gi] = s.f[gi] || [];
            while (fl.length < want) fl.push({ a: rnd(0, TAU), v: rnd(-.6, .6), seed: rnd(0, 100) });
            if (fl.length > want) fl.length = want;
            fl.forEach((f, i) => {
                f.v += rnd(-1.4, 1.4) * dt; f.v *= .97;
                if (target !== null) {
                    // near the glass a few reach for the hand; touching it, most of them collapse onto it
                    const pull = touch > .1 ? (i < Math.ceil(fl.length * .75) ? 14 * touch : 0) : (i < 2 ? 5 * near : 0);
                    let d = target - f.a; d = Math.atan2(Math.sin(d), Math.cos(d)); f.v += d * dt * pull;
                    if (pull) f.v *= .9;
                }
                f.a += f.v * dt;
                const ex = cx + Math.cos(f.a) * R * .97, ey = cy + Math.sin(f.a) * R * .97;
                ctx.beginPath(); ctx.moveTo(cx, cy);
                for (let k = 1; k <= 10; k++) {
                    const u = k / 10, wob = Math.sin(s.t * 9 + f.seed + k * 1.7) * R * .07 * Math.sin(u * Math.PI);
                    ctx.lineTo(cx + (ex - cx) * u - Math.sin(f.a) * wob, cy + (ey - cy) * u + Math.cos(f.a) * wob);
                }
                const g = ctx.createLinearGradient(cx, cy, ex, ey);
                g.addColorStop(0, '#e6fffb'); g.addColorStop(.5, '#2dd4bf'); g.addColorStop(1, '#c084fc');
                ctx.strokeStyle = g; ctx.lineWidth = 1.3 + s.kick; glow(ctx, '#2dd4bf', 10 + 10 * s.kick); ctx.globalAlpha = .9; ctx.stroke();
                ctx.globalAlpha = 1;
                ctx.fillStyle = '#c084fc'; glow(ctx, '#c084fc', 14); ctx.beginPath(); ctx.arc(ex, ey, 2.2, 0, TAU); ctx.fill();
            });
            if (touch > .05 && target !== null) {
                // the channel: one thick, bright discharge to the point of contact
                const tx = cx + Math.cos(target) * R * .98, ty = cy + Math.sin(target) * R * .98;
                const pts = bolt(cx, cy, tx, ty, .12, 4);
                ctx.globalAlpha = touch; ctx.lineCap = 'round';
                ctx.strokeStyle = '#2dd4bf'; ctx.lineWidth = 5; glow(ctx, '#2dd4bf', 26); strokePts(ctx, pts);
                ctx.strokeStyle = '#f0fffd'; ctx.lineWidth = 1.8; glow(ctx, '#ffffff', 10); strokePts(ctx, pts);
                const spot = ctx.createRadialGradient(tx, ty, 0, tx, ty, R * .35);
                spot.addColorStop(0, 'rgba(240,255,253,.9)'); spot.addColorStop(.3, 'rgba(192,132,252,.5)'); spot.addColorStop(1, 'rgba(192,132,252,0)');
                ctx.fillStyle = spot; ctx.beginPath(); ctx.arc(tx, ty, R * .35, 0, TAU); ctx.fill();
                ctx.globalAlpha = 1; ctx.shadowBlur = 0;
            }
            if (s.kick > 0) {                                   // a press: a ring runs out to the glass
                const r = R * (1.2 - s.kick / 1.2) ;
                ctx.strokeStyle = `rgba(45,212,191,${s.kick * .6})`; ctx.lineWidth = 2;
                ctx.beginPath(); ctx.arc(cx, cy, Math.max(2, r), 0, TAU); ctx.stroke();
            }
            ctx.shadowBlur = 0;
            const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * .2);
            core.addColorStop(0, '#fff'); core.addColorStop(.4, '#7ff3e1'); core.addColorStop(1, 'rgba(45,212,191,0)');
            ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy, R * .2 * (1 + .08 * Math.sin(s.t * 5) + .25 * touch), 0, TAU); ctx.fill();
        }
    }
    globe.burst = s => { s.kick = 1.2; };

    // Knowledge drawn in from outside: slow, faint streams curve in from both
    // edges and are absorbed at the glass, each landing a soft flash on the rim.
    function inflow(s, ctx, w, h, dt, cx, cy, R, p) {
        s.streams = s.streams || [];
        const want = Math.round(4 + 6 * clamp(p.amount, 0, 1));
        while (s.streams.length < want) {
            const left = Math.random() < .5, sy = rnd(h * .1, h * .9);
            const a = Math.atan2(sy - cy, (left ? 0 : w) - cx) + rnd(-.35, .35);
            s.streams.push({ x0: left ? -10 : w + 10, y0: sy, a, bend: rnd(-.5, .5) * h, t: rnd(0, 1), v: rnd(.12, .22), purple: Math.random() < .35 });
        }
        if (s.streams.length > want) s.streams.length = want;
        s.rimFlash = s.rimFlash || [];
        s.streams.forEach(q => {
            q.t += q.v * dt;
            const ex = cx + Math.cos(q.a) * R, ey = cy + Math.sin(q.a) * R;
            const mx = (q.x0 + ex) / 2, my = (q.y0 + ey) / 2 + q.bend;
            const P = u => { const b = 1 - u; return [b * b * q.x0 + 2 * b * u * mx + u * u * ex, b * b * q.y0 + 2 * b * u * my + u * u * ey]; };
            if (q.t >= 1) { s.rimFlash.push({ a: q.a, life: .5 }); q.t = 0; q.y0 = rnd(h * .1, h * .9); q.a = Math.atan2(q.y0 - cy, q.x0 - cx) + rnd(-.35, .35); }
            // the path: a whisper of a line; the charge: a short glowing tail riding it
            const col = q.purple ? '192,132,252' : '45,212,191';
            ctx.strokeStyle = `rgba(${col},.07)`; ctx.lineWidth = 1;
            ctx.beginPath(); for (let u = 0; u <= 1.001; u += .05) { const [x, y] = P(u); u ? ctx.lineTo(x, y) : ctx.moveTo(x, y); } ctx.stroke();
            ctx.lineCap = 'round';
            for (let k = 0; k < 6; k++) {
                const u = q.t - k * .012; if (u < 0) break;
                const [x, y] = P(u);
                ctx.fillStyle = `rgba(${col},${(.55 - k * .08) * Math.min(1, q.t * 4)})`;
                ctx.beginPath(); ctx.arc(x, y, 2.2 - k * .25, 0, TAU); ctx.fill();
            }
        });
        s.rimFlash = s.rimFlash.filter(f => (f.life -= dt) > 0);
        s.rimFlash.forEach(f => {
            const x = cx + Math.cos(f.a) * R, y = cy + Math.sin(f.a) * R, g = ctx.createRadialGradient(x, y, 0, x, y, 12);
            g.addColorStop(0, `rgba(230,255,251,${f.life})`); g.addColorStop(1, 'rgba(45,212,191,0)');
            ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 12, 0, TAU); ctx.fill();
        });
    }

    /* ── the feeder: a thin conductor down the side of a zoomed station ── */
    // Shown once the station's scene has scrolled out of view, so some of the
    // live picture stays with you further down the page. Current flows up,
    // toward the station; it quickens a little while you scroll. Quiet on purpose.
    function rail(s, ctx, w, h, dt, p) {
        const col = p.color || '#00d4ff', x = w / 2;
        s.boost = Math.max(0, (s.boost || 0) - dt * 1.5);
        s.off = ((s.off || 0) + dt * (40 + 160 * Math.min(1, p.boost || 0) + 120 * s.boost)) % 48;
        ctx.strokeStyle = col; ctx.globalAlpha = .18; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
        ctx.globalAlpha = .75; ctx.lineWidth = 2; ctx.lineCap = 'round'; glow(ctx, col, 8);
        // drawn top → bottom: a growing offset slides the dashes back toward the top
        ctx.setLineDash([10, 38]); ctx.lineDashOffset = s.off;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
        ctx.setLineDash([]); ctx.shadowBlur = 0; ctx.globalAlpha = 1;
        // insulator ticks every so often, like a conductor on its supports
        ctx.fillStyle = col; ctx.globalAlpha = .3;
        for (let y = 40; y < h; y += 120) { ctx.fillRect(x - 4, y, 8, 2); }
        ctx.globalAlpha = 1;
    }
    scope.burst = s => { s.kick = 1; };

    const SCENES = { scope, breaker, xfmr, globe, rail };

    /* ── mount + loop ────────────────────────────────────────────────── */
    function size(m) {
        const r = m.canvas.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
        m.w = r.width; m.h = r.height;
        m.canvas.width = Math.max(1, Math.round(r.width * dpr)); m.canvas.height = Math.max(1, Math.round(r.height * dpr));
        m.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (REDUCED) draw(m, 0);
    }
    function draw(m, dt) {
        if (!m.w || !m.h) return;
        const fn = SCENES[m.kind];
        m.ctx.clearRect(0, 0, m.w, m.h);
        m.ctx.save();
        fn(m.state, m.ctx, m.w, m.h, dt, PARAMS[m.kind], m.mouse);
        m.ctx.restore();
    }
    function mount(canvas) {
        const kind = canvas.dataset.scene;
        if (!SCENES[kind] || mounts.some(m => m.canvas === canvas)) return;
        const m = { canvas, kind, ctx: canvas.getContext('2d'), state: {}, active: true, seen: true, mouse: null, w: 0, h: 0 };
        mounts.push(m);
        if ('ResizeObserver' in window) new ResizeObserver(() => size(m)).observe(canvas);
        if ('IntersectionObserver' in window) new IntersectionObserver(es => { m.seen = es[es.length - 1].isIntersecting; }).observe(canvas);
        const host = canvas.parentElement;
        const at = e => { const r = canvas.getBoundingClientRect(); m.mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; };
        host.addEventListener('pointermove', at);
        host.addEventListener('pointerdown', e => { at(e); const fn = SCENES[m.kind]; if (fn.burst && m.kind === 'globe') fn.burst(m.state); });
        host.addEventListener('pointerleave', () => { m.mouse = null; });
        host.addEventListener('pointercancel', () => { m.mouse = null; });
        size(m);
    }
    let last = 0;
    function loop(now) {
        const dt = Math.min(.05, (now - (last || now)) / 1000);
        last = now;
        mounts.forEach(m => { if (m.active && m.seen && m.canvas.offsetParent !== null) draw(m, dt); });
        requestAnimationFrame(loop);
    }
    if (!REDUCED) requestAnimationFrame(loop);

    window.StationScenes = {
        mount,
        set(kind, params) {
            if (!PARAMS[kind]) return;
            Object.assign(PARAMS[kind], params);
            if (REDUCED) mounts.filter(m => m.kind === kind).forEach(m => draw(m, 0));
        },
        burst(kind) {
            const fn = SCENES[kind];
            if (fn && fn.burst) mounts.filter(m => m.kind === kind).forEach(m => fn.burst(m.state));
        },
        setActive(canvas, on) { mounts.filter(m => m.canvas === canvas).forEach(m => { m.active = on; }); },
    };
})();
