"""Self-check for route_path in api/index.py. Run: python scripts/test_route_path.py

Uses a made-up straight north-south road, so the expected numbers can be
worked out by hand. No network: the module needs API_KEY and BUS_STOP_CODES
only to import, so dummies are set first.
"""

import os
import pathlib
import sys

os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("BUS_STOP_CODES", "00001")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))
import index  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from fetch_bus_routes import encode_polyline  # noqa: E402

# Polyline codec: Google's documented example, and a round trip.
assert index.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
assert index.decode_polyline(encode_polyline([(1.30001, 103.8), (1.29, 103.81)])) == [(1.30001, 103.8), (1.29, 103.81)]

# A road running north from 1.300 to 1.318 (~2 km), with a bend-free line, and
# stops A, B, C at 0, ~1 km and ~2 km along it.
LINE = [(1.300, 103.8), (1.309, 103.8), (1.318, 103.8)]
STEP = index.haversine_m(*LINE[0], *LINE[1])  # ~1000.8 m
index.BUS_STOPS = {
    "A": {"name": "A", "lat": 1.300, "lon": 103.8},
    "B": {"name": "B", "lat": 1.309, "lon": 103.8},
    "C": {"name": "C", "lat": 1.318, "lon": 103.8},
}


def use(routes):
    index.BUS_ROUTES = routes
    index.route_line.cache_clear()  # decoded lines are cached per service


use({"9": [{"line": encode_polyline(LINE), "stops": [["A", 0], ["B", round(STEP)], ["C", round(2 * STEP)]]}]})
C = (1.318, 103.8)
ETA = 180  # seconds — 1.5 km in 3 min is a plausible 30 km/h


def close(actual, expected, tolerance=3):
    return len(actual) == len(expected) and all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


# A bus halfway between A and B, ~11 m east of the road, heading for C.
path = index.route_path("9", "C", (1.3045, 103.8001), C, ETA)
assert close([p[2] for p in path], [-500, 0, 500, 1500]), path  # A, bus, B, C
assert abs(path[1][1] - 103.8) < 1e-9, path  # drawn on the road, not at the GPS fix
assert path[-1][:2] == list(C)

# Too far from the road (1 km east): falls back to fix -> stop.
far = index.route_path("9", "C", (1.3045, 103.81), C, ETA)
assert len(far) == 2 and far[0][2] == 0, far

# Unknown service, or a stop the route does not serve: straight line.
assert len(index.route_path("999", "C", (1.3045, 103.8), C, ETA)) == 2
assert len(index.route_path("9", "Z", (1.3045, 103.8), C, ETA)) == 2

# Implausible: 1.5 km to go but LTA says 10 s. Even with a minute's slack that
# needs ~77 km/h, so the fit is rejected and the bus goes straight.
assert len(index.route_path("9", "C", (1.3045, 103.8001), C, 10)) == 2

# Only PATH_BEHIND_M of road behind the bus is sent: on a 6 km road with the
# bus 4.5 km in, the path starts 1.5 km back.
long_line = [(1.300 + 0.009 * k, 103.8) for k in range(7)]
index.BUS_STOPS["F"] = {"name": "F", "lat": long_line[-1][0], "lon": 103.8}
use({"9": [{"line": encode_polyline(long_line), "stops": [["F", round(6 * STEP)]]}]})
path = index.route_path("9", "F", (1.300 + 0.0405, 103.8), long_line[-1], ETA)
assert abs(path[0][2] + index.PATH_BEHIND_M) <= 3, path
assert abs(path[-1][2] - 1.5 * STEP) <= 3, path

# A loop passing the same road twice: the fit nearer the stop wins, so a bus
# on the second pass is not sent round the loop again.
loop = LINE + LINE[::-1][1:]  # north to C, then back south to A
use({"9": [{"line": encode_polyline(loop), "stops": [["A", 0], ["C", round(2 * STEP)], ["A", round(4 * STEP)]]}]})
path = index.route_path("9", "A", (1.3045, 103.8), (1.300, 103.8), 600)
assert abs(path[-1][2] - 500) <= 3, path  # 500 m to A on the way back, not 3.5 km

# fit_route says which line and where: what the browser needs to draw the
# tapped bus's route. Back on the straight road, a bus halfway from A to B.
use({"9": [{"line": encode_polyline(LINE), "stops": [["A", 0], ["B", round(STEP)], ["C", round(2 * STEP)]]}]})
n, along, stop_m = index.fit_route("9", "C", (1.3045, 103.8001), ETA)
assert n == 0 and abs(along - STEP / 2) <= 3 and stop_m == round(2 * STEP), (n, along, stop_m)
assert index.fit_route("9", "C", (1.3045, 103.81), ETA) is None  # too far off the road

# /route.json: the whole line with running metres, and every stop on it.
client = TestClient(index.app)
route = client.get("/route.json?service=9&pattern=0")
assert route.status_code == 200 and route.headers["Cache-Control"] == "public, max-age=86400"
body = route.json()
assert [p[2] for p in body["line"]] == [0, round(STEP), round(2 * STEP)], body["line"]
assert [s[0] for s in body["stops"]] == ["A", "B", "C"] and body["stops"][2][4] == round(2 * STEP)
for bad in ("service=999&pattern=0", "service=9&pattern=1", "service=9&pattern=x", "service=9"):
    assert client.get("/route.json?" + bad).status_code == 404, bad

print("route_path: all checks passed")
