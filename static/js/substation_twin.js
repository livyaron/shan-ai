/* The 3D substation on the project's visual report ("התאום הדיגיטלי").
 *
 * Draws one of two stations, both made of the same layers (site / structure /
 * equipment / insulators / conductors): an open yard (static/models/
 * substation.glb) or a closed 161kV GIS station drawn in code
 * (static/js/gis_station.js). Which one comes from the server
 * (project_visual.twin_state: the PM's station type in the lessons module,
 * else the default); the viewer can switch. Beside it the file's own stage
 * ladder. The project's stage is marked with its
 * weeks; each step shows the station as that stage leaves it — built layers
 * stand solid, layers under construction rise again and again under a scan
 * line, layers not reached are a blueprint hologram, a finished station's
 * conductors glow. Click a step to see it; ▶ plays the build from planning to
 * the project's stage. The ring of light on the ground is the computed risk
 * flags, one arc each — the same arcs as the report's first scene.
 * Decoration over data the page already shows: this module computes no number
 * and fetches nothing but the model; text goes in with textContent only.
 *
 * three.js (pinned in the page's import map) is imported only when the scene
 * is reached (window.mountTwin). */
const MODEL_URL = '/static/models/substation.glb';
const BUILD_SECONDS = 6;          // one rise of an active layer
const HOLD_SECONDS = 1.6;         // pause at the top before it rises again
const LEVEL_HEX = { high: 0xef6b6b, medium: 0xe0a028, info: 0x3987e5 };

function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (k === 'class') e.className = v; else if (k === 'style') e.style.cssText = v; else e.setAttribute(k, v);
    }
    for (const c of kids) if (c !== null && c !== undefined) e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
    return e;
}

window.mountTwin = async function mountTwin(host, opts) {
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const small = innerWidth < 820;
    const twin = opts.twin;
    // the canvas lives in its own box: on a phone the stage ladder sits under it, not over it
    const view = el('div', { class: 'twin-view' });
    host.appendChild(view);
    const status = el('div', { class: 'twin-status' }, 'טוען את מודל התחנה…');
    view.appendChild(status);

    let T, GLTFLoader, OrbitControls, EffectComposer, RenderPass, UnrealBloomPass, OutputPass, MeshoptDecoder;
    try {
        // bare specifiers resolve through the page's import map (pinned three@0.169.0)
        [T, { GLTFLoader }, { OrbitControls }, { EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }, { MeshoptDecoder }] =
            await Promise.all([
                import('three'),
                import('three/addons/loaders/GLTFLoader.js'),
                import('three/addons/controls/OrbitControls.js'),
                import('three/addons/postprocessing/EffectComposer.js'),
                import('three/addons/postprocessing/RenderPass.js'),
                import('three/addons/postprocessing/UnrealBloomPass.js'),
                import('three/addons/postprocessing/OutputPass.js'),
                import('three/addons/libs/meshopt_decoder.module.js'),
            ]);
    } catch (e) {
        status.textContent = 'מנוע התלת-ממד לא נטען (ייתכן שהרשת חוסמת את cdn.jsdelivr.net).';
        return;
    }

    let renderer;
    try {
        renderer = new T.WebGLRenderer({ antialias: !small, powerPreference: 'high-performance' });
    } catch (e) {
        status.textContent = 'הדפדפן הזה לא תומך בתלת-ממד (WebGL).';
        return;
    }
    const W = () => view.clientWidth, H = () => view.clientHeight;
    renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.25 : 1.75));
    renderer.setSize(W(), H());
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.25;
    renderer.localClippingEnabled = true;
    renderer.shadowMap.enabled = !small;
    renderer.shadowMap.type = T.PCFSoftShadowMap;
    renderer.domElement.setAttribute('aria-hidden', 'true');
    view.insertBefore(renderer.domElement, view.firstChild);

    const tone = new T.Color(opts.tone || '#8b5cf6');
    const { RoomEnvironment } = await import('three/addons/environments/RoomEnvironment.js');
    const scene = new T.Scene();
    scene.background = new T.Color(0x060a12);
    scene.fog = new T.FogExp2(0x060a12, 0.0042);
    scene.environment = new T.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), .04).texture;
    scene.environmentIntensity = .45;

    const camera = new T.PerspectiveCamera(38, W() / H(), 0.5, 2000);
    // a tall phone screen needs the camera further out to fit the yard's width
    const fit = () => Math.min(Math.max(1.3 / (W() / H()), 1), 2.3);
    const HOME = new T.Vector3(74, 40, 80).multiplyScalar(fit());
    camera.position.set(220, 170, 260);

    scene.add(new T.HemisphereLight(0xc9d8ff, 0x101826, 1.1));
    const moon = new T.DirectionalLight(0xf2f5ff, 2.6);
    moon.position.set(-70, 140, 60);
    moon.castShadow = !small;
    moon.shadow.mapSize.set(2048, 2048);
    Object.assign(moon.shadow.camera, { left: -90, right: 90, top: 90, bottom: -90, near: 10, far: 400 });
    moon.shadow.bias = -0.0004;
    scene.add(moon);
    const rim = new T.DirectionalLight(tone, .7);
    rim.position.set(90, 40, -110);
    scene.add(rim);
    const glow = new T.PointLight(tone, 700, 160, 1.6);
    glow.position.set(0, 55, 0);
    scene.add(glow);

    // ground: dark disc + grid + the flag ring
    const ground = new T.Mesh(new T.CircleGeometry(420, 96), new T.MeshStandardMaterial({ color: 0x070c15, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = -0.05; ground.receiveShadow = true;
    scene.add(ground);
    const grid = new T.GridHelper(420, 84, 0x1e3a5f, 0x12233a);
    grid.material.transparent = true; grid.material.opacity = 0.55;
    scene.add(grid);
    const ring = new T.Group();
    const flags = opts.flags || [];
    const n = flags.length, gap = n > 1 ? 0.05 : 0;
    const arcs = [];
    (n ? flags : [{ level: null }]).forEach((f, i) => {
        const a0 = n ? i / n * Math.PI * 2 + gap / 2 : 0, len = n ? Math.PI * 2 / n - gap : Math.PI * 2;
        const m = new T.Mesh(new T.RingGeometry(63, 66, 96, 1, a0, len),
            new T.MeshBasicMaterial({ color: f.level ? LEVEL_HEX[f.level] : 0x10b981, transparent: true, opacity: .9,
                blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
        m.rotation.x = -Math.PI / 2; m.position.y = 0.08;
        m.userData.level = f.level;
        ring.add(m); arcs.push(m);
    });
    const outer = new T.Mesh(new T.RingGeometry(69, 69.5, 128),
        new T.MeshBasicMaterial({ color: tone, transparent: true, opacity: .35, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
    outer.rotation.x = -Math.PI / 2; outer.position.y = 0.06;
    ring.add(outer);
    scene.add(ring);

    // the models: an open yard (the GLB) or a closed GIS station drawn in code
    // (gis_station.js). Every layer gets a solid (clipped from below while it
    // is built) and a blueprint ghost; the step on screen decides which shows.
    // Each model is prepared once and kept, so switching back is instant.
    const holoMat = () => new T.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true, transparent: true, opacity: 0,
        blending: T.AdditiveBlending, depthWrite: false });
    const loaders = {
        ais: async () => {
            const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
            const gltf = await loader.loadAsync(MODEL_URL, ev => {
                if (ev.total) status.textContent = `טוען את מודל התחנה… ${Math.round(ev.loaded / ev.total * 100)}%`;
            });
            return gltf.scene;
        },
        mobile: async () => {
            const [{ buildMobile }, { mergeGeometries }] = await Promise.all([
                import('/static/js/mobile_station.js'), import('three/addons/utils/BufferGeometryUtils.js')]);
            return buildMobile(T, mergeGeometries);
        },
        gis: async () => {
            const [{ buildGIS }, { mergeGeometries }] = await Promise.all([
                import('/static/js/gis_station.js'), import('three/addons/utils/BufferGeometryUtils.js')]);
            return buildGIS(T, mergeGeometries);
        },
    };
    const pct = (a, q) => { a.sort((x, y) => x - y); return a[Math.floor(q * (a.length - 1))]; };
    function prepare(kind, root) {
        root.updateMatrixWorld(true);
        const layers = {}, meshes = [];
        root.traverse(o => { if (o.isMesh) meshes.push(o); });
        meshes.forEach(o => {
            const key = o.name === 'glass' ? 'equipment' : o.name;   // the GLB's windows belong to the equipment house
            const src = o.material, glass = o.name === 'glass' || !!o.userData.glass;
            o.material = new T.MeshStandardMaterial({
                color: key === 'site' && kind === 'ais' ? new T.Color(0x3a3f47) : src.color, metalness: src.metalness, roughness: src.roughness,
                flatShading: !o.userData.smooth, side: T.DoubleSide, transparent: glass, opacity: glass ? (o.userData.glass ? .16 : .45) : 1,
                depthWrite: !glass, emissive: new T.Color(0x000000),
                clippingPlanes: [new T.Plane(new T.Vector3(0, -1, 0), 1e6)], clipShadows: true,
            });
            o.castShadow = o.receiveShadow = !small && !glass;
            const ghost = new T.Mesh(o.geometry, holoMat());
            ghost.position.copy(o.position); ghost.quaternion.copy(o.quaternion); ghost.scale.copy(o.scale);
            o.parent.add(ghost);
            const L = layers[key] || (layers[key] = { key, solids: [], holos: [], box: new T.Box3(), state: null, riseAt: -1 });
            L.box.union(new T.Box3().setFromObject(o));   // world space, like the clipping planes
            L.solids.push(o); L.holos.push(ghost);
        });
        // the scan line rides over the yard's footprint: the 4th–96th
        // percentile of the equipment's vertices, so a few far parts and the
        // conductors' long spans do not stretch it across the map
        const xs = [], zs = [], v = new T.Vector3();
        (layers.equipment || Object.values(layers)[0]).solids.forEach(m => {
            const pos = m.geometry.attributes.position, step = Math.max(1, Math.floor(pos.count / 4000));
            for (let i = 0; i < pos.count; i += step) { v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld); xs.push(v.x); zs.push(v.z); }
        });
        const fx0 = pct(xs, .04), fx1 = pct(xs, .96), fz0 = pct(zs, .04), fz1 = pct(zs, .96);
        const foot = new T.BufferGeometry().setFromPoints(
            [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]].map(([x, z]) => new T.Vector3(x * (fx1 - fx0) * 1.08, 0, z * (fz1 - fz0) * 1.08)));
        // frame each station by its own site: the GIS plot is smaller than the open yard
        const site = (layers.site || Object.values(layers)[0]).box.getSize(new T.Vector3());
        const frame = Math.min(Math.max(Math.hypot(site.x, site.z) / 150, .42), 1.2);
        return { root, layers, foot, footAt: [(fx0 + fx1) / 2, (fz0 + fz1) / 2], frame };
    }
    const scanLine = new T.LineLoop(new T.BufferGeometry(), new T.LineBasicMaterial({ color: tone, transparent: true, opacity: .95 }));
    scanLine.visible = false;
    scene.add(scanLine);
    const prepared = {};
    let layers = {}, current3d = null, model = null;
    async function install(kind) {
        const p = prepared[kind] || (prepared[kind] = prepare(kind, await loaders[kind]()));
        if (current3d) scene.remove(current3d.root);
        current3d = p; model = kind; layers = p.layers;
        Object.values(layers).forEach(L => { L.state = null; L.holos.forEach(m => { m.material.opacity = 0; }); });
        scene.add(p.root);
        scanLine.geometry = p.foot; scanLine.position.set(p.footAt[0], 0, p.footAt[1]);
        HOME.set(74, 40, 80).multiplyScalar(fit() * p.frame);
        if (controls) {                        // a switch: glide to the new station's framing
            const dir = camera.position.clone().sub(controls.target).normalize();
            flyFrom = camera.position.clone(); flyTo = dir.multiplyScalar(HOME.length()).add(controls.target); flyAt = performance.now();
        }
    }
    let controls = null, flyFrom = null, flyTo = null, flyAt = 0;
    try {
        await install(twin.model in loaders ? twin.model : 'ais');
    } catch (e) {
        status.textContent = 'מודל התחנה לא נטען.';
        console.error(e);
        return;
    }
    status.remove();

    // post: bloom makes energized conductors, the ring and the scan line glow
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new T.Vector2(W(), H()), small ? .4 : .55, .5, .85);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());

    controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 5, 0);
    controls.enableDamping = true; controls.dampingFactor = .06;
    controls.zoomToCursor = true;                 // the wheel zooms where the pointer is
    controls.minDistance = 22; controls.maxDistance = 260; controls.maxPolarAngle = 1.48;
    controls.autoRotate = !reduce; controls.autoRotateSpeed = .55;
    controls.enabled = false;
    let idleAt = 0;
    controls.addEventListener('start', () => { controls.autoRotate = false; });
    controls.addEventListener('end', () => { idleAt = performance.now(); });

    // ── the stage ladder: the file's stages, the project's one marked ──
    const stepsOf = () => twin.models[model].steps, current = twin.current;
    const steps = stepsOf();                                 // labels are the same for every model
    const home = current === null || current === undefined ? null : current;
    let shown = home === null ? steps.length - 2 : home;   // off the ladder: the built station, nothing marked
    let scanBox = null, energized = false;
    const t0 = performance.now();
    const clock = () => (performance.now() - t0) / 1000;

    const panel = el('div', { class: 'twin-legend' });
    // which station is drawn, and why: the PM's choice in the lessons module, else the default
    const why = el('div', { class: 'twin-why' });
    const pick = el('div', { class: 'twin-models' });
    const pickBtns = Object.entries(twin.models).map(([k, m]) => {
        const b = el('button', { type: 'button', 'data-model': k }, m.label);
        b.addEventListener('click', async () => {
            if (k === model) return;
            stopPlay(); pickBtns.forEach(x => { x.disabled = true; });
            try { await install(k); show(shown, true); } catch (e) { console.error(e); }
            pickBtns.forEach(x => { x.disabled = false; });
            sayWhy();
        });
        pick.appendChild(b);
        return b;
    });
    function sayWhy() {
        pickBtns.forEach(b => b.classList.toggle('on', b.dataset.model === model));
        const label = twin.models[model].label;
        why.textContent = model !== twin.model ? `תצוגה ידנית — ${label}` : twin.model_why;   // the server's reason
    }
    panel.appendChild(pick);
    panel.appendChild(why);
    panel.appendChild(el('div', { class: 'twin-phase' }, home === null
        ? `השלב בקובץ${twin.stage ? ` ("${twin.stage}")` : ''} לא על סולם השלבים`
        : 'שלב הפרויקט בקובץ'));
    const rows = steps.map((s, i) => {
        const tag = i === home ? `${twin.weeks_lower_bound ? 'לפחות ' : ''}${twin.weeks ?? '—'} שב׳` : '';
        const row = el('button', { type: 'button', class: 'twin-step' + (i === home ? ' cur' : '') + (home !== null && i < home ? ' past' : ''),
            'aria-label': `${s.label}${i === home ? ' — השלב הנוכחי' : ''}` }, el('i', {}), el('span', {}, s.label), el('em', {}, tag));
        row.addEventListener('click', () => { stopPlay(); show(i); });
        panel.appendChild(row);
        return row;
    });
    const caption = el('div', { class: 'twin-caption' });
    panel.appendChild(caption);
    const bar = el('div', { class: 'twin-bar' });
    const playBtn = el('button', { type: 'button', class: 'twin-btn' }, '▶ נגן את הבנייה');
    const backBtn = el('button', { type: 'button', class: 'twin-btn ghost' }, 'חזרה לשלב הנוכחי');
    bar.appendChild(playBtn); bar.appendChild(backBtn);
    panel.appendChild(bar);
    host.appendChild(panel);
    view.appendChild(el('div', { class: 'twin-hint' }, 'גרור לסיבוב · גלגלת לזום · לחץ על שלב לראות אותו'));

    function show(i, stagger) {
        shown = i;
        const s = stepsOf()[i], t = clock();
        let k = 0;
        Object.values(layers).forEach(L => {
            const st = s.layers[L.key] || 'done';
            if (st === 'done' && L.state !== 'done') L.riseAt = t + (stagger ? .35 * k++ : 0);
            L.state = st;
        });
        energized = s.energized;
        const act = Object.values(layers).filter(L => L.state === 'active');
        scanBox = act.length ? act.reduce((b, L) => b.union(L.box), new T.Box3()) : null;
        rows.forEach((r, j) => r.classList.toggle('on', j === i));
        caption.textContent = s.caption;
        backBtn.style.display = home !== null && i !== home ? '' : 'none';
    }
    let playTimer = null;
    function stopPlay() { clearInterval(playTimer); playTimer = null; playBtn.textContent = '▶ נגן את הבנייה'; }
    playBtn.addEventListener('click', () => {
        if (playTimer) { stopPlay(); return; }
        const end = home === null ? steps.length - 1 : home;
        Object.values(layers).forEach(L => { L.state = null; });
        let i = 0; show(0); playBtn.textContent = '■ עצור';
        playTimer = setInterval(() => { i += 1; if (i > end) { stopPlay(); return; } show(i); }, 2200);
    });
    backBtn.addEventListener('click', () => { stopPlay(); show(home); });

    // render loop — only while on screen
    let visible = true;
    new IntersectionObserver(es => { visible = es[0].isIntersecting; }, { threshold: 0 }).observe(view);
    const intro = reduce ? 0 : 3.2;
    const ease = x => 1 - Math.pow(1 - Math.min(Math.max(x, 0), 1), 3);
    const startPos = camera.position.clone();
    const RISE = 1.4;
    sayWhy();
    show(shown, true);
    if (!reduce) Object.values(layers).forEach(L => { if (L.riseAt >= 0) L.riseAt += .6; });
    const tick = now => {
        requestAnimationFrame(tick);
        if (!visible || document.hidden) return;
        const t = (now - t0) / 1000;
        if (t < intro) camera.position.lerpVectors(startPos, HOME, ease(t / intro));
        else if (!controls.enabled) { camera.position.copy(HOME); controls.enabled = true; }
        if (flyTo) {
            const f = Math.min((now - flyAt) / 1200, 1);
            camera.position.lerpVectors(flyFrom, flyTo, ease(f));
            if (f >= 1) flyTo = null;
        }
        if (!controls.autoRotate && idleAt && now - idleAt > 6000 && !reduce) { controls.autoRotate = true; idleAt = 0; }
        // the active layers rise under the scan line, again and again
        const cyc = BUILD_SECONDS + HOLD_SECONDS, ph = reduce ? 1 : Math.min((t % cyc) / BUILD_SECONDS, 1);
        const scanY = scanBox ? scanBox.min.y + (scanBox.max.y - scanBox.min.y) * ease(ph) : 0;
        scanLine.visible = !!scanBox && ph < 1; scanLine.position.y = scanY;
        Object.values(layers).forEach(L => {
            let clip = 1e6, solid = true, ghost = .055;
            if (L.state === 'todo') { solid = false; }
            else if (L.state === 'active') { clip = scanY; ghost = .045; }
            else {                                            // done: a fresh one rises once, then stands
                const p = reduce ? 1 : ease((t - L.riseAt) / RISE);
                clip = p >= 1 ? 1e6 : L.box.min.y + (L.box.max.y - L.box.min.y) * p;
                ghost = .055 * (1 - p);
            }
            L.solids.forEach(m => {
                m.visible = solid && (L.state !== 'done' || t >= L.riseAt || reduce);
                const mat = m.material;
                mat.clippingPlanes[0].constant = clip;
                if (L.state === 'active') mat.emissive.copy(tone).multiplyScalar(.12 + .1 * Math.sin(t * 3));
                else if (L.key === 'conductors' && energized) { mat.emissive.setHex(0x6fd3ff); mat.emissiveIntensity = 2.2 + .6 * Math.sin(t * 2.2); }
                else { mat.emissive.setScalar(0); mat.emissiveIntensity = 1; }
            });
            L.holos.forEach(m => {
                const o = m.material.opacity;
                m.material.opacity = o + (ghost - o) * .08;     // blueprints fade, never pop
                m.visible = m.material.opacity > .003;
            });
        });
        arcs.forEach((a, i) => {
            const grow = reduce ? 1 : ease((t - .8 - i * .12) / .9);
            a.scale.setScalar(.6 + .4 * grow); a.material.opacity = grow * (a.userData.level === 'high' ? .65 + .35 * Math.sin(t * 4) : .85);
        });
        outer.material.opacity = .25 + .1 * Math.sin(t * 1.3);
        glow.intensity = 600 + 200 * Math.sin(t * 1.7);
        controls.update();
        composer.render();
    };
    requestAnimationFrame(tick);

    new ResizeObserver(() => {
        camera.aspect = W() / H(); camera.updateProjectionMatrix();
        renderer.setSize(W(), H()); composer.setSize(W(), H());
    }).observe(view);
};
dispatchEvent(new Event('twin-ready'));
