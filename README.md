# Bus Arrival Display

A live-updating web dashboard showing LTA bus arrival times for bus stops, displayed on an old 7" Android tablet running a browser in kiosk mode. The app fetches real-time ETAs from DataMall every ~20 seconds and counts them down on the client every second, so the displayed number stays current between polls and keeps working even when the kiosk screen wakes from sleep.

Two pages, for two different questions:

| Page   | Question it answers          | Where it is used                                          |
| ------ | ---------------------------- | --------------------------------------------------------- |
| `/`    | "When is the next bus?"      | The kiosk tablet — glanceable from across the room        |
| `/map` | "Where is it right now?"     | A phone — imminent buses plotted on a map                 |

## Setup

1. **Get an API key** from LTA DataMall (https://datamall.lta.gov.sg/content/datamall/en.html). Free tier has plenty of quota for polling every 20s.
2. **Copy `.env.example` to `.env`** and fill in:
   - `API_KEY` — your DataMall key
   - `BUS_STOP_CODES` — comma-separated stops, e.g. `28571,28519`
   - `BUS_STOP_NAMES` — display names, e.g. `Home334,Blk425`
   - `REFRESH_SECONDS` — poll interval (default 20, keep it ≥ 10)
   - `BUFFER_SECONDS` — safety margin in seconds (default 30; stacks with flooring, so anything under 1m30s shows "Arr")
   - `HOME_LAT` / `HOME_LON` — only needed for `/map`; see [Map Page](#map-page)
3. **Install**: `pip install -r requirements.txt`
4. **Run locally**: `flask --app api/index run --debug`
5. **Visit**: http://localhost:5000

## Environment Variables

| Variable          | Default | Notes                                                                                                                                                                   |
| ----------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `API_KEY`         | —       | Required. LTA DataMall API key. Keep in `.env`, never in git.                                                                                                           |
| `BUS_STOP_CODES`  | —       | Required. Comma-separated stop codes (e.g. `28571,28519`). Find codes at DataMall or Google Maps.                                                                       |
| `BUS_STOP_NAMES`  | —       | Optional. Display names for each stop, comma-separated. Falls back to the code itself if missing.                                                                       |
| `REFRESH_SECONDS` | 20      | Poll interval in seconds. Doesn't affect how fast the countdown ticks (that's 1s client-side); just how often we fetch fresh data.                                      |
| `BUFFER_SECONDS`  | 30      | Seconds shaved off every ETA as a safety margin. Combined with floor-rounding, a 30s buffer means anything under 1m30s shows as "Arr" (red, bold). Set to 0 to disable. |
| `HOME_LAT`        | —       | Required for `/map` only. Latitude the map centres on and distances are measured from. No default — an address does not belong in the repo.                              |
| `HOME_LON`        | —       | Required for `/map` only. Longitude, as above.                                                                                                                          |
| `MAP_WINDOW_MINUTES` | 5    | Only buses arriving within this many minutes are pinned on `/map`. Home view only — picking a stop shows everything running there.                                       |
| `NEARBY_RADIUS_M` | 800     | How far from home the stop picker looks for stops to offer. Needs a `bus_stops.json` carrying coordinates; without one the picker lists only `BUS_STOP_CODES`.           |
| `WEATHER_API_KEY` | —       | Optional. `x-api-key` for data.gov.sg. The endpoint works without one; a key only raises the rate limit.                                                                 |
| `WEATHER_REGION`  | `west`  | Which NEA forecast region you are in: `north`, `south`, `east`, `west`, `central`. Cannot be derived from `HOME_LAT`/`HOME_LON` — the feed ships no boundaries.          |
| `WEATHER_TTL_SECONDS` | 900 | How long a fetched forecast is reused before re-fetching.                                                                                                              |
| `TEMPERATURE_TTL_SECONDS` | 300 | How long the live station temperatures are reused. Shorter than the forecast's: stations report every minute. |

If `HOME_LAT` / `HOME_LON` are unset or unparseable, `/map` says which variables to set and `/` is unaffected — the kiosk display never goes down over a setting only the map needs.

## How the ETA is Computed

Two safety mechanisms, both intentional:

1. **Floor, not round**: `int(seconds // 60)` — a bus 1m31s away shows "1 min", not "2 min". The error always lands toward "leave now" rather than "you have time."
2. **Buffer**: Subtracts `BUFFER_SECONDS` before flooring. A 30s buffer means the tile errs an extra ~30s early. Combined with flooring, this pushes anything under 1m30s to "Arr".

The result is deliberately conservative, not "accurate". A tile reading "2 min" means _at least_ 2 minutes — you might leave slightly early, but you won't miss the bus because the display said you had time.

## Client-Side Countdown

The number doesn't freeze between polls. The server sends both a floored minute count (for the initial paint) and an absolute arrival time as epoch milliseconds in a `data-arrival` attribute. JavaScript ticks every second, recomputes the countdown, and updates the text. This is why the display stays fresh even if the htmx poll timer stalls (e.g., when the kiosk screen sleeps).

The client-side buffer (`BUFFER_MS`) is templated in from the server's `BUFFER_SECONDS`, so they stay in sync — no manual alignment needed.

## Reading the Dashboard

Colour is the urgency tier, and it means the same thing on both pages:

| Colour | Meaning         |
| ------ | --------------- |
| Red    | "Arr" — inside the buffer band |
| Amber  | 1–5 min         |
| Green  | 6+ min          |

Within a service, the **next** bus is drawn 1.3× larger and bold at full opacity; the ones behind it sit at 70%. The eye lands on the number that decides whether you leave. The tier is recomputed client-side every second, so a bus ages green → amber → red without waiting for the next poll.

In portrait the 2×2 grid collapses to a single scrolling column, so the same page works on a phone as on the tablet.

## Destinations

Each bus shows where it is heading — `to Boon Lay Int`, `to Joo Koon Int`. It answers the question a service number alone can't at a stop served in both directions: *is this the one going my way?*

It sits under the service number on `/`, and in the right-hand context column on `/map`. That difference is deliberate: `/map` rows are one grid container each, so a variable-width destination beside the service number would land every row's ETA at a different x and make the list unscannable. The ETA column is pinned so the times share a left edge, and the destination rides with the stop name instead.

Two more things shape how it renders:

- **Loop services read `Loop`, not a destination.** Services like 334 and 98 report the same code for origin and destination. "to Jurong East Int" would be technically true and practically useless, since that is also where the bus started.
- **It is deliberately small and grey.** The ETA is still the number you read from across the room; the destination is there for the second glance, once you've already found the service.

The arrival feed names a destination only by bus stop code (`22009`), and the `BusStops` dataset that maps codes to names has no filter-by-code — resolving one destination means paging all ~5,200 stops. Doing that per request, on a serverless cold start, would add seconds to a page the tablet polls every 20s. So the lookup is baked into `data/bus_stops.json` and read once at import. It is a static asset, not a database; the app stays stateless at runtime.

**Regenerate it** when a stop gains a service whose destination isn't in the file:

```
python scripts/fetch_bus_stops.py
```

The map's route-following glide also needs `data/bus_routes.json` — every service's road line — baked **after** the stops file, since it places each stop on its line using the positions there:

```
python scripts/fetch_bus_routes.py
```

It downloads from [sgbusdata](https://github.com/cheeaun/sgbusdata) on GitHub (~550 KB, no DataMall key or calls) and takes a few seconds. Re-run it when routes change; sgbusdata refreshes from LTA on its own schedule, and the file records which update it was baked from. Without the file, pins glide in a straight line instead.

The file holds a name **and position** per stop — `{"name": ..., "lat": ..., "lon": ...}` — since the `BusStops` response carries coordinates in the same rows the names come from. Positions are what let the picker offer nearby stops and measure distance from a selected one.

A file predating that change (plain `"Hotel Grand Pacific"` values) is read without complaint; those features simply stay switched off until it is regenerated. The script prints how many stops came back without coordinates — anything other than a small number means the `Latitude`/`Longitude` fields have been renamed upstream.

A stale or missing file degrades rather than breaks: an unknown code falls back to displaying the raw code, and if the file is absent entirely every destination does. `vercel.json` includes `data/**` so the file ships with the function.

## Vehicle Type

DataMall reports whether each bus is single deck, double deck, or bendy. It is deliberately **not** drawn on the tiles — it is a curiosity, not something that changes whether you leave the flat, and the display's job is to survive a glance from across the room. So it stays out of the way until asked for:

- **Hover an ETA on `/`** or a row on `/map` — the browser tooltip names the type.
- **Tap a bus on `/map`** — the pin's popup lists it alongside distance and crowding.

**It is per arrival, not per service.** A service's queued buses are routinely a mix — a double decker followed by two single deckers is the common pattern here — so it could not be a per-service label like the destination.

The popup matters more than the tooltip: `title` tooltips need a mouse, so they never appear on the kiosk tablet or a phone. On touch, the map popup is the only place this surfaces.

`Feature` (`WAB`, wheelchair accessible) is not shown at all — every bus in this feed reports it, so it would mark nothing.

## Map Page

`/map` answers a different question: not *when* the bus arrives but *where it is now*. Buses arriving within `MAP_WINDOW_MINUTES` are pinned on a [OneMap](https://www.onemap.gov.sg/) layer via Leaflet, each pin labelled with its service number and coloured by the same tiers above. Below the map, the same buses are listed with ETA, distance, stop name and code, destination, and crowding. Tapping a row pans to that bus and opens its pin.

Filtering by **time** rather than by rank is deliberate: a service's second bus appears exactly when it is also imminent, so no "show all / show next" toggle is needed.

Three things worth knowing about the data:

- **Not every bus can be plotted.** DataMall only sends real coordinates when `Monitored` is `1`. On a schedule-derived estimate it sends the string `"0.0"`, which would place the bus in the Atlantic, so those buses are listed but never pinned — a bus two minutes away still matters when it has no GPS fix. The count of them appears under the list.
- **Distance is straight-line from home, by road from a picked stop.** No route passes the flat, so the home view measures as the crow flies — a bus 400 m away that way can be 2 km by road. Measured from a picked stop, a bus that fits its route quotes the drive (`1.3 km by road`); on live data that ran up to ~25% longer than the straight line.
- **Positions are up to ~20s stale.** That is DataMall's publish interval. Between fixes the pins glide towards the stop, paced by LTA's arrival time — see [Gliding pins](#gliding-pins) for what that does and does not claim.

Markers are keyed per vehicle, so a refresh moves a pin rather than rebuilding it — otherwise an open popup would slam shut every poll. The view is framed once on load and never re-fitted, so a refresh cannot yank the map while you are panning it.

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
| Pace | road distance to stop ÷ time LTA says remains | Pin arrives when the countdown says it will |
| Speed ceiling | 60 km/h | An "Arr" ETA for a bus 1 km out must not fling the pin across the map |
| Hold short of the stop | 80 m | Never "arrive" on a guess; the last stretch waits for a real fix |
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

### Picking a stop

The picker at the top switches the page to any stop: `/map?stop=28519`.

The dropdown holds the quick picks — your configured stops, plus nearby ones once the lookup has coordinates. The search box beside it reaches **any of the ~5,200 stops** by name or code, and works whether or not the lookup has positions: choosing a stop by name never needed one. Matching happens server-side and returns a dozen results at a time, rather than shipping a quarter of a megabyte of options to a phone on every page load.

The selection lives **in the URL**, not on the server. That is deliberate — an environment variable is shared by every page the app serves, so writing the selection back into one would mean tapping the picker on your phone changed what the kiosk tablet displays. It would also not survive: on Vercel each request may be answered by a different instance, so the write would apply to whichever machine happened to catch it. A query parameter is per-tab, bookmarkable, and cannot leak into `/`.

Picking a stop changes what the page means, so the home comparison is dropped entirely:

| | Home view (no selection) | Stop selected |
| --- | --- | --- |
| Question | "Should I leave the flat now?" | "What's coming at this stop?" |
| Distances measured from | `HOME` | That stop |
| Arrival window | `MAP_WINDOW_MINUTES` | None — everything running |
| Focus pin | Home | The stop |
| Needs `HOME_LAT`/`HOME_LON` | Yes | **No** |

Distances would be meaningless measured from the flat to a stop across the island, so they come from the selected stop instead — and are omitted entirely if its position cannot be established, which is better than quoting a number from the wrong origin. A bus with no GPS fix still says so; a tracked bus with no reference point simply shows nothing rather than contradicting its own pin.

The focused stop gets a **ring** marker, against the solid dot used for home — same colour, but you can tell at a glance whether the map is centred on where you live or on somewhere you went looking for.

Positions come from `data/bus_stops.json`. When it has none for the focused stop — an un-regenerated file — one filtered `BusStops` request fills the gap and is remembered for the life of the instance. That fallback is affordable only because the feed now filters by stop code (guide v6.8); asking used to mean paging ~5,000 rows, which is why the file is baked in the first place. It is a safety net, not the plan: a regenerated file skips it entirely. Rows whose code does not match are discarded, since an unsupported filter returns a page of unrelated stops rather than an error, and a pin placed from one of those would be plausible and wrong.

Stop codes from the URL are validated as five digits before being used, so a hand-edited URL cannot forward junk to DataMall.

### Your location

On a phone that shares its location, `/map` shows a blue **you are here** dot, with a faint circle for how sure the phone is, and it moves as you walk. A **You** entry joins the legend once the first position arrives.

If you opened `/map` with no stop chosen, the first position also **picks the stop nearest you**: the page switches to `/map?stop=<code>`, the same bookmarkable view as choosing it by hand. Three things keep that from getting in the way:

- **It only happens when no choice was made.** Picking **Near home** in the dropdown sends `?stop=` (present, but empty), and that is never overridden — the home view stays reachable.
- **Only within 2 km of a stop.** Further than that — or outside Singapore — the page stays as it was.
- **Nothing breaks without it.** Declining the browser's prompt, a device with no location (the kiosk, most desktops), or no connection all leave the page exactly as before. Browsers only offer location on HTTPS or `localhost`, which Vercel provides.

**Privacy.** The position is sent once, as a `POST` body to the app's own `/nearest-stop`, which compares it against `data/bus_stops.json` and keeps nothing. It never goes into a URL, so it cannot end up in server logs or browser history; only the chosen stop code does. Later positions only move the dot and never leave the phone.

Leaflet is loaded from a CDN; it is the only dependency beyond Flask, requests, python-dotenv and htmx. OneMap tiles require the SLA attribution that renders in the map's bottom-right corner — leave it in place.

### Tap a stop

From the zoom the page opens at (15) and closer, every bus stop in view is drawn as a small dim ring. Tap one for a popup with its name, code, and a **Show buses here** button, which opens `/map?stop=<code>` — the same view, link, and weather as choosing it in the search box. Zoomed out further, the rings disappear: 5,000 of them would be clutter, and too dense to hit with a finger.

- **One download, then nothing per pan.** `GET /stops.json` returns every stop as `[code, name, lat, lon]` (~230 KB, ~70 KB compressed), built at startup from the same `data/bus_stops.json` as the picker, and cached for a day (`public, max-age=86400`) so Vercel's CDN and the phone answer repeat visits. Each pan or zoom then picks out the stops inside the visible area in the browser — a sub-millisecond scan of 5,000 positions. Asking the server for "stops in this rectangle" instead would cost a request per pan and put the map's position in URLs.
- **Drawn on one canvas**, not as 5,000 page elements, so panning stays smooth on a phone. The ring is drawn small but its tap area is a finger's width (`tolerance: 10`).
- **Never in the way.** Canvas rings sit below Leaflet's markers, so a ring never covers a bus, the blue *you* dot, or the chosen stop — which keeps its own bigger ring and is not drawn twice.
- **Only what changed is redrawn.** Rings that leave the view are removed and new ones added; clearing everything would close an open popup, and opening a popup pans the map to fit it, which would trigger exactly that.
- **A popup, not an instant switch.** Panning a map on a phone often lands a stray tap; the popup lets you read the name before committing. The button is a real link, so Back returns to the previous stop.
- **Failure is just no rings.** If `/stops.json` can't load, the map works as before and the search box still finds any stop. The rings aren't keyboard-focusable (they are canvas drawings); the search box is the keyboard route to the same place.

## Weather

The `/map` header shows the weather **where the map is looking** instead of a page title — by the time you have opened the page you know what it shows, and what you don't know is whether to take an umbrella to the stop.

```
Thundery Showers
Bedok · 31°C now · 25–34°C today
```

### Local, not regional

"Where the map is looking" is the chosen stop, or home on the **Near home** view. On a phone that shares its location, the page has already switched to the stop nearest you (see [Your location](#your-location)), so the weather follows the phone **without the phone's position being used for it** — the server already knows the stop's coordinates. Picking a stop by hand shows the weather where you are going.

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
- **All three down** gives the plain "Buses near home" title.

The 2-hour window itself (`10.30 am to 12.30 pm`) is left off the header: at phone width it would push the line onto a second row, and "the next two hours" is what anyone glancing at a bus map assumes.

The icon is animated. It comes from [Meteocons](https://github.com/basmilius/weather-icons) by Bas Milius, **MIT licensed** — the fourteen SVGs actually used are vendored into `static/weather/` with the licence alongside them, as MIT requires. They animate via SMIL embedded in the file, which plays inside an `<img>` with no script and no library.

All 23 of NEA's forecast strings are mapped in `WEATHER_ICONS`. Four of them name a time of day (`Fair (Day)`, `Fair (Night)`, and the Partly Cloudy pair); the bare `Fair` and `Partly Cloudy` don't, so those are settled against the clock — sunrise and sunset barely move in Singapore, so a fixed 7am/7pm pair picks the right sun or moon without an ephemeris.

If NEA ever adds a value, it falls through to no icon rather than a broken image, and the text still renders.

Source is [data.gov.sg's real-time API](https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast), which wraps NEA's 24-hour forecast. Three things worth knowing:

- **The API key is optional.** The endpoint answers unauthenticated; `x-api-key` only raises the rate limit, and the spec marks it `required: false` with no `security` block. Set `WEATHER_API_KEY` if you have one, leave it blank if not — at one fetch per 15 minutes it makes no difference.
- **The region can't be derived from `HOME_LAT`/`HOME_LON`.** The feed names five regions but ships no boundaries for them, so `WEATHER_REGION` has to be told which one you are in. It is now only the fallback, used when the 2-hour feed has nothing for the stop. If NEA omits your region for a period, it falls back to the island-wide forecast.
- **It is cached for `WEATHER_TTL_SECONDS`** (default 900). NEA reissues a few times a day, so fetching per page load would spend requests redrawing the same words. The *record* is cached rather than the rendered text, so the period still advances against the clock inside a TTL.

The weather is fetched only on the `/map` page load, never in the `/map-list` poll — the list refreshes every 20s and the forecast does not.

**It cannot take the bus display down.** `read_weather()` returns `None` on any failure — connection error, timeout, HTTP error, malformed payload — rather than raising, and the header falls back to the plain "Buses near home" title. If a forecast was fetched earlier, an outage serves that last good reading instead of nothing. The arrivals page never calls it at all.

## Debug View

Visit `/arrivals?debug=1` to see per-bus:

- Raw `EstimatedArrival` string from DataMall
- `Monitored` flag (0 = schedule-derived, not live GPS)
- Exact float minutes vs. displayed rounded value
- Resolved destination — a raw code here instead of a name means it is missing from `data/bus_stops.json`
- Raw `Type` (`SD` / `DD` / `BD`) behind the vehicle icon

**Use case**: if a tile's time looks wildly off, check `Monitored` first. A `0` means DataMall estimated from the timetable, not a vehicle position — it can legitimately differ from a reference app by several minutes, and there's nothing we can do about it.

## Deploy to Vercel

1. **Push to GitHub** (or connect the repo in Vercel for auto-deploy on push):
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-github-url>
   git push -u origin main
   ```
2. **In Vercel**: Import the repo, set environment variables (`API_KEY`, `BUS_STOP_CODES`, `BUS_STOP_NAMES`, `REFRESH_SECONDS`, `BUFFER_SECONDS`, and — for `/map` — `HOME_LAT`, `HOME_LON`, `MAP_WINDOW_MINUTES`), deploy.

**Root Directory must be set to the repo root** so Vercel finds `api/index.py`. `vercel.json` uses an explicit `@vercel/python` build rather than auto-detection, so the function is built reliably and `templates/` and `static/` are bundled with it.

Note: `.env` is gitignored — set env vars in the Vercel dashboard, not the file, so you don't commit secrets. This applies to `HOME_LAT` / `HOME_LON` as much as to `API_KEY`: the repo is public.

## Testing

No formal test suite. To verify the timing logic works:

1. Hit `/arrivals?debug=1` and check that the float minutes in "Exact minutes" match the `EstimatedArrival` timestamp minus the server's current time.
2. Open `/` and watch a tile for 60+ seconds without refreshing — the number must count itself down on its own, never jump backward when htmx swaps.
3. Put the tablet to sleep for several minutes, wake it, and check the numbers are correct immediately (not frozen at the pre-sleep value).

For `/map`:

4. Cross-check a pin against `/arrivals?debug=1` — any bus showing `Monitored=0` must appear in the list without a pin, and be counted in the "no GPS fix" note.
5. Leave the page open for a minute. Pins should move without the map flickering or re-centring, and an open popup should survive a refresh.
6. Tap a row and confirm the map pans to that bus and opens its pin. Rows with no live position should do nothing.
7. Unset `HOME_LAT` and confirm `/map` explains what to set while `/` still works.
8. Run `node scripts/test_glide.js` — the pacing rules in `static/glide.js` as plain asserts.
9. Watch a pin for a minute: it should creep towards its stop between polls, stop ~80 m short, and never move backwards.
10. Run `node scripts/test_accuracy.js` — the judging rules in `static/accuracy.js`.
11. Leave `/map?stop=<a busy stop>` open for ten minutes. The "LTA vs actual" panel should appear once a tracked bus reaches the stop, and reloading should clear it.
12. Run `python scripts/test_route_path.py` — the polyline decoder and route fitting in `api/index.py`, against made-up roads including a loop.
13. On `/map?stop=<code>`, a tracked bus's distance should read `… by road`, and its pin should follow the road rather than cutting across.
14. Run `python scripts/test_nearest_stop.py` — picking the nearest stop, and `/nearest-stop` refusing bad input and GET.
15. On a phone, open `/map` and allow location: the blue dot should appear and the page should switch to your nearest stop. Choosing **Near home** afterwards must stay on the home view.
16. Run `python scripts/test_local_weather.py` — nearest area and station, a silent station skipped, and each fallback.
17. Open `/map?stop=<a stop in the east>` and `/map?stop=<one in the north>`: each header should name a different nearby area (`Bedok · …`, `Woodlands · …`).
18. Run `python scripts/test_stops_json.py` — the stop list's shape, its day-long caching, and that it matches the picker's lookup.
19. On `/map`, tap a stop ring: the popup should name it, and **Show buses here** should open that stop. Zoom out past 15 and the rings should go.
