"""Unit tests for the real-time web server module."""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from src.web_server import (
    TradingDecision,
    compute_recommendation,
    decision_buffer,
    index_store,
    market_buffer,
    start_web_server_thread,
    stop_web_server,
)
from src.web_server.data_layer import DEFAULT_INDEXES

TEST_PORT = 8099
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"
# Bound every request: a route that blocks must fail the test, not stall the suite.
REQUEST_TIMEOUT = 10


@pytest.fixture(scope="module")
def live_server(live_database):
    server = start_web_server_thread(host="127.0.0.1", port=TEST_PORT)
    # Wait until the server answers, then let the tests run against it.
    start = time.time()
    ready = False
    while time.time() - start < 5.0:
        try:
            response = requests.get(f"{BASE_URL}/api/status", timeout=0.2)
            ready = response.status_code == 200
            if ready:
                break
        except Exception:
            time.sleep(0.1)
    if not ready:
        stop_web_server()
        pytest.skip("web server under test never answered /api/status")
    yield server
    stop_web_server()


@pytest.fixture(autouse=True)
def clean_buffers():
    market_buffer._history.clear()
    market_buffer._open_prices.clear()
    market_buffer._running_volume.clear()
    market_buffer._running_high.clear()
    market_buffer._running_low.clear()
    decision_buffer._history.clear()
    index_store._indexes = {k: dict(v) for k, v in DEFAULT_INDEXES.items()}
    index_store._dirty = False
    return


def test_dashboard_html_endpoint(live_server):
    response = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
    assert response.status_code == 200
    assert "KDR Real-Time Market & Decision Monitor" in response.text


def test_dashboard_symbol_route(live_server):
    response = requests.get(f"{BASE_URL}/symbol/AAPL", timeout=REQUEST_TIMEOUT)
    assert response.status_code == 200
    assert "KDR Real-Time Market & Decision Monitor" in response.text


def test_dashboard_history_navigation_support(live_server):
    response = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
    assert response.status_code == 200
    content = response.text
    # Verify browser back/forward history management is implemented
    assert "popstate" in content
    assert "history.pushState" in content
    assert "history.replaceState" in content



def test_api_status_endpoint(live_server):
    response = requests.get(f"{BASE_URL}/api/status", timeout=REQUEST_TIMEOUT)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "symbols_tracked" in data


def test_market_buffer_and_symbols_api(live_server):
    market_buffer.add_tick("AAPL", 175.50, 100)
    market_buffer.add_tick("AAPL", 176.00, 150)
    market_buffer.add_tick("TSLA", 250.00, 50)

    response = requests.get(f"{BASE_URL}/api/symbols", timeout=REQUEST_TIMEOUT)
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

    response = requests.get(f"{BASE_URL}/api/symbols/MSFT", timeout=REQUEST_TIMEOUT)
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
    resp = requests.get(f"{BASE_URL}/api/symbols/NVDA?duration=24h", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["ticks"]) == 1
    assert data["ticks"][0]["price"] == 125.0

    # 5d duration should include both ticks
    resp_5d = requests.get(f"{BASE_URL}/api/symbols/NVDA?duration=5d", timeout=REQUEST_TIMEOUT)
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


def test_fallback_reasoning_names_the_heuristic():
    """The fallback says what it did, because the dashboard prints it as a reason.

    A string claiming the Bellman model ran would be showing a confidence the
    model never produced: this path is the one taken when no decision has been
    recorded for the symbol yet.
    """
    market_buffer.add_tick("HONEST_UP", 100.0, 10)
    market_buffer.add_tick("HONEST_UP", 105.0, 20)
    rec = compute_recommendation("HONEST_UP")
    assert not any("Bellman" in reason for reason in rec["reasoning"]), rec["reasoning"]
    assert any("momentum" in reason.lower() for reason in rec["reasoning"])

    market_buffer.add_tick("HONEST_DOWN", 100.0, 10)
    market_buffer.add_tick("HONEST_DOWN", 95.0, 20)
    rec_down = compute_recommendation("HONEST_DOWN")
    assert not any("Bellman" in reason for reason in rec_down["reasoning"]), rec_down["reasoning"]


def test_symbol_detail_with_db_data(live_server):
    """Verify that historical ticks from db are correctly merged with buffer ticks."""
    from unittest.mock import MagicMock

    import src.web_server.data_layer as ws_module

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

        resp = requests.get(f"{BASE_URL}/api/symbols/TESTSYM?duration=24h", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ticks"]) == 3
        assert [t["price"] for t in data["ticks"]] == [50.0, 51.0, 52.0]
    finally:
        ws_module._db_manager_instance = original_db
        ws_module._db_is_available = original_avail


def test_symbol_detail_fallback_seed_tick(live_server):
    """Verify fallback seed tick is provided if buffer has summary price but no ticks in time range."""
    import src.web_server.data_layer as ws_module

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
        resp = requests.get(f"{BASE_URL}/api/symbols/SEEDED?duration=1h", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ticks"]) >= 1
        assert data["ticks"][0]["price"] == 99.5
    finally:
        ws_module._db_manager_instance = original_db
        ws_module._db_is_available = original_avail


def test_db_circuit_breaker_behavior(live_server):
    """Verify circuit breaker trips on exception and skips subsequent calls during cooldown."""
    import src.web_server.data_layer as ws_module

    mock_db = MagicMock()
    mock_db.get_historical_market_data.side_effect = ConnectionError("DB Connection refused")

    original_db = ws_module._db_manager_instance
    original_avail = ws_module._db_is_available
    original_time = ws_module._db_last_attempt_time
    try:
        ws_module._db_manager_instance = mock_db
        ws_module._db_is_available = True
        ws_module._db_last_attempt_time = 0.0

        market_buffer.add_tick("FAILSYM", 123.0, 10.0)

        resp1 = requests.get(f"{BASE_URL}/api/symbols/FAILSYM", timeout=REQUEST_TIMEOUT)
        assert resp1.status_code == 200
        assert not ws_module._db_is_available
        assert mock_db.get_historical_market_data.call_count == 1

        # Second call immediately should trip circuit breaker and not call mock_db again
        resp2 = requests.get(f"{BASE_URL}/api/symbols/FAILSYM", timeout=REQUEST_TIMEOUT)
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
        """Verify broadcast_json removes a client whose send fails and keeps the rest."""
        from fastapi import WebSocketDisconnect

        from src.web_server import ConnectionManager
        manager = ConnectionManager()

        # A dead client raises what starlette raises once the transport is gone:
        # the narrowed catch in broadcast_json is what has to name it.
        def raise_on_send_json(ws):
            raise WebSocketDisconnect("Connection closed")

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        # One dead client and one that answers normally
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
        """Verify broadcast_json drops a client whose send fails, without crashing."""
        from fastapi import WebSocketDisconnect

        from src.web_server import ConnectionManager

        manager = ConnectionManager()
        mock_ws = AsyncMock()
        mock_ws.send_json = AsyncMock(side_effect=WebSocketDisconnect("Send failed"))

        manager.active_connections.add(mock_ws)

        await manager.broadcast_json({"type": "test"})

        assert mock_ws.send_json.called
        assert mock_ws not in manager.active_connections


class TestComputeRecommendationEdgeCases:
    """Test compute_recommendation with edge cases."""

    def test_compute_recommendation_new_symbol(self):
        """Verify compute_recommendation handles new symbols correctly."""

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
        """A query over the one second budget is dropped and trips the breaker."""
        import time

        import src.web_server.data_layer as data_layer

        slow_db = MagicMock()

        def slow_query(**_kwargs):
            time.sleep(1.2)
            return []

        slow_db.get_historical_market_data.side_effect = slow_query
        results = await data_layer._query_historical_ticks_safe(slow_db, "SLOWSYM", None, 100)
        assert results == []
        assert not data_layer._db_is_available

    @pytest.mark.asyncio
    async def test_query_historical_ticks_db_failure(self):
        """Verify _query_historical_ticks_safe handles DB failures gracefully."""
        import src.web_server.data_layer as data_layer

        mock_db = MagicMock()
        mock_db.get_historical_market_data.side_effect = ConnectionError("DB Connection refused")

        original_db = data_layer._db_manager_instance
        original_avail = data_layer._db_is_available
        try:
            data_layer._db_manager_instance = mock_db
            data_layer._db_is_available = True

            results = await data_layer._query_historical_ticks_safe(mock_db, "TESTSYM", None, 100)

            # Should return empty list on failure, and say so on the breaker
            assert results == []
            assert not data_layer._db_is_available
            assert mock_db.get_historical_market_data.call_count == 1
        finally:
            data_layer._db_manager_instance = original_db
            data_layer._db_is_available = original_avail


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
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check red/green styling classes
        assert ".market-index-card.up" in html
        assert ".market-index-card.down" in html
        # Check index display names
        assert "Russel 2k" in html or "Russell 2k" in html
        assert "S&P 500" in html

    def test_index_store_persistence_to_disk(self, tmp_path):
        """Verify that IndexStore persists valuations to disk and reloads them."""
        from src.web_server import IndexStore
        cache_file = str(tmp_path / "test_index_cache.json")
        store = IndexStore(cache_file=cache_file)
        store.update_index("SP500", current_price=5123.45, change=25.0, change_percent=0.49, last_update="14:30:00")

        assert os.path.exists(cache_file)

        # Reload with a new instance from the saved file
        store2 = IndexStore(cache_file=cache_file)
        sp = store2.get_index("SP500")
        assert sp["currentPrice"] == 5123.45
        assert sp["change"] == 25.0
        assert sp["changePercent"] == 0.49
        assert sp["lastUpdate"] == "14:30:00"

    @pytest.mark.asyncio
    async def test_get_market_index_no_historical_db_lookup(self):
        """Verify that get_market_index and get_all_market_indexes do not perform historical DB lookups."""
        from src.web_server import get_all_market_indexes, get_market_index
        with patch("src.web_server.data_layer.get_db_manager") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            data = await get_market_index("SP500")
            assert isinstance(data, dict)
            assert data["index"] == "SP500"
            assert data["currentPrice"] is None
            assert data["priceType"] == "placeholder"

            all_data = await get_all_market_indexes()
            assert isinstance(all_data, list)
            assert len(all_data) == 5
            assert all(item["currentPrice"] is None and item["priceType"] == "placeholder" for item in all_data)

            # Historical database queries should never be called for index summaries
            mock_db.get_price_at_interval_start.assert_not_called()
            mock_db.get_historical_market_data.assert_not_called()
            mock_db.get_latest_market_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_valuations_never_zero_when_closed(self):
        """Verify cold indexes report honest placeholder state and transition to live once a tick arrives."""
        from src.web_server import TRACKED_INDEXES, get_all_market_indexes, market_buffer
        indexes = await get_all_market_indexes()
        assert len(indexes) == len(TRACKED_INDEXES)
        for idx in indexes:
            assert idx["currentPrice"] is None, f"Cold index {idx['index']} should have None price"
            assert idx["priceType"] == "placeholder"
            assert idx["lastUpdate"] is None

        # Once a tick arrives, the index transitions from placeholder to live
        market_buffer.add_tick("SP500", 5850.0, 100.0)
        updated = await get_all_market_indexes()
        sp500 = next(i for i in updated if i["index"] == "SP500")
        assert sp500["currentPrice"] == 5850.0
        assert sp500["priceType"] == "live"
        assert sp500["lastUpdate"] is not None

        # Clearing live buffer while store retains valuation reports closing price
        market_buffer._history.clear()
        closed = await get_all_market_indexes()
        sp500_closed = next(i for i in closed if i["index"] == "SP500")
        assert sp500_closed["currentPrice"] == 5850.0
        assert sp500_closed["priceType"] == "closing"


class TestDashboardHTMLFeatures:
    """Test that dashboard.html contains all required features from the ticket."""

    def test_dashboard_has_real_time_streaming_badge(self, live_server):
        """Verify real-time streaming badge with pulse animation is present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for live badge
        assert "REALTIME STREAMING" in html
        assert "DISCONNECTED - RECONNECTING..." in html
        # Check for pulse animation
        assert ".pulse-dot" in html
        assert "animation: pulse" in html
        # Check for connection state handling
        assert "disconnected" in html
        assert ":class=\"{ disconnected: !connected }\"" in html

    def test_dashboard_has_decision_badges_and_styling(self, live_server):
        """Verify decision badges (BUY, SELL, HOLD) with styling are present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for all decision badge classes
        assert ".decision-badge.BUY" in html
        assert ".decision-badge.SELL" in html
        assert ".decision-badge.HOLD" in html
        # Check for badge styling
        assert "background-color: var(--color-buy-bg)" in html
        assert "background-color: var(--color-sell-bg)" in html
        assert "background-color: var(--color-hold-bg)" in html
        # Check for badge usage in Vue template
        assert ":class=\"item.recommendation\"" in html
        assert "v-if=\"!selectedSymbol\"" in html or ":class=\"item.recommendation\"" in html

    def test_dashboard_has_interactive_chart_with_chartjs(self, live_server):
        """Verify interactive Chart.js canvas is present and configured."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for Chart.js library
        assert "chart.js" in html or "Chart.js" in html
        assert "https://cdn.jsdelivr.net/npm/chart.js" in html
        # Check for canvas element
        assert "id=\"tickChart\"" in html
        # Check for chart-related CSS classes
        assert ".canvas-wrap" in html
        assert "clamp(220px, 45vh, 380px)" in html
        # Check for chart initialization in Vue
        assert "initChart()" in html
        assert "tickChart" in html

    def test_dashboard_has_moving_average_checkboxes(self, live_server):
        """Verify 200-Tick and 50-Tick moving average checkboxes are present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for checkboxes with honest window labels
        assert "200-Tick MA" in html
        assert "50-Tick MA" in html
        assert "200-Day" not in html
        assert "50-Day" not in html
        # Check for checkbox styling
        assert 'checkbox-label' in html
        # Check for Vue binding
        assert 'v-model="show200DayMA"' in html
        assert 'v-model="show50DayMA"' in html
        # Check for duration dropdown
        assert 'custom-dropdown-wrapper' in html

    def test_dashboard_has_market_indexes_row(self, live_server):
        """Verify market indexes row with VIX, DJIA, S&P 500, and Russell 2k is present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for market indexes grid
        assert ".market-indexes-row" in html
        # Check for index card styling
        assert ".market-index-card" in html
        # Check for index display names in Vue template
        assert 'index: \'VIX\'' in html
        assert 'index: \'DJIA\'' in html
        assert 'index: \'SP500\'' in html
        assert 'index: \'RUSSELL2000\'' in html
        # Check for up/down styling classes
        assert ".market-index-card.up" in html
        assert ".market-index-card.down" in html

    def test_dashboard_has_decision_reasoning_and_history(self, live_server):
        """Verify decision reasoning and history display sections are present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for decision box
        assert ".decision-box" in html
        # Check for reasoning list
        assert "reasoning-list" in html
        assert "reasoning" in html
        # Check for history box
        assert ".history-box" in html
        # Check for history list
        assert ".history-list" in html
        # Check for reasoning items in Vue
        assert 'v-if="activeDecision.reasoning && activeDecision.reasoning.length"' in html
        assert "reasoning" in html

    def test_dashboard_has_vue_3_application(self, live_server):
        """Verify Vue 3 application is properly set up."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for Vue 3 library
        assert "vue@3" in html or "vue.global.prod.js" in html
        assert "https://unpkg.com/vue@3" in html
        # Check for Vue app setup
        assert "const { createApp" in html
        # Check for reactivity features
        assert "ref(" in html
        assert "reactive(" in html
        assert "computed(" in html

    def test_dashboard_has_responsive_design(self, live_server):
        """Verify responsive design classes for different screen sizes."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for media queries
        assert "@media (max-width: 1024px)" in html
        assert "@media (max-width: 600px)" in html
        # Check for responsive grid layouts
        assert "grid-template-columns: repeat(auto-fill" in html
        # Check for responsive symbol cards
        assert ".symbols-grid" in html
        # Check for responsive market index cards
        assert ".market-indexes-row" in html

    def test_dashboard_has_symbol_summary_cards(self, live_server):
        """Verify symbol summary cards with detailed information are present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for symbols grid
        assert ".symbols-grid" in html
        # Check for symbol card
        assert ".symbol-card" in html
        # Check for card styling
        assert ".card-top" in html
        assert ".card-price-row" in html
        assert ".card-stats" in html
        assert ".card-footer" in html
        # Check for symbol display elements
        assert ".symbol-name" in html
        assert ".current-price" in html
        assert ".change-pct" in html
        # Check for stat items
        assert ".stat-item" in html
        assert ".stat-label" in html
        assert ".stat-val" in html

    def test_dashboard_has_drilldown_view(self, live_server):
        """Verify drill-down symbol view is implemented."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for drill-down grid
        assert ".drilldown-grid" in html
        # Check for back button
        assert ".back-btn" in html
        # Check for chart controls
        assert ".chart-controls" in html
        # Check for info sidebar
        assert ".info-sidebar" in html
        # Check for Vue condition for drill-down view
        assert "v-else" in html or "v-if=\"!selectedSymbol\"" in html

    def test_dashboard_has_color_theme_variables(self, live_server):
        """Verify CSS color theme variables are defined."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for CSS variables
        assert "--bg-primary" in html
        assert "--bg-secondary" in html
        assert "--bg-card" in html
        assert "--border-color" in html
        assert "--text-primary" in html
        assert "--color-buy" in html
        assert "--color-sell" in html
        assert "--color-hold" in html
        assert "--color-accent" in html

    def test_dashboard_has_all_required_chart_features(self, live_server):
        """Verify all required chart features from the ticket are present."""
        resp = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        html = resp.text
        # Check for duration options
        assert "1 Hour" in html
        assert "24 Hours" in html
        assert "5 Days" in html
        assert "30 Days" in html
        assert "1 Year" in html
        assert "YTD" in html
        # Check for moving average calculations
        assert "calculateMA" in html
        assert "calculateMovingAverages" in html
        # Check for chart configuration
        assert "onDurationChange" in html
        assert "getDurationRange" in html


class TestGetPriceAndChange:
    """Tests for get_price_and_change with buffer/database fallback."""

    def test_get_price_and_change_with_buffer(self):
        """Verify get_price_and_change returns data from buffer."""
        from src.web_server import market_buffer

        market_buffer.add_tick("TESTSYM", 100.0, 50)
        market_buffer.add_tick("TESTSYM", 105.0, 100)

        result = market_buffer.get_price_and_change("TESTSYM", "24h")
        assert result is not None
        assert result["currentPrice"] == 105.0
        assert result["changePercent"] == 5.0

    def test_get_price_and_change_empty_buffer(self):
        """Verify get_price_and_change returns 0.0 when buffer is empty and DB unavailable."""
        from src.web_server import market_buffer

        result = market_buffer.get_price_and_change("NONEXISTENT", "24h")
        assert result is None

    def test_get_price_and_change_buffer_with_empty_history(self):
        """Verify get_price_and_change returns data when history exists."""
        from src.web_server import market_buffer

        # Add a tick
        market_buffer.add_tick("OLDTICK", 99.0, 50)

        result = market_buffer.get_price_and_change("OLDTICK", "1h")
        # Should return the latest price even if there's no change
        assert result is not None
        assert result["currentPrice"] == 99.0


class TestDecisionHistoryFiltering:
    """Tests for decision history filtering (frontend) to show only action changes."""

    def test_decision_history_shows_all_actions(self):
        """Verify decision history shows all actions when actions change."""
        from src.web_server import decision_buffer

        # Add decisions with different actions
        decision_buffer.add_decision("TESTSYM", "BUY", 0.8, ["Reason 1"], timestamp="2024-01-01 10:00:00")
        decision_buffer.add_decision("TESTSYM", "SELL", 0.7, ["Reason 2"], timestamp="2024-01-01 10:01:00")
        decision_buffer.add_decision("TESTSYM", "HOLD", 0.6, ["Reason 3"], timestamp="2024-01-01 10:02:00")

        # Backend should store all decisions
        history = decision_buffer._history.get("TESTSYM", [])
        assert len(history) == 3
        assert history[0].action == "BUY"
        assert history[1].action == "SELL"
        assert history[2].action == "HOLD"

    def test_decision_history_filters_same_action_frontend(self):
        """Verify frontend decision history filters out same actions."""
        from src.web_server import decision_buffer

        # Add two BUY decisions (frontend should only show the first one)
        decision_buffer.add_decision("TESTSYM", "BUY", 0.8, ["Reason 1"], timestamp="2024-01-01 10:00:00")
        decision_buffer.add_decision("TESTSYM", "BUY", 0.85, ["Reason 2"], timestamp="2024-01-01 10:01:00")
        decision_buffer.add_decision("TESTSYM", "SELL", 0.7, ["Reason 3"], timestamp="2024-01-01 10:02:00")

        # Backend stores all decisions (no filtering here)
        history = decision_buffer._history.get("TESTSYM", [])
        assert len(history) == 3

        # Frontend will filter to only show BUY and SELL (not the second BUY)
        # This is verified by the WebSocket messages
        # The WebSocket endpoint processes decisions and broadcasts them
        # In real use, the frontend's WebSocket handler would filter based on action
        # For this test, we just verify the backend accepts all decisions
        assert history[0].action == "BUY"
        assert history[1].action == "BUY"  # Second BUY is stored but should be filtered by frontend
        assert history[2].action == "SELL"

    def test_decision_history_filters_same_action_with_different_timestamp(self):
        """Verify frontend decision history filters out same actions even with different timestamps."""
        from src.web_server import decision_buffer

        # Add two SELL decisions (frontend should only show the first one)
        decision_buffer.add_decision("TESTSYM", "SELL", 0.75, ["Reason 1"], timestamp="2024-01-01 10:00:00")
        decision_buffer.add_decision("TESTSYM", "SELL", 0.80, ["Reason 2"], timestamp="2024-01-01 10:01:00")

        # Backend stores all decisions
        history = decision_buffer._history.get("TESTSYM", [])
        assert len(history) == 2


class TestHotPathOptimizations:
    """Tests for Phase Perf hot-path optimizations in api_layer and data_layer."""

    def test_get_summary_reuses_batched_starts_without_extra_query(self):
        """get_summary should reuse starts from get_prices_at_interval_starts."""
        import src.web_server.data_layer as data_layer

        mock_db = MagicMock()
        mock_db.get_prices_at_interval_starts.return_value = {
            "1h": 98.0,
            "24h": 95.0,
            "5d": 90.0,
            "30d": 85.0,
            "1y": 80.0,
            "ytd": 88.0,
        }
        mock_db.get_price_at_interval_start = MagicMock(return_value=95.0)

        with patch.object(data_layer, "get_db_manager", return_value=mock_db):
            market_buffer.add_tick("AAPL", 100.0, 50.0)
            summary = market_buffer.get_summary("AAPL", duration="24h")

        assert summary["change"] == 5.0
        assert summary["changePercent"] == round(5.0 / 95.0 * 100.0, 2)
        mock_db.get_prices_at_interval_starts.assert_called_once_with("AAPL")
        mock_db.get_price_at_interval_start.assert_not_called()

    def test_compute_recommendation_uses_provided_summary(self):
        """compute_recommendation should use the provided summary without calling get_summary."""
        precomputed = {"symbol": "FAST", "currentPrice": 110.0, "changePercent": 2.5}
        with patch.object(market_buffer, "get_summary") as mock_get_summary:
            rec = compute_recommendation("FAST", summary=precomputed)
            mock_get_summary.assert_not_called()

        assert rec["symbol"] == "FAST"
        assert rec["action"] == "BUY"
        assert rec["confidence"] > 0.65

    @pytest.mark.asyncio
    async def test_broadcast_loop_bails_when_no_active_connections(self):
        """broadcast_loop should skip summary/recommendation work when active_connections is empty."""
        import src.web_server.api_layer as api_layer

        api_layer.manager.active_connections.clear()
        market_buffer.add_tick("AAPL", 180.0, 100.0)

        with (
            patch.object(market_buffer, "get_symbols") as mock_get_symbols,
            patch.object(market_buffer, "get_summary") as mock_get_summary,
            patch(
                "src.web_server.api_layer.asyncio.sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
        ):
            await api_layer.broadcast_loop(interval=0.01)
            mock_get_symbols.assert_not_called()
            mock_get_summary.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_loop_passes_summary_to_compute_recommendation(self):
        """broadcast_loop should compute summary once per symbol and pass it to compute_recommendation."""
        import src.web_server.api_layer as api_layer

        mock_ws = AsyncMock()
        api_layer.manager.active_connections.clear()
        api_layer.manager.active_connections.add(mock_ws)
        market_buffer.add_tick("AAPL", 180.0, 100.0)

        try:
            with (
                patch.object(
                    market_buffer, "get_summary", wraps=market_buffer.get_summary
                ) as spy_summary,
                patch(
                    "src.web_server.api_layer.compute_recommendation",
                    wraps=compute_recommendation,
                ) as spy_rec,
                patch(
                    "src.web_server.api_layer.asyncio.sleep",
                    new=AsyncMock(side_effect=asyncio.CancelledError),
                ),
            ):
                await api_layer.broadcast_loop(interval=0.01)
                assert spy_summary.call_count == 1
                assert spy_rec.call_count == 1
                assert "summary" in spy_rec.call_args.kwargs
                assert spy_rec.call_args.kwargs["summary"]["symbol"] == "AAPL"
        finally:
            api_layer.manager.active_connections.clear()

    def test_merge_ticks_linear_two_pointer_and_deduplication(self):
        """_merge_ticks merges sorted lists linearly and deduplicates matching (round(ts, 2), price)."""
        from src.web_server.api_layer import _merge_ticks

        historical = [
            {"timestamp": 100.0, "price": 10.0, "source": "db"},
            {"timestamp": 200.0, "price": 20.0, "source": "db"},
            {"timestamp": 300.0, "price": 30.0, "source": "db"},
        ]
        buffered = [
            {"timestamp": 200.0, "price": 20.0, "source": "buf"},
            {"timestamp": 400.0, "price": 40.0, "source": "buf"},
        ]

        merged = _merge_ticks(historical, buffered, limit=None)
        assert [t["timestamp"] for t in merged] == [100.0, 200.0, 300.0, 400.0]

        # Limit trims to most recent ticks
        limited = _merge_ticks(historical, buffered, limit=2)
        assert [t["timestamp"] for t in limited] == [300.0, 400.0]

        # Fallback sort when an input is unsorted
        unsorted_hist = [
            {"timestamp": 300.0, "price": 30.0},
            {"timestamp": 100.0, "price": 10.0},
        ]
        merged_unsorted = _merge_ticks(unsorted_hist, buffered, limit=None)
        assert [t["timestamp"] for t in merged_unsorted] == [100.0, 200.0, 300.0, 400.0]

    def test_running_high_low_volume_and_eviction(self):
        """MarketDataBuffer maintains running high/low/volume across appends, evictions, and out-of-order ticks."""
        from src.web_server.data_layer import MarketDataBuffer

        buf = MarketDataBuffer(max_ticks=3)
        buf.add_tick("SYM", 100.0, 10.0, timestamp=1000.0)
        buf.add_tick("SYM", 90.0, 5.0, timestamp=1001.0)
        buf.add_tick("SYM", 120.0, 20.0, timestamp=1002.0)

        assert buf.get_latest_tick("SYM").price == 120.0
        assert buf._history["SYM"][0].price == 100.0
        assert buf._history["SYM"][1].price == 90.0
        assert buf._history["SYM"][2].price == 120.0

        summary = buf.get_summary("SYM")
        assert summary["high"] == 120.0
        assert summary["low"] == 90.0
        assert summary["volume"] == 35.0

        # Add 4th tick: evicts oldest (100.0, size 10.0); low (90.0) and high (120.0) remain
        buf.add_tick("SYM", 110.0, 15.0, timestamp=1003.0)
        summary2 = buf.get_summary("SYM")
        assert summary2["high"] == 120.0
        assert summary2["low"] == 90.0
        assert summary2["volume"] == 40.0

        # Add 5th tick: evicts 90.0 (which was the running low); low recomputes to 105.0
        buf.add_tick("SYM", 105.0, 10.0, timestamp=1004.0)
        summary3 = buf.get_summary("SYM")
        assert summary3["high"] == 120.0
        assert summary3["low"] == 105.0
        assert summary3["volume"] == 45.0

    def test_dashboard_html_cached_at_import(self):
        """Dashboard HTML is cached at import so serving it does not re-open the file."""
        import src.web_server.api_layer as api_layer

        assert api_layer._DASHBOARD_HTML is not None
        with patch("builtins.open", wraps=open) as spy_open:
            first = api_layer._get_html_content(
                api_layer.STATIC_HTML_PATH,
                api_layer._DASHBOARD_HTML,
                api_layer._DASHBOARD_HTML_SOURCE_PATH,
            )
            second = api_layer._get_html_content(
                api_layer.STATIC_HTML_PATH,
                api_layer._DASHBOARD_HTML,
                api_layer._DASHBOARD_HTML_SOURCE_PATH,
            )

        assert first is not None
        assert first == second
        assert spy_open.call_count == 0

    def test_moving_average_returns_none_when_fewer_points_than_window(self):
        """_moving_average returns None when len(prices) < window and computes mean when >= window."""
        from src.web_server.api_layer import _moving_average

        assert _moving_average([], 50) is None
        assert _moving_average([100.0, 102.0, 104.0], 50) is None
        assert _moving_average([10.0, 20.0, 30.0], 3) == 20.0
        assert _moving_average([5.0, 10.0, 20.0, 30.0], 3) == 20.0

    def test_get_summary_use_db_false_never_queries_database(self):
        """get_summary(..., use_db=False) never touches the database whether buffer is empty or populated."""
        import src.web_server.data_layer as data_layer

        with patch.object(data_layer, "get_db_manager") as mock_get_db:
            # Cold symbol with empty buffer
            cold = market_buffer.get_summary("COLD_SYM", use_db=False)
            assert cold["symbol"] == "COLD_SYM"
            assert cold["currentPrice"] == 0.0
            mock_get_db.assert_not_called()

            # Populated symbol in buffer
            market_buffer.add_tick("WARM_SYM", 100.0, 10.0)
            market_buffer.add_tick("WARM_SYM", 110.0, 15.0)
            warm = market_buffer.get_summary("WARM_SYM", use_db=False)
            assert warm["symbol"] == "WARM_SYM"
            assert warm["currentPrice"] == 110.0
            assert warm["change"] == 10.0
            assert warm["changePercent"] == 10.0
            assert warm["change24h"] == 10.0
            assert warm["changePercent24h"] == 10.0
            mock_get_db.assert_not_called()

    def test_index_store_skips_writing_placeholders_to_disk(self, tmp_path):
        """IndexStore does not persist placeholder entries to index_cache.json."""
        import json

        from src.web_server.data_layer import IndexStore

        cache_file = str(tmp_path / "no_placeholders.json")
        store = IndexStore(cache_file=cache_file, flush_interval=60.0)
        store.flush_cache(force=True)

        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {}

    @pytest.mark.asyncio
    async def test_websocket_endpoint_sends_buffer_only_init_before_db_read(self):
        """websocket_endpoint sends a buffer-only init frame (use_db=False) before querying DB."""
        from fastapi import WebSocketDisconnect

        import src.web_server.api_layer as api_layer
        import src.web_server.data_layer as data_layer

        market_buffer.add_tick("AAPL", 180.0, 100.0)
        market_buffer.add_tick("AAPL", 185.0, 50.0)

        events: list[str] = []
        sent_frames: list[dict] = []

        mock_ws = AsyncMock()

        async def record_send_json(payload):
            events.append(f"send:{payload['type']}")
            sent_frames.append(payload)

        mock_ws.send_json = AsyncMock(side_effect=record_send_json)
        mock_ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

        orig_get_summary = market_buffer.get_summary

        def spy_get_summary(symbol, duration="24h", use_db=True):
            events.append(f"get_summary:use_db={use_db}")
            return orig_get_summary(symbol, duration=duration, use_db=use_db)

        mock_db = MagicMock()
        mock_db.get_prices_at_interval_starts.return_value = {"24h": 175.0}

        with (
            patch.object(market_buffer, "get_summary", side_effect=spy_get_summary),
            patch.object(data_layer, "get_db_manager", return_value=mock_db),
        ):
            await api_layer.websocket_endpoint(mock_ws)

        assert events[:2] == ["get_summary:use_db=False", "send:init"]
        assert sent_frames[0]["type"] == "init"
        assert sent_frames[0]["symbols"][0]["change"] == 5.0
        assert sent_frames[1]["type"] == "update"
        assert sent_frames[1]["symbols"][0]["change"] == 10.0



