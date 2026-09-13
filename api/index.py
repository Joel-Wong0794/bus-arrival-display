import json
import math
import os
import pathlib
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

def read_home() -> tuple[float, float] | None:
    """Home coordinates, or None when unset.

    No default: a home address does not belong in a public repository. Returning
    None rather than raising keeps a missing value contained to /map — every
    route lives in this one module, so raising here would take the arrivals
    display down with it.
    """
    lat, lon = os.environ.get("HOME_LAT"), os.environ.get("HOME_LON")
    if not lat or not lon:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


HOME = read_home()
# Only buses arriving within this many minutes get plotted. The map answers
# "should I leave now", so anything further out is noise.
MAP_WINDOW_MINUTES = int(os.environ.get("MAP_WINDOW_MINUTES", "5"))
# How far out the stop picker looks for stops worth offering.
NEARBY_RADIUS_M = int(os.environ.get("NEARBY_RADIUS_M", "800"))
NEARBY_LIMIT = 12

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
# this can't be derived from HOME — it has to be told which one you're in.
WEATHER_REGION = os.environ.get("WEATHER_REGION", "west").strip().lower()
# NEA reissues this a few times a day, so fetching per page load buys nothing
# and adds latency. A warm serverless instance keeps the cache; a cold one
# simply fetches again, which is why this needs no store.
WEATHER_TTL_SECONDS = int(os.environ.get("WEATHER_TTL_SECONDS", "900"))

_weather_cache: dict = {"fetched_at": None, "record": None}

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


def weather_record(now: datetime) -> dict | None:
    """The raw NEA record, cached for WEATHER_TTL_SECONDS.

    Caches the record rather than the rendered values so the period is always
    chosen against the current clock — a cached "6am to Midday" would otherwise
    linger up to a full TTL after that window closed.
    """
    fetched_at = _weather_cache["fetched_at"]
    if fetched_at and (now - fetched_at).total_seconds() < WEATHER_TTL_SECONDS:
        return _weather_cache["record"]

    headers = {"accept": "application/json"}
    if WEATHER_API_KEY:
        headers["x-api-key"] = WEATHER_API_KEY
    try:
        response = requests.get(WEATHER_URL, headers=headers, timeout=5)
        response.raise_for_status()
        record = response.json()["data"]["records"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        # Serve the last good reading rather than nothing: a 24-hour forecast
        # ages gracefully, and a blank header is worse than a slightly old one.
        return _weather_cache["record"]

    _weather_cache["fetched_at"] = now
    _weather_cache["record"] = record
    return record


def read_weather(now: datetime) -> dict | None:
    """What it's doing outside, or None if NEA can't be reached.

    None rather than raising, and never on the arrivals path: the weather is a
    garnish on a page whose job is bus times. An NEA outage must not take the
    departure board down with it.
    """
    record = weather_record(now)
    if not record:
        return None

    general = record.get("general", {})
    temperature = general.get("temperature", {})
    period = current_period(record.get("periods", []), now) or {}
    # Region first, island-wide as the fallback — a region NEA didn't send is
    # better answered generally than not at all.
    forecast = period.get("regions", {}).get(WEATHER_REGION) or general.get(
        "forecast", {}
    )
    if not forecast.get("text"):
        return None
    return {
        "text": forecast["text"],
        "icon": weather_icon(forecast["text"], now),
        "low": temperature.get("low"),
        "high": temperature.get("high"),
        "region": WEATHER_REGION,
        "period": period.get("timePeriod", {}).get("text"),
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


def stop_position(code: str) -> tuple[float, float] | None:
    """Where a stop is, or None if the lookup predates coordinates."""
    entry = BUS_STOPS.get(code, {})
    lat, lon = entry.get("lat"), entry.get("lon")
    return (lat, lon) if lat is not None and lon is not None else None


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

stop_codes = os.environ["BUS_STOP_CODES"].split(",")
stop_names = os.environ.get("BUS_STOP_NAMES", "").split(",")
# Pair each stop code with a display name, falling back to the code itself.
STOPS = [
    (
        code.strip(),
        stop_names[i].strip()
        if i < len(stop_names) and stop_names[i].strip()
        else code.strip(),
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


def format_distance(metres: float) -> str:
    if metres < 1000:
        return f"{int(round(metres / 10) * 10)} m"
    return f"{metres / 1000:.1f} km"


def find_nearby_stops() -> list[dict]:
    """Stops within NEARBY_RADIUS_M of home, nearest first.

    Computed once at import: both inputs are import-time constants, and scanning
    5000 stops per request to produce an unchanging list would be waste. Empty
    until data/bus_stops.json is regenerated with coordinates.
    """
    if HOME is None:
        return []
    found = []
    for code, entry in BUS_STOPS.items():
        position = stop_position(code)
        if position is None:
            continue
        metres = haversine_m(*HOME, *position)
        if metres <= NEARBY_RADIUS_M:
            found.append(
                {"code": code, "name": entry["name"], "away": format_distance(metres)}
            )
    found.sort(key=lambda stop: stop["code"])
    return found[:NEARBY_LIMIT]


NEARBY_STOPS = find_nearby_stops()


def valid_stop_code(code: str | None) -> str | None:
    """A stop code safe to query and print, or None.

    Singapore codes are five digits. Anything else came from a hand-edited URL,
    so it is dropped and the page falls back to the home view rather than
    forwarding junk to DataMall.
    """
    if code and len(code) == 5 and code.isdigit():
        return code
    return None


def picker_options(selected_code: str | None) -> list[dict]:
    """Stops offered in the picker: the configured ones, then whatever is near.

    A selected stop that is neither is appended, so arriving by a hand-typed URL
    still shows the picker pointing at where you actually are.
    """
    options = [{"code": code, "name": name, "away": None} for code, name in STOPS]
    seen = {option["code"] for option in options}
    for stop in NEARBY_STOPS:
        if stop["code"] not in seen:
            options.append(stop)
            seen.add(stop["code"])
    if selected_code and selected_code not in seen:
        options.append(
            {"code": selected_code, "name": stop_name(selected_code), "away": None}
        )
    return options


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


def fetch_stops(now: datetime) -> list[dict]:
    stops_data = []
    for code, name in STOPS:
        try:
            services = get_bus_arrival(API_KEY, code, now)
            error = None
        except requests.RequestException:
            services = []
            error = "Unavailable"
        stops_data.append(
            {"code": code, "name": name, "services": services, "error": error}
        )
    return stops_data


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
                }
                if bus["lat"] is not None and origin is not None:
                    row["distance"] = format_distance(
                        haversine_m(*origin, bus["lat"], bus["lon"])
                    )
                rows.append(row)
    rows.sort(key=lambda row: row["minutes"])
    return rows


def eta_label(minutes: int) -> str:
    return "Arr" if minutes == 0 else f"{minutes} min"


def stop_context(now: datetime, code: str) -> dict:
    """The view when a stop has been picked: everything due there, and nothing
    measured from home.

    Home is not the reference point any more, so its distances would be
    meaningless — a stop across the island is not "3 km away" in any sense that
    helps. Distances are taken from the stop instead, and omitted entirely while
    the lookup has no coordinates for it. The arrival window goes too: it exists
    to answer "leave now", which is not the question being asked here.
    """
    try:
        services = get_bus_arrival(API_KEY, code, now)
        error = None
    except requests.RequestException:
        services = []
        error = "Unavailable"
    stops_data = [
        {"code": code, "name": stop_name(code), "services": services, "error": error}
    ]
    origin = stop_position(code)
    buses = collect_map_buses(stops_data, origin)
    return {
        "mode": "stop",
        "selected": {"code": code, "name": stop_name(code)},
        "options": picker_options(code),
        "near": buses,
        "later": [],
        "untracked": [bus for bus in buses if bus["lat"] is None],
        "focus_lat": origin[0] if origin else None,
        "focus_lon": origin[1] if origin else None,
        "show_focus_pin": origin is not None,
        "window_minutes": None,
        "updated_at": now.strftime("%H:%M:%S"),
        "home_missing": False,
    }


def home_context(now: datetime) -> dict:
    buses = collect_map_buses(fetch_stops(now), HOME)
    near = [bus for bus in buses if bus["minutes"] <= MAP_WINDOW_MINUTES]
    return {
        "mode": "home",
        "selected": None,
        "options": picker_options(None),
        "near": near,
        "later": [bus for bus in buses if bus["minutes"] > MAP_WINDOW_MINUTES][:3],
        "untracked": [bus for bus in near if bus["lat"] is None],
        "focus_lat": HOME[0],
        "focus_lon": HOME[1],
        "show_focus_pin": True,
        "window_minutes": MAP_WINDOW_MINUTES,
        "updated_at": now.strftime("%H:%M:%S"),
        "home_missing": False,
    }


def map_context(now: datetime, selected_code: str | None = None) -> dict:
    code = valid_stop_code(selected_code)
    if code:
        # Nothing in this path reads HOME, so the picker keeps working even when
        # the flat's coordinates were never configured.
        return stop_context(now, code)
    if HOME is None:
        return {
            "home_missing": True,
            "mode": "home",
            "selected": None,
            "options": picker_options(None),
            "near": [],
            "later": [],
            "untracked": [],
            "focus_lat": None,
            "focus_lon": None,
            "show_focus_pin": False,
            "window_minutes": MAP_WINDOW_MINUTES,
            "updated_at": now.strftime("%H:%M:%S"),
        }
    return home_context(now)


@app.route("/")
def index():
    return render_template(
        "index.html",
        refresh_seconds=REFRESH_SECONDS,
        buffer_seconds=BUFFER_SECONDS,
    )


@app.route("/map")
def bus_map():
    now = datetime.now(SGT)
    # Only on the page load, not on /map-list: the list polls every 20s and the
    # forecast changes a few times a day, so putting it in the poll would spend
    # a request an hour to redraw the same words.
    response = make_response(
        render_template(
            "map.html",
            refresh_seconds=REFRESH_SECONDS,
            weather=read_weather(now),
            **map_context(now, request.args.get("stop")),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/map-list")
def map_list():
    # The selection rides in the query string rather than being stored: it is a
    # property of the tab you are looking at, not of the app. The kiosk never
    # sends one, so nothing here can change what the tablet displays.
    now = datetime.now(SGT)
    response = make_response(
        render_template("_map_list.html", **map_context(now, request.args.get("stop")))
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/arrivals")
def arrivals():
    # One snapshot for the whole request, so every tile is measured against the
    # same instant and the debug view shows the exact value used.
    now = datetime.now(SGT)
    stops_data = fetch_stops(now)
    template = "_debug.html" if request.args.get("debug") else "_arrivals.html"
    response = make_response(
        render_template(
            template,
            stops=stops_data,
            updated_at=now.strftime("%H:%M:%S"),
            now=now.isoformat(),
            buffer_seconds=BUFFER_SECONDS,
        )
    )
    # Old Android WebViews will otherwise cache this GET heuristically.
    response.headers["Cache-Control"] = "no-store"
    return response
