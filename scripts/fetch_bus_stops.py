"""Regenerate data/bus_stops.json — the stop-code to name and position lookup.

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


def fetch_all(api_key: str) -> tuple[dict[str, dict], int]:
    """Returns {code: {"name", "lat", "lon"}} and a count of stops with no fix.

    Coordinates ride along in the same response the names come from, so keeping
    them costs nothing beyond file size. A stop missing them keeps its name and
    loses only the features that need a position.
    """
    headers = {"AccountKey": api_key, "accept": "application/json"}
    stops: dict[str, dict] = {}
    no_coords = 0
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
            if not code or not description:
                continue
            entry = {"name": description}
            try:
                lat = float(stop.get("Latitude") or 0)
                lon = float(stop.get("Longitude") or 0)
            except (TypeError, ValueError):
                lat = lon = 0
            if lat and lon:
                # 6dp is ~11cm — far finer than a bus stop needs, and it keeps
                # the file a third smaller than full precision.
                entry["lat"] = round(lat, 6)
                entry["lon"] = round(lon, 6)
            else:
                no_coords += 1
            stops[code] = entry
        print(f"  fetched {len(page):>3} rows at skip={skip}", file=sys.stderr)
        # A short final page means we've reached the end; asking again returns [].
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return stops, no_coords


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("API_KEY")
    if not api_key:
        sys.exit("API_KEY is not set — put it in .env before running this.")

    print("Paging the BusStops dataset…", file=sys.stderr)
    stops, no_coords = fetch_all(api_key)
    if not stops:
        sys.exit("Fetched no stops — refusing to overwrite the existing file.")

    OUT.parent.mkdir(exist_ok=True)
    # Sorted so a regeneration produces a readable diff instead of a reshuffle.
    OUT.write_text(
        json.dumps(dict(sorted(stops.items())), indent=0, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(stops)} stops to {OUT}", file=sys.stderr)
    # A handful without a fix is normal. All of them means the coordinate fields
    # are not called Latitude/Longitude any more, and this is where you find out.
    if no_coords:
        print(f"  {no_coords} of them had no coordinates", file=sys.stderr)


if __name__ == "__main__":
    main()
