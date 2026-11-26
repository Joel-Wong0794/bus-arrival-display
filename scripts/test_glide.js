// Self-check for static/glide.js. Run: node scripts/test_glide.js
// No framework on purpose — the app has no JS toolchain, and plain asserts are
// enough to pin down the pacing rules.
const assert = require("node:assert/strict");
const Glide = require("../static/glide.js");

const T0 = 1_000_000; // arbitrary epoch ms
const s = (seconds) => T0 + seconds * 1000;

// Paced by LTA: 600m to go in 120s -> 5 m/s -> 300m after 60s.
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(60)), 300);

// Nothing travelled before the fix was anchored.
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(-5)), 0);

// Holds 80m short of the stop, even once the ETA has passed.
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(500)), 600 - Glide.HOLD_M);

// A fix already inside 80m is the bus pulling in: let it reach the stop.
assert.equal(Glide.shadowTravelled(50, T0, s(10), s(60)), 50);

// "Arr" for a bus 1km out: capped at 60 km/h, not teleported.
const capped = Glide.shadowTravelled(1000, T0, s(0), s(10));
assert.ok(Math.abs(capped - Glide.MAX_SPEED_MPS * 10) < 1e-9, `got ${capped}`);

// Fresh-fix rules: small gaps glide, big ones jump.
assert.equal(Glide.onNewFix(100), "ease");
assert.equal(Glide.onNewFix(-100), "ease");
assert.equal(Glide.onNewFix(-700), "snap"); // e.g. rank shift to the next bus
assert.equal(Glide.onNewFix(2000), "snap"); // e.g. phone slept through polls

// pointAt walks a [lat, lon, metres] path: one point behind the fix, the fix
// at 0, then two stops. Halfway along each leg lands halfway between points.
const path = [[0, 0, -100], [1, 0, 0], [1, 2, 200], [3, 2, 300]];
assert.deepEqual(Glide.pointAt(path, -50), [0.5, 0]); // pin lagging the fix
assert.deepEqual(Glide.pointAt(path, 100), [1, 1]);
assert.deepEqual(Glide.pointAt(path, 250), [2, 2]);
assert.deepEqual(Glide.pointAt(path, 999), [3, 2]); // clamped: never past the stop
assert.deepEqual(Glide.pointAt(path, -999), [0, 0]); // or before the path
// Two points at the same distance (LTA rounds to 0.1 km) must not divide by 0.
assert.deepEqual(Glide.pointAt([[0, 0, 0], [5, 5, 0], [6, 6, 10]], 0), [0, 0]);

// Catch-up: a small gap eases, a big one is capped at 90 km/h, and a shadow
// behind the pin (negative gap) never moves it.
assert.equal(Glide.easeStep(8, 250), 1); // 8 m * 0.125
assert.equal(Glide.easeStep(300, 250), Glide.CATCHUP_MAX_MPS * 0.25); // 6.25 m, not 37.5
assert.equal(Glide.easeStep(-40, 250), 0);

// Easing never overshoots and never goes backwards.
assert.equal(Glide.easeFactor(250), 0.125);
assert.equal(Glide.easeFactor(10_000), 1);
assert.equal(Glide.easeFactor(-50), 0);

// Fix age: a fix is already old when LTA publishes it, and older again by the
// time a poll picks it up (on average half a poll interval later).
assert.equal(Glide.fixAgeS(20), Glide.LTA_LAG_S + 10);
assert.equal(Glide.fixAgeS(10), Glide.LTA_LAG_S + 5);
assert.equal(Glide.fixAgeS(NaN), Glide.LTA_LAG_S); // no interval known: LTA's part only
assert.equal(Glide.fixAgeS(0), Glide.LTA_LAG_S);
assert.equal(Glide.fixAgeS(-20), Glide.LTA_LAG_S);
// A slow poll (REFRESH_SECONDS=300) must not push every pin minutes ahead on a guess.
assert.equal(Glide.fixAgeS(300), Glide.MAX_FIX_AGE_S);

// Anchoring at the fix's estimated time, not the poll's, means the shadow has
// already driven on by the moment the fix is seen: 600 m in 120 s from the
// fix is 5 m/s, so a 15 s old fix puts the bus 75 m on — not back at the fix.
const seenAt = s(15);
assert.equal(Glide.shadowTravelled(600, seenAt - 15_000, s(120), seenAt), 75);

console.log("glide.js: all checks passed");
