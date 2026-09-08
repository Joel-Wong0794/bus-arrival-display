# Bus Arrival Display

A live-updating web dashboard showing LTA bus arrival times for bus stops, displayed on an old 7" Android tablet running a browser in kiosk mode. The app fetches real-time ETAs from DataMall every ~20 seconds and counts them down on the client every second, so the displayed number stays current between polls and keeps working even when the kiosk screen wakes from sleep.

## Setup

1. **Get an API key** from LTA DataMall (https://datamall.lta.gov.sg/content/datamall/en.html). Free tier has plenty of quota for polling every 20s.
2. **Copy `.env.example` to `.env`** and fill in:
   - `API_KEY` — your DataMall key
   - `BUS_STOP_CODES` — comma-separated stops, e.g. `28571,28519`
   - `BUS_STOP_NAMES` — display names, e.g. `Home334,Blk425`
   - `REFRESH_SECONDS` — poll interval (default 20, keep it ≥ 10)
   - `BUFFER_SECONDS` — safety margin in seconds (default 30; stacks with flooring, so anything under 1m30s shows "Arr")
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

## How the ETA is Computed

Two safety mechanisms, both intentional:

1. **Floor, not round**: `int(seconds // 60)` — a bus 1m31s away shows "1 min", not "2 min". The error always lands toward "leave now" rather than "you have time."
2. **Buffer**: Subtracts `BUFFER_SECONDS` before flooring. A 30s buffer means the tile errs an extra ~30s early. Combined with flooring, this pushes anything under 1m30s to "Arr".

The result is deliberately conservative, not "accurate". A tile reading "2 min" means _at least_ 2 minutes — you might leave slightly early, but you won't miss the bus because the display said you had time.

## Client-Side Countdown

The number doesn't freeze between polls. The server sends both a floored minute count (for the initial paint) and an absolute arrival time as epoch milliseconds in a `data-arrival` attribute. JavaScript ticks every second, recomputes the countdown, and updates the text. This is why the display stays fresh even if the htmx poll timer stalls (e.g., when the kiosk screen sleeps).

The client-side buffer (`BUFFER_MS`) is templated in from the server's `BUFFER_SECONDS`, so they stay in sync — no manual alignment needed.

## Debug View

Visit `/arrivals?debug=1` to see per-bus:

- Raw `EstimatedArrival` string from DataMall
- `Monitored` flag (0 = schedule-derived, not live GPS)
- Exact float minutes vs. displayed rounded value

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
2. **In Vercel**: Import the repo, set environment variables (`API_KEY`, `BUS_STOP_CODES`, `BUS_STOP_NAMES`, `REFRESH_SECONDS`, `BUFFER_SECONDS`), deploy.

Note: `.env` is gitignored — set env vars in the Vercel dashboard, not the file, so you don't commit secrets.

## Testing

No formal test suite. To verify the timing logic works:

1. Hit `/arrivals?debug=1` and check that the float minutes in "Exact minutes" match the `EstimatedArrival` timestamp minus the server's current time.
2. Open `/` and watch a tile for 60+ seconds without refreshing — the number must count itself down on its own, never jump backward when htmx swaps.
3. Put the tablet to sleep for several minutes, wake it, and check the numbers are correct immediately (not frozen at the pre-sleep value).
