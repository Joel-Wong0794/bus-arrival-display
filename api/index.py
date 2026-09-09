import math
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

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

# Read with defaults rather than os.environ[...]: every route lives in this one
# module, so a missing value here would take the arrivals display down too.
HOME_LAT = float(os.environ.get("HOME_LAT", "1.3527244966827343"))
HOME_LON = float(os.environ.get("HOME_LON", "103.72300347346224"))
# Only buses arriving within this many minutes get plotted. The map answers
# "should I leave now", so anything further out is noise.
MAP_WINDOW_MINUTES = int(os.environ.get("MAP_WINDOW_MINUTES", "5"))

# OneMap's embeddable map. Markers are passed in the URL, which sidesteps both a
# tile-layer dependency and the same-origin wall around a cross-origin iframe.
AMM_URL = "https://www.onemap.gov.sg/amm/amm.html"

LOAD_LABELS = {"SEA": "Seats", "SDA": "Standing", "LSD": "Limited"}

COMPASS_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

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
    air can be 2km by road, so the display calls this "direct"."""
    radius = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def format_distance(metres: float) -> str:
    if metres < 1000:
        return f"{int(round(metres / 10) * 10)} m"
    return f"{metres / 1000:.1f} km"


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


def collect_map_buses(stops_data: list[dict]) -> list[dict]:
    """Flattens every stop's services into one soonest-first list.

    Buses without a live position stay in the list — a bus arriving in two
    minutes still matters even when it cannot be plotted.
    """
    rows = []
    for stop in stops_data:
        for service_no, buses in stop["services"]:
            for bus in buses:
                row = {
                    "service_no": service_no,
                    "stop_name": stop["name"],
                    "minutes": bus["minutes"],
                    "load": bus["load"],
                    "lat": bus["lat"],
                    "lon": bus["lon"],
                    "distance": None,
                    "compass": None,
                    "bearing": None,
                }
                if bus["lat"] is not None:
                    metres = haversine_m(HOME_LAT, HOME_LON, bus["lat"], bus["lon"])
                    bearing = bearing_deg(HOME_LAT, HOME_LON, bus["lat"], bus["lon"])
                    row["distance"] = format_distance(metres)
                    row["bearing"] = round(bearing)
                    row["compass"] = COMPASS_POINTS[round(bearing / 22.5) % 16]
                rows.append(row)
    rows.sort(key=lambda row: row["minutes"])
    return rows


def eta_label(minutes: int) -> str:
    return "Arr" if minutes == 0 else f"{minutes} min"


def build_map_url(buses: list[dict]) -> str:
    """AMM renders whatever markers the URL carries, so the pins are built here
    rather than drawn over the iframe — a cross-origin frame never reports where
    it has been panned to, so an overlay would drift out of alignment."""
    params = [
        "mapStyle=Grey",
        "zoomLevel=16",
        "popupWidth=200",
        f"marker=latLng:{HOME_LAT},{HOME_LON}!icon:fa-home!colour:darkred",
    ]
    for bus in buses:
        if bus["lat"] is None:
            continue
        colour = "red" if bus["minutes"] == 0 else "orange"
        label = quote(f"{bus['service_no']} - {eta_label(bus['minutes'])}", safe="")
        params.append(
            f"marker=latLng:{bus['lat']},{bus['lon']}"
            f"!icon:fa-bus!colour:{colour}!popupText:{label}"
        )
    return f"{AMM_URL}?{'&'.join(params)}"


def map_context(now: datetime) -> dict:
    buses = collect_map_buses(fetch_stops(now))
    near = [bus for bus in buses if bus["minutes"] <= MAP_WINDOW_MINUTES]
    return {
        "near": near,
        "later": [bus for bus in buses if bus["minutes"] > MAP_WINDOW_MINUTES][:3],
        "untracked": [bus for bus in near if bus["lat"] is None],
        "map_url": build_map_url(near),
        "window_minutes": MAP_WINDOW_MINUTES,
        "updated_at": now.strftime("%H:%M:%S"),
    }


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
    response = make_response(
        render_template(
            "map.html", refresh_seconds=REFRESH_SECONDS, **map_context(now)
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/map-list")
def map_list():
    # Only the list polls. Re-pointing the iframe on a timer would throw away
    # any panning done in it, so the map refreshes on request instead.
    now = datetime.now(SGT)
    response = make_response(render_template("_map_list.html", **map_context(now)))
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
