import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, render_template

load_dotenv()

app = Flask(__name__, template_folder="../templates", static_folder="../static")

# LTA DataMall v3 bus arrival endpoint
BUS_ARRIVAL_URL = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"

API_KEY = os.environ["API_KEY"]
REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "20"))

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


def get_bus_arrival(api_key: str, stop_code: str) -> list[tuple[str, list[int]]]:
    """Returns [(service_no, [eta_minutes, ...]), ...] for a given bus stop."""
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
        etas = []
        for key in ("NextBus", "NextBus2", "NextBus3"):
            arrival = service.get(key, {}).get("EstimatedArrival")
            if not arrival:
                continue
            eta_time = datetime.fromisoformat(arrival)
            minutes = (eta_time - datetime.now(eta_time.tzinfo)).total_seconds() / 60
            etas.append(max(0, round(minutes)))
        results.append((service["ServiceNo"], etas))
    return results


@app.route("/")
def index():
    return render_template("index.html", refresh_seconds=REFRESH_SECONDS)


@app.route("/arrivals")
def arrivals():
    stops_data = []
    for code, name in STOPS:
        try:
            services = get_bus_arrival(API_KEY, code)
            error = None
        except requests.RequestException:
            services = []
            error = "Unavailable"
        stops_data.append(
            {"code": code, "name": name, "services": services, "error": error}
        )
    return render_template(
        "_arrivals.html",
        stops=stops_data,
        updated_at=datetime.now().strftime("%H:%M:%S"),
    )
