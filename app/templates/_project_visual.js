/* The project's visual risk report. Data: project_visual.build (JSON in
   #visual-data). Every value drawn here is a value or a row count from that
   payload — this script computes positions, never numbers. Text goes into the
   DOM with textContent only. Motion is optional: without it (or with reduced
   motion) every scene is drawn in its final state. */
(function () {
const D = JSON.parse(document.getElementById('visual-data').textContent);
const M = window.Motion;
const GO = !!M && !matchMedia('(prefers-reduced-motion: reduce)').matches;
const NS = 'http://www.w3.org/2000/svg';
const EASE = [0.16, 1, 0.3, 1];
const LEVEL = { high: ['גבוה', 'var(--high)', '#ef6b6b'], medium: ['בינוני', 'var(--med)', '#e0a028'], info: ['מידע', 'var(--info)', '#3987e5'] };
const TRIAGE = { escalate: '#ef4444', week: '#f97316', follow: '#f59e0b', fyi: '#10b981' };
const TONE = (D.level && TRIAGE[D.level.key]) || '#8b5cf6';
const STAGE_HUES = ['#3987e5', '#8b5cf6', '#199e70', '#d95926', '#e0a028', '#2bb3c0', '#c05bd9'];

// ── helpers ───────────────────────────────────────────────────────────────
function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v === null || v === undefined) continue;
        if (k === 'class') e.className = v; else if (k === 'style') e.style.cssText = v; else e.setAttribute(k, v);
    }
    for (const c of kids) if (c !== null && c !== undefined) e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
    return e;
}
function sv(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
}
const fmtD = iso => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '—';
const fmtM = iso => iso ? `${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '';
const ms = iso => Date.parse(iso + 'T00:00:00Z');
const signed = (v, dec) => (v > 0 ? '+' : '') + v.toFixed(dec);
const ltr = s => '\u2066' + s + '\u2069';   // LTR isolate: keeps "+6.0" whole inside Hebrew text
const anim = (els, kf, opt) => (GO && els && (els.length === undefined || els.length)) ? M.animate(els, kf, opt) : null;
const stagger = (s, o) => GO ? M.stagger(s, o) : 0;
function tween(node, to, dec, delay, sign) {
    const show = v => { node.textContent = sign ? signed(v, dec) : v.toFixed(dec); };
    if (to === null || to === undefined) { node.textContent = '—'; return; }
    if (!GO) { show(to); return; }
    show(0);
    M.animate(0, to, { duration: 1.5, delay: delay || 0, ease: EASE, onUpdate: show });
}
function tilt(card) {
    if (!GO) return;
    card.addEventListener('pointermove', e => {
        const r = card.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        M.animate(card, { rotateY: x * 12, rotateX: -y * 12, y: -4 }, { duration: .25 });
    });
    card.addEventListener('pointerleave', () => M.animate(card, { rotateY: 0, rotateX: 0, y: 0 }, { type: 'spring', stiffness: 300, damping: 15 }));
}
function arc(cx, cy, r, a0, a1) {
    const p = a => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}
function drawIn(paths, delay, duration) {
    paths.forEach(p => { p.setAttribute('pathLength', '1'); p.style.strokeDasharray = '1 1'; });
    if (GO) M.animate(paths, { strokeDashoffset: [1, 0] }, { duration: duration || 1.2, delay: stagger(.14, { startDelay: delay || 0 }), ease: 'easeInOut' });
}
function glowFilter(svg, id, dev) {
    const f = sv('filter', { id, x: '-50%', y: '-50%', width: '200%', height: '200%' });
    f.appendChild(sv('feGaussianBlur', { stdDeviation: dev || 4, result: 'b' }));
    const m = sv('feMerge'); m.appendChild(sv('feMergeNode', { in: 'b' })); m.appendChild(sv('feMergeNode', { in: 'SourceGraphic' }));
    f.appendChild(m);
    let defs = svg.querySelector('defs'); if (!defs) { defs = sv('defs'); svg.appendChild(defs); }
    defs.appendChild(f);
}
function sceneHead(box, eyebrow, title, lead) {
    const head = el('div', { class: 'head' }, el('div', { class: 'eyebrow' }, eyebrow), el('h2', {}, title), lead ? el('p', { class: 'lead' }, lead) : null);
    box.appendChild(head);
    anim(head.children, { opacity: [0, 1], y: [24, 0] }, { delay: stagger(.08), duration: .7, ease: EASE });
}

// ── scene 1: the reactor (triage + every flag as an arc) ─────────────────
function sceneHero(box) {
    const grid = el('div', { class: 'hero' });
    const svg = sv('svg', { viewBox: '0 0 400 400', class: 'reactor', role: 'img',
        'aria-label': `דרגת טיפול ${D.level ? D.level.label : 'לא נקבעה'} · ${D.flags.length} סימני סיכון` });
    glowFilter(svg, 'gl', 6);
    const defs = svg.querySelector('defs');
    const rg = sv('radialGradient', { id: 'core' });
    rg.appendChild(sv('stop', { offset: '0%', 'stop-color': TONE, 'stop-opacity': '.95' }));
    rg.appendChild(sv('stop', { offset: '60%', 'stop-color': TONE, 'stop-opacity': '.25' }));
    rg.appendChild(sv('stop', { offset: '100%', 'stop-color': TONE, 'stop-opacity': '0' }));
    defs.appendChild(rg);

    const ticks = sv('g', { style: 'transform-box: fill-box; transform-origin: center' });
    for (let i = 0; i < 72; i++) {
        const a = i / 72 * Math.PI * 2, r0 = i % 6 ? 178 : 170;
        ticks.appendChild(sv('line', { x1: 200 + r0 * Math.sin(a), y1: 200 - r0 * Math.cos(a), x2: 200 + 186 * Math.sin(a), y2: 200 - 186 * Math.cos(a),
            stroke: '#2a4a72', 'stroke-width': i % 6 ? 1 : 2 }));
    }
    svg.appendChild(ticks);
    svg.appendChild(sv('circle', { cx: 200, cy: 200, r: 150, fill: 'none', stroke: '#13233a', 'stroke-width': 22 }));

    const arcs = [];
    const n = D.flags.length, gap = n > 1 ? 0.06 : 0;
    if (!n) arcs.push(sv('path', { d: arc(200, 200, 150, 0.001, Math.PI * 2 - 0.001), stroke: '#10b981', 'stroke-width': 18, fill: 'none', 'stroke-linecap': 'round', filter: 'url(#gl)' }));
    D.flags.forEach((f, i) => {
        const a0 = i / n * Math.PI * 2 + gap / 2, a1 = (i + 1) / n * Math.PI * 2 - gap / 2;
        const p = sv('path', { d: arc(200, 200, 150, a0, a1), stroke: LEVEL[f.level][2], 'stroke-width': 18, fill: 'none', 'stroke-linecap': 'round', filter: 'url(#gl)' });
        p.appendChild(sv('title', {}, `${LEVEL[f.level][0]}: ${f.text}`));
        arcs.push(p);
    });
    arcs.forEach(p => svg.appendChild(p));

    const orbit = sv('g', { style: 'transform-box: view-box; transform-origin: 200px 200px' });
    orbit.appendChild(sv('circle', { cx: 200, cy: 50, r: 4, fill: '#fff', filter: 'url(#gl)' }));
    svg.appendChild(orbit);

    const core = sv('circle', { cx: 200, cy: 200, r: 112, fill: 'url(#core)', style: 'transform-box: fill-box; transform-origin: center' });
    svg.appendChild(core);
    svg.appendChild(sv('circle', { cx: 200, cy: 200, r: 84, fill: '#070d18', stroke: TONE, 'stroke-width': 1.5, 'stroke-opacity': .6 }));
    svg.appendChild(sv('text', { x: 200, y: 192, 'text-anchor': 'middle', 'font-size': 34 }, D.level ? D.level.icon : '◎'));
    svg.appendChild(sv('text', { x: 200, y: 226, 'text-anchor': 'middle', 'font-size': 17, 'font-weight': 700, fill: '#fff' }, D.level ? D.level.label : 'ללא דרגה'));
    svg.appendChild(sv('text', { x: 200, y: 248, 'text-anchor': 'middle', 'font-size': 12, fill: '#9fb0c6' }, D.level ? `${D.level.owner} · ${D.level.when}` : ''));
    grid.appendChild(svg);

    const info = el('div', {});
    info.appendChild(el('div', { class: 'eyebrow' }, 'דוח סיכונים ויזואלי'));
    info.appendChild(el('h1', {}, D.name));
    const chips = el('div', { class: 'chips' }, el('span', { class: 'mono' }, D.identifier), el('span', {}, `מנה"פ: ${D.manager || 'טרם הוקצה'}`));
    if (D.stage) chips.appendChild(el('span', {}, `שלב: ${D.stage}`));
    chips.appendChild(el('span', {}, `נכון לדוח ${fmtD(D.as_of)}`));
    if (!D.is_active) chips.appendChild(el('span', {}, 'לא פעיל בקובץ העדכני'));
    info.appendChild(chips);
    const counts = el('div', { class: 'counts' });
    const nums = [];
    for (const lv of ['high', 'medium', 'info']) {
        const b = el('b', {}, '0');
        const c = el('div', { class: 'count glass', style: `color: ${LEVEL[lv][1]}` }, b, el('span', {}, `סיכון ${LEVEL[lv][0]}`));
        counts.appendChild(c); nums.push([b, D.counts[lv]]); tilt(c);
    }
    info.appendChild(counts);
    if (D.level && D.level.reasons.length) {
        const rs = el('div', { class: 'reasons' });
        D.level.reasons.forEach(t => rs.appendChild(el('span', {}, t)));
        info.appendChild(el('div', { class: 'muted', style: 'margin-top:1rem' }, 'הדרגה נקבעה בכללים:'));
        info.appendChild(rs);
    }
    grid.appendChild(info);
    box.appendChild(grid);

    nums.forEach(([b, v], i) => tween(b, v, 0, .5 + i * .15));
    drawIn(arcs, .4, .9);
    if (GO) {
        M.animate(ticks, { rotate: 360 }, { duration: 90, repeat: Infinity, ease: 'linear' });
        M.animate(orbit, { rotate: 360 }, { duration: 7, repeat: Infinity, ease: 'linear' });
        const beat = { escalate: .9, week: 1.4, follow: 2.2, fyi: 3.2 }[D.level && D.level.key] || 2.6;
        M.animate(core, { scale: [1, 1.12, 1], opacity: [.75, 1, .75] }, { duration: beat, repeat: Infinity, ease: 'easeInOut' });
        M.animate(svg, { scale: [.6, 1], opacity: [0, 1] }, { type: 'spring', stiffness: 120, damping: 16 });
        M.animate(info.children, { opacity: [0, 1], x: [40, 0] }, { delay: stagger(.1, { startDelay: .2 }), duration: .8, ease: EASE });
    }
}

// ── scene 1b: the digital twin — the 3D substation in the project's stage ──
function sceneTwin(box) {
    sceneHead(box, 'התאום הדיגיטלי', 'איפה התחנה עומדת',
        'מודל תלת-ממד של תחנת משנה. מה שבנוי עומד, מה שבביצוע נבנה מול העיניים, ומה שעוד לא התחיל מופיע כשרטוט. טבעת האור על הקרקע היא סימני הסיכון, קשת לכל סימן.');
    const stage = el('div', { class: 'twin glass' });
    box.appendChild(stage);
    box.appendChild(el('div', { class: 'muted', style: 'margin-top:.5rem' },
        'מודל להמחשה בלבד (substation-bimfra, Sketchfab), לא התחנה של הפרויקט. מצב הבנייה נגזר מהשלב בקובץ בלבד.'));
    anim(stage, { opacity: [0, 1], scale: [.96, 1] }, { duration: .9, ease: EASE });
    const opts = { twin: D.twin, flags: D.flags, tone: TONE };
    const go = () => window.mountTwin(stage, opts);
    if (window.mountTwin) go(); else addEventListener('twin-ready', go, { once: true });
}

// ── scene 2: instruments ─────────────────────────────────────────────────
function sceneInstruments(box) {
    sceneHead(box, 'מכשירי מדידה', 'המספרים שמספרים את הסיפור', 'כל מכשיר הוא ערך אחד מנתוני הפרויקט. היכן שיש — מול החציון של הפרויקטים הפעילים באותו שלב.');
    const k = D.kpis, pe = D.peers || {};
    const fcMoves = D.events.filter(e => e.kind === 'forecast').length;
    const items = [
        { label: 'פער מול תכנית הפיתוח', v: k.slip_months, dec: 1, unit: 'ח׳', sign: true, c: '#199e70' },
        { label: 'יעד החשמול זז מאז התאריך הראשון', v: k.forecast_moved_months, dec: 1, unit: 'ח׳', sign: true, c: '#3987e5',
          peer: pe.median_moved_months, peerN: pe.n },
        { label: 'תכנית הפיתוח (הבסיס) זזה', v: k.baseline_moved_months, dec: 1, unit: 'ח׳', sign: true, c: '#d95926' },
        { label: `${D.weeks_lower_bound ? 'לפחות ' : ''}זמן בשלב "${D.stage || '—'}"`, v: D.weeks_in_stage, dec: 0, unit: 'שב׳', c: '#8b5cf6',
          peer: pe.median_weeks, peerN: pe.n },
        { label: 'דחיות של יעד החשמול', v: fcMoves, dec: 0, unit: 'פעמים', c: '#ef6b6b' },
        { label: `דיווחים זהים לשבוע הקודם (מתוך ${D.weekly_n})`, v: D.repeats, dec: 0, unit: 'שב׳', c: '#64748b' },
    ];
    const tiles = el('div', { class: 'tiles' });
    const bars = [];
    items.forEach((it, i) => {
        const v = el('span', {}, '—');
        const t = el('div', { class: 'tile glass' }, el('div', { class: 'glow', style: `background:${it.c}` }), el('div', { class: 'k' }, it.label),
            el('div', { class: 'v', style: `color:${it.c}` }, v, el('small', {}, it.unit)));
        if (it.peer !== undefined && it.peer !== null && it.v !== null && it.v !== undefined) {
            const mx = Math.max(Math.abs(it.v), Math.abs(it.peer), 0.0001);
            const a = el('i', { style: `background:${it.c}; width:${Math.abs(it.v) / mx * 100}%` });
            const b = el('i', { style: `background:#475569; width:${Math.abs(it.peer) / mx * 100}%` });
            t.appendChild(el('div', { class: 'cmp' },
                el('div', {}, el('span', {}, 'הפרויקט'), el('span', {}, a), el('em', {}, it.dec ? it.v.toFixed(it.dec) : it.v)),
                el('div', {}, el('span', {}, `חציון (${it.peerN})`), el('span', {}, b), el('em', {}, it.dec ? Number(it.peer).toFixed(it.dec) : it.peer))));
            bars.push(a, b);
        }
        tiles.appendChild(t); tilt(t);
        tween(v, it.v, it.dec, .3 + i * .1, it.sign);
    });
    box.appendChild(tiles);
    anim(tiles.children, { opacity: [0, 1], y: [50, 0], scale: [.92, 1] }, { delay: stagger(.08, { startDelay: .15 }), type: 'spring', stiffness: 200, damping: 20 });
    anim(bars, { scaleX: [0, 1] }, { delay: stagger(.1, { startDelay: .9 }), duration: 1, ease: EASE });
}

// ── scene 3: the three targets on one time axis ─────────────────────────
function sceneTargets(box) {
    const T = D.targets;
    sceneHead(box, 'שלושת היעדים', 'תכנית · מאושר · מסתמן', 'שלושת היעדים על ציר זמן אחד, מול תאריך הדוח.');
    const marks = [
        { key: 'dev', label: 'תכנית פיתוח', c: '#d95926', y: 52 },
        { key: 'appr', label: 'מאושר ע״י מנהל האגף', c: '#a78bfa', y: 92 },
        { key: 'fc', label: T.fc_approx ? 'מסתמן (משוער)' : 'מסתמן', c: '#3987e5', y: 132 },
    ].filter(m => T[m.key]);
    const all = marks.map(m => ms(T[m.key])).concat(D.as_of ? [ms(D.as_of)] : []);
    const card = el('div', { class: 'chart glass' });
    const W = 1000, H = 300, L = 40, R = 40, AX = 190;
    const lo = Math.min(...all), hi = Math.max(...all), pad = Math.max((hi - lo) * .1, 45 * 864e5);
    const x0 = lo - pad, x1 = hi + pad, X = t => L + (t - x0) / (x1 - x0) * (W - L - R);
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'שלושת היעדים על ציר זמן' });
    glowFilter(svg, 'tg', 3);
    const axis = sv('path', { d: `M${L},${AX} H${W - R}`, stroke: '#2a4a72', 'stroke-width': 2 });
    svg.appendChild(axis);
    const tickG = sv('g', {});
    const d0 = new Date(x0); let y = d0.getUTCFullYear(), m = d0.getUTCMonth() < 6 ? 6 : 12;
    for (let i = 0; i < 40; i++) {
        const t = Date.UTC(y + Math.floor(m / 12), m % 12, 1); if (t > x1) break;
        if (t >= x0) {
            const x = X(t), iso = new Date(t).toISOString().slice(0, 10);
            tickG.appendChild(sv('line', { x1: x, y1: AX - 5, x2: x, y2: AX + 5, stroke: '#2a4a72' }));
            tickG.appendChild(sv('text', { x, y: AX + 22, 'text-anchor': 'middle', 'font-size': 12, fill: '#64748b' }, fmtM(iso)));
        }
        m += 6;
    }
    svg.appendChild(tickG);
    if (D.as_of) {
        const x = X(ms(D.as_of));
        const now = sv('g', {});
        now.appendChild(sv('line', { x1: x, y1: 24, x2: x, y2: AX + 34, stroke: '#e8eef7', 'stroke-dasharray': '3 4', 'stroke-opacity': .6 }));
        now.appendChild(sv('text', { x, y: 16, 'text-anchor': 'middle', 'font-size': 12, fill: '#e8eef7' }, `הדוח ${fmtD(D.as_of)}`));
        svg.appendChild(now);
        anim(now, { opacity: [0, 1] }, { delay: .3, duration: .6 });
    }
    const pins = [];
    marks.forEach(mk => {
        const x = X(ms(T[mk.key]));
        const g = sv('g', { style: 'transform-box: fill-box; transform-origin: bottom' });
        g.appendChild(sv('line', { x1: x, y1: mk.y + 8, x2: x, y2: AX, stroke: mk.c, 'stroke-width': 2 }));
        g.appendChild(sv('circle', { cx: x, cy: AX, r: 8, fill: mk.c, filter: 'url(#tg)' }));
        const anchor = x > W * .8 ? 'end' : x < W * .2 ? 'start' : 'middle';
        g.appendChild(sv('text', { x, y: mk.y - 8, 'text-anchor': anchor, 'font-size': 14, 'font-weight': 700, fill: mk.c }, mk.label));
        g.appendChild(sv('text', { x, y: mk.y + 6, 'text-anchor': anchor, 'font-size': 12, fill: '#e8eef7', class: 'mono' }, fmtD(T[mk.key])));
        svg.appendChild(g); pins.push(g);
    });
    const brackets = [];
    const bracket = (a, b, yy, c, label) => {
        if (!T[a] || !T[b] || label === null) return;
        const xa = X(ms(T[a])), xb = X(ms(T[b]));
        const g = sv('g', { style: `transform-box: fill-box; transform-origin: ${xa < xb ? 'left' : 'right'}` });
        g.appendChild(sv('path', { d: `M${xa},${yy - 7} V${yy} H${xb} V${yy - 7}`, fill: 'none', stroke: c, 'stroke-width': 1.6 }));
        g.appendChild(sv('text', { x: (xa + xb) / 2, y: yy + 18, 'text-anchor': 'middle', 'font-size': 13, fill: c, 'font-weight': 600 }, label));
        svg.appendChild(g); brackets.push(g);
    };
    const k = D.kpis;
    bracket('dev', 'fc', 240, '#199e70', k.slip_months === null || k.slip_months === undefined ? null : `פער מול התכנית: ${ltr(signed(k.slip_months, 1))} ח׳`);
    bracket('appr', 'fc', 278, T.over_approved ? '#ef6b6b' : '#a78bfa',
        T.appr_gap_days === null || T.appr_gap_days === undefined ? null : `מול היעד המאושר: ${ltr(signed(T.appr_gap_days, 0))} ימים`);
    card.appendChild(svg);
    if (!T.fc && T.fc_label) card.appendChild(el('div', { class: 'readout' }, `יעד חשמול מסתמן: ${T.fc_label}`));
    box.appendChild(card);
    drawIn([axis], .1, .9);
    anim(tickG, { opacity: [0, 1] }, { delay: .6, duration: .6 });
    anim(pins, { y: [-90, 0], opacity: [0, 1] }, { delay: stagger(.22, { startDelay: .5 }), type: 'spring', stiffness: 220, damping: 13 });
    anim(brackets, { scaleX: [0, 1], opacity: [0, 1] }, { delay: stagger(.3, { startDelay: 1.3 }), duration: .9, ease: EASE });
}

// ── scene 4: the stage journey ──────────────────────────────────────────
function sceneJourney(box) {
    sceneHead(box, 'מסע השלבים', 'כמה זמן בכל שלב', 'כל רצועה היא שלב, ברוחב לפי מספר השבועות שבין הדוחות. השלב הנוכחי פועם.');
    const card = el('div', { class: 'chart glass' });
    const hue = {}; let hi = 0;
    D.journey.forEach(s => { if (!(s.stage in hue)) hue[s.stage] = STAGE_HUES[hi++ % STAGE_HUES.length]; });
    const rib = el('div', { style: 'display:flex; direction:ltr; gap:4px; height:86px; align-items:stretch' });
    const segs = D.journey.map(s => {
        const seg = el('div', {
            title: `${s.stage} · ${fmtD(s.from)} ← ${fmtD(s.to)} · ${s.weeks} שבועות`,
            style: `flex:${Math.max(s.weeks, 1)} 1 0; min-width:10px; border-radius:10px; position:relative; overflow:hidden; transform-origin:left;
                    background:linear-gradient(160deg, ${hue[s.stage]}, ${hue[s.stage]}55); box-shadow:0 0 22px ${hue[s.stage]}44; direction:rtl;
                    padding:.5rem .6rem; color:#fff; font-size:.8rem; line-height:1.25`
        }, el('b', { style: 'display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis' }, s.stage),
           el('span', { class: 'mono', style: 'opacity:.85' }, `${s.weeks} שב׳`));
        if (s.current) {
            const shine = el('i', { style: 'position:absolute; inset:0; background:linear-gradient(100deg, transparent 30%, rgba(255,255,255,.35) 50%, transparent 70%); pointer-events:none' });
            seg.appendChild(shine);
            if (GO) M.animate(shine, { x: ['-120%', '120%'] }, { duration: 2.4, repeat: Infinity, ease: 'easeInOut', repeatDelay: .6 });
        }
        rib.appendChild(seg); return seg;
    });
    card.appendChild(rib);
    const ends = el('div', { class: 'muted mono', style: 'display:flex; direction:ltr; justify-content:space-between; margin-top:.5rem' },
        el('span', {}, fmtD(D.journey[0].from)), el('span', {}, fmtD(D.journey[D.journey.length - 1].to)));
    card.appendChild(ends);
    const list = el('div', { class: 'legend', style: 'margin-top:1rem' });
    Object.entries(hue).forEach(([st, c]) => list.appendChild(el('span', {}, el('i', { style: `background:${c}; height:10px; width:10px; border-radius:3px` }), st)));
    card.appendChild(list);
    box.appendChild(card);
    anim(segs, { scaleX: [0, 1], opacity: [0, 1] }, { delay: stagger(.12, { startDelay: .2 }), duration: .7, ease: EASE });
}

// ── scene 5: the delay story, drawn ─────────────────────────────────────
function sceneDelay(box) {
    const d = D.delay, P = d.points;
    sceneHead(box, 'האיחור מתפתח', 'איך הדחייה נבנתה, דוח אחרי דוח', 'בחודשים, ציר אחד. כל טבעת היא דוח שבו יעד החשמול זז. רחף מעל הגרף לפרטי הדוח.');
    const card = el('div', { class: 'chart glass' });
    const lg = el('div', { class: 'legend' });
    d.series.forEach(s => lg.appendChild(el('span', { title: s.help }, el('i', { style: `background:${s.color}` }), s.label)));
    card.appendChild(lg);
    const W = 1000, H = 400, L = 54, R = 24, Tp = 20, B = 40;
    const t0 = ms(P[0].date), t1 = ms(P[P.length - 1].date);
    const X = t => L + (t1 === t0 ? .5 : (t - t0) / (t1 - t0)) * (W - L - R);
    const Y = v => Tp + (d.y_max - v) / (d.y_max - d.y_min) * (H - Tp - B);
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'התפתחות האיחור בחודשים לאורך הדוחות' });
    glowFilter(svg, 'dg', 4);
    const defs = svg.querySelector('defs');
    const ag = sv('linearGradient', { id: 'area', x1: 0, y1: 0, x2: 0, y2: 1 });
    ag.appendChild(sv('stop', { offset: '0%', 'stop-color': d.series[0].color, 'stop-opacity': .35 }));
    ag.appendChild(sv('stop', { offset: '100%', 'stop-color': d.series[0].color, 'stop-opacity': 0 }));
    defs.appendChild(ag);
    const grid = sv('g', {});
    d.ticks.forEach(t => {
        grid.appendChild(sv('line', { x1: L, y1: Y(t), x2: W - R, y2: Y(t), stroke: t === 0 ? '#3b5b85' : '#14243a', 'stroke-width': t === 0 ? 1.5 : 1 }));
        grid.appendChild(sv('text', { x: L - 10, y: Y(t) + 4, 'text-anchor': 'end', 'font-size': 12, fill: '#64748b', class: 'mono' }, t));
    });
    grid.appendChild(sv('text', { x: L - 10, y: Tp - 6, 'text-anchor': 'end', 'font-size': 11, fill: '#64748b' }, 'ח׳'));
    d.stage_changes.forEach(sc => {
        const x = X(ms(sc));
        grid.appendChild(sv('line', { x1: x, y1: Tp, x2: x, y2: H - B, stroke: '#5b6b82', 'stroke-dasharray': '2 5' }));
        const pt = P.find(p => p.date === sc);
        grid.appendChild(sv('text', { x: x > W * .85 ? x - 6 : x + 6, y: H - B - 8, 'text-anchor': x > W * .85 ? 'end' : 'start', 'font-size': 11, fill: '#9fb0c6' }, pt ? `← ${pt.stage}` : ''));
    });
    [P[0], P[P.length - 1]].forEach((p, i) => grid.appendChild(sv('text', { x: X(ms(p.date)), y: H - 14, 'text-anchor': i ? 'end' : 'start', 'font-size': 12, fill: '#64748b', class: 'mono' }, fmtD(p.date))));
    svg.appendChild(grid);

    const seriesPath = key => {
        let s = '', pen = false;
        P.forEach(p => {
            if (p[key] === null || p[key] === undefined) { pen = false; return; }
            s += `${pen ? 'L' : 'M'}${X(ms(p.date)).toFixed(1)},${Y(p[key]).toFixed(1)} `; pen = true;
        });
        return s.trim();
    };
    const fcPts = P.filter(p => p.fc_slip !== null && p.fc_slip !== undefined);
    let area = null;
    if (fcPts.length > 1) {
        area = sv('path', { d: `${seriesPath('fc_slip')} L${X(ms(fcPts[fcPts.length - 1].date))},${Y(Math.max(d.y_min, 0))} L${X(ms(fcPts[0].date))},${Y(Math.max(d.y_min, 0))} Z`, fill: 'url(#area)' });
        svg.appendChild(area);
    }
    const lines = d.series.map(s => {
        const p = sv('path', { d: seriesPath(s.key), fill: 'none', stroke: s.color, 'stroke-width': 3, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', filter: 'url(#dg)' });
        svg.appendChild(p); return p;
    });
    const rings = [], pings = [];
    P.forEach(p => {
        const mv = p.moves.find(m => m.kind === 'forecast');
        if (!mv || p.fc_slip === null || p.fc_slip === undefined) return;
        const x = X(ms(p.date)), y = Y(p.fc_slip);
        const g = sv('g', { style: 'transform-box: fill-box; transform-origin: center' });
        g.appendChild(sv('circle', { cx: x, cy: y, r: 9, fill: '#070d18', stroke: '#ef6b6b', 'stroke-width': 2.5 }));
        g.appendChild(sv('text', { x, y: y - 16, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 700, fill: '#ef6b6b', class: 'mono' }, signed(mv.months, 1)));
        const ping = sv('circle', { cx: x, cy: y, r: 9, fill: 'none', stroke: '#ef6b6b', 'stroke-width': 2, style: 'transform-box: fill-box; transform-origin: center' });
        svg.appendChild(ping); svg.appendChild(g); rings.push(g); pings.push(ping);
    });
    // crosshair
    const cross = sv('g', { style: 'pointer-events:none', opacity: 0 });
    const vline = sv('line', { y1: Tp, y2: H - B, stroke: '#e8eef7', 'stroke-opacity': .4 });
    cross.appendChild(vline);
    const dots = d.series.map(s => { const c = sv('circle', { r: 5, fill: s.color, stroke: '#05080f', 'stroke-width': 2 }); cross.appendChild(c); return c; });
    svg.appendChild(cross);
    const hit = sv('rect', { x: L, y: Tp, width: W - L - R, height: H - Tp - B, fill: 'transparent', style: 'cursor:crosshair' });
    svg.appendChild(hit);
    card.appendChild(svg);
    const ro = el('div', { class: 'readout' }, 'רחף מעל הגרף.');
    card.appendChild(ro);
    const show = i => {
        const p = P[i], x = X(ms(p.date));
        vline.setAttribute('x1', x); vline.setAttribute('x2', x); cross.setAttribute('opacity', 1);
        d.series.forEach((s, j) => {
            const v = p[s.key];
            dots[j].setAttribute('visibility', v === null || v === undefined ? 'hidden' : 'visible');
            if (v !== null && v !== undefined) { dots[j].setAttribute('cx', x); dots[j].setAttribute('cy', Y(v)); }
        });
        ro.replaceChildren(el('b', {}, fmtD(p.date)), ` · ${p.stage}`,
            ...d.series.map(s => [' · ', s.short, ' ', el('bdi', {}, el('b', {}, p[s.key] === null || p[s.key] === undefined ? '—' : signed(p[s.key], 1)))]).flat(),
            p.moves.length ? ` · זז בדוח: ${p.moves.map(m => (m.kind === 'forecast' ? 'יעד ' : 'תכנית ') + ltr('+' + m.months)).join(', ')}` : '',
            p.week_text ? el('span', { class: 'q' }, `${p.week_repeat ? '🔁 ' : ''}${p.week_text}`) : null);
    };
    const nearest = cx => {
        const r = svg.getBoundingClientRect(), vx = (cx - r.left) / r.width * W;
        let bi = 0, bd = Infinity;
        P.forEach((p, i) => { const dd = Math.abs(X(ms(p.date)) - vx); if (dd < bd) { bd = dd; bi = i; } });
        return bi;
    };
    hit.addEventListener('pointermove', e => show(nearest(e.clientX)));
    hit.addEventListener('pointerleave', () => cross.setAttribute('opacity', 0));
    box.appendChild(card);

    anim(grid, { opacity: [0, 1] }, { duration: .6 });
    drawIn(lines, .3, 1.8);
    if (area) anim(area, { opacity: [0, 1] }, { delay: 1.6, duration: 1 });
    anim(rings, { scale: [0, 1] }, { delay: stagger(.18, { startDelay: 1.8 }), type: 'spring', stiffness: 400, damping: 11 });
    if (GO) pings.forEach((pg, i) => M.animate(pg, { scale: [1, 3.2], opacity: [.9, 0] }, { duration: 1.8, delay: 2 + i * .25, repeat: Infinity, repeatDelay: 1.2, ease: 'easeOut' }));
}

// ── scene 6: the risk pulse (topic × week), lit by a scan beam ──────────
function scenePulse(box) {
    const mx = D.matrix, Wk = mx.weeks;
    sceneHead(box, 'דופק הסיכונים', 'על מה נכתב, שבוע אחרי שבוע', 'כל נקודה היא דיווח שבועי. נקודה מוארת = הנושא הוזכר באותו שבוע. רחף לנקודה לציטוט.');
    const card = el('div', { class: 'chart glass' });
    const LBL = 190, TOT = 90, W = 1000, RH = 34, Tp = 26;
    const pitch = (W - LBL - TOT) / Math.max(Wk.length, 1);
    const H = Tp + mx.rows.length * RH + 30;
    const rad = Math.max(2.5, Math.min(pitch * .36, 9));
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'מטריצת נושאי סיכון לפי שבוע' });
    glowFilter(svg, 'pg', 2.5);
    const maxW = Math.max(...mx.rows.map(r => r.weeks), 1);
    const lit = [], bars = [];
    const ro = el('div', { class: 'readout' }, 'רחף לנקודה מוארת.');
    let col = 0;
    mx.missing_before.forEach((m, i) => {
        if (m > 0) svg.appendChild(sv('line', { x1: LBL + i * pitch, y1: Tp - 8, x2: LBL + i * pitch, y2: H - 30, stroke: '#e0a028', 'stroke-dasharray': '3 4', 'stroke-opacity': .5 }));
    });
    mx.rows.forEach((row, ri) => {
        const y = Tp + ri * RH + RH / 2;
        const tone = row.kind === 'repeat' ? '#94a3b8' : STAGE_HUES[ri % STAGE_HUES.length];
        svg.appendChild(sv('text', { x: LBL - 14, y: y + 4, 'text-anchor': 'end', 'font-size': 13, fill: '#e8eef7' }, row.label));
        row.cells.forEach((c, ci) => {
            const cx = LBL + ci * pitch + pitch / 2;
            if (!c.hit) { svg.appendChild(sv('circle', { cx, cy: y, r: Math.max(1.5, rad * .3), fill: '#1c2f4a' })); return; }
            const dot = sv('circle', { cx, cy: y, r: rad, fill: tone, filter: 'url(#pg)', style: 'transform-box: fill-box; transform-origin: center; cursor: pointer' });
            dot.dataset.col = ci;
            const tell = () => ro.replaceChildren(el('b', {}, fmtD(Wk[ci])), ` · ${row.label}`, c.quote ? el('span', { class: 'q' }, `"${c.quote}"`) : null);
            dot.addEventListener('pointerenter', tell); dot.addEventListener('click', tell);
            svg.appendChild(dot); lit.push(dot);
        });
        const bw = row.weeks / maxW * (TOT - 34);
        const bar = sv('rect', { x: W - TOT + 6, y: y - 5, width: Math.max(bw, 1), height: 10, rx: 5, fill: tone, style: 'transform-box: fill-box; transform-origin: left' });
        svg.appendChild(bar); bars.push(bar);
        svg.appendChild(sv('text', { x: W - 4, y: y + 4, 'text-anchor': 'end', 'font-size': 12, fill: '#9fb0c6', class: 'mono' }, row.weeks));
    });
    svg.appendChild(sv('text', { x: LBL, y: H - 8, 'font-size': 12, fill: '#64748b', class: 'mono' }, fmtD(Wk[0])));
    svg.appendChild(sv('text', { x: W - TOT, y: H - 8, 'text-anchor': 'end', 'font-size': 12, fill: '#64748b', class: 'mono' }, fmtD(Wk[Wk.length - 1])));
    const beam = sv('rect', { x: LBL, y: Tp - 10, width: Math.max(pitch, 6), height: H - Tp - 20, fill: '#8b5cf6', opacity: 0, rx: 3 });
    svg.appendChild(beam);
    card.appendChild(svg); card.appendChild(ro);
    box.appendChild(card);
    col = Wk.length;
    const sweep = 2.6;
    if (GO) {
        M.animate(beam, { x: [LBL, LBL + (col - 1) * pitch], opacity: [0, .28, .28, 0] }, { duration: sweep, delay: .3, ease: 'linear' });
        lit.forEach(dot => M.animate(dot, { scale: [0, 1.8, 1], opacity: [0, 1] }, { duration: .55, delay: .3 + (Number(dot.dataset.col) / Math.max(col - 1, 1)) * sweep, ease: EASE }));
    }
    anim(bars, { scaleX: [0, 1] }, { delay: stagger(.08, { startDelay: sweep + .3 }), duration: .8, ease: EASE });
}

// ── scene 7: the topic constellation ────────────────────────────────────
function sceneTopics(box) {
    sceneHead(box, 'קבוצת הנושאים', 'מה מעסיק את הפרויקט', 'גודל כל כוכב = מספר השבועות שבהם הנושא הוזכר. לחץ על כוכב לציטוט האחרון.');
    const card = el('div', { class: 'chart glass' });
    const sky = el('div', { class: 'stars' });
    card.appendChild(sky);
    const ro = el('div', { class: 'readout' }, 'לחץ על כוכב.');
    card.appendChild(ro);
    box.appendChild(card);
    const W = sky.clientWidth || 900, H = sky.clientHeight || 420;
    const mx = Math.max(...D.topics.map(t => t.weeks), 1);
    const small = W < 600;
    // golden-angle seeds, then push overlapping stars apart (a few relaxation passes)
    const nodes = D.topics.map((t, i) => {
        const size = (small ? 64 : 84) + (t.weeks / mx) * (small ? 56 : 84);
        const a = i * 2.39996, rr = Math.sqrt((i + .5) / D.topics.length) * .4;
        return { t, size, x: W / 2 + Math.cos(a) * rr * W, y: H / 2 + Math.sin(a) * rr * H };
    });
    for (let it = 0; it < 160; it++) {
        for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
            const A = nodes[i], B = nodes[j], dx = B.x - A.x, dy = B.y - A.y, dist = Math.hypot(dx, dy) || .01;
            const need = (A.size + B.size) / 2 + 10;
            if (dist < need) { const push = (need - dist) / 2, ux = dx / dist, uy = dy / dist; A.x -= ux * push; A.y -= uy * push; B.x += ux * push; B.y += uy * push; }
        }
        nodes.forEach(n => { n.x = Math.min(Math.max(n.x, n.size / 2 + 4), W - n.size / 2 - 4); n.y = Math.min(Math.max(n.y, n.size / 2 + 4), H - n.size / 2 - 4); });
    }
    const stars = nodes.map(({ t, size, x, y }, i) => {
        const left = x - size / 2, top = y - size / 2;
        const s = el('button', { type: 'button', class: 'star',
            style: `width:${size}px; height:${size}px; left:${left}px; top:${top}px; font-size:${size > 120 ? .95 : .78}rem; --d:${7 + (i % 5) * 1.3}s` },
            t.category, el('small', {}, t.weeks ? `${t.weeks} שב׳` : 'בעמודה'));
        s.addEventListener('click', () => {
            sky.querySelectorAll('.star.sel').forEach(x => x.classList.remove('sel')); s.classList.add('sel');
            ro.replaceChildren(el('b', {}, t.category), ` · ${t.source && /^\d{4}-/.test(t.source) ? fmtD(t.source) : (t.source || '')}`, el('span', { class: 'q' }, `"${t.quote}"`));
            anim(s, { scale: [1, 1.15, 1] }, { duration: .5 });
        });
        sky.appendChild(s); return s;
    });
    anim(stars, { scale: [0, 1], opacity: [0, 1] }, { delay: stagger(.09, { startDelay: .2 }), type: 'spring', stiffness: 260, damping: 14 });
}

// ── scene 8: the verdict — every computed flag ──────────────────────────
function sceneFlags(box) {
    sceneHead(box, 'השורה התחתונה', `${D.flags.length} סימני סיכון מחושבים`, 'כל שורה היא כלל על הנתונים — לא ניסוח של מודל שפה. מהחמור לקל.');
    const list = el('div', { class: 'flags' });
    D.flags.forEach(f => list.appendChild(el('div', { class: 'flag glass' }, el('i', { style: `background:${LEVEL[f.level][2]}; box-shadow:0 0 14px ${LEVEL[f.level][2]}` }),
        el('div', {}, el('b', { style: `color:${LEVEL[f.level][2]}` }, `סיכון ${LEVEL[f.level][0]}`), f.text))));
    box.appendChild(list);
    anim(list.children, { opacity: [0, 1], x: [-70, 0], filter: ['blur(6px)', 'blur(0px)'] }, { delay: stagger(.09, { startDelay: .2 }), duration: .7, ease: EASE });
}

// ── assembly ────────────────────────────────────────────────────────────
const T = D.targets;
const SCENES = [
    { id: 'hero', label: 'דרגה', build: sceneHero, on: true },
    { id: 'twin', label: 'תאום', build: sceneTwin, on: true },
    { id: 'kpi', label: 'מדדים', build: sceneInstruments, on: true },
    { id: 'targets', label: 'יעדים', build: sceneTargets, on: !!(T.dev || T.appr || T.fc) },
    { id: 'journey', label: 'שלבים', build: sceneJourney, on: D.journey.length > 0 },
    { id: 'delay', label: 'איחור', build: sceneDelay, on: !!D.delay },
    { id: 'pulse', label: 'דופק', build: scenePulse, on: !!(D.matrix && D.matrix.rows.some(r => r.weeks > 0)) },
    { id: 'topics', label: 'נושאים', build: sceneTopics, on: D.topics.length > 0 },
    { id: 'flags', label: 'סיכום', build: sceneFlags, on: D.flags.length > 0 },
].filter(s => s.on);

const main = document.getElementById('report'), dots = document.getElementById('dots');
const boxes = SCENES.map((s, i) => {
    const sec = el('section', { class: 'scene', id: `s-${s.id}`, 'aria-label': s.label });
    main.appendChild(sec);
    const b = el('button', { type: 'button', title: s.label, 'aria-label': s.label });
    b.addEventListener('click', () => sec.scrollIntoView({ behavior: GO ? 'smooth' : 'auto' }));
    dots.appendChild(b);
    return sec;
});
const built = new Set();
const build = i => { if (built.has(i)) return; built.add(i); try { SCENES[i].build(boxes[i]); } catch (e) { console.error(e); } };
let current = 0;
if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => {
        const i = boxes.indexOf(e.target);
        if (e.isIntersecting) build(i);
        if (e.intersectionRatio > .45) { current = i; [...dots.children].forEach((d, j) => d.classList.toggle('on', j === i)); }
    }), { threshold: [0, .2, .45, .7] });
    boxes.forEach(b => io.observe(b));
} else boxes.forEach((_, i) => build(i));
build(0);

// scroll progress
const bar = document.getElementById('progress');
const onScroll = () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    bar.style.transform = `scaleX(${max > 0 ? scrollY / max : 0})`;
};
addEventListener('scroll', onScroll, { passive: true }); onScroll();

// presentation mode: one scene every PRESENT_SECONDS, ←/→ to step, Esc to stop
const PRESENT_SECONDS = 9;
const btn = document.getElementById('present');
let timer = null;
const go = i => { if (i >= 0 && i < boxes.length) boxes[i].scrollIntoView({ behavior: GO ? 'smooth' : 'auto' }); };
const stop = () => { clearInterval(timer); timer = null; btn.textContent = '▶ מצגת'; document.body.classList.remove('present'); };
btn.addEventListener('click', () => {
    if (timer) return stop();
    document.body.classList.add('present'); btn.textContent = '■ עצור';
    if (current >= boxes.length - 1) go(0);
    timer = setInterval(() => { if (current >= boxes.length - 1) stop(); else go(current + 1); }, PRESENT_SECONDS * 1000);
});
addEventListener('keydown', e => {
    if (e.key === 'Escape') stop();
    else if (e.key === 'ArrowLeft' || e.key === 'PageDown') { e.preventDefault(); go(current + 1); }
    else if (e.key === 'ArrowRight' || e.key === 'PageUp') { e.preventDefault(); go(current - 1); }
});

// background: a slow field of sparks in the report's tone — decoration only
const cv = document.getElementById('bg'), cx = cv.getContext('2d');
let sparks = [];
const size = () => {
    cv.width = innerWidth * devicePixelRatio; cv.height = innerHeight * devicePixelRatio;
    sparks = Array.from({ length: Math.min(90, Math.round(innerWidth / 14)) }, () => ({
        x: Math.random() * cv.width, y: Math.random() * cv.height, r: (Math.random() * 1.6 + .4) * devicePixelRatio,
        v: (Math.random() * .25 + .05) * devicePixelRatio, a: Math.random() * .5 + .15 }));
};
const paint = () => {
    cx.clearRect(0, 0, cv.width, cv.height);
    const g = cx.createRadialGradient(cv.width * .8, cv.height * .1, 0, cv.width * .8, cv.height * .1, cv.width * .7);
    g.addColorStop(0, TONE + '22'); g.addColorStop(1, 'transparent');
    cx.fillStyle = g; cx.fillRect(0, 0, cv.width, cv.height);
    const step = 64 * devicePixelRatio;
    cx.strokeStyle = 'rgba(42,74,114,.12)'; cx.lineWidth = 1; cx.beginPath();
    for (let x = (scrollY * .2 * devicePixelRatio) % step; x < cv.width; x += step) { cx.moveTo(x, 0); cx.lineTo(x, cv.height); }
    for (let y = 0; y < cv.height; y += step) { cx.moveTo(0, y); cx.lineTo(cv.width, y); }
    cx.stroke();
    sparks.forEach(s => {
        cx.globalAlpha = s.a; cx.fillStyle = TONE; cx.beginPath(); cx.arc(s.x, s.y, s.r, 0, 6.283); cx.fill();
        if (GO) { s.y -= s.v; if (s.y < -5) { s.y = cv.height + 5; s.x = Math.random() * cv.width; } }
    });
    cx.globalAlpha = 1;
    if (GO) requestAnimationFrame(paint);
};
addEventListener('resize', () => { size(); if (!GO) paint(); });
size(); paint();
})();
