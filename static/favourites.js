// Favourite stops, kept in this browser only (localStorage). No account, and
// nothing about them is stored on the server: the page sends the saved codes
// as ?stops= each time, and the server forgets them once it has answered.
//
// The list does not follow you to another device, and clearing site data or a
// private window starts it empty. For a bus map that is a fair trade for
// having no sign-in at all.
//
// Pure helpers (parse, toggle, url) take and return plain values so
// scripts/test_favourites.js can check them under Node without a browser.
(function (root) {
  var KEY = "favouriteStops";
  var MAX = 6; // matches MAX_FAVOURITES in api/index.py

  // [{code, name}], valid five-digit codes only, no repeats, at most MAX.
  // Anything unreadable (hand-edited storage, an older format) becomes [].
  function parse(raw) {
    var list;
    try {
      list = JSON.parse(raw || "[]");
    } catch (e) {
      return [];
    }
    if (!Array.isArray(list)) return [];
    var seen = {};
    var out = [];
    for (var i = 0; i < list.length && out.length < MAX; i++) {
      var item = list[i];
      if (!item || !/^\d{5}$/.test(item.code) || seen[item.code]) continue;
      seen[item.code] = true;
      out.push({ code: item.code, name: String(item.name || item.code), road: String(item.road || "") });
    }
    return out;
  }

  // Adds the stop if missing, removes it if present. Returns the new list and
  // whether it is now saved; "full" when adding would pass MAX.
  function toggle(list, stop) {
    var without = list.filter(function (s) {
      return s.code !== stop.code;
    });
    if (without.length < list.length) return { list: without, saved: false };
    if (list.length >= MAX) return { list: list, saved: false, full: true };
    return { list: list.concat([{ code: stop.code, name: stop.name, road: stop.road || "" }]), saved: true };
  }

  // A chip's words. Names repeat across the island ("Blk 402" is three
  // stops), so when two saved stops share one, each also names its road.
  function label(list, stop) {
    var twins = list.filter(function (s) { return s.name === stop.name; }).length;
    return twins > 1 && stop.road ? stop.name + " · " + stop.road : stop.name;
  }

  function url(list) {
    return "/map?stops=" + list.map(function (s) { return s.code; }).join(",");
  }

  // ---- Browser side ------------------------------------------------------

  // Storage can throw (Safari private mode, blocked site data): the page
  // then simply behaves as though nothing is saved.
  function load() {
    try {
      return parse(root.localStorage.getItem(KEY));
    } catch (e) {
      return [];
    }
  }

  function save(list) {
    try {
      root.localStorage.setItem(KEY, JSON.stringify(list));
      return true;
    } catch (e) {
      return false;
    }
  }

  function chip(href, text, current, extraClass, title) {
    var a = document.createElement("a");
    a.className = "chip" + (extraClass ? " " + extraClass : "");
    a.href = href;
    a.textContent = text;
    if (title) a.title = title;
    if (current) a.setAttribute("aria-current", "page");
    return a;
  }

  // "Near me", then "All favourites" (when there are any), then each saved
  // stop. Redrawn whenever the star changes, so the row is always the truth.
  function renderChips() {
    var nav = document.getElementById("chips");
    if (!nav) return;
    var list = load();
    var mode = nav.getAttribute("data-mode");
    var current = nav.getAttribute("data-current-stop");
    var nearMe = /[?&]near=1/.test(root.location.search);
    nav.textContent = "";
    nav.appendChild(chip("/map?near=1", "Near me", mode === "start" || nearMe, "chip-near"));
    if (list.length) {
      nav.appendChild(chip(url(list), "All favourites", mode === "favourites", "chip-all"));
      list.forEach(function (s) {
        nav.appendChild(
          chip(
            "/map?stop=" + s.code,
            label(list, s),
            mode === "stop" && current === s.code && !nearMe,
            "",
            s.road ? s.name + ", " + s.road : s.name
          )
        );
      });
    }
  }

  function setStar(button, saved) {
    button.setAttribute("aria-pressed", saved ? "true" : "false");
    button.querySelector(".star-label").textContent = saved ? "Saved" : "Save";
    button.setAttribute(
      "aria-label",
      saved ? "Remove this stop from favourites" : "Save this stop to favourites"
    );
  }

  function bindStar() {
    var button = document.getElementById("star");
    if (!button) return;
    var stop = {
      code: button.getAttribute("data-code"),
      name: button.getAttribute("data-name"),
      road: button.getAttribute("data-road") || "",
    };
    setStar(button, load().some(function (s) { return s.code === stop.code; }));
    button.addEventListener("click", function () {
      var result = toggle(load(), stop);
      if (result.full) {
        toast("You can save up to " + MAX + " stops. Remove one first.");
        return;
      }
      if (!save(result.list)) {
        toast("This browser isn't letting the page save anything.");
        return;
      }
      setStar(button, result.saved);
      toast(result.saved ? "Saved to favourites" : "Removed from favourites");
      renderChips();
    });
  }

  var toastTimer = null;
  function toast(text) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("p");
      el.id = "toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }

  root.Favourites = {
    MAX: MAX,
    parse: parse,
    toggle: toggle,
    label: label,
    url: url,
    load: load,
    renderChips: renderChips,
    bindStar: bindStar,
    toast: toast,
  };
})(typeof window !== "undefined" ? window : globalThis);

if (typeof module !== "undefined") module.exports = globalThis.Favourites;
