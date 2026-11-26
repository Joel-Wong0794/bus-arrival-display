# Bus Arrival Display

A live bus map for Singapore. One page shows where the buses heading to your stops are right now, how long until each one arrives, and the weather for the walk there. It runs in any phone or desktop browser. ETAs come from LTA DataMall every ~20 seconds, and between polls each pin glides along its real road towards the stop.

| Live URL | What it opens on |
| --- | --- |
| [bus-arrival-display.vercel.app](https://bus-arrival-display.vercel.app/) | Your saved favourite stops, or, with none saved, the stop nearest you |

`/map` is the same page. `/map?stop=<code>` opens one stop, and `/map?stops=<code>,<code>` opens a set of favourites, so any view can be bookmarked or shared.

## Architecture at a Glance

```text
Phone / desktop browser
        │  HTML pages and htmx polling
        ▼
Vercel serverless function
        │  Flask routes, Jinja rendering, ETA and map processing
        ├────────► LTA DataMall (arrivals and bus-stop data)
        ├────────► data.gov.sg (optional weather: 24-hour forecast,
        │                       2-hour area forecast, air temperature)
        └────────► Bundled bus_stops.json (names and coordinates)
```

The browser handles presentation, saved favourites (in `localStorage`), and
Leaflet map updates. Flask keeps API keys server-side, fetches and normalizes live data, and
returns complete pages or small HTML fragments. The application is stateless:
configuration comes from environment variables, reference data is bundled as a
static JSON file, favourites stay in each visitor's browser, and no database
is required.

## How the App Works

The app is a stateless Flask application deployed as a Python serverless
function on Vercel. `vercel.json` sends every request to `api/index.py`, while
the Jinja templates, CSS, weather icons, and baked bus-stop lookup are bundled
with the function. There is no database, background worker, or server-side
session.

### Map and stop search

1. `/` (or `/map`) with no stop in the URL is the start screen. The browser
   reads its saved favourites and moves to `/map?stops=<codes>`; with none
   saved it asks for the phone's location instead (step 6). With a stop or
   favourites in the URL, the page renders server-side with the NEA weather
   for the first of them, and loads Leaflet with OneMap tiles.
2. The page polls `/map-list` every `REFRESH_SECONDS`. That response contains
   both the visible bus rows and data attributes used to create or move map
   markers.
3. Markers are keyed by stop, service, and arrival rank. Existing markers move
   in place instead of being rebuilt, which preserves an open popup and avoids
   resetting the map on every refresh.
4. Typing in the stop search sends a delayed htmx request to `/stops`. Selecting
   a result opens `/map?stop=<code>`, making the selected stop bookmarkable and
   keeping the choice local to that browser tab.
5. The page also downloads `/stops.json` once (every stop's code, name, and
   position, cached for a day). From zoom 15 inward the browser draws the stops
   in view as grey bus-stop signs, filtering that list itself, so panning makes
   no requests. Tapping a sign and choosing **Show buses here** opens the same
   `/map?stop=<code>` as the search box.
6. If the phone shares its location, a blue dot shows where you are. On the
   start screen with no favourites saved (or after tapping **Near me**), the
   first fix is sent once as a `POST`
   body to `/nearest-stop`, and the page switches to the stop it returns. The
   position never goes into a URL, and later fixes only move the dot.
7. The favourites view shows buses within `MAP_WINDOW_MINUTES`; a selected-stop view
   shows everything currently running at that stop. Buses without a live GPS
   fix remain in the list but are not plotted.

### Reference data, caching, and failures

- `data/bus_stops.json` maps stop codes to names and coordinates. It is
  generated manually by `scripts/fetch_bus_stops.py` and loaded once when the
  serverless function starts, avoiding a scan of roughly 5,200 stops on every
  request.
- Weather and fallback stop positions use small in-memory caches. Each of the
  three NEA feeds is cached once for the whole island, not per stop, and the
  last good copy is reused if a feed fails. These are performance optimizations
  for warm Vercel instances; correctness does not depend on them surviving
  between requests.
- Dynamic responses use `Cache-Control: no-store` so a browser does
  not reuse stale arrival data.
- A failed bus-stop request displays that stop as unavailable without hiding
  successful stops. Missing GPS data, weather outages, stale reference data, or
  a browser that blocks storage or location each disable only the feature
  that needs them rather than taking down the page.

## Setup

1. **Get an API key** from LTA DataMall (https://datamall.lta.gov.sg/content/datamall/en.html). Free tier has plenty of quota for polling every 20s.
2. **Copy `.env.example` to `.env`** and fill in:
   - `API_KEY` — your DataMall key
   - `REFRESH_SECONDS` — poll interval (default 20, keep it ≥ 10)
   - `BUFFER_SECONDS` — safety margin in seconds (default 30; stacks with flooring, so anything under 1m30s shows "Arr")
3. **Install**: `pip install -r requirements.txt`
4. **Run locally**: `flask --app api/index run --debug`
5. **Visit**: http://localhost:5000

## Environment Variables

| Variable          | Default | Notes                                                                                                                                                                   |
| ----------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `API_KEY`         | —       | Required. LTA DataMall API key. Keep in `.env`, never in git.                                                                                                           |
| `BUS_STOP_CODES`  | —       | Optional. Comma-separated stop codes shown by `/debug` when no `?stop=` is given. Favourites no longer come from here — see [Favourites](#favourites). |
| `BUS_STOP_NAMES`  | —       | Optional. Display names for `BUS_STOP_CODES`, comma-separated. Falls back to the code itself if missing. |
| `REFRESH_SECONDS` | 20      | Poll interval in seconds. Doesn't affect how fast the countdown ticks (that's 1s client-side); just how often we fetch fresh data.                                      |
| `BUFFER_SECONDS`  | 30      | Seconds shaved off every ETA as a safety margin. Combined with floor-rounding, a 30s buffer means anything under 1m30s shows as "Arr" (red, bold). Set to 0 to disable. |
| `MAP_WINDOW_MINUTES` | 5    | Only buses arriving within this many minutes are listed and pinned in the favourites view. Picking a single stop shows everything running there. |
| `WEATHER_API_KEY` | —       | Optional. `x-api-key` for data.gov.sg. The endpoint works without one; a key only raises the rate limit.                                                                 |
| `WEATHER_REGION`  | `west`  | Which NEA forecast region to fall back to when no stop position is known: `north`, `south`, `east`, `west`, `central`. |
| `WEATHER_TTL_SECONDS` | 900 | How long a fetched forecast is reused before re-fetching.                                                                                                              |
| `TEMPERATURE_TTL_SECONDS` | 300 | How long the live station temperatures are reused. Shorter than the forecast's: stations report every minute. |

`HOME_LAT`, `HOME_LON` and `NEARBY_RADIUS_M` from earlier versions are no longer read and can be removed from Vercel.

## How the ETA is Computed

Two safety mechanisms, both intentional:

1. **Floor, not round**: `int(seconds // 60)` — a bus 1m31s away shows "1 min", not "2 min". The error always lands toward "leave now" rather than "you have time."
2. **Buffer**: Subtracts `BUFFER_SECONDS` before flooring. A 30s buffer means the countdown errs an extra ~30s early. Combined with flooring, this pushes anything under 1m30s to "Arr".

The result is deliberately conservative, not "accurate". A countdown reading "2 min" means _at least_ 2 minutes — you might leave slightly early, but you won't miss the bus because the display said you had time.

## Reading the Page

Two colours carry the meaning, on the pins and in the list alike — the amber and red of the LED panels at Singapore bus stops:

| Colour | Meaning |
| ------ | ------- |
| Red    | "Arr" — inside the buffer band |
| Amber  | On the way |

Colour is never the only signal: an arriving bus also reads **Arr** instead of a number. Each list row is a route plate (the service number, filled with its colour), the destination, then a small line with the stop it is due at (favourites view), its distance, crowding and vehicle type, and the countdown on the right. A line over the map's top-left corner says what the map is showing, including when it is empty ("Nothing in 5 min · next 20 in 9 min"), so an empty map is never a silent one.

The page uses Barlow and Barlow Condensed from Google Fonts — typefaces drawn from road signage — and falls back to system fonts if they cannot load.

## Destinations

Each bus shows where it is heading — `to Boon Lay Int`, `to Joo Koon Int`. It answers the question a service number alone can't at a stop served in both directions: *is this the one going my way?*

It is the first line beside the route plate. The plate column has a fixed width, so every row's text starts at the same x whatever the service number, and the countdown is pinned to the right edge.

Two more things shape how it renders:

- **Loop services read `Loop`, not a destination.** Services like 334 and 98 report the same code for origin and destination. "to Jurong East Int" would be technically true and practically useless, since that is also where the bus started.

The arrival feed names a destination only by bus stop code (`22009`), and the `BusStops` dataset that maps codes to names has no filter-by-code — resolving one destination means paging all ~5,200 stops. Doing that per request, on a serverless cold start, would add seconds to a page that polls every 20s. So the lookup is baked into `data/bus_stops.json` and read once at import. It is a static asset, not a database; the app stays stateless at runtime.

**Regenerate it** when a stop gains a service whose destination isn't in the file:

```
python scripts/fetch_bus_stops.py
```

The map's route-following glide also needs `data/bus_routes.json` — every service's road line — baked **after** the stops file, since it places each stop on its line using the positions there:

```
python scripts/fetch_bus_routes.py
```

It downloads from [sgbusdata](https://github.com/cheeaun/sgbusdata) on GitHub (~550 KB, no DataMall key or calls) and takes a few seconds. Re-run it when routes change; sgbusdata refreshes from LTA on its own schedule, and the file records which update it was baked from. Without the file, pins glide in a straight line instead.

The file holds a name, **road and position** per stop — `{"name": ..., "road": ..., "lat": ..., "lon": ...}` — since the `BusStops` response carries coordinates in the same rows the names come from. Positions are what let the page find the stop nearest you and measure distance from a selected one.

A file predating that change (plain `"Hotel Grand Pacific"` values) is read without complaint; those features simply stay switched off until it is regenerated. The script prints how many stops came back without coordinates — anything other than a small number means the `Latitude`/`Longitude` fields have been renamed upstream.

A stale or missing file degrades rather than breaks: an unknown code falls back to displaying the raw code, and if the file is absent entirely every destination does. `vercel.json` includes `data/**` so the file ships with the function.

## Vehicle Type

DataMall reports whether each bus is single deck, double deck, or bendy. It is a curiosity, not something that changes whether you leave, so it sits at the end of each row's small grey line, and in the pin's popup alongside distance and crowding.

**It is per arrival, not per service.** A service's queued buses are routinely a mix — a double decker followed by two single deckers is the common pattern here — so it could not be a per-service label like the destination.

`Feature` (`WAB`, wheelchair accessible) is not shown at all — every bus in this feed reports it, so it would mark nothing.

## Map Page

The page answers *when* and *where* together. Buses are pinned on a [OneMap](https://www.onemap.gov.sg/) layer via Leaflet, each pin a small plate with its service number, coloured as above. In the favourites view only buses arriving within `MAP_WINDOW_MINUTES` are shown; one picked stop shows everything running there. Below the map, the same buses are listed. Tapping a row pans to that bus and opens its pin.

Filtering by **time** rather than by rank is deliberate: a service's second bus appears exactly when it is also imminent, so no "show all / show next" toggle is needed.

Three things worth knowing about the data:

- **Not every bus can be plotted.** DataMall only sends real coordinates when `Monitored` is `1`. On a schedule-derived estimate it sends the string `"0.0"`, which would place the bus in the Atlantic, so those buses are listed but never pinned — a bus two minutes away still matters when it has no GPS fix. The count of them appears under the list.
- **Distance is measured from the stop each bus is heading to.** A bus that fits its route quotes the drive (`1.3 km by road`); on live data that ran up to ~25% longer than the straight line. In the favourites view every row is measured from its own stop, so the numbers mean the same thing in every row.
- **Positions are up to ~20s stale.** That is DataMall's publish interval. Between fixes the pins glide towards the stop, paced by LTA's arrival time — see [Gliding pins](#gliding-pins) for what that does and does not claim.

Markers are keyed per vehicle, so a refresh moves a pin rather than rebuilding it — otherwise an open popup would slam shut every poll. The view is framed once on load and never re-fitted, so a refresh cannot yank the map while you are panning it.

### A bus's route

Tap a bus — its pin on the map or its row in the list — to see where it is on its route:

- **The road still to drive to your stop** is drawn thick, in the bus's own colour (amber, or red when arriving). The road it has already covered is a faint dotted line, and the route beyond your stop is faint and solid, so the whole line is there for context.
- **Every stop on the route** is a dot; the ones between the bus and your stop are brighter, and the card says how many: "4 stops before yours", or "Next stop is yours".
- **A card along the map's bottom edge** names the service, destination and countdown, the stop, the distance by road, vehicle type and crowding. It replaces the old pin popup, which sat on the bus and hid the very road it was about to drive. The zoom buttons moved to the top right to make room.
- **The map frames the bus and the road to your stop** on a tap — not the whole route, which can cross the island — and then stays put: each 20-second refresh moves the split point along the line without moving the map.
- **The grey stop signs step aside** while a route is showing; the route's own stops are the ones that matter. Tap empty map, the card's ✕, or press Escape to put everything back.

The server already works out which line a bus is on, and where along it, to make pins glide on the road (`fit_route` in `api/index.py`). Each list row now carries that as `data-route-service`, `data-route-pattern`, `data-route-along` and `data-route-stop`. Tapping fetches the whole line once from `GET /route.json?service=<no>&pattern=<n>` — `{"line": [[lat, lon, metres], ...], "stops": [[code, name, lat, lon, metres], ...]}`, metres measured along the line — cached for a day like `/stops.json`, and splits it at those two distances in the browser. A bus that did not fit its line (GPS off the road, or a service missing from the bake) still gets the card, which says the route is not available rather than drawing a guess.

### Gliding pins

A pin that only moved on each GPS fix sat still for ~20s and then teleported. Pins now glide along the bus's route in between, using the "shadow bus" model from [Next Stop's write-up](https://nextstopsg.app/interpolation.html), cut down to what DataMall's data supports.

LTA gives two things per bus: where its GPS last put it, and when it should reach the stop. The second sets a pace. An invisible *shadow bus* drives along the route from the last fix towards the stop at that pace, and the visible pin follows it.

**The route.** DataMall has stop sequences but no route shapes, so the road lines come from [sgbusdata](https://github.com/cheeaun/sgbusdata), the data behind [BusRouter SG](https://busrouter.sg): LTA's own route lines, patched from OneMap and Citymapper where LTA's are missing or cut off. `scripts/fetch_bus_routes.py` bakes them into `data/bus_routes.json`, with each stop's distance along its line worked out once, at bake time:

- **Stops are placed in order along the line**, each searched for only beyond the one before, so a loop service's stop lands on the right lap.
- **Some lines do not run the way their stop list does.** Measured at bake time, 27 patterns run backwards, and loop services often start partway round (service 102's line starts 12.7 km in). Taken as published, those put stops on the far side of the same road — 982 stops more than 150 m off their line. The bake tries each line reversed and, for closed loops, rotated to start at the first stop, and keeps whichever fits best: 40 lines are corrected, leaving 194 of ~27,000 stops more than 150 m off, mostly where the source line has a gap.

For each bus, `route_path` in [`api/index.py`](api/index.py) works out where on its line it is and sends the road ahead — plus up to 1.5 km behind — with distances along it:

- Every stretch of line leading to the stop is a candidate, for each pattern and each time it visits the stop. The GPS fix is projected onto each; fits more than 120 m away (Next Stop's figure), or that would need more than 60 km/h to make LTA's ETA, are dropped, and the closest fit wins. The speed test is Next Stop's too: without it, a 502 was once placed 41 km out on a stretch of its own route that merely passed nearby.
- **The pin is drawn on the road** at the projected spot, not where GPS put it a few metres off. On live data the fixes sat a median 7 m from the line, at most 27 m.
- Up to 2 km of road ahead is sent; past that the path cuts straight to the stop, which a pin reaches only if polling stops for minutes. Each bus's path is typically 10–30 points.
- Service not in the bake, or no fit: the path is a straight line from fix to stop.

**The rules** live in [`static/glide.js`](static/glide.js), all in metres along that path:

| Rule | Value | Why |
| --- | --- | --- |
| Fix age | 10 s + half the poll interval (20 s at 20 s polls), at most 30 s | A fix is old before the page sees it; the shadow starts from when the bus was there, so the pin is drawn where it likely is now, not where it was. The 10 s (`LTA_LAG_S` in `static/glide.js`) is tuned by eye: 5 s still left pins trailing buses on the ground |
| Pace | road distance to stop ÷ time LTA says remains | Pin arrives when the countdown says it will |
| Speed ceiling | 60 km/h | An "Arr" ETA for a bus 1 km out must not fling the pin across the map |
| Hold short of the stop | 80 m, until LTA's arrival time | Never "arrive" on a guess early; once LTA says the bus is due (its own time, not the tile's buffered "Arr"), the pin pulls in |
| Never reverse | — | A fresh fix slightly behind the pin makes the pin wait, not back up |
| Catch-up | ~2 s ease, at most 90 km/h | A fix ahead of the pin is a quick slide along the road, not a lurch |
| Snap | fix > 600 m behind or > 1.5 km ahead | Too far off to glide: jump straight to the truth |

Each new path is threaded through the pin's current spot, so a fresh fix never shoves a pin sideways onto the new line.

Measured over ~8,900 samples of live data at a busy stop: all 23 tracked buses fitted their road line, pins sat a median 0 m off it (never more than 13 m), 99.9% of on-screen speeds were under 96 km/h, and no pin ever moved backwards.

**What it does not claim.** This is a presentation fix, not a better forecast: the ETA is still LTA's, and the countdown on `/` is untouched. Next Stop also slows its shadow near stops and bends with a per-stretch "speed map"; that is left out here, because buses skip stops nobody is waiting at, and a pin that dwelt at every stop would lag the real bus more often than match it. A pin is a best guess for up to ~20s; the 80 m hold and the never-reverse rule exist so that guess errs behind the real bus rather than ahead of it.

**Pins follow vehicles, not ranks.** The feed lists a service's buses as next, second and third. When the first passes the stop, the second becomes "next" — so a pin keyed by rank would leap back to the following bus. Each poll instead matches rows to the existing pins of that service by position, so a pin stays on one physical bus and an open popup stays open on it.

Buses whose stop has no known position (an un-regenerated `bus_stops.json`) keep the old behaviour: parked on their fix.

### LTA vs actual

Gliding pins make the map agree with LTA; they do not make LTA's estimate any better. To see how good that estimate is, `/map` keeps score while it is open. When a bus reaches its stop, a panel under the list records what LTA promised against what happened:

```
LTA vs actual, this visit
3 buses · on average off by 2 min · 1 of 3 within a minute
111 · LTA said 16:04, arrived 16:07 · 3 min late (predicted 4 min ahead)
```

How a bus is judged ([`static/accuracy.js`](static/accuracy.js)):

- **The prediction tested is the first one seen** for that bus — the one you would have planned your walk around. Later revisions as it closes in are not what you acted on.
- **Arrival is its first GPS fix within 100 m of the stop.** Buses with no GPS fix are never judged, and a bus that slips past between two fixes is simply missed.
- **Predictions made under a minute out are skipped.** A bus already pulling in is no test, and counting it would flatter the score.
- **The summary averages the size of the miss**, not the signed error, so a late bus and an early one cannot cancel into "on time".
- **Early is red, late is amber.** Early is the costly miss — you were told you had time.

It relies on pins following vehicles rather than ranks (see above); otherwise a passing bus would hand its prediction to the one behind it.

**What it is and is not.** Nothing is stored: the records live in the tab and reset on reload, so the app stays stateless. That makes it a sample of what you happened to watch, not a statistic — fine for a sense of how far to trust the countdown, not for publishing a number. Expect roughly ±30 s of slop: a fix can be ~20 s old when it arrives, and 100 m is a little before the pole, which is why results are shown in whole minutes. Measuring properly — weeks of records, by stop and time of day — needs somewhere to keep them, and is the prerequisite for ever correcting LTA's estimate rather than just displaying it.

### Favourites

Any stop can be saved with the **Save** button beside its name. Saved stops appear as chips under the search box — **Near me**, **All favourites**, then one chip per stop — and opening the site with favourites saved goes straight to **All favourites**: `/map?stops=<code>,<code>`, every saved stop ringed on the map, and the buses due at any of them in the next `MAP_WINDOW_MINUTES` in one soonest-first list.

- **Kept in the browser, not on the server.** The list lives in `localStorage` under `favouriteStops`. There is no account; the page sends the saved codes in the URL and the server forgets them once it has answered. The list does not follow you to another device, and clearing site data or a private window starts it empty.
- **Up to six.** Each saved stop costs one DataMall call per poll, so the list is capped at `MAX_FAVOURITES` (6). The calls run in parallel, so six stops take about as long as one.
- **Nothing saved yet** — a first visit, or someone you sent the link to — falls back to the stop nearest the phone (see [Your location](#your-location)). If location is off, the page says so and puts the search box forward.
- **Safe to hand-edit.** Codes in `?stops=` are validated as five digits, deduplicated, and capped; anything unreadable in storage is treated as nothing saved.

### Picking a stop

The search box reaches **any of the ~5,200 stops** by name or code, and opens `/map?stop=<code>`. Each result shows its **road** under the name, because names repeat across the island ("Blk 402" is three different stops); typing a road name ("Bt Batok Rd") finds the stops on it too. The road also appears beside the stop code above the stop's name, in a tapped stop's popup, and on a favourite chip when two saved stops share a name. Matching happens server-side and returns a dozen results at a time, rather than shipping a quarter of a megabyte of options to a phone on every page load. The choice lives in the URL, so it is per-tab and bookmarkable.

| | Favourites | One stop |
| --- | --- | --- |
| URL | `/map?stops=<codes>` | `/map?stop=<code>` |
| Question | "Should I leave now?" | "What's coming at this stop?" |
| Distances measured from | Each bus's own stop | That stop |
| Arrival window | `MAP_WINDOW_MINUTES` | None — everything running |
| Rings on the map | Every saved stop | The stop |

A distance is omitted if the stop's position cannot be established, which is better than quoting a number from the wrong origin. A bus with no GPS fix still says so; a tracked bus with no reference point simply shows nothing rather than contradicting its own pin.

Positions come from `data/bus_stops.json`. When it has none for the focused stop — an un-regenerated file — one filtered `BusStops` request fills the gap and is remembered for the life of the instance. That fallback is affordable only because the feed now filters by stop code (guide v6.8); asking used to mean paging ~5,000 rows, which is why the file is baked in the first place. It is a safety net, not the plan: a regenerated file skips it entirely. Rows whose code does not match are discarded, since an unsupported filter returns a page of unrelated stops rather than an error, and a pin placed from one of those would be plausible and wrong.

Stop codes from the URL are validated as five digits before being used, so a hand-edited URL cannot forward junk to DataMall.

### Your location

On a phone that shares its location, `/map` shows a blue **you are here** dot, with a faint circle for how sure the phone is, and it moves as you walk. A **You** entry joins the legend once the first position arrives.

If you opened the site with no favourites saved, or tapped **Near me**, the first position also **picks the stop nearest you**: the page switches to `/map?stop=<code>`, the same bookmarkable view as choosing it by hand. Three things keep that from getting in the way:

- **Favourites win.** With stops saved, opening the site goes to them; **Near me** is one tap away.
- **Only within 2 km of a stop.** Further than that — or outside Singapore — the page stays as it was.
- **Nothing breaks without it.** Declining the browser's prompt, a device with no location, or no connection leaves the start screen explaining what happened, with a **Try location again** button and the search box ready. Browsers only offer location on HTTPS or `localhost`, which Vercel provides.

**Privacy.** The position is sent once, as a `POST` body to the app's own `/nearest-stop`, which compares it against `data/bus_stops.json` and keeps nothing. It never goes into a URL, so it cannot end up in server logs or browser history; only the chosen stop code does. Later positions only move the dot and never leave the phone.

Leaflet is loaded from a CDN; it is the only dependency beyond Flask, requests, python-dotenv and htmx. OneMap tiles require the SLA attribution that renders in the map's bottom-right corner — leave it in place.

### Tap a stop

From the zoom the page opens at (15) and closer, every bus stop in view is drawn as a small grey **bus stop sign** — a rounded square with a bus on it. Tap one for a popup with its name, code, and a **Show buses here** button, which opens `/map?stop=<code>` — the same view, link, and weather as choosing it in the search box. Zoomed out further, the signs disappear: 5,000 of them would be clutter, and too dense to hit with a finger.

- **One download, then nothing per pan.** `GET /stops.json` returns every stop as `[code, name, lat, lon, road]` (~335 KB, ~80 KB compressed), built at startup from the same `data/bus_stops.json` as the search box, and cached for a day (`public, max-age=86400`) so Vercel's CDN and the phone answer repeat visits. Each pan or zoom then picks out the stops inside the visible area in the browser — a sub-millisecond scan of 5,000 positions. Asking the server for "stops in this rectangle" instead would cost a request per pan and put the map's position in URLs.
- **A square, on purpose.** Every other place marker — you, and the rings on your stops — is a circle, so the shape alone says "stop". Grey, so it never competes with the red and yellow buses or the blues.
- **Only the stops in view exist on the page** — typically 10–150 small markers, never all 5,000 — so panning stays smooth on a phone. The sign is drawn 16 px inside an invisible 32 px box, which is its finger-sized tap area.
- **Never in the way.** Signs live in their own map layer between the roads and Leaflet's markers, so a sign never covers a bus, the blue *you* dot, or a ringed stop — which keeps its own bigger ring and is not drawn twice.
- **Only what changed is redrawn.** Signs that leave the view are removed and new ones added; clearing everything would close an open popup, and opening a popup pans the map to fit it, which would trigger exactly that.
- **A popup, not an instant switch.** Panning a map on a phone often lands a stray tap; the popup lets you read the name before committing. The button is a real link, so Back returns to the previous stop.
- **Failure is just no signs.** If `/stops.json` can't load, the map works as before and the search box still finds any stop. The signs are kept out of the Tab order — dozens would sit between the search box and the bus list — so the search box is the keyboard route to the same place.

## Weather

The header shows the weather **where the map is looking** — the chosen stop, or the first favourite — beside the stop's name: the temperature, an icon, and NEA's forecast in a few words. Hover or long-press it for the forecast area.

```
Thundery Showers
Bedok · 31°C now · 25–34°C today
```

### Local, not regional

"Where the map is looking" is the chosen stop, or the first favourite. On a phone that shares its location, the page has already switched to the stop nearest you (see [Your location](#your-location)), so the weather follows the phone **without the phone's position being used for it** — the server already knows the stop's coordinates. Picking a stop by hand shows the weather where you are going.

Three NEA feeds from data.gov.sg, all free and keyless:

| Feed | How local | Shown as |
|---|---|---|
| [2-hour forecast](https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast) | 47 named areas, each with a position | The condition, icon, and area name |
| [Air temperature](https://api-open.data.gov.sg/v2/real-time/api/air-temperature) | 17 stations, read every minute | `31°C now` |
| [24-hour forecast](https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast) | 5 regions | The day's low–high, and the fallback |

The area and station are simply the nearest to the stop, by straight-line distance — a scan of 47 and 17 entries, so no index. Each feed returns the whole island in one reply, so one cached copy serves every stop: viewing more stops never means more weather requests.

It degrades one layer at a time:

- **A station that didn't report** in the latest batch is passed over for the next nearest, not shown as blank.
- **An expired 2-hour forecast** (a feed that stopped updating) is ignored rather than left showing hours-old weather; the condition falls back to the 24-hour regional one, and the layout returns to the original `24–34°C · 6 pm to 6 am`.
- **A stop with no coordinates** gets the regional forecast, as before this feature.
- **All three down** shows no weather at all.

The 2-hour window itself (`10.30 am to 12.30 pm`) is left off the header: at phone width it would push the line onto a second row, and "the next two hours" is what anyone glancing at a bus map assumes.

The icon is animated. It comes from [Meteocons](https://github.com/basmilius/weather-icons) by Bas Milius, **MIT licensed** — the fourteen SVGs actually used are vendored into `static/weather/` with the licence alongside them, as MIT requires. They animate via SMIL embedded in the file, which plays inside an `<img>` with no script and no library.

All 23 of NEA's forecast strings are mapped in `WEATHER_ICONS`. Four of them name a time of day (`Fair (Day)`, `Fair (Night)`, and the Partly Cloudy pair); the bare `Fair` and `Partly Cloudy` don't, so those are settled against the clock — sunrise and sunset barely move in Singapore, so a fixed 7am/7pm pair picks the right sun or moon without an ephemeris.

If NEA ever adds a value, it falls through to no icon rather than a broken image, and the text still renders.

Source is [data.gov.sg's real-time API](https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast), which wraps NEA's 24-hour forecast. Three things worth knowing:

- **The API key is optional.** The endpoint answers unauthenticated; `x-api-key` only raises the rate limit, and the spec marks it `required: false` with no `security` block. Set `WEATHER_API_KEY` if you have one, leave it blank if not — at one fetch per 15 minutes it makes no difference.
- **The region can't be derived from a position.** The feed names five regions but ships no boundaries for them, so `WEATHER_REGION` has to be told which one you are in. It is now only the fallback, used when the 2-hour feed has nothing for the stop. If NEA omits your region for a period, it falls back to the island-wide forecast.
- **It is cached for `WEATHER_TTL_SECONDS`** (default 900). NEA reissues a few times a day, so fetching per page load would spend requests redrawing the same words. The *record* is cached rather than the rendered text, so the period still advances against the clock inside a TTL.

The weather is fetched only on the page load, never in the `/map-list` poll — the list refreshes every 20s and the forecast does not.

**It cannot take the bus display down.** `read_weather()` returns `None` on any failure — connection error, timeout, HTTP error, malformed payload — rather than raising, and the header simply shows no weather. If a forecast was fetched earlier, an outage serves that last good reading instead of nothing.

## Debug View

Visit `/debug?stop=<code>` (or `/debug` for the stops in `BUS_STOP_CODES`) to see per-bus:

- Raw `EstimatedArrival` string from DataMall
- `Monitored` flag (0 = schedule-derived, not live GPS)
- Exact float minutes vs. displayed rounded value
- Resolved destination — a raw code here instead of a name means it is missing from `data/bus_stops.json`
- Raw `Type` (`SD` / `DD` / `BD`) behind the vehicle icon

**Use case**: if a countdown looks wildly off, check `Monitored` first. A `0` means DataMall estimated from the timetable, not a vehicle position — it can legitimately differ from a reference app by several minutes, and there's nothing we can do about it.

## Deploy to Vercel

1. **Push to GitHub** (or connect the repo in Vercel for auto-deploy on push):
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-github-url>
   git push -u origin main
   ```
2. **In Vercel**: Import the repo, set environment variables (`API_KEY`, and optionally `REFRESH_SECONDS`, `BUFFER_SECONDS`, `MAP_WINDOW_MINUTES` and the weather settings), deploy.

**Root Directory must be set to the repo root** so Vercel finds `api/index.py`. `vercel.json` uses an explicit `@vercel/python` build rather than auto-detection, so the function is built reliably and `templates/` and `static/` are bundled with it.

Note: `.env` is gitignored — set env vars in the Vercel dashboard, not the file, so you don't commit secrets: the repo is public.

## Testing

No formal test suite. To verify the timing logic works:

1. Hit `/debug?stop=<code>` and check that the float minutes in "Exact minutes" match the `EstimatedArrival` timestamp minus the server's current time.
2. Run `python scripts/test_map_views.py` — the start, stop and favourites views, `?stops=` parsing, and the empty-window message, against a canned DataMall answer.
3. Run `node scripts/test_favourites.js` — reading saved stops, the save/remove toggle, and the six-stop cap.
4. Cross-check a pin against `/debug?stop=<code>` — any bus showing `Monitored=0` must appear in the list without a pin, and be counted in the "no GPS fix" note.
5. Leave the page open for a minute. Pins should move without the map flickering or re-centring, and an open popup should survive a refresh.
6. Tap a row and confirm the map pans to that bus and opens its pin. Rows with no live position should do nothing.
7. Save two stops, then open `/`: it should go to **All favourites** with both ringed. Remove one with **Saved** and the chips should update at once.
8. Run `node scripts/test_glide.js` — the pacing rules in `static/glide.js` as plain asserts.
9. Watch a pin for a minute: it should creep towards its stop between polls, stop ~80 m short, and never move backwards.
10. Run `node scripts/test_accuracy.js` — the judging rules in `static/accuracy.js`.
11. Leave `/map?stop=<a busy stop>` open for ten minutes. The "LTA vs actual" panel should appear once a tracked bus reaches the stop, and reloading should clear it.
12. Run `python scripts/test_route_path.py` — the polyline decoder and route fitting in `api/index.py`, against made-up roads including a loop.
13. On `/map?stop=<code>`, a tracked bus's distance should read `… by road`, and its pin should follow the road rather than cutting across.
14. Run `python scripts/test_nearest_stop.py` — picking the nearest stop, and `/nearest-stop` refusing bad input and GET.
15. With nothing saved, open `/` on a phone and allow location: the blue dot should appear and the page should switch to your nearest stop. Deny location instead and the start screen should say so and offer **Try location again**.
16. Run `python scripts/test_local_weather.py` — nearest area and station, a silent station skipped, and each fallback.
17. Open `/map?stop=<a stop in the east>` and `/map?stop=<one in the north>`: each header should name a different nearby area (`Bedok · …`, `Woodlands · …`).
18. Run `python scripts/test_stops_json.py` — the stop list's shape, its day-long caching, and that it matches the search box's lookup.
20. Tap a bus: its road to your stop should light up, the card should say how many stops are left, and tapping empty map should clear it.
19. On `/map`, tap a stop sign: the popup should name it, and **Show buses here** should open that stop. Zoom out past 15 and the signs should go.
