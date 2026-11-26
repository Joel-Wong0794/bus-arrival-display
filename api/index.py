import functools
import json
import math
import os
import pathlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, make_response, render_template, request

load_dotenv()

app = Flask(__name__, template_folder="../templates", static_folder="../static")

# LTA DataMall v3 bus arrival endpoint
BUS_ARRIVAL_URL = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"

# Singapore has no DST, so a fixed offset is exact and needs no tzdata package.
SGT = timezone(timedelta(hours=8))

API_KEY = os.environ["API_KEY"]
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "20"))
# Deliberate safety margin shaved off every ETA, so the tile errs towards
# "leave now" rather than "you have time". Not a staleness correction — the
# client counts down continuously, so it is independent of REFRESH_SECONDS.
# Combined with flooring, 30 here means anything under 1m30s shows as "Arr".
BUFFER_SECONDS = int(os.environ.get("BUFFER_SECONDS", "30"))

# In the favourites view, only buses arriving within this many minutes get
# plotted. That view answers "should I leave now", so anything further out is
# noise. A single picked stop shows everything running there.
MAP_WINDOW_MINUTES = int(os.environ.get("MAP_WINDOW_MINUTES", "5"))
# Favourites live in each visitor's browser and arrive as ?stops=. Every one
# costs a DataMall call per poll, so the list is capped.
MAX_FAVOURITES = 6

LOAD_LABELS = {"SEA": "Seats", "SDA": "Standing", "LSD": "Limited"}
TYPE_LABELS = {"SD": "Single deck", "DD": "Double deck", "BD": "Bendy"}

# NEA's 24-hour forecast, via data.gov.sg's open real-time API.
WEATHER_URL = (
    "https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast"
)
# Genuinely optional — the endpoint answers without it and the key only buys a
# higher rate limit, so an unset key costs nothing at this polling volume.
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
# One of north/south/east/west/central. The feed ships no region boundaries, so
# this cannot be worked out from a position — it has to be told.
WEATHER_REGION = os.environ.get("WEATHER_REGION", "west").strip().lower()
# NEA reissues this a few times a day, so fetching per page load buys nothing
# and adds latency. A warm serverless instance keeps the cache; a cold one
# simply fetches again, which is why this needs no store.
WEATHER_TTL_SECONDS = int(os.environ.get("WEATHER_TTL_SECONDS", "900"))


# NEA's 23 forecast strings mapped onto Meteocons icons in static/weather.
# "{dn}" is filled with day or night: NEA marks the distinction on some values
# and leaves it off others, so the ambiguous ones are settled by the clock.
WEATHER_ICONS = {
    "Fair": "clear-{dn}",
    "Fair (Day)": "clear-day",
    "Fair (Night)": "clear-night",
    "Fair and Warm": "clear-day",
    "Partly Cloudy": "partly-cloudy-{dn}",
    "Partly Cloudy (Day)": "partly-cloudy-day",
    "Partly Cloudy (Night)": "partly-cloudy-night",
    "Cloudy": "cloudy",
    "Hazy": "haze",
    "Slightly Hazy": "haze",
    "Windy": "wind",
    "Mist": "mist",
    "Fog": "fog",
    "Light Rain": "drizzle",
    "Moderate Rain": "rain",
    "Heavy Rain": "extreme-rain",
    "Passing Showers": "drizzle",
    "Light Showers": "drizzle",
    "Showers": "rain",
    "Heavy Showers": "extreme-rain",
    "Thundery Showers": "thunderstorms-rain",
    "Heavy Thundery Showers": "thunderstorms-extreme-rain",
    "Heavy Thundery Showers with Gusty Winds": "thunderstorms-extreme-rain",
}
# Sunrise and sunset barely move in Singapore -- roughly 7am and 7pm all year --
# so a fixed pair is accurate enough to pick a sun or a moon by, and needs no
# ephemeris.
DAY_STARTS_HOUR = 7
NIGHT_STARTS_HOUR = 19


def weather_icon(text: str, now: datetime) -> str | None:
    """Meteocons filename for an NEA forecast string, or None if unmapped."""
    name = WEATHER_ICONS.get(text)
    if not name:
        return None
    is_day = DAY_STARTS_HOUR <= now.hour < NIGHT_STARTS_HOUR
    return name.replace("{dn}", "day" if is_day else "night")


def current_period(periods: list[dict], now: datetime) -> dict | None:
    """The forecast period covering now, falling back to the first listed."""
    for period in periods:
        window = period.get("timePeriod", {})
        try:
            start = datetime.fromisoformat(window["start"])
            end = datetime.fromisoformat(window["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= now < end:
            return period
    return periods[0] if periods else None


# The 24-hour feed above answers for a fifth of the island at once. These two
# answer for where the map is looking: NEA's 2-hour forecast for 47 named
# areas (Ang Mo Kio, Bedok, ...), and the live reading at 17 weather stations.
# Both ship each area's or station's coordinates, so "nearest" needs no lookup
# table of our own. Same host, same optional key, no extra sign-up.
NOWCAST_URL = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"
TEMPERATURE_URL = "https://api-open.data.gov.sg/v2/real-time/api/air-temperature"
# Stations report every minute, so the temperature goes stale far sooner than
# a forecast does. Five minutes keeps it honest at about one request per warm
# instance per five minutes, whatever the number of stops being viewed: the
# feed returns every station in one reply, so the cache is island-wide.
TEMPERATURE_TTL_SECONDS = int(os.environ.get("TEMPERATURE_TTL_SECONDS", "300"))

# One cache entry per feed URL: {"fetched_at": datetime, "data": dict}.
_feed_cache: dict = {}


def fetch_feed(url: str, now: datetime, ttl_seconds: int) -> dict | None:
    """A data.gov.sg feed's "data" object, cached for ttl_seconds.

    Caches the raw feed rather than a rendered answer, for two reasons: every
    area and station arrives in one reply, so one cached copy serves every stop
    on the island; and the period/staleness checks then always run against the
    current clock instead of the clock at fetch time.
    """
    entry = _feed_cache.get(url)
    if entry and (now - entry["fetched_at"]).total_seconds() < ttl_seconds:
        return entry["data"]

    headers = {"accept": "application/json"}
    if WEATHER_API_KEY:
        headers["x-api-key"] = WEATHER_API_KEY
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()["data"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # Serve the last good copy rather than nothing: a slightly old reading
        # beats a blank header. None only if this instance never got one.
        return entry["data"] if entry else None

    _feed_cache[url] = {"fetched_at": now, "data": data}
    return data


def weather_record(now: datetime) -> dict | None:
    """The raw 24-hour NEA record (regional forecast and the day's low-high)."""
    data = fetch_feed(WEATHER_URL, now, WEATHER_TTL_SECONDS)
    try:
        return data["records"][0]
    except (TypeError, KeyError, IndexError):
        return None


def nearest_named(places: list, lat: float, lon: float, position) -> dict | None:
    """The entry in places closest to (lat, lon); position(entry) -> (lat, lon).

    A straight scan: 47 areas or 17 stations, so there is nothing to index.
    Entries with a missing or malformed position are skipped, not fatal.
    """
    best, best_m = None, None
    for place in places:
        try:
            metres = haversine_m(lat, lon, *position(place))
        except (KeyError, TypeError, ValueError):
            continue
        if best_m is None or metres < best_m:
            best, best_m = place, metres
    return best


def local_forecast(now: datetime, lat: float, lon: float) -> dict | None:
    """NEA's 2-hour forecast for the named area nearest (lat, lon).

    None when the feed is down, the area has no forecast, or the forecast has
    expired (a feed that stopped updating must not keep showing "Fair" from
    hours ago) — the caller then falls back to the 24-hour regional one.
    """
    data = fetch_feed(NOWCAST_URL, now, WEATHER_TTL_SECONDS)
    if not data:
        return None
    area = nearest_named(
        data.get("area_metadata", []),
        lat,
        lon,
        lambda a: (a["label_location"]["latitude"], a["label_location"]["longitude"]),
    )
    try:
        item = data["items"][0]
        end = datetime.fromisoformat(item["valid_period"]["end"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if area is None or now >= end:
        return None
    text = next(
        (f.get("forecast") for f in item.get("forecasts", []) if f.get("area") == area["name"]),
        None,
    )
    if not text:
        return None
    return {"text": text, "area": area["name"], "period": item["valid_period"].get("text")}


def local_temperature(now: datetime, lat: float, lon: float) -> int | None:
    """The latest reading at the weather station nearest (lat, lon), whole °C.

    Only stations that actually reported in the latest batch are considered:
    one offline station must not blank the temperature when a neighbour has it.
    """
    data = fetch_feed(TEMPERATURE_URL, now, TEMPERATURE_TTL_SECONDS)
    if not data:
        return None
    try:
        values = {r["stationId"]: r["value"] for r in data["readings"][0]["data"]}
    except (KeyError, IndexError, TypeError):
        return None
    reporting = [s for s in data.get("stations", []) if s.get("id") in values]
    station = nearest_named(
        reporting,
        lat,
        lon,
        lambda s: (s["location"]["latitude"], s["location"]["longitude"]),
    )
    if station is None:
        return None
    try:
        return round(float(values[station["id"]]))
    except (TypeError, ValueError):
        return None


def read_weather(now: datetime, place: tuple | None = None) -> dict | None:
    """What it's doing outside, or None if NEA can't be reached at all.

    place is where the map is looking: the chosen stop, or home. Since the
    phone-location feature picks the stop nearest the phone, that stop stands
    in for the phone itself, so the device's own position is never needed here
    and never leaves the phone for this. With a place, the condition comes from
    the nearest of NEA's 47 areas and the temperature from the nearest station;
    without one (or if those feeds fail) it is the 24-hour regional forecast,
    exactly as before.

    None rather than raising, and never on the arrivals path: the weather is a
    garnish on a page whose job is bus times. An NEA outage must not take the
    departure board down with it.
    """
    record = weather_record(now) or {}
    general = record.get("general", {})
    temperature = general.get("temperature", {})
    period = current_period(record.get("periods", []), now) or {}
    # Region first, island-wide as the fallback — a region NEA didn't send is
    # better answered generally than not at all.
    regional = period.get("regions", {}).get(WEATHER_REGION) or general.get(
        "forecast", {}
    )

    local = local_forecast(now, *place) if place else None
    now_c = local_temperature(now, *place) if place else None
    text = local["text"] if local else regional.get("text")
    if not text:
        return None
    return {
        # The 2-hour feed suffixes most values with "(Day)" or "(Night)". The
        # icon already says which (a sun or a moon), so the words drop it; the
        # icon lookup still gets the full string, which is what it is keyed on.
        "text": text.removesuffix(" (Day)").removesuffix(" (Night)"),
        "icon": weather_icon(text, now),
        # Set only when the condition is the local 2-hour one; the template
        # uses it to choose between the local and the regional layout.
        "area": local["area"] if local else None,
        "now": now_c,
        "low": temperature.get("low"),
        "high": temperature.get("high"),
        "region": WEATHER_REGION,
        "period": local["period"] if local else period.get("timePeriod", {}).get("text"),
        "outlook": general.get("forecast", {}).get("text"),
    }


# Destination-code to stop-name lookup, baked by scripts/fetch_bus_stops.py.
# Read once at import: the arrival feed names a destination only by code, and
# the BusStops dataset has no filter-by-code, so resolving one at request time
# would mean paging ~5000 rows on every cold start. Missing file or code is not
# fatal — the destination just falls back to the raw code, or is omitted.
BUS_STOPS_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "bus_stops.json"
try:
    _raw_stops = json.loads(BUS_STOPS_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _raw_stops = {}

# The file gained coordinates alongside the name. A plain string is the older
# shape: normalise it here so nothing downstream has to know which it read, and
# a file that predates the change simply lacks positions.
BUS_STOPS = {
    code: value if isinstance(value, dict) else {"name": value}
    for code, value in _raw_stops.items()
}


def stop_name(code: str) -> str:
    """Readable name for a stop code, falling back to the code itself."""
    return BUS_STOPS.get(code, {}).get("name") or code


BUS_STOPS_URL = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
# Positions found at runtime for stops the baked file has none for. Misses are
# remembered too, so a stop is asked about at most once per warm instance.
_position_cache: dict[str, tuple[float, float] | None] = {}


def fetch_stop_position(code: str) -> tuple[float, float] | None:
    """Ask DataMall where one stop is. None on anything unexpected.

    Only reached when data/bus_stops.json has no position for the stop, which a
    regeneration fixes permanently — this is the fallback, not the plan. It is
    affordable because the feed now filters by stop code (guide v6.8); the whole
    reason the file is baked is that asking used to mean paging ~5000 rows.

    Fails to None rather than raising: a missing pin is a smaller problem than a
    map page that will not load.
    """
    try:
        response = requests.get(
            BUS_STOPS_URL,
            headers={"AccountKey": API_KEY, "accept": "application/json"},
            params={"BusStopCode": code},
            timeout=5,
        )
        response.raise_for_status()
        rows = response.json().get("value", [])
    except (requests.RequestException, ValueError):
        return None
    for row in rows:
        # Checked rather than assumed: an unsupported filter returns the first
        # page of every stop rather than an error, and the wrong stop's position
        # would put the pin somewhere plausible and wrong.
        if row.get("BusStopCode") != code:
            continue
        try:
            lat, lon = float(row.get("Latitude") or 0), float(row.get("Longitude") or 0)
        except (TypeError, ValueError):
            return None
        return (lat, lon) if lat and lon else None
    return None


def baked_position(code: str) -> tuple[float, float] | None:
    """Where a stop is according to the file alone. Never calls out.

    Kept separate from stop_position because the nearby scan asks about all
    ~5000 stops: routing that through the fallback would fire one request per
    stop on a file with no coordinates.
    """
    entry = BUS_STOPS.get(code, {})
    lat, lon = entry.get("lat"), entry.get("lon")
    return (lat, lon) if lat is not None and lon is not None else None


def stop_position(code: str) -> tuple[float, float] | None:
    """Where one stop is, from the baked lookup or, failing that, from the feed.

    For the single focused stop only — see baked_position for bulk lookups.
    """
    position = baked_position(code)
    if position is not None:
        return position
    if code not in _position_cache:
        _position_cache[code] = fetch_stop_position(code)
    return _position_cache[code]


def destination_label(next_bus: dict) -> str | None:
    """Where this bus is heading, ready to print.

    A loop service reports the same code for origin and destination, so "to
    Jurong East Int" would be technically true and practically useless — it is
    also where the bus started. Those read "Loop" instead.
    """
    origin, destination = next_bus.get("OriginCode"), next_bus.get("DestinationCode")
    if not destination:
        return None
    if origin == destination:
        return "Loop"
    return f"to {stop_name(destination)}"

# Optional, and only read by the /debug view: favourites now live in each
# visitor's browser rather than in the deployment's settings.
stop_codes = [c.strip() for c in os.environ.get("BUS_STOP_CODES", "").split(",") if c.strip()]
stop_names = os.environ.get("BUS_STOP_NAMES", "").split(",")
# Pair each stop code with a display name, falling back to the code itself.
STOPS = [
    (
        code,
        stop_names[i].strip()
        if i < len(stop_names) and stop_names[i].strip()
        else code,
    )
    for i, code in enumerate(stop_codes)
]


def bus_location(next_bus: dict) -> dict:
    """Returns {"lat": ..., "lon": ..., "load": ...} for one NextBus entry.

    Coordinates are only real when Monitored is 1; on a schedule-derived estimate
    the feed sends "0.0", which would otherwise plot the bus in the Atlantic. The
    zero check repeats that guard rather than trusting Monitored alone.
    """
    location = {"lat": None, "lon": None, "load": LOAD_LABELS.get(next_bus.get("Load"))}
    if next_bus.get("Monitored") != 1:
        return location
    try:
        lat = float(next_bus.get("Latitude") or 0)
        lon = float(next_bus.get("Longitude") or 0)
    except ValueError:
        return location
    if lat and lon:
        location["lat"] = lat
        location["lon"] = lon
    return location


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in metres. Not road distance — a bus 400m away by
    air can be 2km by road."""
    radius = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


# Every service's road line, from cheeaun/sgbusdata (the data behind BusRouter
# SG), baked by scripts/fetch_bus_routes.py. One entry per pattern (direction):
#   {"334": [{"line": "<Google encoded polyline>",
#             "stops": [["28009", 0], ["28441", 412], ...]}], "_meta": {...}}
# "stops" are the pattern's stops in order with their metres along the line,
# worked out at bake time. Missing file = straight-line glide, as before.
BUS_ROUTES_PATH = BUS_STOPS_PATH.parent / "bus_routes.json"
try:
    BUS_ROUTES = json.loads(BUS_ROUTES_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    BUS_ROUTES = {}

# How far a GPS fix may sit from the road line and still count as on it.
# Next Stop's figure: the line follows the road, so this only has to absorb
# GPS error and the width of the road.
SNAP_RADIUS_M = 120
# Candidates whose offsets are within this of the best are treated as equally
# good fits — e.g. a loop service passing the same road both ways — and the one
# nearer the stop wins.
SNAP_TIE_M = 30
# Next Stop's plausibility test: a fit that would need the bus to beat 60 km/h
# to reach the stop by LTA's ETA (with a minute's slack) is the wrong stretch of
# route — in testing, a 502 got placed 41 km out on a stretch that merely
# passes nearby. Mirrors MAX_SPEED_MPS in static/glide.js.
MAX_SPEED_MPS = 60 / 3.6
ETA_SLACK_S = 60
# Road kept behind the fix, so a pin that has fallen behind a fresh fix can
# glide forward along the road instead of lurching. Matches SNAP_AHEAD_M in
# static/glide.js: any further behind than that and the pin jumps anyway.
PATH_BEHIND_M = 1500
# Road sent ahead of the fix. A pin covers at most ~330 m between 20s polls
# (60 km/h), so 2 km is ample; beyond it the path runs straight to the stop,
# which only shows if polling stops for minutes. Keeps each row small.
PATH_AHEAD_M = 2000


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google's encoded polyline format (precision 5) -> [(lat, lon), ...]."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):  # latitude, then longitude
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / 1e5, lon / 1e5))
    return points


@functools.lru_cache(maxsize=512)
def route_line(service_no: str, pattern: int) -> tuple[list, list]:
    """One pattern's road line, decoded, with running metres along it.

    Decoded on first use and kept for the life of the instance: decoding all
    604 services at import would slow every cold start for routes no one asks
    about.
    """
    points = decode_polyline(BUS_ROUTES[service_no][pattern]["line"])
    metres = [0.0]
    for a, b in zip(points, points[1:]):
        metres.append(metres[-1] + haversine_m(*a, *b))
    return points, metres


def local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Metres east/north of (lat0, lon0). Flat-earth, exact enough over a few km."""
    return (
        (lon - lon0) * 111_320 * math.cos(math.radians(lat0)),
        (lat - lat0) * 110_574,
    )


def project(point: tuple, a: tuple, b: tuple) -> tuple[float, float]:
    """Distance in metres from point to segment a-b, and where along it (0-1)
    the nearest spot lies."""
    ax, ay = local_xy(*a, *point)
    bx, by = local_xy(*b, *point)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    return math.hypot(ax + t * dx, ay + t * dy), t


def line_point(points: list, metres: list, at: float) -> tuple[float, float]:
    """The spot `at` metres along a decoded line, clamped to its ends."""
    if at <= 0:
        return points[0]
    for i in range(1, len(points)):
        if at <= metres[i]:
            span = metres[i] - metres[i - 1]
            f = (at - metres[i - 1]) / span if span else 1.0
            a, b = points[i - 1], points[i]
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
    return points[-1]


def route_path(
    service_no: str,
    stop_code: str,
    fix: tuple[float, float],
    target: tuple[float, float],
    eta_s: float,
) -> list[list[float]]:
    """The road this bus will drive to its stop, as [[lat, lon, metres], ...].

    Metres are measured along the road line relative to where the bus is: 0 at
    the bus, rising to its road distance from the stop at the last point. Up to
    PATH_BEHIND_M of road behind the bus comes first (negative metres), so the
    browser can glide a lagging pin forward along the road, and at most
    PATH_AHEAD_M ahead is sent before the path cuts straight to the stop.

    How the bus is placed on its route (Next Stop's "snapping"): every stretch
    of every pattern leading to this stop is a candidate — for each time the
    pattern visits the stop. The GPS fix is projected onto each; fits further
    than SNAP_RADIUS_M are dropped, and so are any that would need more than
    60 km/h to make LTA's ETA (eta_s, seconds from now). The closest fit wins,
    ties going to the one nearer the stop. The bus is then drawn *on* the road
    at that projected spot, since the line follows the road.

    Falls back to a straight line, fix to stop, when the service is not in the
    bake or nothing fits.
    """
    straight = [[fix[0], fix[1], 0], [target[0], target[1], round(haversine_m(*fix, *target))]]
    reachable_m = MAX_SPEED_MPS * (max(0.0, eta_s) + ETA_SLACK_S)
    patterns = BUS_ROUTES.get(service_no)
    if not isinstance(patterns, list):
        return straight

    candidates = []  # (offset_m, remaining_m, pattern, along_m, stop_m)
    for n, pattern in enumerate(patterns):
        points, metres = route_line(service_no, n)
        for code, stop_m in pattern["stops"]:
            if code != stop_code or stop_m is None:
                continue
            for i in range(len(points) - 1):
                if metres[i] >= stop_m:
                    break  # only road leading *to* the stop
                if metres[i + 1] < stop_m - reachable_m:
                    continue  # too far out to make LTA's ETA: skip unprojected
                offset, t = project(fix, points[i], points[i + 1])
                if offset > SNAP_RADIUS_M:
                    continue
                along = metres[i] + t * (metres[i + 1] - metres[i])
                remaining = stop_m - along
                if 0 <= remaining <= reachable_m:
                    candidates.append((offset, remaining, n, along, stop_m))
    if not candidates:
        return straight
    best_offset = min(c[0] for c in candidates)
    _, _, n, along, stop_m = min(
        (c for c in candidates if c[0] <= best_offset + SNAP_TIE_M), key=lambda c: c[1]
    )

    points, metres = route_line(service_no, n)
    low = max(0.0, along - PATH_BEHIND_M)
    high = min(stop_m, along + PATH_AHEAD_M)
    # The line's own points between low and high, plus the three spots that
    # matter — the far end behind, the bus itself, the near end ahead —
    # interpolated onto the line, all in order of distance along it.
    spots = {round(m): points[i] for i, m in enumerate(metres) if low < m < high}
    for m in (low, along, high):
        spots[round(m)] = line_point(points, metres, m)
    path = [[*spots[m], m - round(along)] for m in sorted(spots)]
    # End at the stop as bus_stops.json places it. Ahead of PATH_AHEAD_M that is
    # an extra straight leg; otherwise it replaces the line's own end spot.
    stop_leg = [target[0], target[1], round(stop_m - along)]
    if high < stop_m:
        path.append(stop_leg)
    else:
        path[-1] = stop_leg
    return path


def format_distance(metres: float) -> str:
    if metres < 1000:
        return f"{int(round(metres / 10) * 10)} m"
    return f"{metres / 1000:.1f} km"


# A phone further than this from every stop is not near a bus in any useful
# sense (or is not in Singapore at all): no stop is picked and /map stays as it
# was. Generous, because the nearest stop is still the most useful pick from a
# park or an industrial estate.
LOCATE_RADIUS_M = 2000


def nearest_stop(lat: float, lon: float) -> dict | None:
    """The stop closest to a point, or None if none is within LOCATE_RADIUS_M.

    Straight-line distance over every stop with a position — ~5,200 of them,
    well under a millisecond — so no index is needed. Used by /nearest-stop
    when a phone shares its location on /map.
    """
    best = None
    for code in BUS_STOPS:
        position = baked_position(code)
        if position is None:
            continue
        metres = haversine_m(lat, lon, *position)
        if best is None or metres < best[0]:
            best = (metres, code)
    if best is None or best[0] > LOCATE_RADIUS_M:
        return None
    return {"code": best[1], "name": stop_name(best[1]), "metres": round(best[0])}


def valid_stop_code(code: str | None) -> str | None:
    """A stop code safe to query and print, or None.

    Singapore codes are five digits. Anything else came from a hand-edited URL,
    so it is dropped rather than forwarded to DataMall.
    """
    if code and len(code) == 5 and code.isdigit():
        return code
    return None


def valid_stop_codes(raw: str | None) -> list[str]:
    """Favourites from ?stops=11119,04223 — valid codes only, deduplicated,
    in the order given, at most MAX_FAVOURITES."""
    codes = []
    for part in (raw or "").split(","):
        code = valid_stop_code(part.strip())
        if code and code not in codes:
            codes.append(code)
    return codes[:MAX_FAVOURITES]


def search_stops(query: str | None, limit: int = 12) -> list[dict]:
    """Stops whose name or code matches, closest-looking matches first.

    Works against a lookup with no coordinates, because choosing a stop by name
    never needed a position — only the nearby list does. Scanning all 5000 on
    each keystroke is microseconds, and saves shipping the whole set to a phone.
    """
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []
    hits = []
    for code, entry in BUS_STOPS.items():
        name = entry.get("name") or ""
        lowered = name.lower()
        if code.startswith(query):
            rank = 0
        elif lowered.startswith(query):
            rank = 1
        elif query in lowered:
            rank = 2
        else:
            continue
        hits.append((rank, name, code))
    hits.sort()
    return [{"code": code, "name": name} for _, name, code in hits[:limit]]


def get_bus_arrival(
    api_key: str, stop_code: str, now: datetime
) -> list[tuple[str, list[dict]]]:
    """Returns [(service_no, [bus, ...]), ...] for a given bus stop.

    Each bus carries the floored ETA in minutes, the absolute arrival time as
    epoch milliseconds so the browser can count it down between polls, and the
    raw feed values for the debug view.
    """
    response = requests.get(
        BUS_ARRIVAL_URL,
        headers={"AccountKey": api_key, "accept": "application/json"},
        params={"BusStopCode": stop_code},
        timeout=10,
    )
    response.raise_for_status()
    services = response.json().get("Services", [])

    results = []
    for service in services:
        buses = []
        for key in ("NextBus", "NextBus2", "NextBus3"):
            next_bus = service.get(key, {})
            arrival = next_bus.get("EstimatedArrival")
            if not arrival:
                continue
            eta_time = datetime.fromisoformat(arrival)
            # DataMall normally stamps +08:00. If it ever doesn't, assume SGT
            # rather than let the comparison fall back to the server's UTC clock,
            # which would silently inflate every ETA by 8 hours.
            if eta_time.tzinfo is None:
                eta_time = eta_time.replace(tzinfo=SGT)
            seconds = (eta_time - now).total_seconds()
            buses.append(
                {
                    **bus_location(next_bus),
                    # Floor, not round: "2 min" must mean at least 2 minutes, so
                    # the error always lands in the rider's favour. Clamped at 0
                    # so a departed bus reads "Arr" rather than a negative count.
                    "minutes": max(0, int((seconds - BUFFER_SECONDS) // 60)),
                    "arrival_ms": int(eta_time.timestamp() * 1000),
                    "raw": arrival,
                    # Unbuffered, so the debug view shows what the feed actually said.
                    "exact_minutes": seconds / 60,
                    "monitored": next_bus.get("Monitored"),
                    "destination": destination_label(next_bus),
                    # Per bus, not per service: a service's queued buses are
                    # routinely a mix (a double decker followed by two single).
                    # Raw code kept for the debug view, label for display.
                    "type": next_bus.get("Type"),
                    "type_label": TYPE_LABELS.get(next_bus.get("Type")),
                }
            )
        # Upstream drops services with nothing running rather than listing them.
        if buses:
            results.append((service["ServiceNo"], buses))
    return results


def fetch_stops(now: datetime, stops: list[tuple[str, str]]) -> list[dict]:
    """[(code, name), ...] -> each stop's services, fetched in parallel.

    One DataMall call per stop; side by side, six favourites cost about as long
    as one instead of six in a row.
    """

    def one(stop: tuple[str, str]) -> dict:
        code, name = stop
        try:
            services = get_bus_arrival(API_KEY, code, now)
            error = None
        except requests.RequestException:
            services = []
            error = "Unavailable"
        return {"code": code, "name": name, "services": services, "error": error}

    if not stops:
        return []
    with ThreadPoolExecutor(max_workers=len(stops)) as pool:
        return list(pool.map(one, stops))


def collect_map_buses(
    stops_data: list[dict], origin: tuple[float, float] | None
) -> list[dict]:
    """Flattens every stop's services into one soonest-first list.

    Distances are measured from `origin` — the flat in the home view, the picked
    stop otherwise — and omitted when it is None, which is better than quoting a
    number measured from somewhere irrelevant.

    Buses without a live position stay in the list either way: a bus arriving in
    two minutes still matters even when it cannot be plotted.
    """
    rows = []
    for stop in stops_data:
        # Where the bus is heading. The map glides each pin from its last GPS
        # fix towards this point (see static/glide.js); None leaves the pin
        # parked on its fix, as before. One lookup per stop, not per bus.
        target = stop_position(stop["code"])
        for service_no, buses in stop["services"]:
            for rank, bus in enumerate(buses):
                row = {
                    "service_no": service_no,
                    "stop_name": stop["name"],
                    "stop_code": stop["code"],
                    # Identifies one vehicle across refreshes, so a marker can be
                    # moved rather than destroyed — which would shut its popup.
                    "key": f"{stop['code']}|{service_no}|{rank}",
                    "minutes": bus["minutes"],
                    "load": bus["load"],
                    "lat": bus["lat"],
                    "lon": bus["lon"],
                    "distance": None,
                    "destination": bus["destination"],
                    "type_label": bus["type_label"],
                    # Unbuffered LTA estimate: the glide paces the pin to reach
                    # the stop when LTA says it will, not when the buffered
                    # tile says. The buffer is a display margin, not a forecast.
                    "arrival_ms": bus["arrival_ms"],
                    "stop_lat": target[0] if target else None,
                    "stop_lon": target[1] if target else None,
                    # The road from this bus to its stop; None without a fix
                    # or a stop position. See route_path.
                    "path": route_path(
                        service_no,
                        stop["code"],
                        (bus["lat"], bus["lon"]),
                        target,
                        bus["exact_minutes"] * 60,
                    )
                    if bus["lat"] is not None and target
                    else None,
                }
                if bus["lat"] is not None and origin is not None:
                    row["distance"] = format_distance(
                        haversine_m(*origin, bus["lat"], bus["lon"])
                    )
                # In the stop view distances are measured from the very stop
                # the bus is heading to, so when it fits its route, quote the
                # drive rather than the straight line — on a looping road the
                # two can differ by a kilometre. Home view keeps straight-line:
                # home is not on any bus route.
                path = row["path"]
                if origin is not None and origin == target and path and len(path) > 2:
                    row["distance"] = format_distance(path[-1][2]) + " by road"
                rows.append(row)
    rows.sort(key=lambda row: row["minutes"])
    return rows


def eta_label(minutes: int) -> str:
    return "Arr" if minutes == 0 else f"{minutes} min"


def base_context(now: datetime) -> dict:
    """Keys every view of /map shares, so the template never trips on a gap."""
    return {
        "mode": "start",
        "selected": None,
        "favourites": [],
        "near": [],
        "later": [],
        "untracked": [],
        "places": [],
        "focus_lat": None,
        "focus_lon": None,
        "window_minutes": None,
        "updated_at": now.strftime("%H:%M:%S"),
    }


def place_of(code: str) -> dict | None:
    """A stop as a map marker: {code, name, lat, lon}, or None without a position."""
    position = stop_position(code)
    if position is None:
        return None
    return {"code": code, "name": stop_name(code), "lat": position[0], "lon": position[1]}


def stop_context(now: datetime, code: str) -> dict:
    """One picked stop: everything running there, distances measured from it.

    The arrival window goes: it exists to answer "leave now" across several
    stops, which is not the question when you have gone looking for one.
    """
    stops_data = fetch_stops(now, [(code, stop_name(code))])
    origin = stop_position(code)
    buses = collect_map_buses(stops_data, origin)
    place = place_of(code)
    return {
        **base_context(now),
        "mode": "stop",
        "selected": {"code": code, "name": stop_name(code)},
        "near": buses,
        "untracked": [bus for bus in buses if bus["lat"] is None],
        "places": [place] if place else [],
        "focus_lat": origin[0] if origin else None,
        "focus_lon": origin[1] if origin else None,
    }


def favourites_context(now: datetime, codes: list[str]) -> dict:
    """The visitor's saved stops, together: what is due at any of them soon.

    Each bus is measured from the stop it is heading to (by road where its
    route fits), so "600 m by road" means the same thing in every row whichever
    favourite it belongs to.
    """
    stops_data = fetch_stops(now, [(code, stop_name(code)) for code in codes])
    buses = []
    for stop in stops_data:
        buses.extend(collect_map_buses([stop], stop_position(stop["code"])))
    buses.sort(key=lambda bus: bus["minutes"])
    near = [bus for bus in buses if bus["minutes"] <= MAP_WINDOW_MINUTES]
    places = [p for p in (place_of(code) for code in codes) if p]
    first = places[0] if places else None
    return {
        **base_context(now),
        "mode": "favourites",
        "favourites": [{"code": code, "name": stop_name(code)} for code in codes],
        "near": near,
        "later": [bus for bus in buses if bus["minutes"] > MAP_WINDOW_MINUTES][:3],
        "untracked": [bus for bus in near if bus["lat"] is None],
        "places": places,
        # Weather is taken from the first favourite; the map frames them all.
        "focus_lat": first["lat"] if first else None,
        "focus_lon": first["lon"] if first else None,
        "window_minutes": MAP_WINDOW_MINUTES,
    }


def map_context(now: datetime, args) -> dict:
    """?stop= wins over ?stops=; neither is the start screen, where the browser
    decides between its saved favourites and the stop nearest the phone."""
    code = valid_stop_code(args.get("stop"))
    if code:
        return stop_context(now, code)
    codes = valid_stop_codes(args.get("stops"))
    if codes:
        return favourites_context(now, codes)
    return base_context(now)


@app.route("/")
@app.route("/map")
def bus_map():
    now = datetime.now(SGT)
    context = map_context(now, request.args)
    # Weather only on the page load, not in the 20s poll: the forecast changes
    # a few times a day. Taken where the map is looking; regional without one.
    focus = context["focus_lat"], context["focus_lon"]
    place = focus if None not in focus else None
    response = make_response(
        render_template(
            "map.html",
            refresh_seconds=REFRESH_SECONDS,
            max_favourites=MAX_FAVOURITES,
            weather=read_weather(now, place),
            **context,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@app.route("/nearest-stop", methods=["POST"])
def nearest_stop_route():
    """{"lat": .., "lon": ..} in, {"stop": {"code", "name", "metres"} | null} out.

    POST with a JSON body rather than GET with a query string, so a phone's
    position never lands in a URL — where it would sit in server logs and
    browser history. Nothing here stores it either.
    """
    body = request.get_json(silent=True) or {}
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        return {"error": "lat and lon are required numbers"}, 400
    # Written so NaN and infinity fail too: every comparison with NaN is False.
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"error": "lat or lon out of range"}, 400
    response = make_response({"stop": nearest_stop(lat, lon)})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/stops")
def stop_search():
    query = request.args.get("q", "")
    response = make_response(
        render_template(
            "_stop_results.html", results=search_stops(query), query=query.strip()
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# Every stop with a position, as [code, name, lat, lon], for the tappable stop
# rings on /map. Built once at import from the same BUS_STOPS the rest of the
# app reads, so it can never drift from the picker or the nearest-stop search.
# Compact on purpose (arrays, not objects; 5 decimals is ~1 m): about 230 KB,
# ~70 KB once compressed in transit. Stops from an un-regenerated lookup with
# no position are left out — there is nowhere to draw them.
STOP_LIST = [
    [code, stop["name"], round(stop["lat"], 5), round(stop["lon"], 5)]
    for code, stop in sorted(BUS_STOPS.items())
    if isinstance(stop.get("lat"), (int, float)) and isinstance(stop.get("lon"), (int, float))
]


@app.route("/stops.json")
def stop_list():
    """All stops in one download, rather than "stops in this rectangle" per pan.

    One cached file makes panning instant (no request per move) and keeps the
    map's position out of URLs. The stops change a few times a year, so a day
    of caching costs nothing: Vercel's CDN and the phone answer repeat visits,
    and the function itself is rarely asked.
    """
    response = make_response({"stops": STOP_LIST})
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/map-list")
def map_list():
    # The view rides in the query string rather than being stored: it belongs
    # to the tab you are looking at, not to the app.
    now = datetime.now(SGT)
    response = make_response(
        render_template("_map_list.html", **map_context(now, request.args))
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/debug")
def debug():
    """Raw feed values for one stop (?stop=) or the BUS_STOP_CODES setting."""
    now = datetime.now(SGT)
    code = valid_stop_code(request.args.get("stop"))
    stops = [(code, stop_name(code))] if code else STOPS
    response = make_response(
        render_template(
            "_debug.html",
            stops=fetch_stops(now, stops),
            updated_at=now.strftime("%H:%M:%S"),
            now=now.isoformat(),
            buffer_seconds=BUFFER_SECONDS,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response
