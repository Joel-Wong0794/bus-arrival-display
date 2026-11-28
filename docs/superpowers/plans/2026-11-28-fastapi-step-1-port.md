# FastAPI Step 1: Port, Nothing Else — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap Flask for FastAPI in `api/index.py` so every URL, HTML fragment and JSON shape the browser uses behaves exactly as before.

**Architecture:** One file changes framework: `Flask(...)` becomes `FastAPI()`, `render_template` becomes `Jinja2Templates.TemplateResponse`, and `/static` is mounted with `StaticFiles`. Routes stay plain `def` and keep using `requests`. FastAPI runs plain `def` routes in a thread pool, so blocking calls behave as before. The one exception is `/nearest-stop`, explained in Decision 1. Templates change in two places, and the four test scripts that use Flask's test client move to FastAPI's.

**Tech Stack:** FastAPI 0.165 (Starlette 1.x), Jinja2 3.1, `requests` (unchanged), `httpx` (test client only), `uvicorn` (local dev only), Vercel `@vercel/python` (ASGI).

**Spec:** `docs/superpowers/specs/2026-11-27-fastapi-backend-design.md` (Step 1)

## Global Constraints

- Vercel serverless, `@vercel/python`, Python 3.12, `requirements.txt`. `vercel.json` is not touched.
- Every URL, HTML fragment and JSON shape the browser uses keeps working unchanged. No UI or behaviour change.
- Routes stay plain `def` and keep using `requests`. No `httpx` calls, no `lifespan`, no caching (those are Steps 2–3).
- `/nearest-stop` keeps its hand-written checks and its 400 responses. No Pydantic model.
- Surgical diff: no reformatting, renaming or moving of untouched code. Unrelated issues go in the PR description, not the diff.
- Comments explain *why*, in `index.py`'s existing style. New concepts get a short plain-English comment where they first appear: ASGI, `Request`, `TemplateResponse`, `StaticFiles`, declared query parameters, `async def`/`await`.
- Branch `fastapi/step-1-port`, cut from `feature/fastapi-migration`. The PR goes **into `feature/fastapi-migration`, not `main`**.
- Tests change only where Flask's test client differs from FastAPI's, plus the Review Focus checks below.

## Decisions this plan makes (please check)

The spec leaves these open, or says something slightly different. Each is the smallest choice that keeps the port behaving the same.

1. **`/nearest-stop` becomes `async def`.** A plain `def` route in FastAPI can't read the raw request body. The alternative, a declared body parameter, makes FastAPI answer bad JSON with **422**, which breaks the spec's "keep 400". `await request.json()` inside `async def` keeps the hand-written checks word for word. This is safe on the event loop because the route makes no network call: `nearest_stop` is an in-memory loop over about 5,000 stops that takes under a millisecond.
2. **Static URLs use `url_for(...).path`.** Starlette's `url_for` builds an *absolute* URL (`http://host/static/glide.js`). Behind Vercel's proxy the scheme the app sees can be `http`, and an `http://` script on an `https://` page is blocked as mixed content. Flask wrote `/static/glide.js`, and `.path` gives exactly that, so the HTML stays the same.
3. **`requirements-dev.txt` for `httpx2` and `uvicorn`.** FastAPI's `TestClient` needs an HTTP client library, and running locally needs `uvicorn`, but production uses neither. Keeping them out of `requirements.txt` keeps the function bundle lean, which matters because cold start is the main success measure. Starlette 1.10 deprecates `httpx` for its test client in favour of `httpx2`, the same author's successor (checked: with `httpx` it warns, with `httpx2` it's silent). **This affects Step 2**, which the spec writes as `httpx.AsyncClient`: Step 2's plan should choose between `httpx` and `httpx2` then, not now.
4. **New dependencies pinned with `~=`** (`fastapi~=0.165.0`, `jinja2~=3.1`). FastAPI is still on 0.x, so a minor release can break things, and without a pin any redeploy could pull one in. `flask`, `requests` and `python-dotenv` keep their current style (surgical). A full lockfile is under *Later* (uv).
5. **Declared query parameters where a route reads one key** (`/stops?q=`, `/route.json?service=&pattern=`, `/debug?stop=`). This is FastAPI's idiom, and those parameters then appear in `/docs`. They are all declared as `str`, so bad input still reaches the existing hand-written 404 and fallback paths instead of FastAPI's 422. `/map` and `/map-list` keep passing `request.query_params` to `map_context(now, args)`, which already reads its two keys with `.get` and works unchanged.
6. **One commit for the port and its tests.** Swapping the test client and porting the app have to land together. This repo merges with merge commits, so a red "tests only" commit would end up in `main`'s history and break `git bisect`. TDD's red run still happens (Task 1, Step 5); it just isn't committed.
7. **Housekeeping (already done):** `feature/fastapi-migration` was fast-forwarded to `main` (`285a32b`, which brings in PR #24) before the plan commit, so Step 1 isn't built on stale code. The branch isn't on GitHub yet. Task 1 pushes it, since it is the PR's base.

## Known differences (go in the PR description, none fixed here)

- Static files lose Flask's `Cache-Control: no-cache` header. Starlette sends `ETag` and `Last-Modified` without a `Cache-Control`. Step 4 sets proper caching, and this branch doesn't reach production before then.
- `HEAD` on GET routes now returns 405 (Flask answered it automatically). Nothing in the app sends `HEAD`.
- JSON keys are no longer sorted (Flask sorted them). Clients parse the JSON, so order doesn't matter. `| tojson` in the templates now uses Jinja's built-in filter, which escapes the same characters (`< > & '`).
- A repeated query key (`?stop=1&stop=2`) now gives the last value. Flask gave the first.
- `/map/` (trailing slash) now redirects to `/map`. Flask returned 404.
- `/docs` and `/openapi.json` are now public. That's deliberate for learning; turning them off in production is under *Later*.
- `/nearest-stop` parses JSON even when the `Content-Type` header is missing. Flask's `get_json` required it.
- Plain `def` routes run in a thread pool, so one warm instance can serve requests at the same time. The module-level caches (`_feed_cache`, `_position_cache`) can then race, but harmlessly: the worst case is two fetches, last write wins. `lru_cache` is thread-safe.

Checked in a scratch venv (FastAPI 0.165.0, Starlette 1.10.0, Jinja2 3.1.6): absolute `url_for` URLs, `.path` relative, commas kept in `request.url.query`, autoescape on, Jinja's `tojson` escaping `< > '`, traversal 404s (including `%2e%2e`), the 400s for non-object bodies, 405 on GET and HEAD, 307 on a trailing slash, last value wins on a repeated key, and `StaticFiles` sending no `Cache-Control`.

## Review Focus

These are the most likely ways for the port to break that the existing tests don't exercise, most likely first. Task 1 adds a check for each:

- **Static asset URLs** render as `/static/...`, never `http://...` (mixed-content risk). The page must have `src="/static/glide.js"`, and the stylesheet and weather icon must be relative too.
- **Static files** are still served at the same paths, subfolder included, and nothing outside `static/` is: `/static/glide.js` and `/static/weather/clear-day.svg` return 200, and a traversal path returns 404.
- **The HTMX poll URL** keeps the page's own query: `/map?stop=84009` renders `hx-get="/map-list?stop=84009"`. If `request.url.query` is wrong, polling quietly breaks 20 s in.
- **Autoescaping** is still on in the new Jinja environment. The search box echoes `q`, so `/stops?q=<b>zz` must render `&lt;b&gt;zz`, never `<b>zz`.
- **A JSON body that isn't an object** sent to `/nearest-stop` (`[1, 2]`, `null`, `"x"`) returns 400, not 500.

---

### Task 1: Tests on FastAPI's client, plus the Review Focus checks (red, not committed)

**Files:**
- Modify: `scripts/test_map_views.py`, `scripts/test_nearest_stop.py`, `scripts/test_route_path.py`, `scripts/test_stops_json.py`
- Create: `requirements-dev.txt`
- Unchanged: `scripts/test_local_weather.py` (no HTTP client), the Node tests

**Interfaces:**
- Consumes: `index.app` (Flask today, ASGI after Task 2).
- Produces: tests that need `index.app` to be an ASGI app (`TestClient(index.app)`).

- [ ] **Step 1: Branch, and a green baseline on Flask.**

```bash
git push -u origin feature/fastapi-migration
git switch -c fastapi/step-1-port feature/fastapi-migration
for f in scripts/test_*.py; do python "$f" || break; done
for f in scripts/test_*.js; do node "$f" || break; done
```
Expected: every script prints `... all checks passed`. If anything is already red on Flask, stop: the port mustn't start from a failing baseline.

- [ ] **Step 2: Create `requirements-dev.txt`.**

```
# Local development and tests only; Vercel installs requirements.txt alone.
-r requirements.txt
httpx2~=2.16     # FastAPI's TestClient is built on it
uvicorn~=0.54    # runs the app locally: uvicorn index:app --app-dir api --reload
```
Then run `pip install "fastapi~=0.165.0" "jinja2~=3.1"` and `pip install -r requirements-dev.txt`. FastAPI goes into `requirements.txt` in Task 2, but it has to be installed now so the red run fails for the right reason.

- [ ] **Step 3: Switch the four scripts to `TestClient`.** These are the only mechanical changes:

| Flask | FastAPI |
|---|---|
| `index.app.test_client()` | `TestClient(index.app)`, with `from fastapi.testclient import TestClient` |
| `.get_data(as_text=True)` | `.text` |
| `.get_json()` | `.json()` |
| `client.post(..., data="not json")` | `client.post(..., content="not json")` (httpx's name for a raw body) |

- [ ] **Step 4: Add the Review Focus checks.** In `scripts/test_map_views.py`, after the "One stop" block (`page` is `/map?stop=84009`):

```python
# Asset URLs stay relative, exactly as Flask wrote them: an absolute http://
# URL would be blocked as mixed content on the https site.
assert 'src="/static/glide.js"' in page and 'href="/static/map.css"' in page
# The 20 s poll asks for the same view the page shows.
assert 'hx-get="/map-list?stop=84009"' in page

# Static files: same paths, subfolders included, nothing outside static/.
assert client.get("/static/glide.js").status_code == 200
assert client.get("/static/weather/clear-day.svg").status_code == 200
assert client.get("/static/../api/index.py").status_code == 404

# Autoescaping is still on: the search box echoes what was typed.
echoed = client.get("/stops?q=%3Cb%3Ezz").text
assert "&lt;b&gt;zz" in echoed and "<b>zz" not in echoed
```

Weather icon. For one `/map?stop=84009` request, set `index.read_weather` to return `{"text": "Fair", "icon": "clear-day", "area": None, "now": None, "low": 25, "high": 34, "region": "west", "period": "", "outlook": ""}`. Assert that `'src="/static/weather/clear-day.svg"'` appears in the page, then restore the `None` fake.

In `scripts/test_nearest_stop.py`, after the `"not json"` check:

```python
# Valid JSON that isn't an object is junk too: refused, not a 500.
for body in ("[1, 2]", "null", '"x"'):
    response = client.post("/nearest-stop", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400, (body, response.status_code)
```

- [ ] **Step 5: Run it and confirm red.** Run `python scripts/test_map_views.py`.
Expected: FAIL with `TypeError: Flask.__call__() missing 1 required positional argument: 'start_response'`. That's `TestClient` trying to drive a WSGI app (confirmed in a scratch venv). The other three scripts fail the same way. Don't commit yet (Decision 6).

---

### Task 2: Port `api/index.py` and the templates (green)

**Files:**
- Modify: `api/index.py` (imports at lines 9–15, every route from line 998 to the end)
- Modify: `templates/map.html` (lines 26–29, 67, 147)
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: the uncommitted tests and `requirements-dev.txt` from Task 1.
- Produces: `index.app: fastapi.FastAPI`, `index.templates: Jinja2Templates`, and a static mount named `"static"`. Steps 2–4 build on these names.

- [ ] **Step 1: Update `requirements.txt`.** Remove `flask` and add `fastapi~=0.165.0` and `jinja2~=3.1`. `requests` and `python-dotenv` stay as they are. FastAPI doesn't install Jinja2 itself, so it has to be listed.

- [ ] **Step 2: Set up the app at the top of `index.py`.** Replace the `flask` import and `app = Flask(...)` with:
  - `from fastapi import FastAPI, Request`, `from fastapi.responses import HTMLResponse, JSONResponse`, `from fastapi.staticfiles import StaticFiles`, `from fastapi.templating import Jinja2Templates`
  - `ROOT = pathlib.Path(__file__).resolve().parent.parent`
  - `app = FastAPI()`, `templates = Jinja2Templates(directory=ROOT / "templates")`, `app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")`
  - A short comment explaining that `app` is now an ASGI app, which Vercel's Python runtime finds under the same name, and that the mount serves the same `/static/...` URLs Flask did.

  Leave `BUS_STOPS_PATH` (line 300) as it is, even though it repeats `ROOT`'s expression. Changing it to use `ROOT` would touch code this step doesn't need to touch.

- [ ] **Step 3: Port each route.** Route bodies stay as they are except for the lines listed here. `Cache-Control` values stay exactly as they are today ("no-store" below means `headers={"Cache-Control": "no-store"}`).

| Route | Signature | Response |
|---|---|---|
| `/` and `/map` | `@app.get("/", response_class=HTMLResponse)` + `@app.get("/map", response_class=HTMLResponse)` → `def bus_map(request: Request)` | `map_context(now, request.query_params)`, then `templates.TemplateResponse(request, "map.html", {...same keys...}, headers=no-store)` |
| `/nearest-stop` | `@app.post("/nearest-stop")` → `async def nearest_stop_route(request: Request)` | body = `await request.json()`, falling back to `{}` on `ValueError` (bad JSON). The existing `try` around `float(body["lat"])` already catches `TypeError` for list, null and string bodies. Errors become `JSONResponse({"error": ...}, status_code=400)`; success becomes `JSONResponse({"stop": ...}, headers=no-store)` |
| `/stops` | `@app.get("/stops", response_class=HTMLResponse)` → `def stop_search(request: Request, q: str = "")` | `TemplateResponse(request, "_stop_results.html", {"results": search_stops(q), "query": q.strip()}, headers=no-store)` |
| `/stops.json` | `@app.get("/stops.json")` → `def stop_list()` | `JSONResponse({"stops": STOP_LIST}, headers={"Cache-Control": "public, max-age=86400"})` |
| `/route.json` | `@app.get("/route.json")` → `def route_json(service: str = "", pattern: str = "")` | keep the `int(pattern)` try; the 404 becomes `JSONResponse(..., status_code=404)`; success as today, with the one-day header |
| `/map-list` | `@app.get("/map-list", response_class=HTMLResponse)` → `def map_list(request: Request)` | `TemplateResponse(request, "_map_list.html", map_context(now, request.query_params), headers=no-store)` |
| `/debug` | `@app.get("/debug", response_class=HTMLResponse)` → `def debug(request: Request, stop: str \| None = None)` | `valid_stop_code(stop)`; `TemplateResponse` as today, with no-store |

  Add a comment where each concept first appears, one or two lines in the existing style:
  - why `request` goes into `TemplateResponse`: templates read it, and `url_for` needs it
  - that a declared parameter like `q: str = ""` *is* `?q=` in the URL
  - that a plain `def` route runs in a thread pool, so a blocking `requests` call doesn't stall other requests
  - why `/nearest-stop` alone is `async def` (Decision 1)

  Remove `make_response`, `render_template` and `request` from the imports, since the port leaves them unused. Keep the `ThreadPoolExecutor` in `fetch_stops`; replacing it belongs to Step 2.

- [ ] **Step 4: Update the template (`templates/map.html`).**
  - Lines 26–29 and 67: change `url_for('static', filename=X)` to `url_for('static', path=X).path`. Add one Jinja comment (`{# … #}`) above line 26 explaining `.path` (Decision 2).
  - Line 147: change `request.query_string.decode()` to `request.url.query`. It is still autoescaped, as before.

- [ ] **Step 5: Run everything and confirm green.**

```bash
for f in scripts/test_*.py; do python "$f" || break; done
for f in scripts/test_*.js; do node "$f" || break; done
```
Expected: every script prints `... all checks passed`, including the new Review Focus checks.

- [ ] **Step 6: Smoke-test locally with the real key.** Run `uvicorn index:app --app-dir api --reload --port 8000`, which uses the existing `.env`. In the browser, walk through:
  - the `/map` start screen
  - search for a stop and open it
  - star it, then open favourites
  - wait for one 20 s poll: the list refreshes and the console shows no errors
  - tap a bus: its route draws
  - `/docs`: it lists the seven routes

  Expected: identical to production.

- [ ] **Step 7: Review the diff, then commit.** Run `git diff feature/fastapi-migration -- api/index.py`. Every changed line should trace back to the port (karpathy rule 3): no reformatting and no moved helpers.

```bash
git add requirements.txt requirements-dev.txt api/index.py templates/map.html scripts/test_map_views.py scripts/test_nearest_stop.py scripts/test_route_path.py scripts/test_stops_json.py
git commit -m "Port the backend from Flask to FastAPI"
```

---

### Task 3: Vercel preview, measure, open the PR

**Files:** none (verification and the PR).

- [ ] **Step 1: Push the branch and open the PR into the migration branch.**

```bash
git push -u origin fastapi/step-1-port
gh pr create --base feature/fastapi-migration --title "Step 1: Port the backend from Flask to FastAPI" --body-file <drafted body>
```
The body has four parts:
  - **Why:** spec Step 1.
  - **What changed:** the route table, condensed.
  - **How it was verified:** the tests, plus the numbers from Step 3 below.
  - **Known differences:** the list above.

  It also mentions, without doing it, that a Pydantic model for `/nearest-stop` would be the FastAPI way. That changes 400 to 422, so it's under *Later*.

- [ ] **Step 2: Use the preview to prove `@vercel/python` runs the ASGI app.** On the preview URL:
  - `/map?stop=84009` returns 200 and its `X-Vercel-Id` ends `::sin1`
  - the page source shows `src="/static/glide.js"`, and `/static/glide.js` returns 200
  - the browser walk from Task 2, Step 6 behaves the same

- [ ] **Step 3: Measure against the sin1 baseline.** Get TTFB with `curl -s -o /dev/null -w "%{time_starttransfer}\n"`: 4 warm runs of each request, plus the first request after the deploy (cold).

| Request | sin1 baseline (warm) |
|---|---|
| `/map?stop=84009` | 0.11–0.24 s |
| `/map-list?stop=84009` | 0.10–0.11 s |
| `/map-list?stops=` six favourites | 0.27–0.29 s |

  Expected: within noise of the baseline, since FastAPI's imports cost a little. Write the numbers into the spec's Step 1 section and the PR. If they are clearly worse, the cold start especially, stop and report before merging.

- [ ] **Step 4: Hand over.** Explain the PR in chat: what to read first (the route table, then the `/nearest-stop` change), and the two concepts it teaches:
  - ASGI and declared query parameters
  - sync `def` in a thread pool versus `async def`

  Merging is your call.

## Later, only if wanted (not in this PR)

- A Pydantic model for `/nearest-stop` (400 → 422), as the spec says.
- `/docs` turned off in production (`FastAPI(docs_url=None, ...)` behind an env flag).
- A uv lockfile pinning every transitive dependency.
- Running the tests on Python 3.12 locally to match Vercel (this machine has 3.11).
