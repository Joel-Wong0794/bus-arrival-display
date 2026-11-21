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

# A bus halfway between A and B, a few metres off the line, heading for C.
path = index.route_path("9", "C", (1.3045, 103.8001), C)
assert [p[2] for p in path] == [-500, 0, 500, 1500], path  # A, fix, B, C
assert path[1][:2] == [1.3045, 103.8001]  # starts at the real fix, not snapped
assert path[-1][:2] == list(C)

# Too far from the route (1 km east): falls back to fix -> stop.
far = index.route_path("9", "C", (1.3045, 103.81), C)
assert len(far) == 2 and far[0][2] == 0, far

# Unknown service: straight line too.
assert len(index.route_path("999", "C", (1.3045, 103.8), C)) == 2

# A stop the route does not serve: straight line.
assert len(index.route_path("9", "Z", (1.3045, 103.8), C)) == 2

print("route_path: all checks passed")
