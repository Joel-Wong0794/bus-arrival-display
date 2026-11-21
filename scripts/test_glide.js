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

// Easing never overshoots and never goes backwards.
assert.equal(Glide.easeFactor(250), 0.125);
assert.equal(Glide.easeFactor(10_000), 1);
assert.equal(Glide.easeFactor(-50), 0);

console.log("glide.js: all checks passed");
