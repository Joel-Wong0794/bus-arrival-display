// Self-check for static/favourites.js. Run: node scripts/test_favourites.js
const assert = require("node:assert/strict");
const Favourites = require("../static/favourites.js");

const a = { code: "11111", name: "Alpha" };
const b = { code: "22222", name: "Beta" };

// Unreadable or hand-edited storage is treated as nothing saved.
assert.deepEqual(Favourites.parse(null), []);
assert.deepEqual(Favourites.parse("not json"), []);
assert.deepEqual(Favourites.parse('{"code":"11111"}'), []);

// Bad codes and repeats are dropped; a missing name falls back to the code.
assert.deepEqual(
  Favourites.parse(JSON.stringify([a, { code: "abc" }, a, { code: "22222" }])),
  [a, { code: "22222", name: "22222" }]
);

// Capped at MAX, in saved order.
const many = [];
for (let i = 0; i < 10; i++) many.push({ code: String(10000 + i), name: "S" + i });
assert.equal(Favourites.parse(JSON.stringify(many)).length, Favourites.MAX);

// Toggle adds, then removes.
let r = Favourites.toggle([a], b);
assert.deepEqual(r, { list: [a, b], saved: true });
r = Favourites.toggle(r.list, a);
assert.deepEqual(r, { list: [b], saved: false });

// A full list refuses a new stop but can still remove one.
const full = many.slice(0, Favourites.MAX);
r = Favourites.toggle(full, a);
assert.equal(r.full, true);
assert.equal(r.list, full);
r = Favourites.toggle(full, full[0]);
assert.equal(r.saved, false);
assert.equal(r.list.length, Favourites.MAX - 1);

assert.equal(Favourites.url([a, b]), "/map?stops=11111,22222");

console.log("favourites.js: all checks passed");
