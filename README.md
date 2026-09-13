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

`/map` answers a different question: not *when* the bus arrives but *where it is now*. Buses arriving within `MAP_WINDOW_MINUTES` are pinned on a [OneMap](https://www.onemap.gov.sg/) layer via Leaflet, each pin labelled with its service number and coloured by the same tiers above. Below the map, the same buses are listed with ETA, straight-line distance, stop name and code, destination, and crowding. Tapping a row pans to that bus and opens its pin.

Filtering by **time** rather than by rank is deliberate: a service's second bus appears exactly when it is also imminent, so no "show all / show next" toggle is needed.

Three things worth knowing about the data:

- **Not every bus can be plotted.** DataMall only sends real coordinates when `Monitored` is `1`. On a schedule-derived estimate it sends the string `"0.0"`, which would place the bus in the Atlantic, so those buses are listed but never pinned — a bus two minutes away still matters when it has no GPS fix. The count of them appears under the list.
- **Distance is straight-line, not road distance.** A bus 400 m away as the crow flies can be 2 km away by road.
- **Positions are up to ~20s stale.** That is DataMall's publish interval. Markers jump rather than glide, because interpolating would imply a precision the feed does not have.

Markers are keyed per vehicle, so a refresh moves a pin rather than rebuilding it — otherwise an open popup would slam shut every poll. The view is framed once on load and never re-fitted, so a refresh cannot yank the map while you are panning it.

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

Distances would be meaningless measured from the flat to a stop across the island, so they come from the selected stop instead — and are omitted entirely if the lookup has no coordinates for it, which is better than quoting a number from the wrong origin. A bus with no GPS fix still says so; a tracked bus with no reference point simply shows nothing rather than contradicting its own pin.

Stop codes from the URL are validated as five digits before being used, so a hand-edited URL cannot forward junk to DataMall.

Leaflet is loaded from a CDN; it is the only dependency beyond Flask, requests, python-dotenv and htmx. OneMap tiles require the SLA attribution that renders in the map's bottom-right corner — leave it in place.

## Weather

The `/map` header shows NEA's current forecast for your region instead of a page title — by the time you have opened the page you know what it shows, and what you don't know is whether to take an umbrella to the stop.

```
Cloudy
24–34°C · 6 pm 10 Sep to 6 am 11 Sep
```

The icon is animated. It comes from [Meteocons](https://github.com/basmilius/weather-icons) by Bas Milius, **MIT licensed** — the fourteen SVGs actually used are vendored into `static/weather/` with the licence alongside them, as MIT requires. They animate via SMIL embedded in the file, which plays inside an `<img>` with no script and no library.

All 23 of NEA's forecast strings are mapped in `WEATHER_ICONS`. Four of them name a time of day (`Fair (Day)`, `Fair (Night)`, and the Partly Cloudy pair); the bare `Fair` and `Partly Cloudy` don't, so those are settled against the clock — sunrise and sunset barely move in Singapore, so a fixed 7am/7pm pair picks the right sun or moon without an ephemeris.

If NEA ever adds a value, it falls through to no icon rather than a broken image, and the text still renders.

Source is [data.gov.sg's real-time API](https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast), which wraps NEA's 24-hour forecast. Three things worth knowing:

- **The API key is optional.** The endpoint answers unauthenticated; `x-api-key` only raises the rate limit, and the spec marks it `required: false` with no `security` block. Set `WEATHER_API_KEY` if you have one, leave it blank if not — at one fetch per 15 minutes it makes no difference.
- **The region can't be derived from `HOME_LAT`/`HOME_LON`.** The feed names five regions but ships no boundaries for them, so `WEATHER_REGION` has to be told which one you are in. If NEA omits your region for a period, it falls back to the island-wide forecast.
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
