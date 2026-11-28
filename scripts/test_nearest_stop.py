"""Self-check for nearest_stop and POST /nearest-stop. Run: python scripts/test_nearest_stop.py

Made-up stops, no network: the module needs API_KEY and BUS_STOP_CODES only to
import, so dummies are set first.
"""

import os
import pathlib
import sys

os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("BUS_STOP_CODES", "00001")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

index.BUS_STOPS = {
    "11111": {"name": "North Stop", "lat": 1.310, "lon": 103.80},
    "22222": {"name": "South Stop", "lat": 1.300, "lon": 103.80},
    "33333": {"name": "No Position"},  # an un-regenerated entry: never picked
}

# Picks the closer of the two, with its distance.
near = index.nearest_stop(1.302, 103.80)  # ~220 m from South, ~890 m from North
assert near["code"] == "22222" and near["name"] == "South Stop", near
assert abs(near["metres"] - 222) <= 2, near

# Nothing within 2 km (here ~11 km away): no pick.
assert index.nearest_stop(1.40, 103.80) is None

client = TestClient(index.app)
ok = client.post("/nearest-stop", json={"lat": 1.309, "lon": 103.80})
assert ok.status_code == 200 and ok.json()["stop"]["code"] == "11111", ok.json()
assert ok.headers["Cache-Control"] == "no-store"

far = client.post("/nearest-stop", json={"lat": 1.40, "lon": 103.80})
assert far.status_code == 200 and far.json() == {"stop": None}

# Trust boundary: junk is refused, not crashed on.
for bad in ({}, {"lat": "x", "lon": 1}, {"lat": 1.3}, {"lat": 95, "lon": 103.8}, {"lat": "nan", "lon": 103.8}):
    response = client.post("/nearest-stop", json=bad)
    assert response.status_code == 400, (bad, response.status_code)
assert client.post("/nearest-stop", content="not json").status_code == 400
# Valid JSON that isn't an object is junk too: refused, not a 500.
for body in ("[1, 2]", "null", '"x"'):
    response = client.post("/nearest-stop", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400, (body, response.status_code)

# GET is not allowed: coordinates must not travel in a URL.
assert client.get("/nearest-stop?lat=1.3&lon=103.8").status_code == 405

print("nearest_stop: all checks passed")
