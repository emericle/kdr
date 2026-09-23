"""Unit tests for the real-time web server module."""

import time
import pytest
import requests

from src.web_server import (
    market_buffer,
    decision_buffer,
    compute_recommendation,
    start_web_server_thread,
    stop_web_server,
    TradingDecision
)

TEST_PORT = 8099
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture(scope="module")
def live_server():
    server = start_web_server_thread(host="127.0.0.1", port=TEST_PORT)
    # Wait until server accepts HTTP connections
    start = time.time()
    while time.time() - start < 5.0:
        try:
            r = requests.get(f"{BASE_URL}/api/status", timeout=0.2)
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


def test_dashboard_html_endpoint(live_server):
    response = requests.get(f"{BASE_URL}/")
    assert response.status_code == 200
    assert "KDR Real-Time Market & Decision Monitor" in response.text


def test_api_status_endpoint(live_server):
    response = requests.get(f"{BASE_URL}/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "symbols_tracked" in data


def test_market_buffer_and_symbols_api(live_server):
    market_buffer.add_tick("AAPL", 175.50, 100)
    market_buffer.add_tick("AAPL", 176.00, 150)
    market_buffer.add_tick("TSLA", 250.00, 50)

    response = requests.get(f"{BASE_URL}/api/symbols")
    assert response.status_code == 200
    symbols = response.json()
    assert len(symbols) == 2

    aapl = next(s for s in symbols if s["symbol"] == "AAPL")
    assert aapl["currentPrice"] == 176.0
    assert aapl["volume"] == 250.0
    assert aapl["recommendation"] in ["BUY", "SELL", "HOLD"]


def test_symbol_detail_api(live_server):
    market_buffer.add_tick("MSFT", 400.0, 50)
    market_buffer.add_tick("MSFT", 405.0, 75)

    decision_buffer.add_decision(
        symbol="MSFT",
        action="BUY",
        confidence=0.88,
        reasoning=["Strong momentum", "Bellman optimal"]
    )

    response = requests.get(f"{BASE_URL}/api/symbols/MSFT")
    assert response.status_code == 200
    detail = response.json()

    assert detail["summary"]["symbol"] == "MSFT"
    assert detail["summary"]["currentPrice"] == 405.0
    assert len(detail["ticks"]) == 2
    assert detail["recommendation"]["action"] == "BUY"
    assert detail["recommendation"]["confidence"] == 0.88
    assert len(detail["decisions"]) == 1
    assert "moving_averages" in detail


def test_symbol_detail_with_duration_param(live_server):
    now_ts = time.time()
    # Old tick (older than 24h)
    market_buffer.add_tick("NVDA", 120.0, 10, timestamp=now_ts - 100000)
    # Recent tick (within 24h)
    market_buffer.add_tick("NVDA", 125.0, 20, timestamp=now_ts - 1800)

    # 24h default duration
    resp = requests.get(f"{BASE_URL}/api/symbols/NVDA?duration=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["ticks"]) == 1
    assert data["ticks"][0]["price"] == 125.0

    # 5d duration should include both ticks
    resp_5d = requests.get(f"{BASE_URL}/api/symbols/NVDA?duration=5d")
    assert resp_5d.status_code == 200
    data_5d = resp_5d.json()
    assert len(data_5d["ticks"]) == 2


def test_compute_recommendation_momentum():
    market_buffer.add_tick("UP_STOCK", 100.0, 10)
    market_buffer.add_tick("UP_STOCK", 105.0, 20)
    rec = compute_recommendation("UP_STOCK")
    assert rec["action"] == "BUY"
    assert rec["confidence"] > 0.65

    market_buffer.add_tick("DOWN_STOCK", 100.0, 10)
    market_buffer.add_tick("DOWN_STOCK", 95.0, 20)
    rec_down = compute_recommendation("DOWN_STOCK")
    assert rec_down["action"] == "SELL"

    market_buffer.add_tick("FLAT_STOCK", 100.0, 10)
    market_buffer.add_tick("FLAT_STOCK", 100.02, 20)
    rec_flat = compute_recommendation("FLAT_STOCK")
    assert rec_flat["action"] == "HOLD"
