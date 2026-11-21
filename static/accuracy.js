// "LTA vs actual" readout for /map: how far off was LTA's arrival estimate
// for buses that reached the stop while this page was open?
//
// Nothing is stored. Records live in the tab and are gone when it closes —
// the app stays stateless, at the price of measuring a sample rather than a
// statistic. See README "Map Page → LTA vs actual".
//
// How a bus is judged:
// 1. When a pin first appears, note LTA's predicted arrival and the time.
//    That first prediction is the one being tested: it is what you would have
//    planned your walk to the stop around.
// 2. The first GPS fix within ARRIVED_M of the stop counts as the arrival.
// 3. error = arrival - prediction. Positive = the bus was later than LTA said.
//
// Known slop, roughly +/-30s: a fix can be up to ~20s old when it reaches us,
// and "within 100 m" is a little before the pole. Hence results are shown in
// whole minutes, and anything under half a minute reads "on time".
//
// Relies on map.html keeping one marker per physical bus across polls (the
// position matching in syncMarkers). Pure logic only — testable from Node via
// scripts/test_accuracy.js.

var Accuracy = (function () {
  // A fix this close counts as "at the stop". Wider than glide.js's 80 m hold
  // so a bus doing 40 km/h is less likely to slip past between two fixes.
  var ARRIVED_M = 100;

  // A prediction made under a minute out is not a test of anything — the bus
  // was already there. Skipped rather than recorded as a flattering "on time".
  var MIN_LEAD_MS = 60 * 1000;

  /** Starts watching a newly seen bus. predictedMs and nowMs are epoch ms. */
  function watch(predictedMs, nowMs) {
    return { predicted: predictedMs, firstSeen: nowMs, done: false };
  }

  /**
   * Called on every fresh fix. Returns a record the first time the bus is
   * within ARRIVED_M of the stop, otherwise null. Marks the watch done either
   * way once it arrives, so one bus is never counted twice.
   */
  function check(w, distanceM, nowMs) {
    if (!w || w.done || distanceM > ARRIVED_M) return null;
    w.done = true;
    var lead = w.predicted - w.firstSeen;
    if (lead < MIN_LEAD_MS) return null;
    return {
      predicted: w.predicted,
      arrived: nowMs,
      errorS: Math.round((nowMs - w.predicted) / 1000),
      leadS: Math.round(lead / 1000),
    };
  }

  /** "2 min late", "1 min early" or "on time" for an error in seconds. */
  function describe(errorS) {
    var minutes = Math.round(Math.abs(errorS) / 60);
    if (minutes === 0) return "on time";
    return minutes + " min " + (errorS > 0 ? "late" : "early");
  }

  /** Count, mean error and share within a minute, or null with no records. */
  function summarize(records) {
    if (!records.length) return null;
    var total = 0;
    var close = 0;
    for (var i = 0; i < records.length; i++) {
      total += records[i].errorS;
      if (Math.abs(records[i].errorS) <= 60) close++;
    }
    return { count: records.length, meanErrorS: Math.round(total / records.length), withinMinute: close };
  }

  return {
    ARRIVED_M: ARRIVED_M,
    MIN_LEAD_MS: MIN_LEAD_MS,
    watch: watch,
    check: check,
    describe: describe,
    summarize: summarize,
  };
})();

// Lets scripts/test_accuracy.js require() this file; browsers ignore it.
if (typeof module !== "undefined") module.exports = Accuracy;
