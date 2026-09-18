"""Unit tests for data adapters."""
import pytest
import datetime
from src.data_adapters import AlpacaDataAdapter
from src.sentiment_adapter import AdanosDataAdapter
from src.domain import MarketState, PortfolioState, FullState

# --- Test AlpacaDataAdapter ---

class TestAlpacaDataAdapter:
    """Test AlpacaDataAdapter functionality."""

    def test_parse_tick_data_success(self):
        """Should successfully parse valid tick data."""
        raw_tick = {
            'symbol': 'AAPL',
            'price': 150.0,
            'qty': 10,
            'timestamp': '2023-01-01T12:00:00Z',
            'side': 'BUY'
        }

        result = AlpacaDataAdapter.parse_tick_data(raw_tick)

        assert result is not None
        assert result['symbol'] == 'AAPL'
        assert result['price'] == 150.0
        assert result['size'] == 10.0
        assert result['side'] == 'BUY'
        assert isinstance(result['timestamp'], datetime.datetime)

    def test_parse_tick_data_invalid_timestamp(self):
        """Should handle invalid timestamp gracefully."""
        raw_tick = {
            'symbol': 'TSLA',
            'price': 700.0,
            'qty': 5,
            'timestamp': 'invalid-timestamp'
        }

        result = AlpacaDataAdapter.parse_tick_data(raw_tick)

        assert result is None

    def test_parse_tick_data_with_datetime_object(self):
        """Should parse tick data when timestamp is already datetime."""
        now = datetime.datetime.now()
        raw_tick = {
            'symbol': 'AAPL',
            'price': 150.0,
            'qty': 10,
            'timestamp': now,
            'side': 'BUY'
        }
        result = AlpacaDataAdapter.parse_tick_data(raw_tick)
        assert result is not None
        assert result['timestamp'] == now

    def test_parse_bar_data_success(self):
        """Should successfully parse bar data."""
        raw_bar = {
            'symbol': 'AAPL',
            'timestamp': '2023-01-01T12:00:00Z',
            'open': 145.0,
            'high': 155.0,
            'low': 144.0,
            'close': 153.0,
            'volume': 1000
        }

        result = AlpacaDataAdapter.parse_bar_data(raw_bar)

        assert result is not None
        assert result['symbol'] == 'AAPL'
        assert result['open'] == 145.0
        assert result['high'] == 155.0
        assert result['low'] == 144.0
        assert result['close'] == 153.0

    def test_parse_bar_data_with_datetime_object(self):
        """Should parse bar data when timestamp is already datetime."""
        now = datetime.datetime.now()
        raw_bar = {
            'symbol': 'AAPL',
            'timestamp': now,
            'open': 145.0,
            'high': 155.0,
            'low': 144.0,
            'close': 153.0,
            'volume': 1000
        }
        result = AlpacaDataAdapter.parse_bar_data(raw_bar)
        assert result is not None
        assert result['timestamp'] == now

    def test_create_market_state_from_ticks(self):
        """Should create market state from tick data."""
        ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'},
            {'symbol': 'TSLA', 'price': 700.0, 'size': 5, 'timestamp': '2023-01-01T12:00:01Z'}
        ]

        parsed_ticks = [AlpacaDataAdapter.parse_tick_data(t) for t in ticks]
        parsed_ticks = [t for t in parsed_ticks if t is not None]

        state = AlpacaDataAdapter.create_market_state(parsed_ticks)

        assert isinstance(state, MarketState)
        # Note: Current implementation uses last tick only
        # So only TSLA will be in the state
        assert 'TSLA' in state.prices
        assert state.prices['TSLA'] == 700.0

    def test_create_portfolio_state_with_positions(self):
        """Should create portfolio state from positions."""
        positions = [
            {'symbol': 'AAPL', 'qty': 100, 'avg_entry_price': 150.0},
            {'symbol': 'MSFT', 'qty': 50, 'avg_entry_price': 300.0}
        ]

        state = AlpacaDataAdapter.create_portfolio_state(positions)

        assert isinstance(state, PortfolioState)
        assert state.holdings['AAPL'] == 100.0
        assert state.holdings['MSFT'] == 50.0

    def test_create_portfolio_state_empty(self):
        """Should create empty portfolio state when no positions provided."""
        state = AlpacaDataAdapter.create_portfolio_state()

        assert isinstance(state, PortfolioState)
        assert len(state.holdings) == 0
        assert state.cash == 0.0

    def test_create_full_state(self):
        """Should create full state from ticks and positions."""
        ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'}
        ]
        positions = []

        state = AlpacaDataAdapter.create_full_state(ticks, positions)

        assert isinstance(state, FullState)
        assert 'AAPL' in state.market.prices
        assert len(state.portfolio.holdings) == 0


# --- Test AdanosDataAdapter ---

class TestAdanosDataAdapter:
    """Test AdanosDataAdapter functionality."""

    def test_fetch_sentiment_with_api_key(self):
        """Should fetch sentiment when API key is present."""
        # Note: This test relies on ADANOS_API_KEY environment variable
        AdanosDataAdapter.ADANOS_API_KEY = "test_key"

        result = AdanosDataAdapter.fetch_sentiment(['AAPL', 'TSLA'])

        assert result is not None
        assert len(result) > 0
        assert all('symbol' in r for r in result)

    def test_fetch_sentiment_without_api_key(self):
        """Should return empty list when API key is missing."""
        AdanosDataAdapter.ADANOS_API_KEY = ""

        result = AdanosDataAdapter.fetch_sentiment(['AAPL'])

        assert result == []

    def test_parse_sentiment_response_list(self):
        """Should parse list response from API."""
        response = [
            {'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'score': 0.8, 'source': 'test'}
        ]

        result = AdanosDataAdapter.parse_sentiment_response(response)

        assert len(result) == 1
        assert result[0]['symbol'] == 'AAPL'
        assert result[0]['score'] == 0.8

    def test_parse_sentiment_response_dict(self):
        """Should parse dict response from API."""
        response = {'data': [{'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'score': 0.6}]}

        result = AdanosDataAdapter.parse_sentiment_response(response)

        assert len(result) == 1
        assert result[0]['symbol'] == 'AAPL'

    def test_parse_sentiment_invalid_format(self):
        """Should handle invalid response format gracefully."""
        response = {'invalid': 'format'}

        result = AdanosDataAdapter.parse_sentiment_response(response)

        assert result == []

    def test_create_market_state_with_sentiment(self):
        """Should create market state with enriched sentiment scores."""
        market_ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'}
        ]
        sentiment_data = [
            {'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'score': 0.8}
        ]

        state = AdanosDataAdapter.create_market_state_with_sentiment(
            market_ticks, sentiment_data
        )

        assert isinstance(state, MarketState)
        assert 'AAPL' in state.prices
        assert 'AAPL' in state.sentiment_scores
        assert state.sentiment_scores['AAPL'] == 0.8

    def test_create_full_state_with_sentiment(self):
        """Should create full state with sentiment integration."""
        market_ticks = [
            {'symbol': 'AAPL', 'price': 150.0, 'size': 10, 'timestamp': '2023-01-01T12:00:00Z'}
        ]
        sentiment_data = [
            {'symbol': 'AAPL', 'timestamp': '2023-01-01T12:00:00Z', 'score': 0.5}
        ]

        state = AdanosDataAdapter.create_full_state_with_sentiment(
            market_ticks=market_ticks,
            sentiment_data=sentiment_data
        )

        assert isinstance(state, FullState)
        assert 'AAPL' in state.market.sentiment_scores

    def test_validate_sentiment_score_valid(self):
        """Should return True for valid sentiment score."""
        assert AdanosDataAdapter.validate_sentiment_score(0.8)
        assert AdanosDataAdapter.validate_sentiment_score(-0.5)

    def test_validate_sentiment_score_invalid(self):
        """Should return False for invalid sentiment score."""
        assert not AdanosDataAdapter.validate_sentiment_score(1.5)
        assert not AdanosDataAdapter.validate_sentiment_score(-1.5)