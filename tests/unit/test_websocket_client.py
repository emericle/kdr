"""Automated client-side WebSocket tests.

Verifies that a real WebSocket client connecting to /ws:
1. Receives the initial 'init' payload.
2. The payload contains both 'market_indexes' and 'marketIndexes' as plain dictionaries.
3. The connection remains open and does not immediately disconnect.
4. Can exchange ping messages and receive broadcast updates.
"""

import asyncio
import json
import time
import pytest
import requests
import websockets

from src.web_server import (
    market_buffer,
    decision_buffer,
    start_web_server_thread,
    stop_web_server,
)

WS_TEST_PORT = 8098
HTTP_BASE = f"http://127.0.0.1:{WS_TEST_PORT}"
WS_BASE = f"ws://127.0.0.1:{WS_TEST_PORT}"


@pytest.fixture(scope="module")
def ws_server():
    """Start web server for WebSocket client integration tests."""
    server = start_web_server_thread(host="127.0.0.1", port=WS_TEST_PORT)
    start = time.time()
    while time.time() - start < 5.0:
        try:
            r = requests.get(f"{HTTP_BASE}/api/status", timeout=0.2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    yield server
    stop_web_server()


@pytest.fixture(autouse=True)
def clean_buffers():
    market_buffer._history.clear()
    market_buffer._open_prices.clear()
    decision_buffer._history.clear()
    yield


@pytest.mark.asyncio
async def test_websocket_client_init_and_no_immediate_disconnect(ws_server):
    """
    Connect as a real WebSocket client to /ws.
    Verify receipt of 'init' message with market_indexes and marketIndexes,
    send a ping, and assert that the connection does not immediately disconnect.
    """
    # Seed buffer with sample data
    market_buffer.add_tick("AAPL", 150.0, 10)
    market_buffer.add_tick("GOOG", 2800.0, 5)

    ws_url = f"{WS_BASE}/ws"

    async with websockets.connect(ws_url, ping_interval=10, ping_timeout=20) as ws:
        # 1. Assert connection is open
        assert not ws.closed

        # 2. Receive initial message
        init_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        init_data = json.loads(init_raw)

        # Assert format and type
        assert init_data.get("type") == "init"
        assert "symbols" in init_data
        assert "decisions" in init_data

        # Verify market_indexes and marketIndexes are present and plain dicts
        assert "market_indexes" in init_data
        assert "marketIndexes" in init_data
        assert isinstance(init_data["market_indexes"], list)
        assert isinstance(init_data["marketIndexes"], list)
        assert len(init_data["market_indexes"]) == 4

        expected_indices = {"VIX", "DJIA", "SP500", "RUSSELL2000"}
        received_indices = {item["index"] for item in init_data["market_indexes"]}
        assert expected_indices == received_indices

        for item in init_data["market_indexes"]:
            assert isinstance(item, dict)
            assert "index" in item
            assert "ticker" in item
            assert "currentPrice" in item
            assert "change" in item
            assert "changePercent" in item
            assert "priceType" in item

        # 3. Assert connection is still open after processing init message
        assert not ws.closed

        # 4. Bidirectional communication: Send a ping message from client
        await ws.send(json.dumps({"type": "ping", "timestamp": time.time()}))

        # 5. Wait for at least one broadcast update message (broadcast runs at 1s interval)
        update_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        update_data = json.loads(update_raw)

        assert update_data.get("type") == "update"
        assert "market_indexes" in update_data
        assert "marketIndexes" in update_data
        assert len(update_data["market_indexes"]) == 4

        # 6. Verify connection remains healthy and open over time
        await asyncio.sleep(1.5)
        assert not ws.closed


@pytest.mark.asyncio
async def test_websocket_updates_endpoint(ws_server):
    """Verify that the secondary /ws/updates endpoint works identically."""
    ws_url = f"{WS_BASE}/ws/updates"

    async with websockets.connect(ws_url, ping_interval=10, ping_timeout=20) as ws:
        assert not ws.closed

        init_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        init_data = json.loads(init_raw)

        assert init_data.get("type") == "init"
        assert "market_indexes" in init_data
        assert "marketIndexes" in init_data
        assert not ws.closed


def test_market_index_rest_endpoints(ws_server):
    """Verify REST endpoints for market indexes return plain dicts / JSON."""
    # Single index endpoint
    r_vix = requests.get(f"{HTTP_BASE}/api/market-index/VIX")
    assert r_vix.status_code == 200
    vix_data = r_vix.json()
    assert isinstance(vix_data, dict)
    assert vix_data["index"] == "VIX"
    assert "currentPrice" in vix_data

    # Bulk indexes endpoint
    r_all = requests.get(f"{HTTP_BASE}/api/market-indexes")
    assert r_all.status_code == 200
    all_data = r_all.json()
    assert isinstance(all_data, list)
    assert len(all_data) == 4
    assert {item["index"] for item in all_data} == {"VIX", "DJIA", "SP500", "RUSSELL2000"}


def test_test_websocket_html_endpoint(ws_server):
    """Verify that test-websocket.html is served."""
    resp = requests.get(f"{HTTP_BASE}/test-websocket")
    assert resp.status_code == 200
    assert "WebSocket Diagnostic Test" in resp.text
