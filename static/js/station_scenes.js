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
 *   arc    משימות  electric arc between two electrodes; red arcs when overdue
 *   line   פרויקטים transmission line; pulses ride the conductors, red = late
 *   globe  לקחים   plasma globe; filaments reach for the cursor
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
        arc:   { level: .4, alarm: false },
        line:  { count: 40, bad: 0 },
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

    /* ── משימות: arc between electrodes ──────────────────────────────── */
    function arc(s, ctx, w, h, dt, p) {
        const pairs = Math.max(1, Math.round(w / 420)), col = p.alarm ? '#ff5f6d' : '#ff9f43';
        s.t = (s.t || 0) + dt; s.flash = Math.max(0, (s.flash || 0) - dt * 2.5);
        s.sparks = s.sparks || [];
        if ((s.reroll = (s.reroll || 0) - dt) <= 0) {
            s.reroll = rnd(.05, .11);
            s.on = Math.random() < .55 + p.level * .4;           // a busier board arcs more of the time
            s.bolts = [];
            for (let i = 0; i < pairs; i++) {
                const x1 = w * (i + .16) / pairs, x2 = w * (i + .84) / pairs, y = h * .52;
                const strands = 1 + Math.round(p.level * 2);
                for (let k = 0; k < strands; k++) s.bolts.push(bolt(x1 + 6, y + rnd(-4, 4), x2 - 6, y + rnd(-4, 4), .28, 6));
                if (s.on && Math.random() < .25 + p.level * .5)
                    for (let n = 0; n < 3; n++) s.sparks.push({ x: Math.random() < .5 ? x1 + 6 : x2 - 6, y, vx: rnd(-60, 60), vy: rnd(-90, -20), life: rnd(.3, .7) });
            }
            if (p.alarm && Math.random() < .06) s.flash = 1;
        }
        // the electrodes
        for (let i = 0; i < pairs; i++) {
            [w * (i + .16) / pairs, w * (i + .84) / pairs].forEach((x, side) => {
                const g = ctx.createLinearGradient(x - 6, 0, x + 6, 0);
                g.addColorStop(0, '#1e3a5f'); g.addColorStop(.5, '#8fa0b8'); g.addColorStop(1, '#1e3a5f');
                ctx.fillStyle = g; ctx.fillRect(x - 5 + (side ? 0 : -1), h * .52, 10, h * .44);
                ctx.beginPath(); ctx.arc(x, h * .52, 7, 0, TAU); ctx.fillStyle = '#c9d6e8'; ctx.fill();
                if (s.on) { glow(ctx, col, 22); ctx.beginPath(); ctx.arc(x, h * .52, 4, 0, TAU); ctx.fillStyle = '#fff'; ctx.fill(); ctx.shadowBlur = 0; }
            });
        }
        if (s.on && s.bolts) {
            s.bolts.forEach((pts, i) => {
                ctx.strokeStyle = col; ctx.lineWidth = 3.2; ctx.globalAlpha = .45; glow(ctx, col, 20); strokePts(ctx, pts);
                ctx.strokeStyle = '#fff'; ctx.lineWidth = i ? 1 : 1.5; ctx.globalAlpha = .95; glow(ctx, col, 8); strokePts(ctx, pts);
            });
            ctx.globalAlpha = 1; ctx.shadowBlur = 0;
        }
        s.sparks = s.sparks.filter(k => (k.life -= dt) > 0);
        ctx.fillStyle = col; glow(ctx, col, 6);
        s.sparks.forEach(k => { k.vy += 260 * dt; k.x += k.vx * dt; k.y += k.vy * dt; ctx.globalAlpha = clamp(k.life * 2, 0, 1); ctx.fillRect(k.x, k.y, 2, 2); });
        ctx.globalAlpha = 1; ctx.shadowBlur = 0;
        if (s.flash > 0) { ctx.fillStyle = `rgba(255,95,109,${s.flash * .22})`; ctx.fillRect(0, 0, w, h); }
    }
    arc.burst = s => { s.flash = .8; s.reroll = 0; };

    /* ── פרויקטים: transmission line ─────────────────────────────────── */
    function line(s, ctx, w, h, dt, p) {
        const n = Math.max(3, Math.round(w / 230)), base = h * .96, top = h * .14;
        const xs = Array.from({ length: n }, (_, i) => w * (.06 + .88 * i / (n - 1)));
        const arms = [top + h * .1, top + h * .26, top + h * .42];
        // pylons: a lattice tower per span end
        ctx.strokeStyle = 'rgba(143,160,184,.55)'; ctx.lineWidth = 1;
        xs.forEach(x => {
            const bw = h * .16, tw = h * .04;
            ctx.beginPath();
            ctx.moveTo(x - bw, base); ctx.lineTo(x - tw, top); ctx.lineTo(x + tw, top); ctx.lineTo(x + bw, base);
            for (let k = 1; k < 6; k++) {
                const y1 = top + (base - top) * (k - 1) / 5, y2 = top + (base - top) * k / 5;
                const w1 = tw + (bw - tw) * (k - 1) / 5, w2 = tw + (bw - tw) * k / 5;
                ctx.moveTo(x - w1, y1); ctx.lineTo(x + w2, y2); ctx.moveTo(x + w1, y1); ctx.lineTo(x - w2, y2);
            }
            arms.forEach(y => { ctx.moveTo(x - h * .16, y); ctx.lineTo(x + h * .16, y); });
            ctx.stroke();
        });
        // conductors sag between towers
        const wire = (k, span, u) => {
            const x1 = xs[span] + (k - 1) * h * .14, x2 = xs[span + 1] + (k - 1) * h * .14, y = arms[k] + 3;
            const cx = (x1 + x2) / 2, cy = y + h * .2, a = 1 - u;
            return [a * a * x1 + 2 * a * u * cx + u * u * x2, a * a * y + 2 * a * u * cy + u * u * y];
        };
        ctx.strokeStyle = 'rgba(139,156,244,.45)'; ctx.lineWidth = 1.2;
        for (let k = 0; k < 3; k++) for (let sp = 0; sp < n - 1; sp++) {
            ctx.beginPath();
            for (let u = 0; u <= 1.001; u += .05) { const [x, y] = wire(k, sp, u); u ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
            ctx.stroke();
        }
        // pulses: power flowing right → left, the late share in red
        const want = clamp(Math.round(p.count / 6), 4, 26);
        s.pulses = s.pulses || [];
        while (s.pulses.length < want) s.pulses.push({ k: (Math.random() * 3) | 0, t: Math.random(), v: rnd(.06, .14), bad: false });
        if (s.pulses.length > want) s.pulses.length = want;
        const badN = Math.round(want * clamp(p.bad, 0, 1));
        s.pulses.forEach((q, i) => {
            q.bad = i < badN;
            q.t -= q.v * dt * (q.fast ? 4 : 1);
            if (q.t < 0) { q.t += 1; q.fast = false; q.k = (Math.random() * 3) | 0; }
            const pos = q.t * (n - 1), sp = Math.min(n - 2, Math.floor(pos)), [x, y] = wire(q.k, sp, pos - sp);
            const col = q.bad ? '#ff5f6d' : '#8b9cf4';
            const g = ctx.createRadialGradient(x, y, 0, x, y, 9);
            g.addColorStop(0, '#fff'); g.addColorStop(.3, col); g.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 9, 0, TAU); ctx.fill();
        });
        // corona crackle at a random insulator
        if (Math.random() < .04) s.corona = { x: xs[(Math.random() * n) | 0] + ((Math.random() * 3 | 0) - 1) * h * .14, y: arms[(Math.random() * 3) | 0], life: .25 };
        if (s.corona && (s.corona.life -= dt) > 0) {
            ctx.strokeStyle = '#bff4ff'; glow(ctx, '#00d4ff', 12); ctx.lineWidth = 1;
            for (let i = 0; i < 4; i++) strokePts(ctx, bolt(s.corona.x, s.corona.y, s.corona.x + rnd(-12, 12), s.corona.y + rnd(-12, 4), .4, 3));
            ctx.shadowBlur = 0;
        }
    }
    line.burst = s => (s.pulses || []).slice(0, 10).forEach(q => { q.fast = true; });

    /* ── לקחים: plasma globe ─────────────────────────────────────────── */
    function globe(s, ctx, w, h, dt, p, m) {
        const count = Math.max(1, Math.round(w / 380));
        s.t = (s.t || 0) + dt; s.kick = Math.max(0, (s.kick || 0) - dt);
        s.f = s.f || [];
        for (let gi = 0; gi < count; gi++) {
            const cx = w * (gi + .5) / count, cy = h * .52, R = Math.min(h * .44, w / count * .4);
            // glass and base
            const bg = ctx.createRadialGradient(cx, cy, R * .1, cx, cy, R);
            bg.addColorStop(0, 'rgba(45,212,191,.10)'); bg.addColorStop(1, 'rgba(10,20,35,.55)');
            ctx.fillStyle = bg; ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fill();
            ctx.strokeStyle = 'rgba(45,212,191,.35)'; ctx.lineWidth = 1; ctx.stroke();
            // charge ring: how much of the knowledge is approved
            ctx.strokeStyle = '#2dd4bf'; ctx.lineWidth = 2.5; glow(ctx, '#2dd4bf', 10);
            ctx.beginPath(); ctx.arc(cx, cy, R + 5, -Math.PI / 2, -Math.PI / 2 + TAU * clamp(p.charge, 0, 1)); ctx.stroke();
            ctx.shadowBlur = 0;
            // filaments
            const want = 4 + Math.round(clamp(p.amount, 0, 1) * 7) + (s.kick > 0 ? 4 : 0);
            const fl = s.f[gi] = s.f[gi] || [];
            while (fl.length < want) fl.push({ a: rnd(0, TAU), v: rnd(-.6, .6), seed: rnd(0, 100) });
            if (fl.length > want) fl.length = want;
            let target = null;
            if (m && Math.hypot(m.x - cx, m.y - cy) < R * 2.2) target = Math.atan2(m.y - cy, m.x - cx);
            fl.forEach((f, i) => {
                f.v += rnd(-1.4, 1.4) * dt; f.v *= .97;
                if (target !== null && i < 3) {                     // the hand draws the arc to the glass
                    let d = target - f.a; d = Math.atan2(Math.sin(d), Math.cos(d)); f.v += d * dt * 6;
                }
                f.a += f.v * dt;
                const ex = cx + Math.cos(f.a) * R * .97, ey = cy + Math.sin(f.a) * R * .97;
                ctx.beginPath(); ctx.moveTo(cx, cy);
                for (let k = 1; k <= 10; k++) {
                    const u = k / 10, wob = Math.sin(s.t * 9 + f.seed + k * 1.7) * R * .07 * Math.sin(u * Math.PI);
                    const x = cx + (ex - cx) * u - Math.sin(f.a) * wob, y = cy + (ey - cy) * u + Math.cos(f.a) * wob;
                    ctx.lineTo(x, y);
                }
                const g = ctx.createLinearGradient(cx, cy, ex, ey);
                g.addColorStop(0, '#e6fffb'); g.addColorStop(.5, '#2dd4bf'); g.addColorStop(1, '#c084fc');
                ctx.strokeStyle = g; ctx.lineWidth = 1.3; glow(ctx, '#2dd4bf', 10); ctx.globalAlpha = .9; ctx.stroke();
                ctx.globalAlpha = 1;
                ctx.fillStyle = '#c084fc'; glow(ctx, '#c084fc', 14); ctx.beginPath(); ctx.arc(ex, ey, 2.2, 0, TAU); ctx.fill();
            });
            ctx.shadowBlur = 0;
            const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * .2);
            core.addColorStop(0, '#fff'); core.addColorStop(.4, '#7ff3e1'); core.addColorStop(1, 'rgba(45,212,191,0)');
            ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy, R * .2 * (1 + .08 * Math.sin(s.t * 5)), 0, TAU); ctx.fill();
        }
    }
    globe.burst = s => { s.kick = 1.2; };
    scope.burst = s => { s.kick = 1; };

    const SCENES = { scope, arc, line, globe };

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
        host.addEventListener('pointermove', e => { const r = canvas.getBoundingClientRect(); m.mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; });
        host.addEventListener('pointerleave', () => { m.mouse = null; });
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
