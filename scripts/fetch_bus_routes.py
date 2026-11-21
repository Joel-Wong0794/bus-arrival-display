"""Regenerate data/bus_routes.json — every service's road line and stops.

Source: cheeaun/sgbusdata (https://github.com/cheeaun/sgbusdata), the data
behind BusRouter SG. It publishes each service's route as a real road line —
LTA's route KMLs, patched from OneMap/Citymapper where LTA's are missing or cut
off — which is what Next Stop's interpolation glides along. LTA's DataMall has
no route geometry at all, only stop sequences.

Run this by hand, not at request time: it downloads ~550 KB from GitHub and
projects ~26,000 stops onto their lines. No LTA API key or calls needed.
data/bus_stops.json must be current first — stop positions come from it.

    python scripts/fetch_bus_routes.py

Output — one entry per pattern (direction), stops with metres along the line:

    {"334": [{"line": "<Google encoded polyline>",
              "stops": [["28009", 0], ["28441", 412], ...]}],
     "_meta": {"source": ..., "updated": ...}}

A stop with no position in bus_stops.json is kept with metres = null, so the
router simply never targets it.
"""

import json
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "bus_routes.json"
BASE = "https://raw.githubusercontent.com/cheeaun/sgbusdata/main/data/v1/"

# Reuse the app's own geometry (decoder, projection, distances, stop positions)
# so bake time and request time can never disagree. The module only needs
# these two variables to import; nothing here calls DataMall.
os.environ.setdefault("API_KEY", "unused-by-this-script")
os.environ.setdefault("BUS_STOP_CODES", "00000")
sys.path.insert(0, str(ROOT / "api"))
import index  # noqa: E402

# A stop's first pass within this of the line is taken as its spot. Searching
# forward from the previous stop, and stopping at the first close pass, keeps
# loop services from placing a stop on a later lap past the same road.
NEAR_M = 60
# Having found a close pass, keep looking this much further for a closer one
# before settling — the line may run alongside a stop for a while.
LOOKAHEAD_M = 400
# Report stops that sit further than this from their own line: a sign the line
# and the stop list disagree, worth a look but not fatal.
WARN_M = 150


def encode_polyline(points: list) -> str:
    """Inverse of index.decode_polyline (Google format, precision 5)."""
    out, last = [], (0, 0)
    for lat, lon in points:
        now = (round(lat * 1e5), round(lon * 1e5))
        for value in (now[0] - last[0], now[1] - last[1]):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        last = now
    return "".join(out)


def metres_along(points: list) -> list[float]:
    metres = [0.0]
    for a, b in zip(points, points[1:]):
        metres.append(metres[-1] + index.haversine_m(*a, *b))
    return metres


def orientations(points: list, codes: list[str]) -> list[list]:
    """The published line, plus the variants worth trying against the stops.

    sgbusdata's lines do not always run the way the stop list does. Measured
    at bake time: 27 patterns run backwards, and loop services often start
    partway round the loop instead of at the interchange (service 102 starts
    12.7 km in). Placing stops on those as published lands them on the far
    side of the same road. So: try the line reversed, and for a closed loop,
    rotated to begin at the first stop — the bake keeps whichever fits best.
    """
    options = [points, points[::-1]]
    first = index.baked_position(codes[0]) if codes else None
    closed = len(points) > 2 and index.haversine_m(*points[0], *points[-1]) < 200
    if first and codes[0] == codes[-1] and closed:
        for line in list(options):
            k = min(range(len(line)), key=lambda i: index.haversine_m(*line[i], *first))
            # Closed: the last point repeats the first, so drop one copy.
            options.append(line[k:] + line[1 : k + 1] if k else line)
    return options


def best_fit(published: list, codes: list[str]) -> tuple[int, list, list]:
    """(far_count, points, placed stops) for the orientation that fits best.
    Strictly fewer far-off stops is needed to beat the published line."""
    best = None
    for line in orientations(published, codes):
        placed, far = place_stops(line, metres_along(line), codes)
        if best is None or far < best[0]:
            best = (far, line, placed)
    return best


def fetch(name: str):
    response = requests.get(BASE + name, timeout=60)
    response.raise_for_status()
    return response.json() if name.endswith(".json") else response.text.strip().strip('"')


def place_stops(points: list, metres: list, codes: list[str]) -> tuple[list, int]:
    """Each stop's metres along the line, in order. Returns (placed, far_count)."""
    placed, far, segment = [], 0, 0
    for code in codes:
        position = index.baked_position(code)
        if position is None:
            placed.append([code, None])
            continue
        best = None  # (offset, metres_along, segment)
        for i in range(segment, len(points) - 1):
            offset, t = index.project(position, points[i], points[i + 1])
            along = metres[i] + t * (metres[i + 1] - metres[i])
            if best is None or offset < best[0]:
                best = (offset, along, i)
            if best[0] <= NEAR_M and metres[i] - best[1] > LOOKAHEAD_M:
                break
        if best is None:  # line shorter than one segment
            placed.append([code, None])
            continue
        if best[0] > WARN_M:
            far += 1
        segment = best[2]  # never search backwards: stops come in order
        placed.append([code, round(best[1])])
    return placed, far


def main() -> None:
    if not index.BUS_STOPS or index.baked_position(next(iter(index.BUS_STOPS))) is None:
        sys.exit("data/bus_stops.json has no positions — run fetch_bus_stops.py first.")

    print("Downloading sgbusdata…", file=sys.stderr)
    lines, services, updated = fetch("routes.min.json"), fetch("services.min.json"), fetch("last-updated.txt")

    routes: dict = {}
    total_stops = total_far = reoriented = 0
    for service_no, encoded_lines in sorted(lines.items()):
        stop_lists = services.get(service_no, {}).get("routes", [])
        if len(stop_lists) != len(encoded_lines):
            print(f"  skipped {service_no}: {len(encoded_lines)} lines vs {len(stop_lists)} stop lists", file=sys.stderr)
            continue
        patterns = []
        for encoded, codes in zip(encoded_lines, stop_lists):
            published = index.decode_polyline(encoded)
            far, points, placed = best_fit(published, codes)
            if points is not published:
                reoriented += 1
                encoded = encode_polyline(points)
                # The re-encoded line must decode to exactly what was placed.
                assert index.decode_polyline(encoded) == [(round(a, 5), round(b, 5)) for a, b in points]
            total_stops += len(placed)
            total_far += far
            patterns.append({"line": encoded, "stops": placed})
        routes[service_no] = patterns

    if not routes:
        sys.exit("Got no routes — refusing to overwrite the existing file.")
    routes["_meta"] = {"source": "https://github.com/cheeaun/sgbusdata", "updated": updated}
    # One service per line: compact enough to ship, readable enough to diff.
    body = ",\n".join(f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in routes.items())
    OUT.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    print(f"Wrote {len(routes) - 1} services ({total_stops} stops) to {OUT}", file=sys.stderr)
    print(f"  sgbusdata last updated {updated}", file=sys.stderr)
    print(f"  {reoriented} lines reversed or rotated to match their stop order", file=sys.stderr)
    print(f"  {total_far} stops sit more than {WARN_M} m from their line", file=sys.stderr)


if __name__ == "__main__":
    main()
