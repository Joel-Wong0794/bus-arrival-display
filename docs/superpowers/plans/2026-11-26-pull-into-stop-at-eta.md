# Pull Into Stop at ETA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once LTA's unbuffered arrival time has passed, let the pin drive the last 80 m onto the watched stop instead of waiting for a GPS fix.

**Architecture:** One rule change inside the pure function `Glide.shadowTravelled` in `static/glide.js`: the 80 m hold applies only while `now < tA`. The tick loop in `templates/map.html` already eases the pin after the shadow (≤ 90 km/h), so no page code changes.

**Tech Stack:** Plain browser JS (no build), Node `node:assert` self-checks.

**Spec:** `docs/superpowers/specs/2026-11-26-pull-into-stop-at-eta-design.md`

## Global Constraints

- Trigger is LTA's unbuffered arrival time (`tA`, from `data-arrival`), never the tile's buffered "Arr".
- 60 km/h shadow ceiling (`MAX_SPEED_MPS`) still applies after release.
- Branch from `main` (`feature/pull-into-stop`), separate from PR #20.

## Review Focus

- ETA already due when the fix is anchored (`tA <= t0`): shadow drives to the stop, capped at 60 km/h — not instantly. Pinned in Task 1 tests.
- Exactly at `now === tA`: hold released (boundary is `now < tA` holds). Pinned in Task 1.
- Fix already inside 80 m before `tA`: still allowed to reach the stop (existing rule kept). Existing test kept.
- ETA revised later after pull-in: hold re-applies but pin never reverses — handled by the tick loop's existing `easeStep` (returns 0 for negative gaps); covered by the existing `easeStep(-40, 250) === 0` check.
- Far bus with "Arr" ETA: travels at most `MAX_SPEED_MPS * elapsed`, so no fling. Existing capped test kept.

---

### Task 1: Release the hold at the arrival time

**Files:**
- Modify: `static/glide.js` (`HOLD_M` comment ~line 27-30, `shadowTravelled` doc + body ~line 70-84)
- Test: `scripts/test_glide.js:16-17`
- Modify: `README.md` rules table row "Hold short of the stop"
- Modify: `docs/glide-logic.excalidraw` (regenerate; tick-lane box text "up to 80 m short of the stop" → "80 m short until LTA's arrival time")

**Interfaces:**
- Consumes: nothing new.
- Produces: `Glide.shadowTravelled(d0, t0, tA, now) -> metres` — same signature; hold now conditional on `now < tA`.

- [ ] **Step 1: Write the failing tests** — replace the "Holds 80m short … even once the ETA has passed" check in `scripts/test_glide.js` with:

```js
// Before LTA's arrival time: hold 80 m short of the stop.
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(119)), 600 - Glide.HOLD_M);
// From LTA's arrival time on, the bus is assumed in: drive onto the stop.
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(120)), 600);
assert.equal(Glide.shadowTravelled(600, T0, s(120), s(500)), 600);
// ETA already due at the fix: reaches the stop, but only at 60 km/h.
assert.equal(Glide.shadowTravelled(1000, T0, s(-5), s(10)), Glide.MAX_SPEED_MPS * 10);
assert.equal(Glide.shadowTravelled(1000, T0, s(-5), s(100)), 1000);
```

- [ ] **Step 2: Run** `node scripts/test_glide.js` — Expected: FAIL (AssertionError, 520 !== 600).

- [ ] **Step 3: Implement** in `shadowTravelled`: hold is `HOLD_M` only when `d0 > HOLD_M && now < tA`, else 0. Update the `HOLD_M` comment and the function doc to say the hold lifts at LTA's arrival time.

- [ ] **Step 4: Run** all checks: `for f in scripts/test_*.js; do node $f; done` and each `python scripts/test_*.py` — Expected: every script prints "all checks passed".

- [ ] **Step 5: Docs** — README row: `| Hold short of the stop | 80 m, until LTA's arrival time | Never "arrive" on a guess early; once LTA says the bus is due, it pulls in |`. Regenerate the diagram with the updated tick-lane text.

- [ ] **Step 6: Commit, push, open PR** against `main`, linking the spec; test plan includes a production field test.
