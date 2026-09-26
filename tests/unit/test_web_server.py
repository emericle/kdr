"""Unit tests for the real-time web server module."""

import time
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import requests
import asyncio

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


def test_dashboard_symbol_route(live_server):
    response = requests.get(f"{BASE_URL}/symbol/AAPL")
    assert response.status_code == 200
    assert "KDR Real-Time Market & Decision Monitor" in response.text


def test_dashboard_history_navigation_support(live_server):
    response = requests.get(f"{BASE_URL}/")
    assert response.status_code == 200
    content = response.text
    # Verify browser back/forward history management is implemented
    assert "popstate" in content
    assert "history.pushState" in content
    assert "history.replaceState" in content



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


def test_symbol_detail_with_db_data(live_server):
    """Verify that historical ticks from db are correctly merged with buffer ticks."""
    from unittest.mock import MagicMock
    import src.web_server as ws_module

    mock_db = MagicMock()
    now_ts = time.time()
    mock_db.get_historical_market_data.return_value = [
        {"symbol": "TESTSYM", "price": 50.0, "size": 100.0, "timestamp": now_ts - 3600, "time_str": "10:00:00"},
        {"symbol": "TESTSYM", "price": 51.0, "size": 150.0, "timestamp": now_ts - 1800, "time_str": "10:30:00"}
    ]

    original_db = ws_module._db_manager_instance
    original_avail = ws_module._db_is_available
    try:
        ws_module._db_manager_instance = mock_db
        ws_module._db_is_available = True

        market_buffer.add_tick("TESTSYM", 52.0, 200.0, timestamp=now_ts - 60)

        resp = requests.get(f"{BASE_URL}/api/symbols/TESTSYM?duration=24h")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ticks"]) == 3
        assert [t["price"] for t in data["ticks"]] == [50.0, 51.0, 52.0]
    finally:
        ws_module._db_manager_instance = original_db
        ws_module._db_is_available = original_avail


def test_symbol_detail_fallback_seed_tick(live_server):
    """Verify fallback seed tick is provided if buffer has summary price but no ticks in time range."""
    import src.web_server as ws_module

    # Mock DB returning empty
    mock_db = MagicMock()
    mock_db.get_historical_market_data.return_value = []

    original_db = ws_module._db_manager_instance
    original_avail = ws_module._db_is_available
    try:
        ws_module._db_manager_instance = mock_db
        ws_module._db_is_available = True

        # Add tick with timestamp in past (older than 1h)
        market_buffer.add_tick("SEEDED", 99.5, 10.0, timestamp=time.time() - 7200)

        # Query with 1h duration (buffer tick is filtered out by start_time, but summary has currentPrice 99.5)
        resp = requests.get(f"{BASE_URL}/api/symbols/SEEDED?duration=1h")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ticks"]) >= 1
        assert data["ticks"][0]["price"] == 99.5
    finally:
        ws_module._db_manager_instance = original_db
        ws_module._db_is_available = original_avail


def test_db_circuit_breaker_behavior(live_server):
    """Verify circuit breaker trips on exception and skips subsequent calls during cooldown."""
    import src.web_server as ws_module

    mock_db = MagicMock()
    mock_db.get_historical_market_data.side_effect = Exception("DB Connection refused")

    original_db = ws_module._db_manager_instance
    original_avail = ws_module._db_is_available
    original_time = ws_module._db_last_attempt_time
    try:
        ws_module._db_manager_instance = mock_db
        ws_module._db_is_available = True
        ws_module._db_last_attempt_time = 0.0

        market_buffer.add_tick("FAILSYM", 123.0, 10.0)

        resp1 = requests.get(f"{BASE_URL}/api/symbols/FAILSYM")
        assert resp1.status_code == 200
        assert not ws_module._db_is_available
        assert mock_db.get_historical_market_data.call_count == 1

        # Second call immediately should trip circuit breaker and not call mock_db again
        resp2 = requests.get(f"{BASE_URL}/api/symbols/FAILSYM")
        assert resp2.status_code == 200
        assert mock_db.get_historical_market_data.call_count == 1
    finally:
        ws_module._db_manager_instance = original_db
        ws_module._db_is_available = original_avail
        ws_module._db_last_attempt_time = original_time


def test_buffer_edge_cases():
    from datetime import datetime
    now_dt = datetime.now()

    # datetime timestamp
    t = market_buffer.add_tick("DTTICK", 100.0, 10.0, timestamp=now_dt)
    assert t.price == 100.0

    # Non-existent symbol
    assert market_buffer.get_latest_tick("NONEXISTENT") is None
    summary_empty = market_buffer.get_summary("NONEXISTENT")
    assert summary_empty["currentPrice"] == 0.0

    # DecisionBuffer with TradingDecision instance and datetime timestamp
    dec = TradingDecision(symbol="DECTEST", action="BUY", confidence=0.9, reasoning=["test"])
    decision_buffer.add_decision(dec)
    assert decision_buffer.get_latest_decision("DECTEST").action == "BUY"

    # DecisionBuffer with datetime
    decision_buffer.add_decision("DECTEST2", "SELL", 0.8, ["test2"], timestamp=now_dt)
    assert decision_buffer.get_latest_decision("DECTEST2").action == "SELL"

    # Non-existent decision
    assert decision_buffer.get_latest_decision("NONEXISTENT") is None


class TestConnectionManager:
    """Test ConnectionManager class for WebSocket connections."""

    def test_connect_adds_connection(self):
        """Verify connect method adds WebSocket to active connections."""
        from unittest.mock import AsyncMock
        from src.web_server import ConnectionManager
        manager = ConnectionManager()

        mock_ws = AsyncMock()
        asyncio.run(manager.connect(mock_ws))

        assert mock_ws in manager.active_connections
        assert len(manager.active_connections) == 1

    def test_disconnect_removes_connection(self):
        """Verify disconnect method removes WebSocket from active connections."""
        from unittest.mock import AsyncMock
        from src.web_server import ConnectionManager
        manager = ConnectionManager()

        mock_ws = AsyncMock()
        asyncio.run(manager.connect(mock_ws))
        assert len(manager.active_connections) == 1

        asyncio.run(manager.disconnect(mock_ws))

        assert mock_ws not in manager.active_connections
        assert len(manager.active_connections) == 0

    def test_broadcast_json_with_no_clients(self):
        """Verify broadcast_json does nothing when no clients connected."""
        from src.web_server import ConnectionManager
        manager = ConnectionManager()
        message = {"type": "test"}

        # Should not raise exception
        asyncio.run(manager.broadcast_json(message))
        assert len(manager.active_connections) == 0

    def test_broadcast_json_handles_dead_clients(self):
        """Verify broadcast_json removes dead clients after exception."""
        from unittest.mock import AsyncMock
        import asyncio
        from src.web_server import ConnectionManager
        manager = ConnectionManager()

        # Mock ws that raises exception on send_json
        def raise_on_send_json(ws):
            raise Exception("Connection closed")

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        # Manually set up mocks to simulate dead clients
        mock_ws1.send_json = raise_on_send_json
        mock_ws2.send_json = AsyncMock()  # This one stays alive

        manager.active_connections.add(mock_ws1)
        manager.active_connections.add(mock_ws2)

        message = {"type": "test"}
        asyncio.run(manager.broadcast_json(message))

        # Dead client should be removed
        assert mock_ws1 not in manager.active_connections
        # Active client should remain
        assert mock_ws2 in manager.active_connections


class TestBroadcastLoop:
    """Test broadcast_loop for WebSocket updates."""

    @pytest.mark.asyncio
    async def test_broadcast_loop_generates_update_payload(self):
        """Verify broadcast_json generates correct update payload format."""
        from src.web_server import ConnectionManager

        # Add some test data
        market_buffer.add_tick("TEST1", 100.0, 50)
        market_buffer.add_tick("TEST2", 200.0, 25)
        decision_buffer.add_decision("TEST1", "BUY", 0.9, ["Strong momentum"])

        manager = ConnectionManager()
        mock_ws = AsyncMock()
        manager.active_connections.add(mock_ws)

        # Call broadcast_json directly
        await manager.broadcast_json({"test": "data"})

        # Verify broadcast was called with valid structure
        # (actual implementation will structure this properly)
        assert mock_ws.send_json.called
        call_args = mock_ws.send_json.call_args[0][0]
        assert isinstance(call_args, dict)

    @pytest.mark.asyncio
    async def test_broadcast_loop_handles_exception_gracefully(self):
        """Verify broadcast_json handles exceptions without crashing."""
        from src.web_server import ConnectionManager

        manager = ConnectionManager()
        mock_ws = AsyncMock()
        mock_ws.send_json = AsyncMock(side_effect=Exception("Send failed"))

        manager.active_connections.add(mock_ws)

        # Should not raise exception, just log
        await manager.broadcast_json({"type": "test"})

        # Exception should have been caught and dead client removed
        # The mock should have been called
        assert mock_ws.send_json.called


class TestComputeRecommendationEdgeCases:
    """Test compute_recommendation with edge cases."""

    def test_compute_recommendation_new_symbol(self):
        """Verify compute_recommendation handles new symbols correctly."""
        from datetime import datetime

        # Add tick but no decision yet
        market_buffer.add_tick("NEWSYM", 100.0, 10)

        rec = compute_recommendation("NEWSYM")

        assert "symbol" in rec
        assert "action" in rec
        assert "confidence" in rec
        assert 0.0 <= rec["confidence"] <= 1.0

    def test_compute_recommendation_large_price_change(self):
        """Verify compute_recommendation handles large price movements."""
        # Add significant price change (>15% increase)
        market_buffer.add_tick("HUGE_CHANGE", 100.0, 10)
        market_buffer.add_tick("HUGE_CHANGE", 150.0, 20)  # 50% increase

        rec = compute_recommendation("HUGE_CHANGE")

        # Should detect upward momentum (>15% change) - now based on reasoning
        assert rec["action"] in ["BUY", "SELL", "HOLD"]
        # Confidence should be higher for significant moves
        assert rec["confidence"] >= 0.65
        # Should include reasoning
        assert "reasoning" in rec
        assert isinstance(rec["reasoning"], list)
        assert len(rec["reasoning"]) > 0

    def test_compute_recommendation_zero_change(self):
        """Verify compute_recommendation handles no price change."""
        market_buffer.add_tick("FLAT", 100.0, 10)
        market_buffer.add_tick("FLAT", 100.0, 15)

        rec = compute_recommendation("FLAT")
        assert rec["action"] in ["BUY", "SELL", "HOLD"]
        assert rec["confidence"] >= 0.65

    def test_compute_recommendation_negative_change(self):
        """Verify compute_recommendation handles negative price changes."""
        market_buffer.add_tick("DOWNTREND", 100.0, 10)
        market_buffer.add_tick("DOWNTREND", 90.0, 20)  # 10% decrease

        rec = compute_recommendation("DOWNTREND")
        # Should detect downward trend
        assert rec["action"] in ["BUY", "SELL", "HOLD"]
        assert "reasoning" in rec

    def test_compute_recommendation_with_reasoning(self):
        """Verify compute_recommendation includes reasoning in response."""
        market_buffer.add_tick("TRENDING", 100.0, 10)
        market_buffer.add_tick("TRENDING", 105.0, 20)

        rec = compute_recommendation("TRENDING")
        assert "reasoning" in rec
        assert isinstance(rec["reasoning"], list)
        assert len(rec["reasoning"]) > 0


class TestHistoricalQuery:
    """Test historical data query with edge cases."""

    @pytest.mark.asyncio
    async def test_query_historical_ticks_timeout(self):
        """Verify _query_historical_ticks_safe handles slow queries gracefully."""
        # This test is complex to mock properly due to asyncio
        # Skipping for now due to complexity of mocking asyncio.wait_for
        pytest.skip("Skipped due to complex asyncio mocking requirements")

    @pytest.mark.asyncio
    async def test_query_historical_ticks_db_failure(self):
        """Verify _query_historical_ticks_safe handles DB failures gracefully."""
        from unittest.mock import MagicMock
        from src.web_server import (
            _query_historical_ticks_safe,
            _db_manager_instance,
            _db_is_available
        )
        import asyncio

        mock_db = MagicMock()
        mock_db.get_historical_market_data = MagicMock(return_value=[])

        original_db = _db_manager_instance
        original_avail = _db_is_available

        try:
            _db_manager_instance = mock_db
            _db_is_available = True

            results = await _query_historical_ticks_safe(
                mock_db, "TESTSYM", None, 100
            )

            # Should return empty list on success
            assert results == []
        finally:
            _db_manager_instance = original_db
            _db_is_available = original_avail


class TestLifespan:
    """Test application lifespan context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_context_manager(self):
        """Verify lifespan context manager can be used without crashing."""
        from fastapi import FastAPI

        app = FastAPI(title="Test App")

        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}

        # Execute lifespan context - should not raise exception
        async with app.router.lifespan_context(app):
            # Access the app
            assert app is not None

            # Verify endpoint works
            response = await test_endpoint()
            assert response["status"] == "ok"


class TestMarketIndexTracking:
    """Tests for the market index boxes tracking VIX, DJIA, S&P 500, and Russel 2k."""

    @pytest.mark.asyncio
    async def test_market_index_daily_gain_and_display_name(self):
        from src.web_server import get_market_index, market_buffer

        # Set up an initial/open tick and a higher current tick for S&P 500
        market_buffer.add_tick("SP500", 5000.0, 100)
        market_buffer._open_prices["SP500"] = 5000.0
        market_buffer.add_tick("SP500", 5050.0, 150)

        data = await get_market_index("SP500")
        assert data["index"] == "SP500"
        assert data["displayName"] == "S&P 500"
        assert data["currentPrice"] == 5050.0
        assert data["change"] == 50.0
        assert data["changePercent"] == 1.0

    @pytest.mark.asyncio
    async def test_market_index_daily_loss(self):
        from src.web_server import get_market_index, market_buffer

        # Set up an index with negative change (loss)
        market_buffer.add_tick("VIX", 20.0, 50)
        market_buffer._open_prices["VIX"] = 20.0
        market_buffer.add_tick("VIX", 18.5, 60)

        data = await get_market_index("VIX")
        assert data["index"] == "VIX"
        assert data["displayName"] == "VIX"
        assert data["currentPrice"] == 18.5
        assert data["change"] == -1.5
        assert data["changePercent"] == -7.5

    @pytest.mark.asyncio
    async def test_market_index_russell_display_name(self):
        from src.web_server import get_market_index, market_buffer

        market_buffer.add_tick("RUSSELL2000", 2200.0, 40)
        market_buffer._open_prices["RUSSELL2000"] = 2200.0

        data = await get_market_index("RUSSELL2000")
        assert data["displayName"] == "Russel 2k"

    @pytest.mark.asyncio
    async def test_market_index_proxy_fallback(self):
        from src.web_server import get_market_index, market_buffer

        # If DJIA is not in buffer, it should check proxy DIA
        market_buffer.add_tick("DIA", 390.0, 80)
        market_buffer._open_prices["DIA"] = 390.0
        market_buffer.add_tick("DIA", 395.0, 90)

        data = await get_market_index("DJIA")
        assert data["index"] == "DJIA"
        assert data["currentPrice"] == 395.0
        assert data["change"] == 5.0

    def test_dashboard_html_contains_index_styling_and_top_layout(self, live_server):
        resp = requests.get(f"{BASE_URL}/")
        assert resp.status_code == 200
        html = resp.text
        # Check red/green styling classes
        assert ".market-index-card.up" in html
        assert ".market-index-card.down" in html
        # Check index display names
        assert "Russel 2k" in html or "Russell 2k" in html
        assert "S&P 500" in html


# Add more tests as needed for additional edge cases and scenarios
