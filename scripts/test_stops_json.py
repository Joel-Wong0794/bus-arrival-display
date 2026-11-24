"""Self-check for GET /stops.json (the tappable stop rings on /map).

Run: python scripts/test_stops_json.py

Uses the real data/bus_stops.json, no network: the module needs API_KEY and
BUS_STOP_CODES only to import, so dummies are set first.
"""

import os
import pathlib
import sys

os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("BUS_STOP_CODES", "00001")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402

response = index.app.test_client().get("/stops.json")
assert response.status_code == 200, response.status_code
# Cached for a day: the CDN and the phone answer repeat visits.
assert response.headers["Cache-Control"] == "public, max-age=86400", response.headers
stops = response.get_json()["stops"]

# Every stop with a position, and only those: an entry with no lat/lon has
# nowhere to be drawn, so it is left out rather than sent as nulls.
with_position = {c for c, s in index.BUS_STOPS.items() if s.get("lat") is not None and s.get("lon") is not None}
assert {s[0] for s in stops} == with_position and len(stops) > 5000, len(stops)

# Shape: [code, name, lat, lon], all inside Singapore.
for code, name, lat, lon in stops:
    assert len(code) == 5 and code.isdigit(), code
    assert isinstance(name, str) and name, code
    assert 1.1 < lat < 1.5 and 103.5 < lon < 104.1, (code, lat, lon)

# Agrees with the picker and nearest-stop search, which read the same lookup.
known = {code: name for code, name, _, _ in stops}
assert known["84009"] == index.stop_name("84009"), known["84009"]

print(f"stops.json: all checks passed ({len(stops)} stops)")
