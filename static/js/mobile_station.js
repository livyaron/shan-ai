/* A 161kV mobile substation, drawn in code for the visual report's digital twin
 * (user decision 2026-10-24). Illustration only — not any real unit.
 *
 * A typical two-trailer arrangement (HV section at the back — the practical
 * layout for these units): trailer A carries a compact 161kV gas-insulated
 * breaker module with SF6/air bushings and the power transformer (radiators,
 * fans, conservator, HV/MV bushings, surge arresters);
 * trailer B carries the MV switchgear and protection container. No tractor
 * stays on site; both stand on outrigger jacks on a temporary gravel pad, fed from a dead-end portal; MV
 * cables run on the ground to the container and out to the network.
 *
 * Same five layers as the other stations (project_visual.TWIN_LAYERS):
 * site = pad, fence, jack pads · structure = portal, lights, the two
 * station-service poles ·
 * equipment = the trailers and everything on them · insulators = bushings and
 * arresters · conductors = the line, jumpers and cables. Units: metres, Y up. */
import { kit } from './station_kit.js';

export function buildMobile(T, mergeGeometries) {
    const { box, rod, sphere, bushing, wire, finish } = kit(T, mergeGeometries);

    // ── site: temporary pad, fence, jack pads, access road ────────────────
    // the pad is just big enough for the portal and the two trailers (user correction 2026-10-24)
    const X0 = -31, X1 = 9, Z0 = -12, Z1 = 16, CX = (X0 + X1) / 2, CZ = (Z0 + Z1) / 2;
    box('site', 'gravel', X1 - X0 + 2, .3, Z1 - Z0 + 2, CX, -.3, CZ);
    box('site', 'asphalt', X1 - X0 + 2, .05, 5, CX, 0, Z1 + 3.5);
    for (let x = X0; x <= X1; x += 4) { box('site', 'steel', .1, 2.2, .1, x, 0, Z0); box('site', 'steel', .1, 2.2, .1, x, 0, Z1); }
    for (let z = Z0; z <= Z1; z += 4) { box('site', 'steel', .1, 2.2, .1, X0, 0, z); box('site', 'steel', .1, 2.2, .1, X1, 0, z); }
    for (const y of [.7, 2.1]) {
        box('site', 'steel', X1 - X0, .05, .05, CX, y, Z0); box('site', 'steel', X1 - X0, .05, .05, CX, y, Z1);
        box('site', 'steel', .05, .05, Z1 - Z0, X0, y, CZ); box('site', 'steel', .05, .05, Z1 - Z0, X1, y, CZ);
    }
    for (const [x, z] of [[-15, -1.9], [-15, 1.9], [-4, -1.9], [-4, 1.9], [-10, 6.6], [-10, 10.4], [0, 6.6], [0, 10.4]])
        box('site', 'concrete', 1.2, .25, 1.2, x, 0, z);                                     // jack pads
    box('site', 'concrete', 3, .5, 3, -25, 0, -4); box('site', 'concrete', 3, .5, 3, -25, 0, 4);   // portal footings

    // ── structure: dead-end portal, flood lights ──────────────────────────
    for (const z of [-4, 4]) {
        box('structure', 'steel', .8, 14, .8, -25, .5, z);
        for (let y = 2; y < 14; y += 2) rod('structure', 'steel', .05, [-25.4, y, z - .4], [-24.6, y + 2, z + .4], 4);
    }
    box('structure', 'steel', .9, .9, 9.6, -25, 13.6, 0);
    for (const [x, z] of [[X0 + 1.5, Z0 + 1.5], [X1 - 1.5, Z0 + 1.5], [X0 + 1.5, Z1 - 1.5], [X1 - 1.5, Z1 - 1.5]]) {
        box('structure', 'steel', .25, 10, .25, x, 0, z);
        box('structure', 'cabinet', 1.2, .5, .6, x, 10, z);
    }

    // ── equipment: trailer A (HV + transformer), trailer B (MV container) ──
    const DECK = 1.3;
    const trailer = (x0, x1, z, deck) => {
        box('equipment', 'chassis', x1 - x0, .35, 2.6, (x0 + x1) / 2, deck - .35, z);               // deck
        box('equipment', 'chassis', x1 - x0, .5, .25, (x0 + x1) / 2, deck - .85, z - .9);           // main beams
        box('equipment', 'chassis', x1 - x0, .5, .25, (x0 + x1) / 2, deck - .85, z + .9);
        for (let a = 0; a < 3; a++) for (const s of [-1, 1]) {                                     // rear bogie
            const x = x0 + 1.4 + a * 1.35;
            rod('equipment', 'tyre', .5, [x, .5, z + s * .75], [x, .5, z + s * 1.3], 18);
        }
        for (const x of [x0 + .6, x1 - 1.5]) for (const s of [-1, 1]) {                           // outrigger jacks
            box('equipment', 'chassis', .35, deck - .3, .35, x, 0, z + s * 1.9);
            box('equipment', 'chassis', .3, .25, 1.6, x, deck - .6, z + s * 1.1);
        }
    };
    // trailer A, low bed + gooseneck, standing on its own legs — no tractor on site (user correction 2026-10-24)
    trailer(-16, -1, 0, DECK);
    box('equipment', 'chassis', 3.5, .4, 2.5, .6, 1.6, 0);                                          // gooseneck
    for (const s of [-1, 1]) box('equipment', 'chassis', .2, 1.6, .2, 1.6, 0, s * .9);                // landing legs
    // HV section at the back: a compact gas-insulated breaker module
    rod('equipment', 'gis', .6, [-15.6, DECK + .9, 0], [-11.4, DECK + .9, 0], 20);                  // breaker tank
    sphere('equipment', 'gis', .6, -15.6, DECK + .9, 0, 1); sphere('equipment', 'gis', .6, -11.4, DECK + .9, 0, 1);
    box('equipment', 'cabinet', 1.2, 1.4, 1, -13.5, DECK, 1.1);                               // drive / control box
    for (const z of [-.8, 0, .8]) rod('equipment', 'gis', .22, [-14.8, DECK + 1.3, z], [-14.8, DECK + 2.2, z], 14);
    box('equipment', 'chassis', 4.6, .3, 2.2, -13.5, DECK, 0);
    // the power transformer
    box('equipment', 'xfmr', 6, 3, 2.5, -6.5, DECK, 0);
    box('equipment', 'xfmr', 6.3, .25, 2.7, -6.5, DECK + 3, 0);
    for (const s of [-1, 1]) for (let f = -2.6; f <= 2.6; f += .45)                                // radiators
        box('equipment', 'xfmr', .09, 2.3, .55, -6.5 + f, DECK + .35, s * 1.55);
    for (const s of [-1, 1]) for (const f of [-2, 0, 2]) {                                          // cooling fans
        box('equipment', 'chassis', .9, .9, .05, -6.5 + f, DECK + .75, s * 1.85);
        rod('equipment', 'xfmrRound', .42, [-6.5 + f, DECK + 1.2, s * 1.86], [-6.5 + f, DECK + 1.2, s * 2.0], 18);
    }
    rod('equipment', 'xfmrRound', .35, [-9, DECK + 3.9, -.6], [-5, DECK + 3.9, -.6], 16);          // conservator
    box('equipment', 'steel', .15, .7, .15, -8.6, DECK + 3.2, -.6); box('equipment', 'steel', .15, .7, .15, -5.4, DECK + 3.2, -.6);
    box('equipment', 'cabinet', 1, 1.6, .5, -2.4, DECK, 1.05);                                     // marshalling / OLTC
    // trailer B: the MV switchgear and protection container
    trailer(-11, 2, 8.5, DECK);
    box('equipment', 'container', 12, 2.9, 2.5, -4.5, DECK, 8.5);
    for (const x of [-9, -6, -3, 0]) box('equipment', 'chassis', .05, 2.2, .9, x, DECK + .2, 9.78);     // doors
    for (const x of [-8, 0]) box('equipment', 'cabinet', 1.4, .8, .9, x, DECK + 2.9, 8.5);             // AC units
    box('equipment', 'chassis', 2, .25, 2.2, 2.6, 1.6, 8.5);                                         // kingpin plate
    for (const s of [-1, 1]) box('equipment', 'chassis', .2, 1.6, .2, 1.8, 0, 8.5 + s * .9);           // landing legs

    // ── insulators: SF6/air bushings, transformer bushings, surge arresters ──
    for (const z of [-.8, 0, .8]) bushing(-14.8, DECK + 2.2, z, 2.6, .2);                             // breaker → line
    for (const z of [-.75, 0, .75]) bushing(-8.6, DECK + 3.25, z, 1.9, .17);                         // HV 161kV
    for (const z of [-.6, 0, .6]) bushing(-4.2, DECK + 3.25, z, .7, .1);                              // MV
    for (const z of [-.75, 0, .75]) {                                                                // surge arresters
        rod('insulators', 'arrester', .14, [-10.4, DECK + 1.6, z], [-10.4, DECK + 3.6, z], 12);
        for (let y = DECK + 1.8; y < DECK + 3.5; y += .22) rod('insulators', 'arrester', .22, [-10.4, y, z], [-10.4, y + .05, z], 12);
    }
    box('insulators', 'chassis', .4, .3, 2, -10.4, DECK + 1.3, 0);
    for (const z of [-3, 0, 3]) bushing(-25, 12.1, z * 1.1, 1.4, .13);                                 // portal strings (hung, drawn standing)

    // ── conductors: line → portal → breaker bushings → transformer; MV cables ──
    const tops = [-.8, 0, .8];
    tops.forEach((z, i) => {
        const pz = [-3.3, 0, 3.3][i];
        wire([-60, 18, pz * 1.4], [-25, 13.5, pz], 2.5);                                             // incoming line
        wire([-25, 12.1, pz], [-14.8, DECK + 4.8, z], 1.2);                                          // drop to the breaker
        wire([-14.8, DECK + 4.8, z], [-8.6, DECK + 5.15, [-.75, 0, .75][i]], .5, .05);              // jumper to the transformer
        rod('conductors', 'wire', .04, [-10.4, DECK + 3.6, [-.75, 0, .75][i]], [-8.6, DECK + 4.6, [-.75, 0, .75][i]], 6);
    });
    for (const z of [-.6, 0, .6]) {                                                                  // MV cables to the container
        rod('conductors', 'wire', .07, [-4.2, DECK + 3.95, z], [-4.2, DECK + 4.3, z], 6);
        wire([-4.2, DECK + 4.3, z], [-1, .4, 4.2 + z], 1.4, .07);
        rod('conductors', 'wire', .07, [-1, .4, 4.2 + z], [-1, .4, 7.2], 6);
        rod('conductors', 'wire', .07, [2.4, .6, 8.5 + z], [X1 - .5, .6, 8.5 + z], 6);                // feeders out to the network
        rod('conductors', 'wire', .07, [X1 - .5, .6, 8.5 + z], [X1 - .5, -.3, 8.5 + z], 6);
    }
    // ── two poles with station-service (house) transformers (user request 2026-10-24) ──
    // fed in MV from the container through fuse cut-outs; the LV goes back to it
    for (const px of [-14, 4]) {
        const pz = 13.6;
        rod('structure', 'concrete', .2, [px, 0, pz], [px, 9.5, pz], 12);                          // pole
        box('structure', 'steel', .12, .12, 2.2, px, 8.6, pz);                                      // cross-arm
        box('structure', 'steel', .1, .1, 1, px, 5.9, pz - .5);                                     // transformer bracket
        rod('equipment', 'xfmrRound', .45, [px, 5.1, pz - .75], [px, 6.4, pz - .75], 18);           // pole-mounted transformer
        sphere('equipment', 'xfmrRound', .46, px, 6.4, pz - .75, .25);
        for (const a of [0, 1, 2, 3]) {                                                              // cooling fins
            const ang = a * Math.PI / 2 + Math.PI / 4;
            box('equipment', 'xfmr', .05, .9, .32, px + Math.cos(ang) * .5, 5.25, pz - .75 + Math.sin(ang) * .5);
        }
        for (const dz of [-.18, .18]) bushing(px + dz, 6.5, pz - .75, .45, .07);                     // MV bushings
        for (const dz of [-.8, 0, .8]) {                                                             // fuse cut-outs
            bushing(px, 8.72, pz + dz, .55, .06);
            rod('conductors', 'wire', .025, [px, 9.27, pz + dz], [px + (dz < 0 ? -.18 : .18), 6.95, pz - .75], 6);
        }
        rod('conductors', 'wire', .06, [px + .12, 0, pz + .25], [px + .12, 8.7, pz + .25], 6);       // MV riser cable
        rod('conductors', 'wire', .06, [px + .12, 0, pz + .25], [Math.max(Math.min(px, 1.5), -10.5), 0, 9.8], 6);   // to the container
        rod('conductors', 'wire', .045, [px - .15, 5.2, pz - .55], [px - .15, 0, pz - .2], 6);      // LV back down
    }
    return finish();
}
