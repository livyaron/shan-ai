/* A 161kV mobile substation, drawn in code for the visual report's digital twin
 * (user decision 2026-10-24). Illustration only — not any real unit.
 *
 * A typical two-trailer arrangement (HV section at the back — the practical
 * layout for these units): trailer A carries a compact 161kV gas-insulated
 * breaker module with SF6/air bushings and the power transformer (radiators,
 * fans, conservator, HV/MV bushings, surge arresters), coupled to its tractor;
 * trailer B carries the MV switchgear and protection container. They stand on
 * outrigger jacks on a temporary gravel pad, fed from a dead-end portal; MV
 * cables run on the ground to the container and out to the network.
 *
 * Same five layers as the other stations (project_visual.TWIN_LAYERS):
 * site = pad, fence, jack pads · structure = portal, cable supports, lights ·
 * equipment = the trailers and everything on them · insulators = bushings and
 * arresters · conductors = the line, jumpers and cables. Units: metres, Y up. */
import { kit } from './station_kit.js';

export function buildMobile(T, mergeGeometries) {
    const { box, rod, sphere, bushing, wire, finish } = kit(T, mergeGeometries);

    // ── site: temporary pad, fence, jack pads, access road ────────────────
    box('site', 'gravel', 64, .3, 44, 0, -.3, 0);
    box('site', 'asphalt', 64, .05, 6, 0, 0, 25);
    for (let x = -31; x <= 31; x += 4) { box('site', 'steel', .1, 2.2, .1, x, 0, -21); box('site', 'steel', .1, 2.2, .1, x, 0, 21); }
    for (let z = -21; z <= 21; z += 4) { box('site', 'steel', .1, 2.2, .1, -31, 0, z); box('site', 'steel', .1, 2.2, .1, 31, 0, z); }
    for (const y of [.7, 2.1]) {
        box('site', 'steel', 62, .05, .05, 0, y, -21); box('site', 'steel', 62, .05, .05, 0, y, 21);
        box('site', 'steel', .05, .05, 42, -31, y, 0); box('site', 'steel', .05, .05, 42, 31, y, 0);
    }
    for (const [x, z] of [[-15, -1.9], [-15, 1.9], [-4, -1.9], [-4, 1.9], [-10, 6.6], [-10, 10.4], [0, 6.6], [0, 10.4]])
        box('site', 'concrete', 1.2, .25, 1.2, x, 0, z);                                     // jack pads
    box('site', 'concrete', 3, .5, 3, -25, 0, -4); box('site', 'concrete', 3, .5, 3, -25, 0, 4);   // portal footings

    // ── structure: dead-end portal, cable supports, flood lights ──────────
    for (const z of [-4, 4]) {
        box('structure', 'steel', .8, 14, .8, -25, .5, z);
        for (let y = 2; y < 14; y += 2) rod('structure', 'steel', .05, [-25.4, y, z - .4], [-24.6, y + 2, z + .4], 4);
    }
    box('structure', 'steel', .9, .9, 9.6, -25, 13.6, 0);
    for (let x = -1; x <= 26; x += 4.5) box('structure', 'steel', .15, .8, 1.2, x, 0, 4.2);          // cable stands
    for (const [x, z] of [[-29, -19], [29, -19], [-29, 19], [29, 19]]) {
        box('structure', 'steel', .25, 10, .25, x, 0, z);
        box('structure', 'cabinet', 1.2, .5, .6, x, 10, z);
    }

    // ── equipment: trailer A (HV + transformer + tractor), trailer B (MV container) ──
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
    // trailer A, low bed + gooseneck, coupled to its tractor
    trailer(-16, -1, 0, DECK);
    box('equipment', 'chassis', 3.5, .4, 2.5, .6, 1.6, 0);                                          // gooseneck
    box('equipment', 'cab', 2.4, 2.6, 2.5, 4.2, 1.2, 0);                                            // tractor cab
    box('equipment', 'glass', .1, 1, 2.2, 5.45, 2.6, 0);
    box('equipment', 'chassis', 6, .5, 2.2, 3.3, .7, 0);
    for (const x of [1.4, 2.8, 4.9]) for (const s of [-1, 1]) rod('equipment', 'tyre', .5, [x, .5, s * .75], [x, .5, s * 1.3], 18);
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
        rod('conductors', 'wire', .07, [2, .6, 8.5 + z], [26, .6, 8.5 + z], 6);                       // feeders out to the network
    }
    return finish();
}
