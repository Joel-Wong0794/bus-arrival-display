"""Self-check for route_path in api/index.py. Run: python scripts/test_route_path.py

Uses a made-up three-stop route on a straight north-south road, so the
expected numbers can be worked out by hand. No network: the module needs
API_KEY and BUS_STOP_CODES only to import, so dummies are set first.
"""

import os
import pathlib
import sys

os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("BUS_STOP_CODES", "00001")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402

# 0.009 deg of latitude is ~995 m. Stops A, B, C run north, 1 km apart.
index.BUS_STOPS = {
    "A": {"name": "A", "lat": 1.300, "lon": 103.8},
    "B": {"name": "B", "lat": 1.309, "lon": 103.8},
    "C": {"name": "C", "lat": 1.318, "lon": 103.8},
}
index.BUS_ROUTES = {"9": {"1": [["A", 0.0], ["B", 1.0], ["C", 2.0]]}}
C = (1.318, 103.8)

ETA = 180  # seconds — 1.5 km in 3 min is a plausible 30 km/h

def close(actual, expected, tolerance=3):
    """Metres within a few of what was worked out by hand (chords vs 0.009 deg)."""
    return len(actual) == len(expected) and all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


# A bus halfway between A and B, a few metres off the line, heading for C.
path = index.route_path("9", "C", (1.3045, 103.8001), C, ETA)
assert close([p[2] for p in path], [-500, 0, 500, 1500]), path  # A, fix, B, C
assert path[1][:2] == [1.3045, 103.8001]  # starts at the real fix, not snapped
assert path[-1][:2] == list(C)

# Too far from the route (1 km east): falls back to fix -> stop.
far = index.route_path("9", "C", (1.3045, 103.81), C, ETA)
assert len(far) == 2 and far[0][2] == 0, far

# Unknown service: straight line too.
assert len(index.route_path("999", "C", (1.3045, 103.8), C, ETA)) == 2

# A stop the route does not serve: straight line.
assert len(index.route_path("9", "Z", (1.3045, 103.8), C, ETA)) == 2

# Implausible: 1.5 km to go but LTA says 10 s. Even with a minute's slack that
# needs ~77 km/h, so the fit is rejected and the bus goes straight.
assert len(index.route_path("9", "C", (1.3045, 103.8001), C, 10)) == 2

# Road behind the fix: on a longer route, stops up to 1.5 km back are kept.
index.BUS_STOPS.update({
    "P": {"name": "P", "lat": 1.282, "lon": 103.8},  # 2 km before A
    "Q": {"name": "Q", "lat": 1.291, "lon": 103.8},  # 1 km before A
})
index.BUS_ROUTES["9"]["1"] = [["P", 0.0], ["Q", 1.0], ["A", 2.0], ["B", 3.0], ["C", 4.0]]
path = index.route_path("9", "C", (1.3045, 103.8001), C, ETA)
assert close([p[2] for p in path], [-1500, -500, 0, 500, 1500]), path  # Q, A, fix, B, C

# LTA's 0.1 km rounding: B listed at the same distance as A. Metres must still
# rise with the map distance (the bug that made pins leap 120 m in a tick),
# and the total is stretched to LTA's road figure, never below the chords.
index.BUS_ROUTES["9"]["1"] = [["A", 0.0], ["B", 0.0], ["C", 2.4]]
path = index.route_path("9", "C", (1.3045, 103.8001), C, ETA)
metres = [p[2] for p in path]
assert metres == sorted(metres) and len(set(metres)) == len(metres), path
assert abs(metres[-1] - 2400) <= 3, path  # road distance from the fix: 2.4 km - 0 km along
assert abs((metres[2] - metres[1]) / (metres[3] - metres[2]) - 0.5) < 0.01, path  # fix->B is half of B->C

print("route_path: all checks passed")
