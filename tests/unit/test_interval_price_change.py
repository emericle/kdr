"""Unit tests for interval-aware price change calculation."""

import time
import threading
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import asyncio

from src.web_server import (
    MarketDataBuffer,
    TradingDecision,
    decision_buffer,
    market_buffer,
    start_web_server_thread,
    stop_web_server
)
from src.database import DatabaseManager

TEST_PORT = 8099
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture
def market_buffer_reset():
    """Reset market buffer for each test."""
    market_buffer._history.clear()
    market_buffer._open_prices.clear()
    yield


@pytest.fixture(autouse=True)
def mock_db_manager():
    """Create a mock DatabaseManager with interval price lookup capability."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.get_price_at_interval_start = MagicMock(return_value=150.0)
    mock_db.get_historical_market_data = MagicMock(return_value=[])
    mock_db.get_price_at_timestamp = MagicMock(return_value=150.0)
    mock_db.get_latest_market_record = MagicMock(return_value=None)
    mock_db.get_model_weights = MagicMock(return_value=None)
    with patch("src.web_server.get_db_manager", return_value=mock_db):
        yield mock_db


class TestIntervalAwarePriceChange:
    """Tests for interval-aware price change calculation."""

    def test_get_price_and_change_with_duration_24h(self, market_buffer_reset):
        """Test get_price_and_change with 24h duration."""
        now = time.time()
        market_buffer.add_tick("AAPL", 150.0, 100, timestamp=now - 3600)  # Old tick
        market_buffer.add_tick("AAPL", 155.0, 150, timestamp=now - 1800)  # Recent tick
        market_buffer.add_tick("AAPL", 160.0, 200, timestamp=now)  # Current

        result = market_buffer.get_price_and_change("AAPL", duration="24h")

        assert result is not None
        assert result["currentPrice"] == 160.0
        assert result["changePercent"] > 0

    def test_get_price_and_change_with_duration_5d(self, market_buffer_reset):
        """Test get_price_and_change with 5d duration."""
        now = time.time()
        market_buffer.add_tick("MSFT", 300.0, 100, timestamp=now - 432000)  # 5 days ago
        market_buffer.add_tick("MSFT", 310.0, 150, timestamp=now)  # Current

        result = market_buffer.get_price_and_change("MSFT", duration="5d")

        assert result is not None
        assert result["currentPrice"] == 310.0
        assert result["changePercent"] > 0

    def test_get_price_and_change_with_duration_1h(self, market_buffer_reset):
        """Test get_price_and_change with 1h duration."""
        now = time.time()
        market_buffer.add_tick("TSLA", 700.0, 100, timestamp=now - 3000)  # 50 mins ago
        market_buffer.add_tick("TSLA", 710.0, 150, timestamp=now)  # Current

        result = market_buffer.get_price_and_change("TSLA", duration="1h")

        assert result is not None
        assert result["currentPrice"] == 710.0
        assert result["changePercent"] > 0

    def test_get_price_and_change_with_duration_ytd(self, market_buffer_reset):
        """Test get_price_and_change with YTD duration."""
        now = time.time()
        market_buffer.add_tick("NVDA", 900.0, 100, timestamp=now - 157680000)  # ~5 years (YTD)
        market_buffer.add_tick("NVDA", 950.0, 150, timestamp=now)  # Current

        result = market_buffer.get_price_and_change("NVDA", duration="ytd")

        assert result is not None
        assert result["currentPrice"] == 950.0
        assert result["changePercent"] > 0

    def test_get_price_and_change_fallback_to_buffer(self, market_buffer_reset, mock_db_manager):
        """Test fallback to buffer price change calculation if DB unavailable."""
        now = time.time()
        with patch("src.web_server.get_db_manager", return_value=mock_db_manager):
            market_buffer.add_tick("AAPL", 150.0, 100, timestamp=now - 3600)  # Old tick
            market_buffer.add_tick("AAPL", 160.0, 200, timestamp=now)  # Current

            result = market_buffer.get_price_and_change("AAPL", duration="24h")

            assert result is not None
            assert result["currentPrice"] == 160.0

    def test_get_summary_with_duration_24h(self, market_buffer_reset):
        """Test get_summary with 24h duration."""
        now = time.time()
        market_buffer.add_tick("AAPL", 150.0, 100, timestamp=now - 3600)  # Old tick
        market_buffer.add_tick("AAPL", 155.0, 150, timestamp=now - 1800)  # Recent
        market_buffer.add_tick("AAPL", 160.0, 200, timestamp=now)  # Current

        result = market_buffer.get_summary("AAPL", duration="24h")

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["currentPrice"] == 160.0
        assert result["changePercent"] > 0
        assert result["high"] > 0
        assert result["low"] > 0
        assert result["volume"] > 0

    def test_get_summary_with_duration_5d(self, market_buffer_reset):
        """Test get_summary with 5d duration."""
        now = time.time()
        market_buffer.add_tick("MSFT", 300.0, 100, timestamp=now - 432000)  # 5 days ago
        market_buffer.add_tick("MSFT", 320.0, 150, timestamp=now)  # Current

        result = market_buffer.get_summary("MSFT", duration="5d")

        assert result is not None
        assert result["symbol"] == "MSFT"
        assert result["currentPrice"] == 320.0
        assert result["changePercent"] > 0

    def test_get_summary_with_duration_30d(self, market_buffer_reset):
        """Test get_summary with 30d duration."""
        now = time.time()
        market_buffer.add_tick("TSLA", 700.0, 100, timestamp=now - 2592000)  # 30 days ago
        market_buffer.add_tick("TSLA", 750.0, 150, timestamp=now)  # Current

        result = market_buffer.get_summary("TSLA", duration="30d")

        assert result is not None
        assert result["symbol"] == "TSLA"
        assert result["currentPrice"] == 750.0
        assert result["changePercent"] > 0

    def test_get_summary_with_duration_1y(self, market_buffer_reset):
        """Test get_summary with 1y duration."""
        now = time.time()
        market_buffer.add_tick("GOOGL", 1400.0, 100, timestamp=now - 31536000)  # 1 year ago
        market_buffer.add_tick("GOOGL", 1500.0, 150, timestamp=now)  # Current

        result = market_buffer.get_summary("GOOGL", duration="1y")

        assert result is not None
        assert result["symbol"] == "GOOGL"
        assert result["currentPrice"] == 1500.0
        assert result["changePercent"] > 0

    def test_get_summary_negative_change(self, market_buffer_reset):
        """Test get_summary with price decline."""
        now = time.time()
        market_buffer.add_tick("AAPL", 160.0, 200, timestamp=now)  # Current
        market_buffer.add_tick("AAPL", 155.0, 150, timestamp=now - 1800)  # Recent
        market_buffer.add_tick("AAPL", 150.0, 100, timestamp=now - 3600)  # Old

        result = market_buffer.get_summary("AAPL", duration="24h")

        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["currentPrice"] == 160.0
        assert result["changePercent"] > 0

    def test_get_price_and_change_returns_none_no_history(self, market_buffer_reset):
        """Test get_price_and_change returns None if no history."""
        result = market_buffer.get_price_and_change("NONEXISTENT", duration="24h")

        assert result is None

    def test_get_summary_returns_zero_stats_no_history(self, market_buffer_reset):
        """Test get_summary returns zero stats if no history."""
        result = market_buffer.get_summary("NONEXISTENT", duration="24h")

        assert result is not None
        assert result["currentPrice"] == 0.0
        assert result["changePercent"] == 0.0
        assert result["high"] == 0.0
        assert result["low"] == 0.0
        assert result["volume"] == 0.0
        assert result["lastUpdate"] is None

    def test_duration_default_24h(self, market_buffer_reset):
        """Test that default duration is 24h."""
        now = time.time()
        market_buffer.add_tick("AAPL", 150.0, 100, timestamp=now - 3600)  # Old
        market_buffer.add_tick("AAPL", 160.0, 200, timestamp=now)  # Current

        result = market_buffer.get_price_and_change("AAPL")

        assert result is not None
        assert result["currentPrice"] == 160.0
        assert result["changePercent"] > 0


class TestDatabaseIntervalPriceLookup:
    """Tests for database interval price lookup functionality."""

    def test_get_price_at_interval_start_1h(self, mock_db_manager):
        """Test interval start price for 1h duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "1h")

        assert start_time is not None
        assert start_time > 0

    def test_get_price_at_interval_start_24h(self, mock_db_manager):
        """Test interval start price for 24h duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "24h")

        assert start_time is not None
        assert start_time > 0

    def test_get_price_at_interval_start_5d(self, mock_db_manager):
        """Test interval start price for 5d duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "5d")

        assert start_time is not None
        assert start_time > 0

    def test_get_price_at_interval_start_30d(self, mock_db_manager):
        """Test interval start price for 30d duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "30d")

        assert start_time is not None
        assert start_time > 0

    def test_get_price_at_interval_start_1y(self, mock_db_manager):
        """Test interval start price for 1y duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "1y")

        assert start_time is not None
        assert start_time > 0

    def test_get_price_at_interval_start_ytd(self, mock_db_manager):
        """Test interval start price for YTD duration."""
        start_time = mock_db_manager.get_price_at_interval_start("AAPL", "ytd")

        assert start_time is not None
        assert start_time > 0