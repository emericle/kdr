"""Unit tests for state mapper."""
import pytest
from unittest.mock import MagicMock, patch
from src.state_mapper import StateMapper
from src.data_adapters import AlpacaDataAdapter
from src.sentiment_adapter import AdanosDataAdapter
from src.domain import FullState
import sys

# --- Test StateMapper ---

@pytest.fixture
def state_mapper():
    """Create StateMapper instance for testing with mocked environment."""
    with patch('sys.exit'), patch('src.config_gatekeeper.validate_configs'):
        return StateMapper()


class TestStateMapper:
    """Test StateMapper functionality."""

    def test_initialization(self, state_mapper):
        """Should initialize state mapper with adapters."""
        assert state_mapper is not None
        assert state_mapper.alpaca_adapter is not None
        assert state_mapper.adanos_adapter is not None

    def test_map_bar_data_to_state(self, state_mapper):
        """Should map a single OHLC bar to market and portfolio state."""
        bar_data = {
            'symbol': 'AAPL',
            'timestamp': '2023-01-01T12:00:00Z',
            'open': 145.0,
            'high': 155.0,
            'low': 144.0,
            'close': 153.0,
            'volume': 1000
        }

        market_state, portfolio_state = state_mapper.map_bar_data_to_state(bar_data)

        assert market_state is not None
        # The last close price should be used
        assert 'AAPL' in market_state.prices
        # MarketState stores the last known price (should be 153.0 from close)

    def test_map_multiple_bars_to_state(self, state_mapper):
        """Should map multiple OHLC bars into current state."""
        bars = [
            {
                'symbol': 'AAPL',
                'timestamp': '2023-01-01T12:00:00Z',
                'open': 145.0,
                'high': 155.0,
                'low': 144.0,
                'close': 150.0,
                'volume': 1000
            },
            {
                'symbol': 'TSLA',
                'timestamp': '2023-01-01T12:01:00Z',
                'open': 680.0,
                'high': 710.0,
                'low': 675.0,
                'close': 700.0,
                'volume': 500
            }
        ]

        market_state = state_mapper.map_multiple_bars_to_state(bars)

        assert market_state is not None
        assert 'AAPL' in market_state.prices
        assert 'TSLA' in market_state.prices

    def test_map_tick_data_to_state(self, state_mapper):
        """Should map tick data to current market state."""
        ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'},
            {'symbol': 'MSFT', 'price': 300.0, 'size': 5, 'timestamp': '2023-01-01T12:00:01Z'}
        ]

        market_state = state_mapper.map_tick_data_to_state(ticks)

        assert market_state is not None
        # Only captures the last price per symbol
        assert 'AAPL' in market_state.prices
        assert 'MSFT' in market_state.prices

    def test_map_positions_to_portfolio_state(self, state_mapper):
        """Should map positions to portfolio state."""
        positions = [
            {'symbol': 'AAPL', 'qty': 100, 'avg_entry_price': 150.0},
            {'symbol': 'MSFT', 'qty': 50, 'avg_entry_price': 300.0}
        ]

        portfolio_state = state_mapper.map_positions_to_portfolio_state(positions)

        assert portfolio_state is not None
        assert portfolio_state.holdings['AAPL'] == 100.0
        assert portfolio_state.holdings['MSFT'] == 50.0

    def test_map_and_combine_with_sentiment(self, state_mapper):
        """Should create market state with sentiment enrichment."""
        market_ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'}
        ]
        sentiment_data = [
            {'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'score': 0.8}
        ]

        market_state = state_mapper.map_and_combine_with_sentiment(
            market_ticks, sentiment_data
        )

        assert market_state is not None
        assert 'AAPL' in market_state.sentiment_scores

    def test_create_full_state_from_raw_data(self, state_mapper):
        """Should create full state from raw data dict."""
        raw_data = {
            'ticks': [
                {'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'price': 150.0, 'size': 10}
            ],
            'positions': [
                {'symbol': 'AAPL', 'qty': 100, 'avg_entry_price': 150.0}
            ],
            'sentiment': []
        }

        full_state = state_mapper.create_full_state_from_raw_data(raw_data)

        assert full_state is not None

    def test_create_full_state_from_raw_data_no_sentiment(self, state_mapper):
        """Should create full state without sentiment enrichment."""
        raw_data = {
            'ticks': [
                {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'}
            ]
        }

        full_state = state_mapper.create_full_state_from_raw_data(raw_data, include_sentiment=False)

        assert full_state is not None
        # Verify that if sentiment is not requested, it doesn't try to fetch
        assert isinstance(full_state, FullState)

    def test_create_full_state_for_symbol(self, state_mapper):
        """Should create focused full state for single symbol."""
        market_data = {
            'AAPL': {
                'symbol': 'AAPL',
                'price': 150.0,
                'size': 10,
                'timestamp': '2023-01-01T12:00:00Z'
            }
        }
        sentiment_score = 0.7
        portfolio_data = {'AAPL': 100.0}

        full_state = state_mapper.create_full_state_for_symbol(
            'AAPL', market_data, sentiment_score, portfolio_data
        )

        assert full_state is not None
        assert 'AAPL' in full_state.market.prices
        assert 'AAPL' in full_state.market.sentiment_scores

    def test_create_full_state_for_symbol_missing_market_data(self, state_mapper):
        """Should raise ValueError when symbol has no market data."""
        market_data = {'MSFT': {'price': 300.0}}
        portfolio_data = {}

        with pytest.raises(ValueError):
            state_mapper.create_full_state_for_symbol('AAPL', market_data, 0.5, portfolio_data)

    def test_aggregate_state_from_components(self, state_mapper):
        """Should aggregate market and portfolio states."""
        from src.domain import MarketState, PortfolioState
    
        market_state = MarketState(
            prices={'AAPL': 150.0, 'MSFT': 300.0},
            sentiment_scores={'AAPL': 0.7}
        )
        portfolio_state = PortfolioState(
            holdings={'AAPL': 100.0},
            cash=10000.0
        )
    
        full_state = state_mapper.aggregate_state_from_components(
            market_state, portfolio_state
        )
    
        assert full_state is not None
        assert isinstance(full_state, FullState)
        # Verify aggregated components

    def test_sync_with_database(self, state_mapper):
        """Should return list when syncing with database."""
        # This is a placeholder - actual implementation would query DB
        result = state_mapper.sync_with_database(None, ['AAPL'])

        assert result == []

    def test_sync_with_database_symbols(self, state_mapper):
        """Should return list for multiple symbols."""
        result = state_mapper.sync_with_database(None, ['AAPL', 'TSLA', 'MSFT'])

        assert result == []

    def test_map_bar_data_to_state_invalid(self, state_mapper):
        with pytest.raises(ValueError):
            state_mapper.map_bar_data_to_state({"symbol": "BAD"})

    def test_map_multiple_bars_to_state_invalid(self, state_mapper):
        with pytest.raises(ValueError):
            state_mapper.map_multiple_bars_to_state([])

    def test_map_tick_data_to_state_invalid(self, state_mapper):
        with pytest.raises(ValueError):
            state_mapper.map_tick_data_to_state([])

    def test_create_full_state_from_raw_data_invalid(self, state_mapper):
        with pytest.raises(ValueError):
            state_mapper.create_full_state_from_raw_data({"ticks": []})

    def test_create_full_state_for_symbol_variations(self, state_mapper):
        import datetime
        now = datetime.datetime.now()

        # Bar data with close
        bar_item = {"symbol": "AAPL", "close": 155.0, "timestamp": now, "open": 150.0, "high": 156.0, "low": 149.0, "volume": 100}
        res_bar = state_mapper.create_full_state_for_symbol("AAPL", {"AAPL": bar_item}, 0.5, {"AAPL": 10.0})
        assert res_bar.market.prices["AAPL"] == 155.0

        # Dict with price
        dict_item = {"price": 160.0}
        res_dict = state_mapper.create_full_state_for_symbol("AAPL", {"AAPL": dict_item}, 0.5, {"AAPL": 10.0})
        assert res_dict.market.prices["AAPL"] == 160.0

        # Unparseable item
        with pytest.raises(ValueError, match="Could not parse market data"):
            state_mapper.create_full_state_for_symbol("AAPL", {"AAPL": "not-valid"}, 0.5, {})

    def test_init_with_invalid_configs(self):
        with patch('src.config_gatekeeper.validate_configs', return_value=False):
            sm = StateMapper()
            assert sm is not None