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
// The shadow drives the bus's route, not a straight line: the server sends
// the stops ahead with LTA's road distances (route_path in api/index.py), and
// every distance here is road metres along that path. pointAt() turns metres
// back into a map position. With no route, the path is just fix -> stop.
//
// Still simpler than Next Stop (see README "Map Page → Gliding pins"):
// - Stop-to-stop chords, not a detailed road line, so the pin still trims
//   bends between two stops.
// - No 2.5x pace cap. A flat 60 km/h ceiling covers the case that matters
//   (LTA saying "Arr" for a bus 1 km out).
//
// Pure maths only: no Leaflet, no DOM. That keeps it testable from Node
// (scripts/test_glide.js) and keeps map.html responsible for drawing.

var Glide = (function () {
  // Stay this far short of the stop until a real fix says the bus is closer,
  // or until LTA's own arrival time comes. Without it the pin would "arrive"
  // on a guess while the bus is still a junction away. But fixes come only
  // every 12-60 s, so holding until one lands left pins parked short while
  // the real bus pulled in (field test, 26 Nov 2026): once LTA says the bus
  // is due, it is assumed in.
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

  // A fix is old before we see it. Measured on DataMall (Nov 2026), a bus's
  // position changes every 12-37 s, occasionally ~60 s, and the page only
  // polls every REFRESH_SECONDS. Treating the fix as "now" started every
  // shadow from where the bus *was*, so pins ran 150-300 m behind the real
  // bus. LTA_LAG_S is how stale a fix already is when LTA publishes it: an
  // estimate, not a measured figure — tune it against buses on the ground.
  // Field test, 26 Nov 2026: at 5 s, pins on production still trailed the
  // bus, so raised to 10 (20 s of shift at 20 s polls).
  var LTA_LAG_S = 10;

  // Ceiling on the age estimate. A slow poll (REFRESH_SECONDS set high) would
  // otherwise start every shadow minutes back and draw pins far ahead of any
  // fix. 30 s is half the slowest gap seen between DataMall fixes (~60 s).
  var MAX_FIX_AGE_S = 30;

  /**
   * Estimated age, in seconds, of a fix first seen on a poll. LTA's own lag,
   * plus half a poll interval: the fix landed at some point since the last
   * poll, on average halfway through. A missing or nonsense interval counts
   * only LTA's part; the total never exceeds MAX_FIX_AGE_S.
   */
  function fixAgeS(refreshS) {
    var poll = refreshS > 0 ? refreshS / 2 : 0;
    return Math.min(MAX_FIX_AGE_S, LTA_LAG_S + poll);
  }

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
   * MAX_SPEED_MPS, and never into the last HOLD_M before tA — unless the fix
   * already was inside HOLD_M, in which case the bus really is pulling in.
   * From tA on the hold lifts and the shadow drives onto the stop.
   */
  function shadowTravelled(d0, t0, tA, now) {
    var hold = d0 > HOLD_M && now < tA ? HOLD_M : 0;
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

  /**
   * The [lat, lon] that lies `metres` along a path of [lat, lon, metres]
   * points (as built by route_path in api/index.py), interpolating between
   * the two points either side. Clamped to the path's ends, so a pin can never
   * be drawn off the route. Linear in lat/lon between neighbouring points:
   * exact enough over the few hundred metres between two stops.
   */
  function pointAt(path, metres) {
    if (metres <= path[0][2]) return [path[0][0], path[0][1]];
    for (var i = 1; i < path.length; i++) {
      var a = path[i - 1];
      var b = path[i];
      if (metres <= b[2]) {
        var f = b[2] === a[2] ? 1 : (metres - a[2]) / (b[2] - a[2]);
        return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      }
    }
    var last = path[path.length - 1];
    return [last[0], last[1]];
  }

  /** Share of the pin-to-shadow gap to close in a tick lasting dtMs. */
  function easeFactor(dtMs) {
    return Math.min(1, Math.max(0, dtMs) / 1000 / CATCHUP_S);
  }

  // Fastest a pin may close a gap to its shadow: 90 km/h, Next Stop's 1.5x
  // allowance over the 60 km/h cap. Only matters when a fresh fix lands well
  // ahead of the pin — without it a 300 m gap closed in ~2 s, a visible lurch.
  var CATCHUP_MAX_MPS = 1.5 * MAX_SPEED_MPS;

  /** Metres to move the pin forward this tick, for a gap of gapM metres. */
  function easeStep(gapM, dtMs) {
    if (gapM <= 0) return 0;
    return Math.min(gapM * easeFactor(dtMs), (CATCHUP_MAX_MPS * Math.max(0, dtMs)) / 1000);
  }

  return {
    HOLD_M: HOLD_M,
    LTA_LAG_S: LTA_LAG_S,
    MAX_FIX_AGE_S: MAX_FIX_AGE_S,
    fixAgeS: fixAgeS,
    MAX_SPEED_MPS: MAX_SPEED_MPS,
    SNAP_BEHIND_M: SNAP_BEHIND_M,
    SNAP_AHEAD_M: SNAP_AHEAD_M,
    shadowTravelled: shadowTravelled,
    onNewFix: onNewFix,
    pointAt: pointAt,
    easeFactor: easeFactor,
    easeStep: easeStep,
    CATCHUP_MAX_MPS: CATCHUP_MAX_MPS,
  };
})();

// Lets scripts/test_glide.js require() this file; browsers ignore it.
if (typeof module !== "undefined") module.exports = Glide;
