// Pin interpolation for /map — phase 1 of the "shadow bus" idea described at
// https://nextstopsg.app/interpolation.html, cut down to what this app's data
// supports.
//
// The problem: DataMall publishes a GPS fix roughly every 20s, so a pin that
// only moves on each fix sits still, then teleports. The idea: LTA also says
// *when* the bus reaches the stop. That arrival time sets a pace, and an
// invisible "shadow bus" drives from the last fix towards the stop at that
// pace. The visible pin eases after the shadow, so it moves continuously and
// arrives roughly when the countdown says it will.
//
// What this version does NOT have (see README "Map Page → Gliding pins"):
// - No route geometry. The shadow drives a straight line from fix to stop, so
//   on a winding road the pin cuts corners. Phase 2 swaps in the route's stop
//   sequence.
// - No per-stretch speed map, so no 2.5x pace cap. A flat 60 km/h ceiling
//   covers the case that matters (LTA saying "Arr" for a bus 1 km out).
//
// Pure maths only: no Leaflet, no DOM. That keeps it testable from Node
// (scripts/test_glide.js) and keeps map.html responsible for drawing.

var Glide = (function () {
  // Stay this far short of the stop until a real fix says the bus is closer.
  // Without it the pin would "arrive" on a guess while the bus is still a
  // junction away — running ahead of LTA is worse than lagging behind it.
  var HOLD_M = 80;

  // Ceiling on shadow speed: 60 km/h, the top of normal bus driving here.
  // Stops an overdue or "Arr" ETA from flinging the pin across the map.
  var MAX_SPEED_MPS = 60 / 3.6;

  // When a fresh fix lands this far off the pin, glide no further: jump.
  // Behind by 600m+ means the shadow guessed badly (traffic, a long dwell) —
  // or, more often, it is a different vehicle after the rank shift. Ahead by
  // 1.5km+ means the phone slept through several polls.
  var SNAP_BEHIND_M = 600;
  var SNAP_AHEAD_M = 1500;

  // Seconds the visible pin takes to close most of its gap to the shadow, so a
  // corrected fix is a quick slide rather than a jerk.
  var CATCHUP_S = 2;

  /**
   * Metres the shadow has driven from its fix by time `now`.
   *
   * d0  metres from the fix to the stop (straight line)
   * t0  epoch ms when the fix was anchored
   * tA  epoch ms LTA says the bus reaches the stop (unbuffered)
   * now epoch ms
   *
   * Pace is LTA's: cover d0 in (tA - t0). Then two limits: never faster than
   * MAX_SPEED_MPS, and never into the last HOLD_M — unless the fix already
   * was inside HOLD_M, in which case the bus really is pulling in.
   */
  function shadowTravelled(d0, t0, tA, now) {
    var hold = d0 > HOLD_M ? HOLD_M : 0;
    var room = Math.max(0, d0 - hold);
    var elapsedS = Math.max(0, now - t0) / 1000;
    var budgetS = (tA - t0) / 1000;
    // An ETA already due means "go now" — the speed cap decides how fast.
    var paced = budgetS > 0 ? (d0 * elapsedS) / budgetS : Infinity;
    return Math.min(paced, MAX_SPEED_MPS * elapsedS, room);
  }

  /**
   * What to do with the visible pin when a fresh fix arrives.
   *
   * gap = (pin's distance to stop) - (fix's distance to stop).
   * Positive: the fix is ahead of the pin. Negative: behind it.
   * Returns "snap" (jump to the fix) or "ease" (keep gliding; the tick loop
   * catches up forwards and holds still rather than reversing).
   */
  function onNewFix(gap) {
    if (gap > SNAP_AHEAD_M || gap < -SNAP_BEHIND_M) return "snap";
    return "ease";
  }

  /** Share of the pin-to-shadow gap to close in a tick lasting dtMs. */
  function easeFactor(dtMs) {
    return Math.min(1, Math.max(0, dtMs) / 1000 / CATCHUP_S);
  }

  return {
    HOLD_M: HOLD_M,
    MAX_SPEED_MPS: MAX_SPEED_MPS,
    SNAP_BEHIND_M: SNAP_BEHIND_M,
    SNAP_AHEAD_M: SNAP_AHEAD_M,
    shadowTravelled: shadowTravelled,
    onNewFix: onNewFix,
    easeFactor: easeFactor,
  };
})();

// Lets scripts/test_glide.js require() this file; browsers ignore it.
if (typeof module !== "undefined") module.exports = Glide;
