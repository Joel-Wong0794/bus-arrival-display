"""Self-check for the upstream calls themselves. No network: the real request
code runs on a client whose transport answers from canned replies.

Run: python scripts/test_upstream.py
"""

import asyncio
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("API_KEY", "test")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
import index  # noqa: E402
import httpx2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 29, 8, 0, tzinfo=SGT)
ARRIVALS = {
    "Services": [{
        "ServiceNo": "10",
        "NextBus": {"EstimatedArrival": (NOW + timedelta(minutes=5)).isoformat(), "Monitored": 0},
    }]
}
FEED_URL = "https://feed.test/feed"
ARRIVAL_URL = index.BUS_ARRIVAL_URL  # kept: the redirect check swaps the module's copy
# Codes the baked stops file doesn't have, so each one's behaviour is ours to set.
OK_CODE, BROKEN_CODE, NOT_JSON_CODE, UNKNOWN_CODE, WRONG_ROW_CODE = (
    "84009", "99999", "88888", "77777", "66666",
)
for code in (BROKEN_CODE, NOT_JSON_CODE, UNKNOWN_CODE, WRONG_ROW_CODE):
    assert code not in index.BUS_STOPS, code

seen = []  # every request the transport answered
delay = {"seconds": 0.0}
feed_reply = {"make": lambda request: httpx2.Response(200, json={"data": {"v": 1}})}


async def handler(request: httpx2.Request) -> httpx2.Response:
    seen.append(request)
    await asyncio.sleep(delay["seconds"])
    path, params = request.url.path, request.url.params
    if path == "/moved":
        return httpx2.Response(301, headers={"location": ARRIVAL_URL + "?BusStopCode=84009"})
    if path.endswith("/BusArrival"):
        code = params.get("BusStopCode")
        if code == BROKEN_CODE:
            return httpx2.Response(500)
        if code == NOT_JSON_CODE:
            return httpx2.Response(200, content=b"<html>Service Unavailable</html>")
        return httpx2.Response(200, json=ARRIVALS)
    if path.endswith("/BusStops"):
        code = params.get("BusStopCode")
        # An unsupported filter answers with some other stop: must not be used.
        row_code = "00001" if code == WRONG_ROW_CODE else code
        return httpx2.Response(200, json={"value": [{"BusStopCode": row_code, "Latitude": 1.35, "Longitude": 103.85}]})
    if request.url.host == "feed.test":
        return feed_reply["make"](request)
    return httpx2.Response(404)


index.http_client = index.new_client(transport=httpx2.MockTransport(handler))


def calls_to(suffix: str) -> int:
    return sum(1 for r in seen if r.url.path.endswith(suffix))


async def checks() -> None:
    # Arrivals: the key and stop code go upstream, the reply is parsed.
    seen.clear()
    services = await index.get_bus_arrival(index.API_KEY, OK_CODE, NOW)
    assert seen[0].headers["AccountKey"] == index.API_KEY, seen[0].headers
    assert seen[0].url.params["BusStopCode"] == OK_CODE
    assert [s for s, _ in services] == ["10"] and len(services[0][1]) == 1, services

    # Stops are fetched side by side: three 0.3 s calls in well under 0.9 s.
    delay["seconds"] = 0.3
    started = time.perf_counter()
    await index.fetch_stops(NOW, [(OK_CODE, "A"), (OK_CODE, "B"), (OK_CODE, "C")])
    elapsed = time.perf_counter() - started
    delay["seconds"] = 0.0
    assert elapsed < 0.6, elapsed

    # One stop failing, or answering with something that isn't JSON, marks
    # only that stop; every stop comes back, in the order asked for.
    rows = await index.fetch_stops(
        NOW, [(OK_CODE, "A"), (BROKEN_CODE, "B"), (NOT_JSON_CODE, "C")]
    )
    assert [r["code"] for r in rows] == [OK_CODE, BROKEN_CODE, NOT_JSON_CODE], rows
    assert [r["error"] for r in rows] == [None, "Unavailable", "Unavailable"], rows
    assert rows[0]["services"] and rows[1]["services"] == [] == rows[2]["services"], rows

    # A moved endpoint is followed, as requests did, rather than failing.
    index.BUS_ARRIVAL_URL = "https://datamall2.mytransport.sg/moved"
    try:
        services = await index.get_bus_arrival(index.API_KEY, OK_CODE, NOW)
    finally:
        index.BUS_ARRIVAL_URL = ARRIVAL_URL
    assert [s for s, _ in services] == ["10"], services

    # Feeds: cached for the TTL, and the last good copy outlives an outage.
    index._feed_cache.clear()
    seen.clear()
    assert await index.fetch_feed(FEED_URL, NOW, 60) == {"v": 1}
    feed_reply["make"] = lambda request: httpx2.Response(500)
    assert await index.fetch_feed(FEED_URL, NOW + timedelta(seconds=30), 60) == {"v": 1}
    assert len(seen) == 1, "inside the TTL, nothing is fetched"
    assert await index.fetch_feed(FEED_URL, NOW + timedelta(seconds=61), 60) == {"v": 1}

    def time_out(request):
        raise httpx2.ConnectTimeout("too slow", request=request)

    feed_reply["make"] = time_out
    assert await index.fetch_feed(FEED_URL, NOW + timedelta(seconds=122), 60) == {"v": 1}
    index._feed_cache.clear()
    assert await index.fetch_feed(FEED_URL, NOW, 60) is None, "no copy yet: None"

    # Position fallback: asked once for a stop the baked file lacks.
    seen.clear()
    await index.ensure_positions([UNKNOWN_CODE, OK_CODE])
    assert index.stop_position(UNKNOWN_CODE) == (1.35, 103.85)
    assert calls_to("/BusStops") == 1, "a baked stop is never asked about"
    await index.ensure_positions([UNKNOWN_CODE])
    assert calls_to("/BusStops") == 1, "a known answer is not asked again"
    await index.ensure_positions([WRONG_ROW_CODE])
    assert index.stop_position(WRONG_ROW_CODE) is None


asyncio.run(checks())

# Through the routes: an answer that isn't JSON leaves the page standing.
client = TestClient(index.app)  # no `with`: lifespan stays off, the fake client stays
assert client.get(f"/map-list?stop={NOT_JSON_CODE}").status_code == 200
debug = client.get(f"/debug?stop={NOT_JSON_CODE}")
assert debug.status_code == 200 and "Unavailable" in debug.text

async def connection_cap_check() -> None:
    """A burst of calls never has more than UPSTREAM_MAX_CONNECTIONS open at
    once: the rest wait their turn. Real sockets on 127.0.0.1, since the mock
    transport has no connection pool to limit."""
    open_now, peak = 0, 0

    async def serve(reader, writer):
        nonlocal open_now, peak
        open_now += 1
        peak = max(peak, open_now)
        try:
            while await reader.readuntil(b"\r\n\r\n"):
                await asyncio.sleep(0.05)  # slow enough for calls to pile up
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            open_now -= 1
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    calls = index.UPSTREAM_MAX_CONNECTIONS * 3
    async with index.new_client() as capped:
        replies = await asyncio.gather(
            *(capped.get(f"http://127.0.0.1:{port}/", timeout=10) for _ in range(calls))
        )
    server.close()
    await server.wait_closed()
    assert all(r.status_code == 200 for r in replies), "queued calls still complete"
    assert peak <= index.UPSTREAM_MAX_CONNECTIONS, peak


asyncio.run(connection_cap_check())

# Last, because it replaces the fake client: startup opens one shared client
# and shutdown closes it.
with TestClient(index.app):
    opened = index.http_client
    assert isinstance(opened, httpx2.AsyncClient) and not opened.is_closed
assert opened.is_closed

print("upstream: all checks passed")
