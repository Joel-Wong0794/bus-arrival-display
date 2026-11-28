"""Self-check for the local weather in the /map header. Run: python scripts/test_local_weather.py

No network: fetch_feed is swapped for canned copies of NEA's three feeds, shaped
like the live ones (two areas, a few stations). The module needs API_KEY and
BUS_STOP_CODES only to import, so dummies are set first.
"""

import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("API_KEY", "test")
os.environ.setdefault("BUS_STOP_CODES", "00001")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 24, 11, 0, tzinfo=SGT)
NORTH = (1.43, 103.80)  # near "Woodlands" and station N1
EAST = (1.33, 103.93)  # near "Bedok" and stations E1/E2

DAY = {
    "records": [{
        "general": {"temperature": {"low": 25, "high": 34}, "forecast": {"text": "Partly Cloudy"}},
        "periods": [{
            "timePeriod": {"start": "2026-11-24T06:00:00+08:00", "end": "2026-11-24T18:00:00+08:00", "text": "6am to 6pm"},
            "regions": {"west": {"text": "Showers"}},
        }],
    }]
}
NOWCAST = {
    "area_metadata": [
        {"name": "Woodlands", "label_location": {"latitude": 1.432, "longitude": 103.786}},
        {"name": "Bedok", "label_location": {"latitude": 1.321, "longitude": 103.924}},
        {"name": "Broken"},  # malformed entry: skipped, not fatal
    ],
    "items": [{
        "valid_period": {"start": "2026-11-24T10:30:00+08:00", "end": "2026-11-24T12:30:00+08:00", "text": "10.30 am to 12.30 pm"},
        "forecasts": [{"area": "Woodlands", "forecast": "Thundery Showers"}, {"area": "Bedok", "forecast": "Fair (Day)"}],
    }],
}
TEMPS = {
    "stations": [
        {"id": "N1", "location": {"latitude": 1.43, "longitude": 103.79}},
        {"id": "E1", "location": {"latitude": 1.33, "longitude": 103.94}},
        {"id": "E2", "location": {"latitude": 1.331, "longitude": 103.931}},  # nearest to EAST but silent
    ],
    "readings": [{"data": [{"stationId": "N1", "value": 27.6}, {"stationId": "E1", "value": 31.2}]}],
}
feeds = {index.WEATHER_URL: DAY, index.NOWCAST_URL: NOWCAST, index.TEMPERATURE_URL: TEMPS}
async def fake_feed(url, now, ttl):
    return feeds.get(url)


index.fetch_feed = fake_feed

# Each place gets its own area's forecast and its nearest station's reading.
north = asyncio.run(index.read_weather(NOW, NORTH))
assert (north["text"], north["area"], north["now"]) == ("Thundery Showers", "Woodlands", 28), north
assert north["icon"] == "thunderstorms-rain" and (north["low"], north["high"]) == (25, 34), north
east = asyncio.run(index.read_weather(NOW, EAST))
# E2 is closer but sent no reading, so E1 answers rather than nothing.
assert (east["text"], east["area"], east["now"]) == ("Fair", "Bedok", 31), east
# "(Day)" leaves the words but still picks the sun icon.
assert east["icon"] == "clear-day", east

# No place (a stop without coordinates): the 24-hour regional forecast, as before.
regional = asyncio.run(index.read_weather(NOW))
assert (regional["text"], regional["area"], regional["now"]) == ("Showers", None, None), regional
assert regional["period"] == "6am to 6pm", regional

# An expired 2-hour forecast must not linger: fall back to regional, keep the live temperature.
stale = asyncio.run(index.read_weather(NOW + timedelta(hours=2), EAST))
assert (stale["text"], stale["area"], stale["now"]) == ("Showers", None, 31), stale

# 2-hour and temperature feeds down: regional, no crash.
feeds[index.NOWCAST_URL] = feeds[index.TEMPERATURE_URL] = None
down = asyncio.run(index.read_weather(NOW, EAST))
assert (down["text"], down["area"], down["now"]) == ("Showers", None, None), down

# Everything down: None, and the template shows the plain title.
feeds[index.WEATHER_URL] = None
assert asyncio.run(index.read_weather(NOW, EAST)) is None

print("local weather: all checks passed")
