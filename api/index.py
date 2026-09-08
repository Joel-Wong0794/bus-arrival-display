import os
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


@app.route("/")
def index():
    return render_template(
        "index.html",
        refresh_seconds=REFRESH_SECONDS,
        buffer_seconds=BUFFER_SECONDS,
    )


@app.route("/arrivals")
def arrivals():
    # One snapshot for the whole request, so every tile is measured against the
    # same instant and the debug view shows the exact value used.
    now = datetime.now(SGT)
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
