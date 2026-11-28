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
from fastapi.testclient import TestClient  # noqa: E402

# ?stops= parsing: valid five-digit codes, no repeats, in order, capped.
assert index.valid_stop_codes("84009, 11119,84009,abc,1234,") == ["84009", "11119"]
assert index.valid_stop_codes(None) == []
codes = ",".join(str(10000 + i) for i in range(20))
assert len(index.valid_stop_codes(codes)) == index.MAX_FAVOURITES

calls = []


async def fake_arrival(api_key, code, now):
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


async def no_weather(now, place=None):
    return None


async def fair_weather(now, place=None):
    return {
        "text": "Fair", "icon": "clear-day", "area": None, "now": None, "low": 25,
        "high": 34, "region": "west", "period": "", "outlook": "",
    }


index.get_bus_arrival = fake_arrival
index.read_weather = no_weather
client = TestClient(index.app)

# Start screen: no stop asked for, so no DataMall call and no bus list poll.
calls.clear()
page = client.get("/").text
assert calls == [], calls
assert 'data-mode="start"' in page and 'id="start"' in page
assert "hx-get=\"/map-list" not in page
assert client.get("/map").status_code == 200

# Same-name stops are told apart by road, in search results and the header.
hits = [h for h in index.search_stops("blk 402") if h["name"] == "Blk 402"]
assert len(hits) >= 2 and all(h["road"] for h in hits), hits
assert len({h["road"] for h in hits}) == len(hits), hits
results = client.get("/stops?q=blk%20402").text
assert results.count('class="stop-result-road"') >= 2
assert index.search_stops("bt batok rd"), "a road name should find its stops"

# One stop: everything running there.
page = client.get("/map?stop=84009").text
assert 'data-mode="stop"' in page and 'id="star"' in page
assert 'class="eyebrow-road"> · ' + index.stop_road("84009") in page
assert 'class="plate">10<' in page and "Next buses" in page

# Asset URLs stay relative, exactly as Flask wrote them: an absolute http://
# URL would be blocked as mixed content on the https site.
assert 'src="/static/glide.js"' in page and 'href="/static/map.css"' in page
# The 20 s poll asks for the same view the page shows.
assert 'hx-get="/map-list?stop=84009"' in page

# The weather icon is a static file too, in a subfolder.
index.read_weather = fair_weather
assert 'src="/static/weather/clear-day.svg"' in client.get("/map?stop=84009").text
index.read_weather = no_weather

# Static files: same paths, subfolders included, nothing outside static/.
assert client.get("/static/glide.js").status_code == 200
assert client.get("/static/weather/clear-day.svg").status_code == 200
# Encoded on purpose: the test client would tidy a literal "/static/../" into
# "/api/..." before sending, so the static handler would never see it.
assert client.get("/static/%2e%2e/api/index.py").status_code == 404

# Autoescaping is still on: the search box echoes what was typed.
echoed = client.get("/stops?q=%3Cb%3Ezz").text
assert "&lt;b&gt;zz" in echoed and "<b>zz" not in echoed

# Favourites: both stops fetched, the 9-minute bus moves to "next up".
calls.clear()
page = client.get("/map?stops=84009,11119").text
assert sorted(calls) == ["11119", "84009"], calls
assert 'data-mode="favourites"' in page and "2 saved stops" in page
assert 'class="plate">10<' in page and 'class="plate">20<' not in page
assert "After that:" in page and "<strong>20</strong> in 9 min" in page

# Nothing within the window: the list and the map pill both say what is next.
listing = client.get("/map-list?stops=11119").text
assert "Nothing due in the next" in listing
assert 'data-status="Nothing in 5 min · next 20 in 9 min"' in listing

# ?stop= wins over ?stops=, and junk falls back to the start screen.
assert 'data-mode="stop"' in client.get("/map?stop=84009&stops=11119").text
assert 'data-mode="start"' in client.get("/map?stop=nope&stops=x").text

# The old dashboard is gone; the debug view takes one stop.
assert client.get("/arrivals").status_code == 404
assert client.get("/debug?stop=84009").status_code == 200

print("map views: all checks passed")
