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

LOAD_LABELS = {"SEA": "Seats", "SDA": "Standing", "LSD": "Limited"}

# Destination-code to stop-name lookup, baked by scripts/fetch_bus_stops.py.
# Read once at import: the arrival feed names a destination only by code, and
# the BusStops dataset has no filter-by-code, so resolving one at request time
# would mean paging ~5000 rows on every cold start. Missing file or code is not
# fatal — the destination just falls back to the raw code, or is omitted.
BUS_STOPS_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "bus_stops.json"
try:
    BUS_STOPS = json.loads(BUS_STOPS_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    BUS_STOPS = {}


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
    return f"to {BUS_STOPS.get(destination, destination)}"

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
                }
                if bus["lat"] is not None:
                    row["distance"] = format_distance(
                        haversine_m(*HOME, bus["lat"], bus["lon"])
                    )
                rows.append(row)
    rows.sort(key=lambda row: row["minutes"])
    return rows


def eta_label(minutes: int) -> str:
    return "Arr" if minutes == 0 else f"{minutes} min"


def map_context(now: datetime) -> dict:
    if HOME is None:
        return {
            "home_missing": True,
            "near": [],
            "later": [],
            "untracked": [],
            "home_lat": None,
            "home_lon": None,
            "window_minutes": MAP_WINDOW_MINUTES,
            "updated_at": now.strftime("%H:%M:%S"),
        }
    buses = collect_map_buses(fetch_stops(now))
    near = [bus for bus in buses if bus["minutes"] <= MAP_WINDOW_MINUTES]
    return {
        "near": near,
        "later": [bus for bus in buses if bus["minutes"] > MAP_WINDOW_MINUTES][:3],
        "untracked": [bus for bus in near if bus["lat"] is None],
        "home_lat": HOME[0],
        "home_lon": HOME[1],
        "window_minutes": MAP_WINDOW_MINUTES,
        "updated_at": now.strftime("%H:%M:%S"),
        "home_missing": False,
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
