"""Regenerate data/bus_routes.json — every service's stops, in order.

Run this by hand, not at request time — same reasoning as fetch_bus_stops.py:
BusRoutes has ~26,000 rows across ~50 pages, far too slow for a serverless
cold start, and it changes only when LTA amends a route. Baked once, the app
makes no route calls at runtime.

What it is for: /map glides each pin along the route towards its stop rather
than in a straight line (see static/glide.js and README "Gliding pins").
Stop positions come from data/bus_stops.json, so regenerate that first if it
predates coordinates.

    python scripts/fetch_bus_routes.py

Output shape — service -> direction -> stops in route order, each with its
distance from the start of the route in km, exactly as LTA reports it:

    {"334": {"1": [["28009", 0.0], ["28011", 0.4], ...]}}
"""

import json
import os
import pathlib
import sys

import requests
from dotenv import load_dotenv

BUS_ROUTES_URL = "https://datamall2.mytransport.sg/ltaodataservice/BusRoutes"
# The feed's fixed page size; it ignores any $top we ask for.
PAGE_SIZE = 500
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "bus_routes.json"


def fetch_all(api_key: str) -> dict[str, dict[str, list]]:
    headers = {"AccountKey": api_key, "accept": "application/json"}
    # (service, direction) -> [(sequence, code, km)], sorted at the end because
    # the feed's ordering is not documented as guaranteed.
    rows: dict[tuple[str, str], list[tuple[int, str, float]]] = {}
    skip = 0
    while True:
        response = requests.get(
            BUS_ROUTES_URL, headers=headers, params={"$skip": skip}, timeout=30
        )
        response.raise_for_status()
        page = response.json().get("value", [])
        if not page:
            break
        for row in page:
            try:
                key = (str(row["ServiceNo"]), str(row["Direction"]))
                stop = (int(row["StopSequence"]), str(row["BusStopCode"]), float(row["Distance"] or 0))
            except (KeyError, TypeError, ValueError):
                continue  # a malformed row costs one stop, not the whole bake
            rows.setdefault(key, []).append(stop)
        print(f"  fetched {len(page):>3} rows at skip={skip}", file=sys.stderr)
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE

    routes: dict[str, dict[str, list]] = {}
    for (service, direction), stops in rows.items():
        stops.sort()
        routes.setdefault(service, {})[direction] = [[code, km] for _, code, km in stops]
    return routes


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("API_KEY")
    if not api_key:
        sys.exit("API_KEY is not set — put it in .env before running this.")

    print("Paging the BusRoutes dataset…", file=sys.stderr)
    routes = fetch_all(api_key)
    if not routes:
        sys.exit("Fetched no routes — refusing to overwrite the existing file.")

    # One service per line: compact enough to ship, readable enough to diff.
    lines = [f"{json.dumps(s)}: {json.dumps(d, separators=(',', ':'))}" for s, d in sorted(routes.items())]
    OUT.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"Wrote {len(routes)} services to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
