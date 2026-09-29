/* Living substation background for the main dashboard.
 *
 * An elevation view of an outdoor HV substation — gantries, overhead busbar,
 * surge arresters, VTs, disconnectors, CTs, live-tank breakers and power
 * transformers — drawn once to an offscreen canvas, with current flowing
 * through it as glowing particles, occasional flashovers, corona on the
 * insulators, and an arc that jumps to the cursor when it gets close.
 *
 * Decoration only: it never shows a number. The page drives it through
 * window.SubstationBG — setLoad(0..1) (flow density), setAlarm(bool) (one
 * breaker blinks red), surge() (a burst, used when a new decision lands).
 */
(function () {
    'use strict';

    const canvas = document.getElementById('substation-bg');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const C = {
        steel:   'rgba(96,140,186,0.55)',
        steelHi: 'rgba(130,180,225,0.75)',
        porc:    'rgba(176,138,104,0.70)',   // porcelain sheds
        tank:    '#0b1522',
        cond:    'rgba(0,212,255,0.22)',     // idle conductor
        glow:    [0, 212, 255],
        arc:     [170, 225, 255],
        alarm:   [255, 77, 94],
        far:     'rgba(60,95,135,0.35)',
    };

    let W = 0, H = 0, DPR = 1, G = 0, s = 1;       // size, ground line, scale
    let staticLayer = null;
    let paths = [];            // conductor polylines particles run on
    let particles = [];
    let insulatorTops = [];    // corona candidates
    let breakerHeads = [];     // alarm candidates
    let flashPairs = [];       // disconnector gaps that can flash over
    let transformers = [];
    let arcs = [];
    let load = 0.5, alarm = false, surgeUntil = 0, running = true;
    let mouse = { x: -1e4, y: -1e4 };
    let last = performance.now();

    /* ── geometry helpers ─────────────────────────────────────────── */
    function line(c, x1, y1, x2, y2) { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke(); }

    function lattice(c, x, yTop, yBot, wBot, wTop) {
        const l1 = x - wBot / 2, r1 = x + wBot / 2, l2 = x - wTop / 2, r2 = x + wTop / 2;
        line(c, l1, yBot, l2, yTop); line(c, r1, yBot, r2, yTop);
        const n = Math.max(4, Math.round((yBot - yTop) / (14 * s)));
        for (let i = 0; i < n; i++) {
            const t1 = i / n, t2 = (i + 1) / n;
            const ya = yBot - (yBot - yTop) * t1, yb = yBot - (yBot - yTop) * t2;
            const la = l1 + (l2 - l1) * t1, ra = r1 + (r2 - r1) * t1, lb = l1 + (l2 - l1) * t2, rb = r1 + (r2 - r1) * t2;
            line(c, la, ya, rb, yb); line(c, ra, ya, lb, yb);
        }
    }

    function support(c, x, h) {            // steel pedestal, returns its top y
        const top = G - h;
        c.strokeStyle = C.steel; c.lineWidth = 1;
        lattice(c, x, top, G, 10 * s, 6 * s);
        line(c, x - 7 * s, top, x + 7 * s, top);
        return top;
    }

    function insulator(c, x, yBase, h, porcelain) {   // shed stack, returns its top y
        const top = yBase - h;
        c.strokeStyle = C.steel; c.lineWidth = 1.2;
        line(c, x, yBase, x, top);
        c.strokeStyle = porcelain === false ? 'rgba(150,160,170,0.6)' : C.porc;
        c.lineWidth = 1;
        for (let y = yBase - 3 * s; y > top + 2 * s; y -= 4.5 * s) {
            c.beginPath(); c.ellipse(x, y, 4.2 * s, 1.3 * s, 0, 0, Math.PI * 2); c.stroke();
        }
        insulatorTops.push({ x, y: top });
        return top;
    }

    function arrester(c, x) {
        const t = insulator(c, x, support(c, x, 48 * s), 62 * s, false);
        c.strokeStyle = C.steelHi; c.beginPath(); c.ellipse(x, t + 4 * s, 9 * s, 2.5 * s, 0, 0, Math.PI * 2); c.stroke();
        return { x, y: t };
    }

    function vt(c, x) {
        const t = insulator(c, x, support(c, x, 48 * s), 48 * s);
        c.fillStyle = C.tank; c.strokeStyle = C.steelHi;
        c.beginPath(); c.ellipse(x, t - 6 * s, 7 * s, 8 * s, 0, 0, Math.PI * 2); c.fill(); c.stroke();
        return { x, y: t - 14 * s };
    }

    function ct(c, x) {
        const t = insulator(c, x, support(c, x, 48 * s), 50 * s);
        c.fillStyle = C.tank; c.strokeStyle = C.steelHi;
        c.beginPath(); c.roundRect(x - 9 * s, t - 16 * s, 18 * s, 16 * s, 7 * s); c.fill(); c.stroke();
        return { x, y: t - 16 * s };
    }

    function breaker(c, x) {               // live tank: insulator, interrupter head, insulator
        const base = support(c, x, 44 * s);
        const mid = insulator(c, x, base, 38 * s);
        c.fillStyle = C.tank; c.strokeStyle = C.steelHi;
        c.beginPath(); c.roundRect(x - 8 * s, mid - 18 * s, 16 * s, 18 * s, 3 * s); c.fill(); c.stroke();
        c.fillStyle = 'rgba(0,212,255,0.25)'; c.fillRect(x - 3 * s, mid - 13 * s, 6 * s, 8 * s);
        breakerHeads.push({ x, y: mid - 9 * s });
        const top = insulator(c, x, mid - 18 * s, 22 * s);
        return { x, y: top };
    }

    function disconnector(c, x, open) {    // centre-break: two rotating posts, a blade across
        const gap = 26 * s;
        const b1 = support(c, x - gap / 2, 48 * s), b2 = support(c, x + gap / 2, 48 * s);
        c.strokeStyle = C.steel; line(c, x - gap / 2 - 6 * s, b1, x + gap / 2 + 6 * s, b2);
        const t1 = insulator(c, x - gap / 2, b1, 46 * s), t2 = insulator(c, x + gap / 2, b2, 46 * s);
        c.strokeStyle = C.steelHi; c.lineWidth = 2;
        if (open) {
            line(c, x - gap / 2, t1, x - gap / 2 + 9 * s, t1 - 9 * s);
            line(c, x + gap / 2, t2, x + gap / 2 - 9 * s, t2 - 9 * s);
        } else {
            line(c, x - gap / 2, t1, x + gap / 2, t2);
        }
        c.lineWidth = 1;
        flashPairs.push({ a: { x: x - gap / 2, y: t1 }, b: { x: x + gap / 2, y: t2 } });
        return [{ x: x - gap / 2, y: t1 }, { x: x + gap / 2, y: t2 }];
    }

    function transformer(c, x) {           // x = centre of the tank
        const w = 92 * s, h = 58 * s, top = G - 8 * s - h;
        c.fillStyle = C.tank; c.strokeStyle = C.steelHi; c.lineWidth = 1.2;
        c.fillRect(x - w / 2, G - 8 * s, w, 8 * s);                     // plinth
        c.beginPath(); c.rect(x - w / 2 + 6 * s, top, w - 12 * s, h); c.fill(); c.stroke();
        c.strokeStyle = C.steel; c.lineWidth = 1;                       // radiator banks
        for (let i = 0; i < 7; i++) {
            const rx = x + w / 2 - 4 * s + i * 3 * s;
            line(c, rx, top + 8 * s, rx, G - 12 * s);
            const lx = x - w / 2 + 4 * s - i * 3 * s;
            line(c, lx, top + 8 * s, lx, G - 12 * s);
        }
        line(c, x + w / 2 - 4 * s, top + 8 * s, x + w / 2 + 14 * s, top + 8 * s);
        line(c, x - w / 2 + 4 * s, top + 8 * s, x - w / 2 - 14 * s, top + 8 * s);
        c.strokeStyle = C.steelHi;                                      // conservator on its bracket
        line(c, x + w / 2 - 20 * s, top, x + w / 2 - 20 * s, top - 26 * s);
        c.beginPath(); c.roundRect(x + w / 2 - 44 * s, top - 38 * s, 44 * s, 12 * s, 6 * s); c.fill(); c.stroke();
        const bushings = [-24, -8, 8].map(dx => {                        // HV bushings
            const bt = insulator(c, x + dx * s, top, 40 * s);
            return { x: x + dx * s, y: bt };
        });
        [-30, -18].forEach(dx => insulator(c, x + (dx + 44) * s, top, 18 * s));   // MV bushings
        c.strokeStyle = C.steel;
        c.strokeRect(x - 14 * s, top + 16 * s, 28 * s, 20 * s);         // rating plate
        transformers.push({ x, y: top + h / 2, w, h, glow: 0 });
        return bushings;
    }

    function farTower(c, x, base, h) {     // distant transmission tower silhouette
        c.strokeStyle = C.far; c.lineWidth = 1;
        lattice(c, x, base - h, base, h * 0.28, h * 0.06);
        [0.62, 0.78, 0.92].forEach((f, i) => {
            const y = base - h * f, arm = h * (0.24 - i * 0.05);
            line(c, x - arm, y, x + arm, y);
        });
    }

    function sag(p1, p2, depth, n = 14) {  // catenary-ish polyline
        const pts = [];
        for (let i = 0; i <= n; i++) {
            const t = i / n;
            pts.push({ x: p1.x + (p2.x - p1.x) * t, y: p1.y + (p2.y - p1.y) * t + Math.sin(Math.PI * t) * depth });
        }
        return pts;
    }

    function strokePts(c, pts) {
        c.beginPath(); c.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) c.lineTo(pts[i].x, pts[i].y);
        c.stroke();
    }

    function addPath(pts) {
        const segs = []; let len = 0;
        for (let i = 1; i < pts.length; i++) {
            const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
            segs.push(d); len += d;
        }
        paths.push({ pts, segs, len });
    }

    function pointAt(p, dist) {
        let d = dist;
        for (let i = 0; i < p.segs.length; i++) {
            if (d <= p.segs[i]) {
                const t = d / p.segs[i], a = p.pts[i], b = p.pts[i + 1];
                return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
            }
            d -= p.segs[i];
        }
        return p.pts[p.pts.length - 1];
    }

    /* ── scene ────────────────────────────────────────────────────── */
    function build() {
        const r = canvas.getBoundingClientRect();
        W = r.width; H = r.height;
        DPR = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
        s = Math.max(0.7, Math.min(1.35, H / 760));
        G = H - 10 * s;
        paths = []; insulatorTops = []; breakerHeads = []; flashPairs = []; transformers = [];

        staticLayer = document.createElement('canvas');
        staticLayer.width = canvas.width; staticLayer.height = canvas.height;
        const c = staticLayer.getContext('2d');
        c.setTransform(DPR, 0, 0, DPR, 0, 0);

        // night sky with a faint glow on the horizon
        const sky = c.createLinearGradient(0, 0, 0, H);
        sky.addColorStop(0, '#060a11'); sky.addColorStop(0.6, '#070d17'); sky.addColorStop(1, '#0a1624');
        c.fillStyle = sky; c.fillRect(0, 0, W, H);

        // distant line of transmission towers
        const farBase = G - 150 * s, farH = 120 * s;
        const towers = [];
        for (let x = -60; x < W + 120; x += 240 * s) { farTower(c, x, farBase, farH); towers.push(x); }
        c.strokeStyle = C.far;
        for (let i = 1; i < towers.length; i++) {
            [0.62, 0.78, 0.92].forEach(f => strokePts(c, sag({ x: towers[i - 1], y: farBase - farH * f }, { x: towers[i], y: farBase - farH * f }, 10 * s)));
        }

        // overhead tubular busbar across the whole yard, on post insulators
        const busY = G - 196 * s;
        const B = 380 * s;
        const nBays = Math.ceil(W / B) + 1;
        for (let i = 0; i <= nBays; i++) {
            const x = i * B + 8 * s;
            insulator(c, x, support(c, x, 128 * s), 60 * s);
        }
        c.strokeStyle = C.steelHi; c.lineWidth = 2.4;
        line(c, 0, busY, W, busY);
        c.lineWidth = 1;
        addPath([{ x: W + 20, y: busY }, { x: -20, y: busY }]);
        addPath([{ x: -20, y: busY - 3 * s }, { x: W + 20, y: busY - 3 * s }]);

        // bays: line feeders bring power in, transformer bays take it out
        const pattern = ['line', 'trafo', 'line', 'line', 'trafo'];
        const gantryTops = [];
        for (let i = 0; i < nBays; i++) {
            const x0 = i * B, kind = pattern[i % pattern.length];
            let chain;
            if (kind === 'line') {
                const gx = x0 + 34 * s, gTop = G - 262 * s, beam = G - 248 * s;
                c.strokeStyle = C.steel;
                lattice(c, gx, gTop, G, 24 * s, 12 * s);
                line(c, gx - 30 * s, beam, gx + 44 * s, beam);
                gantryTops.push({ x: gx, y: gTop });
                const strEnd = { x: gx + 34 * s, y: beam + 22 * s };      // strain insulator string
                c.strokeStyle = C.porc; line(c, gx + 34 * s, beam, strEnd.x, strEnd.y);
                const a = arrester(c, x0 + 82 * s);
                const v = vt(c, x0 + 118 * s);
                const d1 = disconnector(c, x0 + 170 * s, false);
                const t = ct(c, x0 + 222 * s);
                const b = breaker(c, x0 + 262 * s);
                const d2 = disconnector(c, x0 + 314 * s, false);
                const riser = { x: x0 + 348 * s, y: busY };
                chain = [strEnd, a, v, d1[0], d1[1], t, b, d2[0], d2[1], riser];
                // incoming overhead line from the far towers
                const inPts = sag({ x: gx - 140 * s, y: farBase - farH * 0.78 }, strEnd, 26 * s);
                c.strokeStyle = C.cond; strokePts(c, inPts); addPath(inPts);
            } else {
                const drop = { x: x0 + 30 * s, y: busY };
                const d = disconnector(c, x0 + 74 * s, i % 2 === 0);
                const b = breaker(c, x0 + 124 * s);
                const t = ct(c, x0 + 162 * s);
                const bush = transformer(c, x0 + 262 * s);
                chain = [drop, d[0], d[1], b, t, bush[0]];
            }
            // jumpers between equipment tops, each with a little sag
            const pts = [];
            for (let k = 1; k < chain.length; k++) {
                const seg = sag(chain[k - 1], chain[k], Math.min(14 * s, Math.abs(chain[k].x - chain[k - 1].x) * 0.18), 8);
                pts.push(...(k === 1 ? seg : seg.slice(1)));
            }
            c.strokeStyle = C.cond; c.lineWidth = 1.4; strokePts(c, pts); c.lineWidth = 1;
            addPath(pts);
        }
        // earth / shield wire across the gantry tops
        c.strokeStyle = 'rgba(96,140,186,0.3)';
        for (let i = 1; i < gantryTops.length; i++) strokePts(c, sag(gantryTops[i - 1], gantryTops[i], 22 * s));

        // perimeter fence and gravel line
        c.strokeStyle = 'rgba(96,140,186,0.28)';
        line(c, 0, G, W, G);
        for (let x = 0; x < W; x += 22 * s) line(c, x, G, x, G - 26 * s);
        line(c, 0, G - 26 * s, W, G - 26 * s);
        c.strokeStyle = 'rgba(96,140,186,0.12)';
        for (let x = 0; x < W; x += 11 * s) { line(c, x, G - 26 * s, x + 11 * s, G); line(c, x + 11 * s, G - 26 * s, x, G); }

        // fade the yard into the sky so the page content above stays readable
        c.globalCompositeOperation = 'destination-out';
        const fade = c.createLinearGradient(0, 0, 0, H);
        fade.addColorStop(0, 'rgba(0,0,0,1)'); fade.addColorStop(0.28, 'rgba(0,0,0,0.85)'); fade.addColorStop(0.62, 'rgba(0,0,0,0.25)'); fade.addColorStop(1, 'rgba(0,0,0,0)');
        c.fillStyle = fade; c.fillRect(0, 0, W, H);
        c.globalCompositeOperation = 'destination-over';
        c.fillStyle = '#060a11'; c.fillRect(0, 0, W, H);
        c.globalCompositeOperation = 'source-over';

        seedParticles();
    }

    const sprite = (() => {                 // one pre-rendered glow dot, blitted per particle
        const cv = document.createElement('canvas'); cv.width = cv.height = 32;
        const g = cv.getContext('2d'), gr = g.createRadialGradient(16, 16, 0, 16, 16, 16);
        gr.addColorStop(0, 'rgba(230,250,255,1)'); gr.addColorStop(0.25, 'rgba(0,212,255,0.9)'); gr.addColorStop(1, 'rgba(0,212,255,0)');
        g.fillStyle = gr; g.fillRect(0, 0, 32, 32);
        return cv;
    })();

    function seedParticles() {
        particles = [];
        paths.forEach((p, pi) => {
            const n = Math.max(2, Math.round(p.len / (70 * s)));
            for (let i = 0; i < n; i++) particles.push({ pi, d: Math.random() * p.len, v: 0.6 + Math.random() * 0.8, size: 0.6 + Math.random() * 0.8 });
        });
    }

    /* ── effects ──────────────────────────────────────────────────── */
    function bolt(a, b, rough) {            // midpoint-displacement lightning
        let pts = [a, b];
        let off = rough;
        for (let k = 0; k < 5; k++) {
            const next = [pts[0]];
            for (let i = 1; i < pts.length; i++) {
                const p = pts[i - 1], q = pts[i];
                const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2, nx = -(q.y - p.y), ny = q.x - p.x, nl = Math.hypot(nx, ny) || 1;
                const o = (Math.random() - 0.5) * off;
                next.push({ x: mx + nx / nl * o, y: my + ny / nl * o }, q);
            }
            pts = next; off /= 2;
        }
        return pts;
    }

    function drawBolt(pts, alpha, rgb) {
        ctx.lineJoin = 'round';
        [[7, 0.12], [3, 0.35], [1.2, 1]].forEach(([w, a]) => {
            ctx.strokeStyle = a === 1 ? `rgba(240,250,255,${alpha})` : `rgba(${rgb},${alpha * a})`;
            ctx.lineWidth = w;
            ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
            ctx.stroke();
        });
    }

    function flashover(big) {
        if (!flashPairs.length) return;
        const f = flashPairs[Math.floor(Math.random() * flashPairs.length)];
        arcs.push({ a: f.a, b: f.b, t0: performance.now(), life: big ? 520 : 320, rough: 22 * s, big });
    }

    function nearestConductor(x, y) {
        let best = null, bd = 1e9;
        for (const p of paths) {
            for (const q of p.pts) {
                const d = (q.x - x) ** 2 + (q.y - y) ** 2;
                if (d < bd) { bd = d; best = q; }
            }
        }
        return { p: best, d: Math.sqrt(bd) };
    }

    /* ── frame ────────────────────────────────────────────────────── */
    let nextFlash = performance.now() + 3500;

    function frame(now) {
        if (!running) return;
        const dt = Math.min(0.05, (now - last) / 1000); last = now;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(staticLayer, 0, 0);
        ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
        ctx.globalCompositeOperation = 'lighter';

        const surging = now < surgeUntil;
        const speed = (40 + 110 * load) * s * (surging ? 3 : 1);
        const shown = surging ? 1 : 0.35 + 0.65 * load;       // quieter yard = fewer visible charges

        // particles along every conductor
        for (let i = 0; i < particles.length; i++) {
            const q = particles[i], p = paths[q.pi];
            const nd = q.d + speed * q.v * dt;
            if (nd >= p.len) {                 // a charge reached the end: if that is a bushing, the transformer hums
                const end = p.pts[p.pts.length - 1];
                for (const t of transformers) if (Math.abs(t.x - end.x) < t.w) t.glow = Math.min(1, t.glow + 0.35);
            }
            q.d = nd % p.len;
            if (i % 100 > shown * 100) continue;
            const pt = pointAt(p, q.d), r = (surging ? 14 : 9) * q.size * s;
            ctx.globalAlpha = surging ? 1 : 0.85;
            ctx.drawImage(sprite, pt.x - r, pt.y - r, r * 2, r * 2);
        }
        ctx.globalAlpha = 1;

        // transformer hum: a slow 100 Hz-inspired breathing glow, kicked by arriving current
        for (const t of transformers) {
            t.glow = Math.max(0, t.glow - dt * 0.8);
            const breath = 0.18 + 0.1 * Math.sin(now / 420 + t.x) + t.glow * 0.4;
            const g = ctx.createRadialGradient(t.x, t.y, 0, t.x, t.y, t.w * 0.9);
            g.addColorStop(0, `rgba(0,212,255,${breath * 0.35})`); g.addColorStop(1, 'rgba(0,212,255,0)');
            ctx.fillStyle = g; ctx.fillRect(t.x - t.w, t.y - t.w, t.w * 2, t.w * 2);
        }

        // corona: a few insulator tops shimmer violet-blue
        for (let i = 0; i < 3; i++) {
            const c = insulatorTops[(Math.floor(now / 900) * 7 + i * 13) % Math.max(1, insulatorTops.length)];
            if (!c) break;
            const a = 0.25 + 0.25 * Math.sin(now / 60 + i * 2);
            const g = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, 10 * s);
            g.addColorStop(0, `rgba(170,150,255,${a})`); g.addColorStop(1, 'rgba(170,150,255,0)');
            ctx.fillStyle = g; ctx.fillRect(c.x - 10 * s, c.y - 10 * s, 20 * s, 20 * s);
        }

        // alarm: a breaker head blinks red while approvals wait
        if (alarm && breakerHeads.length && Math.floor(now / 500) % 2 === 0) {
            const b = breakerHeads[0];
            const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, 26 * s);
            g.addColorStop(0, `rgba(${C.alarm},0.9)`); g.addColorStop(1, `rgba(${C.alarm},0)`);
            ctx.fillStyle = g; ctx.fillRect(b.x - 26 * s, b.y - 26 * s, 52 * s, 52 * s);
        }

        // flashovers across disconnector gaps, a few seconds apart
        if (now > nextFlash) { flashover(false); nextFlash = now + 3500 + Math.random() * 6000; }
        arcs = arcs.filter(a => now - a.t0 < a.life);
        for (const a of arcs) {
            const k = 1 - (now - a.t0) / a.life;
            drawBolt(bolt(a.a, a.b, a.rough), k, C.arc.join(','));
            if (a.big && k > 0.7) {                    // the whole yard lights up for an instant
                ctx.fillStyle = `rgba(120,200,255,${(k - 0.7) * 0.25})`; ctx.fillRect(0, 0, W, H);
            }
        }

        // the cursor draws an arc from the nearest live conductor
        if (mouse.y > H * 0.45) {
            const n = nearestConductor(mouse.x, mouse.y);
            if (n.p && n.d < 90 * s && Math.random() < 0.75) drawBolt(bolt(n.p, mouse, 16 * s), 0.9, C.arc.join(','));
        }

        ctx.globalCompositeOperation = 'source-over';
        requestAnimationFrame(frame);
    }

    function drawStill() {                  // reduced motion / switched off: the yard, no current
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(staticLayer, 0, 0);
    }

    /* ── wiring ───────────────────────────────────────────────────── */
    let resizeT = 0;
    window.addEventListener('resize', () => {
        clearTimeout(resizeT);
        resizeT = setTimeout(() => { build(); if (REDUCED || !running) drawStill(); }, 150);
    });
    window.addEventListener('pointermove', e => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });
    document.addEventListener('pointerleave', () => { mouse.x = mouse.y = -1e4; });

    build();
    if (REDUCED) { running = false; drawStill(); }
    else requestAnimationFrame(frame);

    window.SubstationBG = {
        setLoad(v) { load = Math.max(0, Math.min(1, +v || 0)); },
        setAlarm(on) { alarm = !!on; },
        surge() {
            if (!running) return;
            surgeUntil = performance.now() + 1800;
            flashover(true);
            setTimeout(() => flashover(true), 260);
        },
        setRunning(on) {
            if (REDUCED) return;
            const was = running; running = !!on;
            if (running && !was) { last = performance.now(); requestAnimationFrame(frame); }
            if (!running) drawStill();
        },
    };
})();
