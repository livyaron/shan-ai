/* The 3D substation on the project's visual report ("התאום הדיגיטלי").
 *
 * Draws static/models/substation.glb (one mesh per layer: site / structure /
 * equipment / insulators / conductors / glass) in the state the server read
 * from the project's stage (project_visual.twin_state): a built layer stands
 * solid, a layer under construction rises again and again under a scan line,
 * a layer not yet reached is a blueprint hologram, and a finished station's
 * conductors glow. The ring of light on the ground is the project's computed
 * risk flags, one arc each in its level's colour — the same arcs as the
 * report's first scene. Decoration over data the page already shows: this
 * module computes no number and fetches nothing but the model.
 *
 * three.js (pinned in the page's import map) is imported only when the scene
 * is reached (window.mountTwin). */
const MODEL_URL = '/static/models/substation.glb';
const BUILD_SECONDS = 6;          // one rise of an active layer
const HOLD_SECONDS = 1.6;         // pause at the top before it rises again
const LEVEL_HEX = { high: 0xef6b6b, medium: 0xe0a028, info: 0x3987e5 };
const STATE_HE = { done: 'הושלם', active: 'בביצוע', todo: 'טרם' };

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
    const stateOf = k => (twin ? (twin.layers.find(l => l.key === k) || {}).state : 'done') || 'done';
    const status = el('div', { class: 'twin-status' }, 'טוען את מודל התחנה…');
    host.appendChild(status);

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
    const W = () => host.clientWidth, H = () => host.clientHeight;
    renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.25 : 1.75));
    renderer.setSize(W(), H());
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.25;
    renderer.localClippingEnabled = true;
    renderer.shadowMap.enabled = !small;
    renderer.shadowMap.type = T.PCFSoftShadowMap;
    renderer.domElement.setAttribute('aria-hidden', 'true');
    host.insertBefore(renderer.domElement, host.firstChild);

    const tone = new T.Color(opts.tone || '#8b5cf6');
    const { RoomEnvironment } = await import('three/addons/environments/RoomEnvironment.js');
    const scene = new T.Scene();
    scene.background = new T.Color(0x060a12);
    scene.fog = new T.FogExp2(0x060a12, 0.0042);
    scene.environment = new T.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), .04).texture;
    scene.environmentIntensity = .45;

    const camera = new T.PerspectiveCamera(38, W() / H(), 0.5, 2000);
    // a tall phone screen needs the camera further out to fit the yard's width
    const HOME = new T.Vector3(74, 40, 80).multiplyScalar(Math.min(Math.max(1.3 / (W() / H()), 1), 2.3));
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

    // the model
    const layers = {};
    const holoMat = () => new T.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true, transparent: true, opacity: 0.16,
        blending: T.AdditiveBlending, depthWrite: false });
    try {
        const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
        const gltf = await loader.loadAsync(MODEL_URL, ev => {
            if (ev.total) status.textContent = `טוען את מודל התחנה… ${Math.round(ev.loaded / ev.total * 100)}%`;
        });
        gltf.scene.updateMatrixWorld(true);
        gltf.scene.traverse(o => {
            if (!o.isMesh) return;
            o.userData.y0 = o.position.y;   // meshopt quantization puts an offset on the node
            const key = o.name === 'glass' ? 'equipment' : o.name;   // windows belong to the equipment house
            const state = stateOf(key);
            const src = o.material;
            const solid = new T.MeshStandardMaterial({
                color: key === 'site' ? new T.Color(0x3a3f47) : src.color, metalness: src.metalness, roughness: src.roughness, flatShading: true,
                side: T.DoubleSide, transparent: o.name === 'glass', opacity: o.name === 'glass' ? .45 : 1,
                emissive: new T.Color(0x000000),
            });
            const entry = layers[key] || (layers[key] = { key, state, solids: [], holos: [], box: new T.Box3() });
            entry.box.union(new T.Box3().setFromObject(o));   // world space, like the clipping planes
            const parent = o.parent;
            if (state === 'todo') {
                o.material = holoMat();
                entry.holos.push(o);
            } else {
                o.material = solid; o.castShadow = o.receiveShadow = !small;
                entry.solids.push(o);
                if (state === 'active') {
                    solid.clippingPlanes = [new T.Plane(new T.Vector3(0, -1, 0), 0)];
                    solid.clipShadows = true;
                    const ghost = new T.Mesh(o.geometry, holoMat());
                    ghost.position.copy(o.position); ghost.quaternion.copy(o.quaternion); ghost.scale.copy(o.scale);
                    ghost.userData.y0 = o.position.y;
                    parent.add(ghost);
                    entry.holos.push(ghost);
                }
                if (key === 'conductors' && twin && twin.energized) {
                    solid.emissive = new T.Color(0x6fd3ff); solid.emissiveIntensity = 2.5;
                }
            }
        });
        scene.add(gltf.scene);
    } catch (e) {
        status.textContent = 'מודל התחנה לא נטען.';
        console.error(e);
        return;
    }
    status.remove();

    // one scan line sweeps every active layer together while they "build":
    // an outline only, over the yard's footprint, so the
    // conductors' long spans do not stretch it across the sky — the equipment's
    // footprint is the yard itself
    const active = Object.values(layers).filter(l => l.state === 'active');
    let scan = null;
    if (active.length) {
        const box = new T.Box3(); active.forEach(l => box.union(l.box));
        // 4th–96th percentile of the equipment's vertices: a few stray parts
        // far out in the field must not blow the outline up to the whole map
        const xs = [], zs = [], v = new T.Vector3();
        (layers.equipment || active[0]).solids.concat((layers.equipment || active[0]).holos).forEach(m => {
            const pos = m.geometry.attributes.position, step = Math.max(1, Math.floor(pos.count / 4000));
            for (let i = 0; i < pos.count; i += step) { v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld); xs.push(v.x); zs.push(v.z); }
        });
        const pct = (a, q) => { a.sort((x, y) => x - y); return a[Math.floor(q * (a.length - 1))]; };
        const foot = { min: { x: pct(xs, .04), z: pct(zs, .04) }, max: { x: pct(xs, .96), z: pct(zs, .96) } };
        const sx = (foot.max.x - foot.min.x) * 1.08, sz = (foot.max.z - foot.min.z) * 1.08;
        const line = new T.LineLoop(new T.BufferGeometry().setFromPoints(
            [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]].map(([x, z]) => new T.Vector3(x * sx, 0, z * sz))),
            new T.LineBasicMaterial({ color: tone, transparent: true, opacity: .95 }));
        line.position.set((foot.min.x + foot.max.x) / 2, box.min.y, (foot.min.z + foot.max.z) / 2);
        scene.add(line);
        scan = { box, line };
    }

    // post: bloom makes energized conductors, the ring and the scan lines glow
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new T.Vector2(W(), H()), small ? .4 : .55, .5, .85);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 5, 0);
    controls.enableDamping = true; controls.dampingFactor = .06;
    controls.minDistance = 22; controls.maxDistance = 260; controls.maxPolarAngle = 1.48;
    controls.autoRotate = !reduce; controls.autoRotateSpeed = .55;
    controls.enabled = false;
    let idleAt = 0;
    controls.addEventListener('start', () => { controls.autoRotate = false; });
    controls.addEventListener('end', () => { idleAt = performance.now(); });

    // legend: each layer with its state; hovering one lifts it, the rest dim
    const legend = el('div', { class: 'twin-legend' });
    if (twin) legend.appendChild(el('div', { class: 'twin-phase' }, twin.phase));
    else legend.appendChild(el('div', { class: 'twin-phase' }, 'השלב בקובץ לא נמצא על סולם השלבים — המודל מוצג ללא מצב בנייה'));
    let focus = null;
    (twin ? twin.layers : []).forEach(L => {
        const row = el('button', { type: 'button', class: `twin-row s-${L.state}` }, el('i', {}), el('span', {}, L.label), el('em', {}, STATE_HE[L.state]));
        row.addEventListener('pointerenter', () => { focus = L.key; });
        row.addEventListener('pointerleave', () => { focus = null; });
        row.addEventListener('focus', () => { focus = L.key; });
        row.addEventListener('blur', () => { focus = null; });
        legend.appendChild(row);
    });
    host.appendChild(legend);
    host.appendChild(el('div', { class: 'twin-hint' }, 'גרור לסיבוב · גלגלת לזום'));

    // render loop — only while on screen
    let visible = true, t0 = performance.now();
    new IntersectionObserver(es => { visible = es[0].isIntersecting; }, { threshold: 0 }).observe(host);
    const intro = reduce ? 0 : 3.2;
    const ease = x => 1 - Math.pow(1 - Math.min(Math.max(x, 0), 1), 3);
    const startPos = camera.position.clone();
    const order = ['site', 'structure', 'equipment', 'insulators', 'conductors'];
    const tick = now => {
        requestAnimationFrame(tick);
        if (!visible || document.hidden) return;
        const t = (now - t0) / 1000;
        if (t < intro) camera.position.lerpVectors(startPos, HOME, ease(t / intro));
        else if (!controls.enabled) { camera.position.copy(HOME); controls.enabled = true; }
        if (!controls.autoRotate && idleAt && now - idleAt > 6000 && !reduce) { controls.autoRotate = true; idleAt = 0; }
        // built layers assemble in construction order during the intro
        order.forEach((k, i) => {
            const L = layers[k]; if (!L) return;
            const p = reduce ? 1 : ease((t - .4 - i * .35) / 1.1);
            L.solids.forEach(m => { m.position.y = m.userData.y0 - (1 - p) * 18; });
            L.holos.forEach(m => { m.material.opacity = (focus && focus !== k ? .03 : focus === k ? .3 : L.state === 'active' ? .045 : .055) * p; });
            L.solids.forEach(m => {
                const mat = m.material;
                const dim = focus && focus !== k;
                mat.opacity = m.name === 'glass' ? .45 : dim ? .25 : 1;
                mat.transparent = dim || m.name === 'glass';
                if (L.state === 'active') mat.emissive.copy(tone).multiplyScalar(.12 + .1 * Math.sin(t * 3) + (focus === k ? .25 : 0));
                else if (!(k === 'conductors' && twin && twin.energized)) mat.emissive.setScalar(focus === k ? .18 : 0);
                else mat.emissiveIntensity = 2.2 + .6 * Math.sin(t * 2.2);
            });
        });
        // active layers: the solid rises under a scan line, then rises again
        if (scan) {
            const cyc = BUILD_SECONDS + HOLD_SECONDS, ph = reduce ? 1 : Math.min(((t - intro) % cyc) / BUILD_SECONDS, 1);
            const y = scan.box.min.y + (scan.box.max.y - scan.box.min.y) * (t < intro ? 0 : ease(ph));
            active.forEach(l => l.solids.forEach(m => { m.material.clippingPlanes[0].constant = y; }));
            scan.line.position.y = y; scan.line.visible = ph < 1;
        }
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
    }).observe(host);
};
dispatchEvent(new Event('twin-ready'));
