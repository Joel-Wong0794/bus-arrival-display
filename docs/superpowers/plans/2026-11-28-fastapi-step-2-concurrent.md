# FastAPI Step 2: Call LTA and NEA Concurrently — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A page load costs about the slowest upstream call instead of their sum. A slow NEA never holds the bus times back by more than ~1.5 s.

**Architecture:**
- `requests` is replaced by one shared `httpx2.AsyncClient`, opened and closed in FastAPI's `lifespan`.
- The four functions that call upstream become `async def`: `get_bus_arrival`, `fetch_feed`, `fetch_stop_position` and `fetch_stops`. `fetch_stops` uses `asyncio.gather` in place of its `ThreadPoolExecutor`.
- `/map` gathers the arrivals and the weather together. The weather runs under `asyncio.wait_for`.
- The three routes that reach upstream become `async def`: `/map`, `/map-list` and `/debug`. Everything CPU-only stays plain `def`: `collect_map_buses`, `route_path` and so on.

**Tech Stack:** FastAPI 0.165 / Starlette 1.10, `httpx2` 2.16 (new in production), `asyncio`, Vercel `@vercel/python`.

**Spec:** `docs/superpowers/specs/2026-11-27-fastapi-backend-design.md` (Step 2)

## Global Constraints

- Vercel serverless, `@vercel/python`, Python 3.12, region sin1. `vercel.json` is not touched.
- Every URL, HTML fragment and JSON shape stays the same. The only visible change: when NEA is slower than the limit, a page loads without the weather chip.
- No caching beyond what already exists. The LTA cache is Step 3.
- No `Server-Timing` header (that is Step 4).
- No README changes (that is Step 5).
- Surgical diff: functions turn `async` and their callers `await` them, and nothing else is renamed, moved or reformatted.
- Per-call upstream timeouts stay as they are: 10 s for LTA arrivals, 5 s for the NEA feeds and the stop-position lookup.
- Comments explain *why*, in `index.py`'s existing style. New concepts get a short plain-English comment where they first appear:
  - `async`/`await`
  - the event loop
  - `lifespan`
  - `asyncio.gather`
  - `asyncio.wait_for`
- Branch `fastapi/step-2-concurrent`, cut from `feature/fastapi-migration` at `efe0c40`. The PR goes **into `feature/fastapi-migration`, not `main`**.

## Decisions this plan makes (please check)

1. **`httpx2`, not `httpx`.** The spec says `httpx`. Facts checked on 28 Nov:
   - httpx2 is the successor to httpx, by the same author, Tom Christie. It lives at `github.com/pydantic/httpx2` and is marked *Production/Stable*. It has had 22 releases since 2.0.0 in May 2026; the latest is 2.16.2 from 16 Nov.
   - Plain httpx is maintained too (0.29.0 came out on 26 Nov).
   - Starlette 1.10, which we already pin, builds its `TestClient` on httpx2 and deprecates httpx for it. httpx2 is therefore already installed for the tests.
   - Choosing httpx2 means one HTTP library for both the app and the tests. Its API is the same as httpx's: `AsyncClient`, `Timeout`, `HTTPError` and `MockTransport` are all there. It will be pinned `httpx2~=2.16`.
   - Cost if wrong: it is younger than httpx, so a breaking change is more likely. The pin limits that, and moving back to httpx is a rename of the import.
2. **The client is a module global, `http_client`, set in `lifespan`.** The spec asks for `lifespan`. A module global, rather than `app.state`, lets the deep functions (`fetch_feed`, `fetch_stop_position`) reach the client without passing a `request` down through every call. The global starts as `None`. If `lifespan` never ran, every upstream call fails with a loud 500 instead of quietly working without a shared client. Task 4 checks that Vercel runs `lifespan` with our legacy `builds` config. Vercel announced `lifespan` support in Dec 2025, but only for the zero-config FastAPI setup.
3. **`follow_redirects=True` on the client.** `requests` followed redirects by default, and httpx2 doesn't. Without this, an upstream that moves to a new URL turns into errors (an "Unavailable" stop, a blank weather chip) where it used to just work.
4. **The client keeps its default connection limits** (keep-alive expiry 5 s, 20 idle connections). This is a deliberate choice: the spec's promise of "connections reused between calls" holds when calls come within 5 s of each other, which happens under real traffic. One viewer's 20 s poll will still open a fresh connection each time. A longer expiry would bring the risk of reusing a socket that has gone stale while the function slept between requests. Step 4's measurements decide whether tuning is worth it.
5. **`read_weather` also gathers its own three feeds** (the 24-hour record, the 2-hour nowcast and the temperature). The spec only says "weather together with arrivals". Left one after another, those three calls would use up most of the 1.5 s limit on their own. They are independent, so they gather like the stops do.
6. **The stop-position fallback moves out of `stop_position`.** `stop_position` is called deep inside `collect_map_buses`, which is CPU-only and should stay a plain `def`. So a new `async def ensure_positions(codes)` asks DataMall for any stop that the baked file has no position for, all at once, before that work starts. `stop_position` then only reads the baked file or that cache. With an up-to-date `data/bus_stops.json` this costs nothing. It is a fallback, as before.
7. **The weather place is worked out before the arrivals arrive, so the two can run in parallel.** Today `/map` takes the weather place from the finished view (`focus_lat`/`focus_lon`). The new code takes it as the position of the first stop the view asks for. For `?stop=` and `?stops=` that is the same place the old code chose, and the tests pin it.
8. **The weather limit is a constant, `WEATHER_BUDGET_SECONDS = 1.5`, not an env setting.** The spec says "~1.5 s", and nothing needs to tune it per deploy.
9. **`requests` moves to `requirements-dev.txt`.** The app no longer uses it, but the offline bake scripts `scripts/fetch_bus_stops.py` and `fetch_bus_routes.py` still do. `httpx2` moves from the dev file into `requirements.txt`.
10. **Two code commits on one branch**, one each for Task 2 and Task 3. Each is green on its own, so `git bisect` stays useful.

## Known differences (go in the PR description, none fixed here)

- When NEA takes longer than 1.5 s, the page loads without weather, and that fetch is cancelled before it can fill the cache. If NEA is *always* slower than 1.5 s, the weather never shows. The old code waited, up to 5 s per feed. If that ever happens in practice, the fix is to let the fetch finish in the background, but Vercel may freeze a function once it has answered, so that idea needs its own test first.
- The CPU work (fitting each bus to its route) now runs on the event loop instead of in a thread pool. Python's GIL already ran it one request at a time, so throughput should hold. While it runs, other requests' network waits are not looked after. Task 4 measures this.
- Cold-start imports get about 60 ms lighter: `fastapi` plus `requests` measured ~500 ms locally, `fastapi` plus `httpx2` ~440 ms.
- httpx2 checks HTTPS certificates against the operating system's certificate store (through `truststore`), while requests used the `certifi` bundle. Task 4 checks that this works on Vercel.

## Review Focus

The ways this step is most likely to break that the existing tests don't exercise, most likely first. Each has a test in the task named after it.

- **LTA answers with something that isn't JSON** (an HTML error page, a cut-off body). requests raised a `RequestException` subclass for this, so the stop showed "Unavailable". httpx2 raises a plain `ValueError`. That stop must still say "Unavailable", the other stops must still show, and the page must not return a 500 (Task 2).
- **One stop's call fails while the others succeed.** `gather` must give back every stop, in the order asked for, with only the failed one marked (Task 2).
- **`lifespan` doesn't run on Vercel**, which would make every page a 500. Locally, a test shows the client is opened on startup and closed on shutdown (Task 2). On the preview, a page with live buses proves it (Task 4).
- **A slow NEA** must not delay `/map` much past 1.5 s, and the page still renders, without the weather chip (Task 3).
- **The weather place stays the same** for `?stop=`, `?stops=`, both together (`?stop=` wins), and the start screen (no place) (Task 3).

---

### Task 1: Measure before

**Files:** none committed. Numbers go into the ledger and later into the PR description.

- [ ] **Step 1: Time the current preview.** You sign in to Vercel in the browser pane. Then, on `https://bus-arrival-display-ivghcmpzx-jo-1e98.vercel.app` (the preview for `efe0c40`), use `fetch` timing in the page (`performance.getEntriesByType("resource")` or `Date.now()` around `fetch`) to measure:
  - the first `/map?stop=84009` (cold)
  - then 5 warm runs each of `/map?stop=84009`, `/map-list?stop=84009` and `/map?stops=` with six favourites (the same six as Step 0)

  Expected: a cold time, and warm medians close to Step 0's numbers (`/map` 0.11–0.24 s, six favourites 0.27–0.29 s). Write all of them to the ledger.
- [ ] **Step 2: Local import time.** Run `python -c "import time;t=time.perf_counter();import index;print(time.perf_counter()-t)"` from `api/` 5 times, with `API_KEY=test`. Write the median to the ledger.
- [ ] **Step 3: Local load test, before.** Use a scratch script in the plan workspace, never committed. It:
  - imports `index`
  - replaces `get_bus_arrival` with a fake that sleeps 150 ms and returns one bus, and `read_weather` with one that sleeps 150 ms
  - runs `uvicorn` on 127.0.0.1:8001
  - sends 600 mixed requests (`/map?stop=84009`, `/map-list?stops=84009,11119`, `/stops.json`) from 50 concurrent clients

  The fakes must be plain functions for this run and `async` for the after run in Task 4. The script switches on `inspect.iscoroutinefunction(index.get_bus_arrival)`. Expected: no 5xx. Write req/s, p50 and p95 to the ledger.

### Task 2: Async upstream calls on one shared client

**Files:**
- Modify: `api/index.py`. The imports; `fetch_feed`, `weather_record`, `local_forecast` and `local_temperature` (`async`; the last three only `await fetch_feed`); `fetch_stop_position`, plus a new `ensure_positions`; `stop_position`; `get_bus_arrival`; `fetch_stops`; `stop_context`, `favourites_context` and `map_context` (`async`); the `map_list` and `debug` routes; a new `lifespan`.
- Modify: `requirements.txt`, `requirements-dev.txt`
- Create: `scripts/test_upstream.py`
- Modify: `scripts/test_map_views.py`, `scripts/test_local_weather.py`. The fakes become `async`, and `read_weather` is run with `asyncio.run`.

**Interfaces (Produces):**
- `http_client: httpx2.AsyncClient | None`: a module global, set by `lifespan`.
- `async def lifespan(app: FastAPI)`: an `@contextlib.asynccontextmanager`, passed as `FastAPI(lifespan=lifespan)`. It opens `httpx2.AsyncClient(follow_redirects=True)` and closes it on exit.
- `async def get_bus_arrival(api_key: str, stop_code: str, now: datetime) -> list[tuple[str, list[dict]]]`
- `async def fetch_feed(url: str, now: datetime, ttl_seconds: int) -> dict | None`
- `async def read_weather(now: datetime, place: tuple | None = None) -> dict | None`: gathers its three feeds (Decision 5).
- `async def fetch_stop_position(code: str) -> tuple[float, float] | None`
- `async def ensure_positions(codes: list[str]) -> None`: fills `_position_cache` for each code that has no baked position and isn't cached yet, all at once.
- `def stop_position(code: str) -> tuple[float, float] | None`: stays sync, and reads only the baked file, then `_position_cache`.
- `async def fetch_stops(now: datetime, stops: list[tuple[str, str]]) -> list[dict]`: the same output shape and order as today.
- `async def map_context(now: datetime, args) -> dict`, plus `stop_context` and `favourites_context`, both `async`. Each context function `await`s `ensure_positions` for its codes first.
- What each function catches:
  - `fetch_stops`: `(httpx2.HTTPError, ValueError)` → "Unavailable"
  - `fetch_feed`: `(httpx2.HTTPError, ValueError, KeyError, TypeError)`
  - `fetch_stop_position`: `(httpx2.HTTPError, ValueError)`

- [ ] **Step 1: Write the failing test `scripts/test_upstream.py`.** No network. `index.http_client` is replaced with `httpx2.AsyncClient(transport=httpx2.MockTransport(handler))`, so the real request code runs against canned answers. Checks:
  - **Arrivals parse and carry the key:** the request has an `AccountKey` header equal to `index.API_KEY` and `BusStopCode` set to `84009`. One service, "10", comes back with one bus.
  - **Concurrency:** the handler is `async` and sleeps 0.3 s. `fetch_stops` for three stops finishes in under 0.6 s; one after another would take 0.9 s.
  - **Order and isolation:** for stops `[84009 (ok), 99999 (HTTP 500), 88888 (body "not json")]`, `[r["error"] for r in rows] == [None, "Unavailable", "Unavailable"]` and `rows[0]["services"]` is not empty. *(Review Focus 1, 2)*
  - **Redirects are followed:** a `301` to a second URL that answers OK gives the parsed services. (Decision 3)
  - **The feed cache:** the first `fetch_feed` returns `data`. After the TTL, a 500 returns the same last good copy. A `httpx2.ConnectTimeout` raised by the handler does the same.
  - **The position fallback:** with a code missing from `BUS_STOPS`, `asyncio.run(index.ensure_positions([code]))` puts the handler's position into `index.stop_position(code)`, after one call. A second `ensure_positions` makes no new call. A reply whose `BusStopCode` doesn't match gives `None`.
  - **A non-JSON LTA answer through the route:** with that handler, `TestClient(index.app).get("/map-list?stop=88888")` returns 200. *(Review Focus 1)* The test client is created without `with`, so `lifespan` doesn't run and the mock client stays in place. 88888 isn't in `BUS_STOPS`, so the handler must also answer the BusStops URL: it dispatches on `request.url.path`.
  - **Lifespan:** this check goes **last** in the file, because it swaps in a real client and then closes it. Inside `with TestClient(index.app):`, `index.http_client` is an open `httpx2.AsyncClient`. After the `with` block, the client it held `.is_closed`. *(Review Focus 3)*

  Update `test_map_views.py` (`async def fake_arrival`, and `read_weather` fakes as `async def`) and `test_local_weather.py` (`async` `fetch_feed` fake, and each `index.read_weather(...)` becomes `asyncio.run(index.read_weather(...))`). Their assertions stay the same.
- [ ] **Step 2: Run the tests and watch them fail.** Run: `python scripts/test_upstream.py`, then the other four Python test scripts. Expected: `test_upstream.py` fails at `index.http_client`, with `AttributeError`. The updated scripts fail too, because the routes call the async fakes synchronously.
- [ ] **Step 3: Implement the Interfaces block above in `api/index.py`.**
  - Remove `requests` and `ThreadPoolExecutor`. `fetch_stops` gathers `one(stop)` over all the stops.
  - Change `requirements.txt` from `requests` to `httpx2~=2.16`, with a one-line why-comment.
  - In `requirements-dev.txt`, the `httpx2` line goes and `requests  # the offline bake scripts in scripts/fetch_*.py` comes in.
  - Replace the route comment above `bus_map`, since routes are no longer all plain `def`. It should say why the routes that reach upstream are `async`, and why CPU-only work stays sync.
  - `bus_map` changes only as much as this task needs: `context = await map_context(...)` and `"weather": await read_weather(now, place)`, with the route made `async def`. Task 3 restructures it.
- [ ] **Step 4: Run all tests.** Run: each `scripts/test_*.py`, plus `node scripts/test_glide.js`, `node scripts/test_accuracy.js` and `node scripts/test_favourites.js`. Expected: each prints "all checks passed". Also run `python -W error::DeprecationWarning scripts/test_upstream.py`. Expected: passes, with no httpx deprecation warning.
- [ ] **Step 5: Commit.** Run `git add api/index.py requirements.txt requirements-dev.txt scripts/test_upstream.py scripts/test_map_views.py scripts/test_local_weather.py`, then `git commit -m "Call upstream with one shared async client"`.

### Task 3: `/map` gathers arrivals and weather, with a 1.5 s limit for weather

**Files:**
- Modify: `api/index.py`: the `bus_map` route; a new `WEATHER_BUDGET_SECONDS`; new `view_codes` and `weather_in_time`.
- Modify: `scripts/test_map_views.py`

**Interfaces:**
- Consumes: from Task 2, `map_context`, `read_weather`, `ensure_positions` and `stop_position`.
- Produces:
  - `WEATHER_BUDGET_SECONDS = 1.5`
  - `def view_codes(args) -> list[str]`: `[code]` for a valid `?stop=`, otherwise `valid_stop_codes(args.get("stops"))`, which is `[]` on the start screen.
  - `async def weather_in_time(now: datetime, place: tuple | None) -> dict | None`: `read_weather` under `asyncio.wait_for(..., WEATHER_BUDGET_SECONDS)`. A `TimeoutError` gives `None`.
  - `bus_map` does three things in order:
    1. `await ensure_positions(view_codes(args))`
    2. `place` = the first of those codes that has a `stop_position`, or `None`
    3. `context, weather = await asyncio.gather(map_context(now, args), weather_in_time(now, place))`

    The old `focus` lines go.

- [ ] **Step 1: Write the failing tests in `test_map_views.py`.**
  - **Arrivals and weather together:** the arrival fake sleeps 0.4 s and the weather fake sleeps 0.4 s. `/map?stop=84009` takes under 0.7 s, where the sum would be 0.8 s plus overhead.
  - **Weather limit:** the weather fake sleeps 5 s. `/map?stop=84009` returns 200 in under `index.WEATHER_BUDGET_SECONDS + 0.5` s, still has `'class="plate">10<'`, and has no `class="wx"`. *(Review Focus 4)*
  - **Weather place:** the weather fake records `place`.
    - `/map?stop=84009` gives `index.stop_position("84009")`.
    - `/map?stops=11119,84009` gives `stop_position("11119")`.
    - `/map?stop=84009&stops=11119` gives `stop_position("84009")`.
    - `/` gives `None`.

    *(Review Focus 5)*

  Reset both fakes to instant ones after these checks, so the rest of the file stays fast.
- [ ] **Step 2: Run the tests and watch them fail.** Run: `python scripts/test_map_views.py`. Expected: the "together" timing check fails, because Task 2 left `bus_map` calling the two one after the other.
- [ ] **Step 3: Implement the Interfaces block in `api/index.py`.** Give `weather_in_time` a why-comment: the weather is a garnish, so a slow NEA costs the page its chip, never its buses.
- [ ] **Step 4: Run all tests.** Run them as in Task 2, Step 4. Expected: all pass.
- [ ] **Step 5: Commit.** Run `git add api/index.py scripts/test_map_views.py`, then `git commit -m "Fetch arrivals and weather together, weather within 1.5 s"`.

### Task 4: Verify on the preview, then open the PR

**Files:** none, unless a ruling needs one.

- [ ] **Step 1: Local load test, after.** Run Task 1 Step 3's script again, with the fakes now `async`. Expected:
  - no 5xx
  - req/s no lower than before
  - p95 no worse than before by more than 20 %

  If p95 regresses past that, the CPU work on the event loop is the suspect (see Known differences). Ledger it and raise it in the PR; don't fix it here.
- [ ] **Step 2: Local import time, after.** Run the same command as Task 1 Step 2. Expected: about 60 ms faster than before.
- [ ] **Step 3: Push, and get the preview URL.** Run `git push -u origin fastapi/step-2-concurrent`, then open the PR into `feature/fastapi-migration`. Its description holds the before/after numbers, the Decisions, the Known differences and the rulings. Expected: Vercel posts a preview URL.
- [ ] **Step 4: Check the preview.** In the signed-in pane, load `/map?stop=84009`. Expected:
  - live buses, which proves that `lifespan` ran and that HTTPS works with the system certificates
  - the weather chip
  - a `/map-list` poll that succeeds

  Then repeat Task 1 Step 1's timings. Expected: six favourites warm at about the time of one stop, not the sum. `/map` warm should be no slower than before. Ledger the numbers.
- [ ] **Step 5: Final whole-branch review**, as executing-plans describes, then hand back for you to merge.
