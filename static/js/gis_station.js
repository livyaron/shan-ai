/* A closed 161kV gas-insulated (GIS) substation, drawn in code for the visual
 * report's digital twin (user decision 2026-10-24: no downloadable GIS model
 * exists under a usable licence). Illustration only — not any real station.
 *
 * 8 bays in one hall on a double busbar: 3 overhead lines (SF6/air bushings
 * through the roof to a line gantry), 3 transformer bays (gas-insulated ducts
 * out to three outdoor power transformers between fire walls), a bus coupler
 * and a measurement bay. Plus the control building, the site, a Yehuda fence
 * with its entrance gate, and the access road.
 *
 * Returns a THREE.Group of a few merged meshes. Each mesh is named by its
 * layer — site / structure / equipment / insulators / conductors, the keys
 * project_visual.TWIN_LAYERS uses — so the twin builds and lights it exactly
 * like the open-yard model. userData.smooth = smooth shading (round parts);
 * userData.glass = the hall's see-through cladding. Units: metres, Y up. */
import { kit } from './station_kit.js';

export function buildGIS(T, mergeGeometries) {
    const { box, rod, sphere, ring, bushing, wire, fence, finish } = kit(T, mergeGeometries);

    // ── site ────────────────────────────────────────────────────────────
    box('site', 'gravel', 96, .3, 76, 0, -.3, 0);
    box('site', 'asphalt', 8, .05, 76, 40, 0, 0);                   // access road
    box('site', 'asphalt', 40, .05, 7, 22, 0, 33);
    box('site', 'concrete', 46, .6, 22, 0, 0, 0);                   // hall raft
    box('site', 'concrete', 18, .5, 12, 31, 0, -2);                 // control building raft
    for (const x of [-14, 0, 14]) box('site', 'concrete', 9, .5, 7, x, 0, 23);   // transformer bunds
    box('site', 'concrete', 40, .4, 3, 0, 0, -24);                  // gantry footings
    fence(-47.5, 47.5, -37.5, 37.5, { side: 'z1', at: 40, width: 8 });   // Yehuda fence + entrance gate on the access road (user 2026-10-24)
    box('site', 'asphalt', 8, .05, 8, 40, 0, 41.5);                 // the road continues out through the gate

    // ── structure: the GIS hall, control building, crane, fire walls, gantry ──
    const HL = 44, HW = 18, HH = 13, Y0 = .6;
    for (let x = -HL / 2; x <= HL / 2 + .01; x += 5.5) {         // portal frames
        box('structure', 'steel', .45, HH, .45, x, Y0, -HW / 2);
        box('structure', 'steel', .45, HH, .45, x, Y0, HW / 2);
        box('structure', 'steel', .4, .7, HW, x, Y0 + HH - .7, 0);
    }
    box('structure', 'steel', HL, .5, .4, 0, Y0 + HH - .5, -HW / 2); box('structure', 'steel', HL, .5, .4, 0, Y0 + HH - .5, HW / 2);
    box('structure', 'concrete', HL, HH, .35, 0, Y0, -HW / 2 - .2);                     // back wall
    box('structure', 'concrete', .35, HH, HW, -HL / 2 - .2, Y0, 0);                     // west gable
    box('structure', 'concrete', HL, 1.2, .35, 0, Y0, HW / 2 + .2);                     // front plinth wall
    box('structure', 'glass', HL, HH - 1.2, .12, 0, Y0 + 1.2, HW / 2 + .2);           // front cladding (see-through)
    box('structure', 'glass', .12, HH, HW, HL / 2 + .2, Y0, 0);                         // east gable (see-through)
    box('structure', 'glass', HL, .1, HW, 0, Y0 + HH, 0);                               // roof sheeting (see-through)
    box('structure', 'steel', HL, .3, .3, 0, Y0 + 10.2, -HW / 2 + .6);                  // crane rails
    box('structure', 'steel', HL, .3, .3, 0, Y0 + 10.2, HW / 2 - .6);
    box('structure', 'steel', 1.1, .9, HW - 1, 6, Y0 + 10.5, 0);                        // overhead crane
    box('structure', 'cabinet', 1.6, .8, 1.4, 6, Y0 + 9.8, 2);
    box('structure', 'concrete', 16, 5, 10, 31, .5, -2);                                // control building
    box('structure', 'concrete', 16.6, .4, 10.6, 31, 5.5, -2);
    for (let x = 25; x <= 37; x += 3) box('structure', 'glass', 1.6, 1.4, .1, x, 2.2, 3.05);
    for (const x of [-21, -7, 7, 21]) box('structure', 'concrete', .5, 8, 8, x, .5, 23); // transformer fire walls
    for (const x of [-18, -6, 6, 18]) {                                                // line gantry
        box('structure', 'steel', .7, 16, .7, x, .4, -24);
        rod('structure', 'steel', .08, [x - .35, .4, -23.65], [x + .35, 16, -23.65], 4);
    }
    box('structure', 'steel', 37, .8, .8, 0, 15.2, -24);

    // ── equipment: GIS bays on a double busbar, transformers, cabinets ────
    const BAYS = 8, PITCH = 4.6, X0 = -(BAYS - 1) * PITCH / 2, BUS_Z = -4.5, BUS_Y = [2.3, 3.7];
    const kind = ['line', 'line', 'line', 'xfmr', 'xfmr', 'xfmr', 'coupler', 'measure'];
    for (const y of BUS_Y) {
        rod('equipment', 'gis', .42, [X0 - 2.6, Y0 + y, BUS_Z], [X0 + (BAYS - 1) * PITCH + 2.6, Y0 + y, BUS_Z], 20);
        for (let i = 0; i <= BAYS; i++) ring('equipment', 'gis', .5, .07, X0 - PITCH / 2 + i * PITCH, Y0 + y, BUS_Z, 'x');
    }
    box('equipment', 'steel', (BAYS + 1) * PITCH, .25, 2.4, 0, Y0, BUS_Z);            // bus support beam
    const bayX = i => X0 + i * PITCH;
    for (let i = 0; i < BAYS; i++) {
        const x = bayX(i), k = kind[i];
        for (const y of BUS_Y) {                                    // bus disconnector modules
            rod('equipment', 'gis', .3, [x, Y0 + y, BUS_Z + .4], [x, Y0 + y, -1.4], 16);
            sphere('equipment', 'gis', .45, x, Y0 + y, -1.6);
            box('equipment', 'cabinet', .5, .5, .4, x + .5, Y0 + y - .25, -2.4);       // drive
        }
        if (k === 'measure') {                                      // voltage transformers on both bars
            for (const y of BUS_Y) { rod('equipment', 'gis', .32, [x + 1.2, Y0 + y, BUS_Z], [x + 1.2, Y0 + y + 1.4, BUS_Z], 14); sphere('equipment', 'gis', .32, x + 1.2, Y0 + y + 1.4, BUS_Z, .7); }
            continue;
        }
        rod('equipment', 'gis', .62, [x, Y0 + .6, -.6], [x, Y0 + 4.6, -.6], 22);        // circuit breaker
        sphere('equipment', 'gis', .62, x, Y0 + 4.6, -.6, .55);
        box('equipment', 'cabinet', 1.4, 1.6, .9, x, Y0, -.6);                         // operating mechanism
        ring('equipment', 'gis', .7, .07, x, Y0 + 2.2, -.6, 'y'); ring('equipment', 'gis', .7, .07, x, Y0 + 3.9, -.6, 'y');
        box('equipment', 'cabinet', 1, 2.3, .7, x, Y0, 3.4);                           // local control cubicle
        if (k === 'coupler') { rod('equipment', 'gis', .3, [x, Y0 + 2.3, -1.6], [x, Y0 + 3.7, -1.6]); continue; }
        rod('equipment', 'gis', .32, [x, Y0 + 3, -.6], [x, Y0 + 3, 1.6], 16);           // to the outgoing module
        sphere('equipment', 'gis', .42, x, Y0 + 3, 1.7);
        box('equipment', 'cabinet', .9, .9, .9, x, Y0 + 3.6, 1.7);                     // CT / earthing switch
    }
    // line bays: a duct up through the roof
    for (const i of [0, 1, 2]) rod('equipment', 'gis', .32, [bayX(i), Y0 + 3, 1.7], [bayX(i), Y0 + HH + .3, 1.7], 16);
    // transformer bays: gas-insulated ducts out through the front wall to each transformer
    const TX = [-14, 0, 14];
    [3, 4, 5].forEach((b, j) => {
        const x = bayX(b), tx = TX[j], y = Y0 + 2.4;
        rod('equipment', 'gis', .32, [x, Y0 + 3, 1.7], [x, y, 1.7]);
        rod('equipment', 'gis', .32, [x, y, 1.7], [x, y, 13], 16);
        rod('equipment', 'gis', .32, [x, y, 13], [tx, y, 13], 16);
        rod('equipment', 'gis', .32, [tx, y, 13], [tx, y, 19.2], 16);
        for (const z of [5, 9, 13]) box('equipment', 'steel', .3, y - .4, .3, x, 0, z);
        // the power transformer
        box('equipment', 'xfmr', 6.4, 4.2, 3.6, tx, .5, 23);
        box('equipment', 'xfmr', 6.8, .3, 4, tx, 4.7, 23);
        for (const s of [-1, 1]) for (let f = -2.6; f <= 2.6; f += .52)        // radiators both long sides
            box('equipment', 'xfmr', .1, 3.2, 1.3, tx + f, .9, 23 + s * 2.55);
        rod('equipment', 'xfmrRound', .5, [tx - 2.6, 6.4, 21.6], [tx + 2.6, 6.4, 21.6], 16);   // conservator
        box('equipment', 'steel', .2, 1.4, .2, tx - 2, 5, 21.6); box('equipment', 'steel', .2, 1.4, .2, tx + 2, 5, 21.6);
        box('equipment', 'cabinet', 1, 1.8, .6, tx + 3.6, .5, 24.5);                          // OLTC / marshalling
    });
    box('equipment', 'cabinet', 12, 2.2, .8, 31, .5, -5.5);                                     // protection panels (seen through windows)

    // ── insulators: roof bushings for the lines, transformer bushings ────
    for (const i of [0, 1, 2]) bushing(bayX(i), Y0 + HH + .3, 1.7, 5.2, .34);
    TX.forEach(tx => {
        for (const dx of [-1.6, 0, 1.6]) bushing(tx + dx, 4.95, 22.6, 2.8, .2);     // HV (161kV)
        for (const dx of [-1.6, 0, 1.6]) bushing(tx + dx, 4.95, 24.2, 1.1, .12);    // MV
    });
    for (const x of [-12, 0, 12]) for (const d of [-1.2, 0, 1.2]) bushing(x + d, 14.4, -24, 1.1, .14);   // gantry insulator strings

    // ── conductors: roof bushing → gantry → out of the station; MV cables ──
    [0, 1, 2].forEach((i, j) => {
        const x = bayX(i), gx = [-12, 0, 12][j], top = Y0 + HH + .3 + 5.2;
        for (const d of [-1.2, 0, 1.2]) {
            wire([x, top, 1.7], [gx + d, 14.4, -24], 1.4);
            wire([gx + d, 14.4, -24], [gx * 1.6 + d * 2, 11, -60], 3.2);
        }
    });
    TX.forEach(tx => {
        for (const dx of [-1.6, 0, 1.6]) {
            rod('conductors', 'wire', .07, [tx + dx, 6.05, 24.2], [tx + dx, 6.6, 24.2], 6);
            rod('conductors', 'wire', .07, [tx + dx, 6.6, 24.2], [tx + dx, 6.6, 27.5], 6);
            rod('conductors', 'wire', .07, [tx + dx, 6.6, 27.5], [tx + dx, 0, 27.5], 6);   // MV cables down to the trench
        }
    });
    box('conductors', 'concrete', 30, .25, 1, 0, 0, 28);            // cable trench cover
    box('conductors', 'concrete', 1, .25, 26, 15, 0, 15);

    return finish();
}
