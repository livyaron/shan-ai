/* Shared drawing kit for the stations drawn in code (gis_station.js,
 * mobile_station.js): primitives placed in metres, collected per layer ×
 * material, then merged into one mesh each. Each mesh is named by its layer
 * (project_visual.TWIN_LAYERS); userData.smooth = smooth shading, userData.glass
 * = see-through cladding. Geometry only — no text, no network. */
export const MAT = {
    concrete: { color: 0x8d939a, metalness: 0, roughness: .92 },
    gravel:   { color: 0x3a3f47, metalness: 0, roughness: 1 },
    asphalt:  { color: 0x23272d, metalness: 0, roughness: 1 },
    steel:    { color: 0x5d6673, metalness: .7, roughness: .45 },
    gis:      { color: 0xb9c2cc, metalness: .55, roughness: .38, smooth: true },
    cabinet:  { color: 0x8a96a3, metalness: .3, roughness: .6 },
    xfmr:     { color: 0x56635f, metalness: .45, roughness: .55 },
    xfmrRound:{ color: 0x56635f, metalness: .45, roughness: .55, smooth: true },
    bushing:  { color: 0x9aa1aa, metalness: .05, roughness: .5, smooth: true },
    wire:     { color: 0xc3c9d1, metalness: .85, roughness: .3, smooth: true },
    glass:    { color: 0x8fb8de, metalness: .1, roughness: .1, glass: true },
    chassis:  { color: 0x2b3038, metalness: .6, roughness: .55 },
    tyre:     { color: 0x15171a, metalness: 0, roughness: .95, smooth: true },
    container:{ color: 0x9aa4ae, metalness: .2, roughness: .7 },
    cab:      { color: 0xa9b3bd, metalness: .3, roughness: .5 },
    arrester: { color: 0x7c848e, metalness: .05, roughness: .55, smooth: true },
};

export function kit(T, mergeGeometries) {
    const parts = {};                         // layer → material → [geometry]
    const put = (layer, mat, g) => { ((parts[layer] ||= {})[mat] ||= []).push(g); return g; };
    const m4 = new T.Matrix4(), q = new T.Quaternion(), up = new T.Vector3(0, 1, 0);

    // a box standing on y0 (x, z = its centre)
    const box = (layer, mat, w, h, d, x, y0, z) =>
        put(layer, mat, new T.BoxGeometry(w, h, d).translate(x, y0 + h / 2, z));
    // a cylinder between two points
    const rod = (layer, mat, r, a, b, seg = 16) => {
        const A = new T.Vector3(...a), B = new T.Vector3(...b), d = B.clone().sub(A), len = d.length();
        const g = new T.CylinderGeometry(r, r, len, seg, 1);
        q.setFromUnitVectors(up, d.normalize());
        g.applyMatrix4(m4.compose(A.clone().add(B).multiplyScalar(.5), q, new T.Vector3(1, 1, 1)));
        return put(layer, mat, g);
    };
    const sphere = (layer, mat, r, x, y, z, sy = 1) =>
        put(layer, mat, new T.SphereGeometry(r, 16, 10).scale(1, sy, 1).translate(x, y, z));
    const ring = (layer, mat, r, tube, x, y, z, axis) => {
        const g = new T.TorusGeometry(r, tube, 6, 20);
        if (axis === 'x') g.rotateY(Math.PI / 2); else if (axis === 'y') g.rotateX(Math.PI / 2);
        return put(layer, mat, g.translate(x, y, z));
    };
    // a shedded bushing standing at (x, y0, z), height h
    const bushing = (x, y0, z, h, r = .32) => {
        const pts = [new T.Vector2(0, 0), new T.Vector2(r * 1.25, 0)];
        const n = Math.max(6, Math.round(h / .28));
        for (let i = 0; i < n; i++) {
            const y = .25 + i * (h - .5) / n;
            pts.push(new T.Vector2(r, y), new T.Vector2(r * 1.85, y + .06), new T.Vector2(r, y + .14));
        }
        pts.push(new T.Vector2(r * .9, h - .15), new T.Vector2(r * .5, h), new T.Vector2(0, h));
        put('insulators', 'bushing', new T.LatheGeometry(pts, 16).translate(x, y0, z));
    };
    // a sagging conductor
    const wire = (a, b, sag, r = .06) => {
        const A = new T.Vector3(...a), B = new T.Vector3(...b), M = A.clone().lerp(B, .5);
        M.y -= sag;
        put('conductors', 'wire', new T.TubeGeometry(new T.QuadraticBezierCurve3(A, M, B), 24, r, 6, false));
    };

    // A "Yehuda" security fence (IEC's standard station fence — user 2026-10-24):
    // 2.6 m of welded mesh on steel posts, an arm at the top of every post bent
    // 45° OUTWARD, three barbed strands along the arms. Drawn round the
    // rectangle x0..x1 × z0..z1; `gate` = { side: 'x0'|'x1'|'z0'|'z1', at, width }
    // leaves a gap for the double entrance gate, which is drawn too.
    const FENCE_H = 2.6, ARM = .6, POST = 2.5, MESH = .35;
    const fence = (x0, x1, z0, z1, gate) => {
        const sides = [   // [from, to, outward normal, side name]
            [[x0, z0], [x1, z0], [0, -1], 'z0'], [[x1, z0], [x1, z1], [1, 0], 'x1'],
            [[x1, z1], [x0, z1], [0, 1], 'z1'], [[x0, z1], [x0, z0], [-1, 0], 'x0']];
        for (const [[ax, az], [bx, bz], [nx, nz], name] of sides) {
            const len = Math.hypot(bx - ax, bz - az), ux = (bx - ax) / len, uz = (bz - az) / len;
            const runs = [];
            if (gate && gate.side === name) {
                const g0 = (gate.at - (ux ? ax : az)) * (ux || uz) - gate.width / 2;
                runs.push([0, g0], [g0 + gate.width, len]);
            } else runs.push([0, len]);
            for (const [r0, r1] of runs) {
                if (r1 - r0 < .2) continue;
                const P = t => [ax + ux * t, az + uz * t];
                const n = Math.max(1, Math.round((r1 - r0) / POST));
                for (let i = 0; i <= n; i++) {                                   // posts + angled arms
                    const [px, pz] = P(r0 + (r1 - r0) * i / n);
                    box('site', 'steel', .09, FENCE_H, .09, px, 0, pz);
                    rod('site', 'steel', .03, [px, FENCE_H, pz], [px + nx * ARM * .7, FENCE_H + ARM * .7, pz + nz * ARM * .7], 4);
                }
                const [sx, sz] = P(r0), [ex, ez] = P(r1);
                for (const y of [.08, 1.3, FENCE_H - .05]) rod('site', 'steel', .025, [sx, y, sz], [ex, y, ez], 4);     // rails
                for (let k = 1; k <= 3; k++) {                                   // barbed strands along the arms
                    const o = ARM * .7 * k / 3;
                    rod('site', 'steel', .012, [sx + nx * o, FENCE_H + o, sz + nz * o], [ex + nx * o, FENCE_H + o, ez + nz * o], 3);
                }
                for (let t = r0; t <= r1; t += MESH) {                           // the mesh
                    const [mx, mz] = P(t);
                    box('site', 'steel', .015, FENCE_H - .1, .015, mx, .05, mz);
                }
            }
        }
        if (gate) {                                                              // double entrance gate
            const along = gate.side === 'z0' || gate.side === 'z1';
            const fixed = { x0, x1, z0, z1 }[gate.side];
            const at = (t, y) => along ? [gate.at + t, y, fixed] : [fixed, y, gate.at + t];
            const half = gate.width / 2;
            for (const t of [-half, half]) {                                     // heavy gate posts
                const [gx, , gz] = at(t, 0);
                box('site', 'steel', .22, FENCE_H + .4, .22, gx, 0, gz);
            }
            for (const [t0, t1] of [[-half + .1, -.05], [.05, half - .1]]) {     // the two leaves
                for (const y of [.15, 1.3, FENCE_H - .1]) rod('site', 'steel', .04, at(t0, y), at(t1, y), 4);
                rod('site', 'steel', .04, at(t0, .15), at(t0, FENCE_H - .1), 4);
                rod('site', 'steel', .04, at(t1, .15), at(t1, FENCE_H - .1), 4);
                rod('site', 'steel', .03, at(t0, .15), at(t1, FENCE_H - .1), 4);  // diagonal brace
                for (let t = t0 + MESH; t < t1; t += MESH) rod('site', 'steel', .01, at(t, .15), at(t, FENCE_H - .1), 3);
            }
        }
    };

    // merge: one mesh per layer × material
    const finish = () => {
        const group = new T.Group();
        for (const [layer, mats] of Object.entries(parts)) {
            for (const [mk, geoms] of Object.entries(mats)) {
                const spec = MAT[mk];
                const g = mergeGeometries(geoms, false);
                const mesh = new T.Mesh(g, new T.MeshStandardMaterial({ color: spec.color, metalness: spec.metalness, roughness: spec.roughness }));
                mesh.name = layer;
                mesh.userData = { smooth: !!spec.smooth, glass: !!spec.glass };
                group.add(mesh);
            }
        }
        return group;
    };
    return { put, box, rod, sphere, ring, bushing, wire, fence, finish };
}
