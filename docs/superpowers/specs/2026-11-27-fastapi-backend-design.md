# v3 backend: Flask → FastAPI

Design and build order, 27 Nov 2026. Status: **draft for review**, revised
against the `karpathy-guidelines` skill (simplicity first, surgical changes,
a verifiable check for every step).

## Why

- **Learning.** FastAPI is the industry standard for Python web APIs, and v3
  is a learning journey. Claude writes the code; you learn from clear
  comments, clear PRs and an explanation of each step.
- **Efficiency.** The real gain is in how the backend *waits*: it currently
  calls LTA and NEA one after another, opens a fresh connection every time,
  and runs every static file through Python. Async FastAPI makes the first two
  easy to fix. The third is a Vercel config change.

## Main success measure

**Cold first load of `/map` on a phone**: what the family feels after the app
has sat idle. Likely causes today: the Vercel cold start (booting Python and
loading 1.3 MB of `data/*.json` at import), upstream calls made one after
another, and static files going through the function. Step 0 measures it,
every step checks it hasn't got worse (FastAPI's imports cost a little), and
Step 4 must show it improved. If loading the data files turns out to dominate,
that's a separate follow-up PR, not part of the migration.

## Goals

1. FastAPI replaces Flask. Every URL, HTML fragment and JSON shape the
   browser uses keeps working unchanged.
2. A page load costs the **slowest** upstream call, not the **sum** of them.
3. Several polls of the same stop within ~10 s cost **one** LTA call.
4. A slow NEA weather feed can't delay the bus times.
5. Static files (JS, CSS, icons) are served by Vercel's CDN, not by Python.

## Non-goals

- PWA, collector, push, database, Redis, front-end tooling (later modules).
- Any UI or behaviour change.
- Splitting `api/index.py` into modules, adding Pydantic models for internal
  data, settings classes, or new tooling (uv, Ruff, pytest). Each is a fine
  idea on its own, but none is needed to switch frameworks or to speed things up.
  They're listed under *Later, only if wanted*.

## Anti-goals

- Users notice anything different, apart from it being faster.
- More LTA calls than today.
- A PR you can't follow: mixed concerns, unrelated reformatting, or code moved
  around for no reason.
- Phone location ending up anywhere new: logs, headers, cache keys.

## Constraints

- Vercel serverless, `@vercel/python`, Python 3.12, `requirements.txt`.
- HTMX polls `/map-list` every 20 s per open tab.
- The tests in `scripts/` (run as `python scripts/test_x.py`) are the safety
  net. They change only where Flask's test client differs from FastAPI's, e.g.
  `.get_data(as_text=True)` → `.text` and `.get_json()` → `.json()`.

## Approaches (how to migrate)

- **A. Direct port on the migration branch, in small steps. Picked
  (my recommendation, please confirm).** The separate branch already keeps
  `main` safe, so no temporary scaffolding is needed. Step 1 swaps Flask for
  FastAPI with the code otherwise unchanged. Later steps each change one thing.
- B. "Strangler": run Flask inside FastAPI via `a2wsgi` and move routes over
  one at a time. Dropped: it adds a dependency and a step that only exist to be
  deleted, and the branch gives the same safety.
- C. Rewrite from scratch. Dropped: it throws away working, tested code.

## Branches

All work lives on `feature/fastapi-migration` (cut from `main` at
`dcf596d`). Each step gets its own short branch, e.g. `fastapi/step-1-port`,
and a PR into `feature/fastapi-migration`, with its own Vercel preview. One
final PR merges the migration into `main`.

## How each PR is written

- **One concern per PR.** The title says what changed; the description says
  *why*, *what changed*, and *how it was verified* (tests plus the preview check).
- **Comments explain why, not what**, in the style `index.py` already uses.
  New concepts (async, `await`, `lifespan`, the cache) get a short plain-English
  comment where they first appear.
- **Surgical diffs.** No reformatting, renaming or "tidying" of untouched
  code. Unrelated issues get mentioned in the PR, not fixed in it.
- Claude explains each PR in chat after opening it: what to look at first,
  and the one or two concepts it teaches.

## Build order

### Step 0: Baseline (no code)
Time the current production app before anything changes: server time for
`/map` and `/map-list` (one stop, six favourites) from the browser's Network
tab, plus one cold start. Also note a typical day's function invocations per
route from Vercel's **Usage / Observability** tabs. That's the stand-in for
"how many API calls a day" until exact counters arrive with the collector.
→ **verify:** numbers written into this doc, to compare against later.

**Baseline, 27 Nov 2026** (curl from Singapore against production, Flask,
`main` at `dcf596d`; TTFB = time until the first byte comes back):

| Request | TTFB |
|---|---|
| `/map?stop=84009`, cold (first after idle) | **3.0 s** |
| `/map?stop=84009`, warm (4 runs) | 0.64–0.88 s |
| `/map?stops=` six favourites, warm | 0.74–0.90 s |
| `/map-list?stop=84009` (the 20 s refresh) | 0.60–0.72 s |
| `/map-list?stops=` six favourites | 0.71–1.25 s |
| `static/glide.js`, `static/map.css` (each) | ~0.29 s |
| `/stops.json` (84 KB compressed) | 0.29 s TTFB, 0.96 s total |
| 404 on a missing static path (pure round trip) | 0.27 s |

What the response headers show:
- **`X-Vercel-Id: sin1::iad1`: the function runs in Washington DC (`iad1`)**,
  while the family and LTA/NEA are in Singapore. Every page request crosses
  the Pacific (~0.27 s on its own), and so does every LTA/NEA call the function
  makes. This is likely the single biggest cause of slow loads.
- Static files go through the function (`iad1`) with `Cache-Control: no-cache`,
  as expected (E6 / Step 4).
- `/stops.json` says `public, max-age=86400` but showed `X-Vercel-Cache: MISS`.
  Vercel's CDN probably needs `s-maxage` to cache it (check in Step 4).
- Responses are already Brotli-compressed by Vercel, so no compression work is needed.
- The `/map` HTML is ~90 KB (inline script), and `/map-list` is ~50 KB.

Vercel Usage/Observability per-route counts: to be read from the dashboard
(needs your login).

**After moving the function to `sin1`** (PR #23, a separate PR into `main`,
measured on its preview while warm): `/map` 0.11–0.24 s, `/map-list` one stop
0.10–0.11 s, six favourites 0.27–0.29 s, static files 0.05–0.07 s. That's
4–6× faster with no code change. **This is the baseline the FastAPI steps are
measured against**. Merged as `c7cfbf7`. Production now reads `sin1::sin1`.
The first `/map` right after that deploy took 0.65 s (was 3.0 s cold), then
0.37 s and 0.11 s. A true cold start after a long idle is still worth
re-checking.

### Step 1: Port to FastAPI, nothing else
- `Flask(...)` → `FastAPI()`. `render_template` → `Jinja2Templates`. Static
  files mounted with `StaticFiles`.
- Routes stay plain `def` (not `async` yet) and keep using `requests`.
  FastAPI runs plain `def` routes in a thread pool, so the behaviour stays the same.
- `/nearest-stop` keeps its existing hand-written checks and its 400
  responses, so its test is unchanged. (FastAPI's usual way is a Pydantic
  model, which returns 422. It's mentioned in the PR as an option, not done.)
- Template changes: `url_for('static', filename=…)` → `url_for('static', path=…)`,
  and `request.query_string.decode()` → `request.url.query`.
- `requirements.txt`: `flask` out, `fastapi`, `jinja2` in.
- *Learn:* FastAPI routes, query params, templates, ASGI, `/docs`.
→ **verify:** every `scripts/test_*.py` and the Node tests pass. The preview
loads `/map`, search, favourites and the tapped-bus route exactly as before.
The first preview also confirms `@vercel/python` runs the FastAPI app.

### Step 2: Call LTA and NEA concurrently
- One shared `httpx.AsyncClient` (opened at startup in FastAPI's `lifespan`)
  replaces `requests`, so connections are reused between calls.
- `get_bus_arrival` and `fetch_feed` become `async`. `fetch_stops` uses
  `asyncio.gather` instead of the `ThreadPoolExecutor`.
- `/map` gathers arrivals and weather **together**. The weather gets a time limit
  (`asyncio.wait_for`, ~1.5 s); past it the page renders with
  `weather=None`, which the template already handles.
- Routes that call upstream become `async def`. The CPU-only work
  (`collect_map_buses`) stays as it is unless Step 4's numbers say otherwise.
- `requirements.txt`: `requests` out, `httpx` in.
- *Learn:* async/await, the event loop, `gather`, timeouts.
→ **verify:** tests pass, with fakes made async. A new test proves a slow fake
weather call doesn't delay the page past the limit. The preview's `/map` server
time is about the slowest single call, not the sum (compare with Step 0).

### Step 3: Cache each stop's LTA answer for 10 seconds
- A plain dict, `{stop_code: (fetched_at, raw_json)}`, the same pattern as
  `_feed_cache`. The **raw** JSON is cached and minutes are recomputed against the
  current time on every request, so countdowns stay correct.
- No locks or coalescing. Add them only if Step 4 shows concurrent misses
  are a real cost.
- *Learn:* caching, TTLs, why cache raw data rather than finished output.
→ **verify:** a test shows two requests for the same stop within 10 s make
one upstream call, and one 11 s later makes a second.

### Step 4: Static files from the CDN, then measure
- `vercel.json`: serve `static/` directly with a modest `Cache-Control`
  (e.g. 1 hour), so a deploy shows up within the hour and no cache-busting is
  needed.
- Add a `Server-Timing` header (upstream time, total time) to `/map` and
  `/map-list`, so DevTools shows where time goes. It holds durations only.
- *Learn:* CDNs, cache headers, measuring before tuning.
→ **verify:** static requests no longer show as function invocations in
Vercel. The Step 0 numbers are re-measured and written into this doc.

### Step 5: Final PR into `main`
Update the README (how to run locally with `uvicorn`).
→ **verify:** the production deploy matches the last preview.

## Later, only if wanted

Each of these would be its own small PR, done only if you ask or the numbers
say so:
- Pydantic model for `/nearest-stop` (FastAPI-idiomatic, changes 400 → 422).
- Split `index.py` into modules once v3 adds code to it.
- Locks to coalesce concurrent misses; edge-caching `/map-list`.
- CI on GitHub Actions; pytest, Ruff, uv.
- Faster cold start by loading the data files more lazily, if Step 0/4
  numbers show they dominate.
- Leaflet → MapLibre GL. Leaflet is a fine standard; only revisit if the map
  feels heavy on phones.
- Exact daily LTA/NEA call counts (a per-day counter in Upstash Redis).
  Planned for the **collector module**, which sets up storage anyway.

## Who uses it

Mostly family: a few phones plus the kiosk tablet, likely sharing the same
home stops. So the Step 3 cache stays a plain dict with no locks, and there's
no accounts or scaling work. The kiosk is only switched on ad hoc around
peak hours, so there's no overnight polling to cut and no "quiet hours"
feature is needed.

## My calls (check these)

- Approach A (direct port) instead of the strangler I suggested before.
- `/nearest-stop` keeps 400 and its hand-written checks in Step 1.
- 10 s arrivals cache, 1.5 s weather limit, 1 h static cache.
- Keep the existing `builds` block in `vercel.json`; add only the static route.
