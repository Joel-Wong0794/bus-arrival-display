"""Regenerate data/bus_stops.json — the destination-code to stop-name lookup.

Run this by hand, not at request time. The BusStops dataset has no
filter-by-code parameter, so resolving a single destination means paging the
whole ~5000-stop feed; doing that on a serverless cold start would add seconds
to a page the tablet polls every 20s. Baking the result into the repo keeps the
app stateless at runtime — this is a static asset, not a database.

Re-run only when a stop in BUS_STOP_CODES gains a service whose destination is
missing from the file (the dashboard falls back to the raw code, so a stale
file degrades rather than breaks).

    python scripts/fetch_bus_stops.py
"""

import json
import os
import pathlib
import sys

import requests
from dotenv import load_dotenv

BUS_STOPS_URL = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
# The feed's fixed page size; it ignores any $top we ask for.
PAGE_SIZE = 500
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "bus_stops.json"


def fetch_all(api_key: str) -> dict[str, str]:
    headers = {"AccountKey": api_key, "accept": "application/json"}
    stops: dict[str, str] = {}
    skip = 0
    while True:
        response = requests.get(
            BUS_STOPS_URL, headers=headers, params={"$skip": skip}, timeout=30
        )
        response.raise_for_status()
        page = response.json().get("value", [])
        if not page:
            break
        for stop in page:
            code, description = stop.get("BusStopCode"), stop.get("Description")
            if code and description:
                stops[code] = description
        print(f"  fetched {len(page):>3} rows at skip={skip}", file=sys.stderr)
        # A short final page means we've reached the end; asking again returns [].
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return stops


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("API_KEY")
    if not api_key:
        sys.exit("API_KEY is not set — put it in .env before running this.")

    print("Paging the BusStops dataset…", file=sys.stderr)
    stops = fetch_all(api_key)
    if not stops:
        sys.exit("Fetched no stops — refusing to overwrite the existing file.")

    OUT.parent.mkdir(exist_ok=True)
    # Sorted so a regeneration produces a readable diff instead of a reshuffle.
    OUT.write_text(
        json.dumps(dict(sorted(stops.items())), indent=0, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(stops)} stops to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
