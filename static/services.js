/* Per-device hiding of bus services, shared by the dashboard and the map.
 *
 * The list lives in localStorage, so the kiosk and a phone each keep their own
 * and the app stays stateless — nothing to store server-side, nothing to
 * configure, and a redeploy can't wipe it.
 *
 * Keyed by stop AND service, because the same number serves two of our stops:
 * hiding 334 at Lakeside must not also hide it at the home stop.
 *
 * ES5 on purpose — the kiosk is an old Android WebView. No const/let, no arrow
 * functions, no NodeList.forEach, no classList.
 */
(function (global) {
  var KEY = "hiddenServices";

  function load() {
    try {
      var raw = global.localStorage.getItem(KEY);
      if (!raw) return {};
      var list = JSON.parse(raw);
      var set = {};
      for (var i = 0; i < list.length; i++) set[list[i]] = true;
      return set;
    } catch (e) {
      // Private mode, cleared site data, or a WebView that throws on access.
      // An empty set means "show everything", which is the safe direction.
      return {};
    }
  }

  function save(set) {
    try {
      var list = [];
      for (var key in set) if (set[key]) list.push(key);
      global.localStorage.setItem(KEY, JSON.stringify(list));
    } catch (e) {
      // Nothing to do — the page still works, the choice just won't persist.
    }
  }

  var hidden = load();

  function id(stop, service) {
    return stop + "|" + service;
  }

  global.Services = {
    editing: false,

    isHidden: function (stop, service) {
      return hidden[id(stop, service)] === true;
    },

    toggle: function (stop, service) {
      var key = id(stop, service);
      if (hidden[key]) delete hidden[key];
      else hidden[key] = true;
      save(hidden);
    },

    showAll: function () {
      hidden = {};
      save(hidden);
    },

    count: function () {
      var n = 0;
      for (var key in hidden) if (hidden[key]) n++;
      return n;
    }
  };
})(window);
