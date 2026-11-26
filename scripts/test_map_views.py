"""Self-check for the three views of the single map page. No network: DataMall
is replaced with a canned answer.

Run: python scripts/test_map_views.py
"""

import os
import pathlib
import sys

os.environ.setdefault("API_KEY", "test")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402

# ?stops= parsing: valid five-digit codes, no repeats, in order, capped.
assert index.valid_stop_codes("84009, 11119,84009,abc,1234,") == ["84009", "11119"]
assert index.valid_stop_codes(None) == []
codes = ",".join(str(10000 + i) for i in range(20))
assert len(index.valid_stop_codes(codes)) == index.MAX_FAVOURITES

calls = []


def fake_arrival(api_key, code, now):
    calls.append(code)
    minutes = {"84009": 2, "11119": 9}.get(code)
    if minutes is None:
        return []
    bus = {
        "lat": None, "lon": None, "load": "Seats", "minutes": minutes,
        "arrival_ms": 0, "raw": "", "exact_minutes": minutes, "monitored": 0,
        "destination": "to Somewhere", "type": "SD", "type_label": "Single deck",
    }
    return [("10" if code == "84009" else "20", [bus])]


index.get_bus_arrival = fake_arrival
index.read_weather = lambda now, place=None: None
client = index.app.test_client()

# Start screen: no stop asked for, so no DataMall call and no bus list poll.
calls.clear()
page = client.get("/").get_data(as_text=True)
assert calls == [], calls
assert 'data-mode="start"' in page and 'id="start"' in page
assert "hx-get=\"/map-list" not in page
assert client.get("/map").status_code == 200

# Same-name stops are told apart by road, in search results and the header.
hits = [h for h in index.search_stops("blk 402") if h["name"] == "Blk 402"]
assert len(hits) >= 2 and all(h["road"] for h in hits), hits
assert len({h["road"] for h in hits}) == len(hits), hits
results = client.get("/stops?q=blk%20402").get_data(as_text=True)
assert results.count('class="stop-result-road"') >= 2
assert index.search_stops("bt batok rd"), "a road name should find its stops"

# One stop: everything running there.
page = client.get("/map?stop=84009").get_data(as_text=True)
assert 'data-mode="stop"' in page and 'id="star"' in page
assert 'class="eyebrow-road"> · ' + index.stop_road("84009") in page
assert 'class="plate">10<' in page and "Next buses" in page

# Favourites: both stops fetched, the 9-minute bus moves to "next up".
calls.clear()
page = client.get("/map?stops=84009,11119").get_data(as_text=True)
assert sorted(calls) == ["11119", "84009"], calls
assert 'data-mode="favourites"' in page and "2 saved stops" in page
assert 'class="plate">10<' in page and 'class="plate">20<' not in page
assert "After that:" in page and "<strong>20</strong> in 9 min" in page

# Nothing within the window: the list and the map pill both say what is next.
listing = client.get("/map-list?stops=11119").get_data(as_text=True)
assert "Nothing due in the next" in listing
assert 'data-status="Nothing in 5 min · next 20 in 9 min"' in listing

# ?stop= wins over ?stops=, and junk falls back to the start screen.
assert 'data-mode="stop"' in client.get("/map?stop=84009&stops=11119").get_data(as_text=True)
assert 'data-mode="start"' in client.get("/map?stop=nope&stops=x").get_data(as_text=True)

# The old dashboard is gone; the debug view takes one stop.
assert client.get("/arrivals").status_code == 404
assert client.get("/debug?stop=84009").status_code == 200

print("map views: all checks passed")
