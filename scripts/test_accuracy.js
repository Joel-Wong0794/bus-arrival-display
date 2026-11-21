// Self-check for static/accuracy.js. Run: node scripts/test_accuracy.js
const assert = require("node:assert/strict");
const Accuracy = require("../static/accuracy.js");

const T0 = 1_000_000;
const min = (m) => T0 + m * 60_000;

// Seen 5 min out, predicted at T0+5min, reaches the stop 2 min late.
let w = Accuracy.watch(min(5), T0);
assert.equal(Accuracy.check(w, 800, min(3)), null); // still far off
const rec = Accuracy.check(w, 60, min(7));
assert.deepEqual(rec, { predicted: min(5), arrived: min(7), errorS: 120, leadS: 300 });

// Counted once only, even if the next fix is still at the stop.
assert.equal(Accuracy.check(w, 20, min(7.3)), null);

// Already arriving when first seen: not a test, so no record.
w = Accuracy.watch(T0 + 30_000, T0);
assert.equal(Accuracy.check(w, 50, T0 + 40_000), null);
assert.equal(w.done, true);

// Boundary: exactly ARRIVED_M counts.
w = Accuracy.watch(min(3), T0);
assert.ok(Accuracy.check(w, Accuracy.ARRIVED_M, min(3)));

// Wording: positive = late, under half a minute = on time.
assert.equal(Accuracy.describe(120), "2 min late");
assert.equal(Accuracy.describe(-70), "1 min early");
assert.equal(Accuracy.describe(25), "on time");

// Summary.
assert.equal(Accuracy.summarize([]), null);
assert.deepEqual(
  Accuracy.summarize([{ errorS: 120 }, { errorS: -30 }, { errorS: 0 }]),
  { count: 3, meanErrorS: 30, withinMinute: 2 }
);

console.log("accuracy.js: all checks passed");
