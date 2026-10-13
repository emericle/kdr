import pytest
import numpy as np
from pydantic import ValidationError
from src.domain import (
    MarketState, 
    PortfolioState, 
    FullState, 
    Action, 
    TradeMovement,
    state_vector_schema,
)

import math

from datetime import datetime, timezone


class TestMarketState:
    """Tests for MarketState model."""
    
    def test_market_state_valid(self):
        """Test creating a valid MarketState."""
        state = MarketState(
            prices={"AAPL": 150.0, "TSLA": 700.0},
            sentiment_scores={"AAPL": 0.5, "TSLA": 0.7}
        )
        assert state.prices == {"AAPL": 150.0, "TSLA": 700.0}
        assert state.sentiment_scores == {"AAPL": 0.5, "TSLA": 0.7}
    
    def test_market_state_with_empty_prices(self):
        """Test that empty prices raises ValidationError."""
        with pytest.raises(ValidationError):
            MarketState(prices={}, sentiment_scores={})
    
    def test_market_state_with_one_price(self):
        """Test creating MarketState with a single price."""
        state = MarketState(prices={"AAPL": 150.0})
        assert state.prices == {"AAPL": 150.0}
        assert state.sentiment_scores == {}


class TestPortfolioState:
    """Tests for PortfolioState model."""
    
    def test_portfolio_state_valid(self):
        """Test creating a valid PortfolioState."""
        state = PortfolioState(
            holdings={"AAPL": 10.0, "TSLA": 5.0},
            cash=5000.0
        )
        assert state.holdings == {"AAPL": 10.0, "TSLA": 5.0}
        assert state.cash == 5000.0
    
    def test_portfolio_state_with_zero_cash(self):
        """Test PortfolioState with zero cash."""
        state = PortfolioState(holdings={}, cash=0.0)
        assert state.cash == 0.0
    
    def test_portfolio_state_with_negative_holdings_raises_error(self):
        """Test that negative holdings raise ValidationError."""
        with pytest.raises(ValidationError):
            PortfolioState(holdings={"AAPL": -10.0}, cash=5000.0)
    
    def test_portfolio_state_with_one_holding(self):
        """Test creating PortfolioState with a single holding."""
        state = PortfolioState(holdings={"AAPL": 10.0}, cash=5000.0)
        assert state.holdings == {"AAPL": 10.0}
    
    def test_portfolio_state_holding_zero(self):
        """Test PortfolioState with zero quantity for a holding."""
        state = PortfolioState(holdings={"AAPL": 0.0}, cash=5000.0)
        assert state.holdings["AAPL"] == 0.0


class TestFullState:
    """Tests for FullState model."""
    
    def test_full_state_valid(self):
        """Test creating a valid FullState."""
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        assert state.market == market
        assert state.portfolio == portfolio
        assert state.timestamp is None
    
    def test_full_state_with_timestamp(self):
        """Test creating FullState with timestamp."""
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(
            market=market, 
            portfolio=portfolio, 
            timestamp=1234567890.0
        )
        assert state.timestamp == datetime.fromtimestamp(
            1234567890.0, tz=timezone.utc
        )
    
    def test_full_state_to_vector(self):
        """Test converting FullState to vector."""
        market = MarketState(
            prices={"AAPL": 150.0, "TSLA": 700.0},
            sentiment_scores={"AAPL": 0.5, "TSLA": 0.7}
        )
        portfolio = PortfolioState(
            holdings={"AAPL": 10.0, "TSLA": 5.0},
            cash=5000.0
        )
        state = FullState(market=market, portfolio=portfolio)
        
        symbols = ["AAPL", "TSLA"]
        vector = state.to_vector(symbols)

        # Per symbol: log price change, sentiment rescaled onto [-1, 1], position
        # flag. Then cash as a fraction of the portfolio's total value.
        # No previous prices, so both price changes are 0.
        # Sentiment: (0.5 - 0.5) / 0.5 = 0, (0.7 - 0.5) / 0.5 = 0.4
        # Portfolio value: 5000 + 10 * 150 + 5 * 700 = 10000, so cash is half.
        expected = np.array([0.0, 0.0, 1.0, 0.0, 0.4, 1.0, 0.5], dtype=np.float32)
        assert np.allclose(vector, expected)

    def test_full_state_to_vector_uses_previous_prices(self):
        """A price change is reported when the state carries the previous price."""
        market = MarketState(
            prices={"AAPL": 150.0},
            previous_prices={"AAPL": 100.0},
            sentiment_scores={"AAPL": 0.5},
        )
        portfolio = PortfolioState(holdings={}, cash=1000.0)
        state = FullState(market=market, portfolio=portfolio)

        vector = state.to_vector(["AAPL"])
        # log(150 / 100) is the price change; nothing is held, so cash is all of it.
        assert np.allclose(vector, [math.log(1.5), 0.0, 0.0, 1.0])

    def test_full_state_to_vector_custom_sentiment_range(self):
        """A sentiment scale other than 0 to 1 is honoured by the normalization."""
        market = MarketState(
            prices={"AAPL": 150.0},
            sentiment_scores={"AAPL": 0.0},
            sentiment_min=-1.0,
            sentiment_max=1.0,
        )
        portfolio = PortfolioState(holdings={}, cash=1000.0)
        state = FullState(market=market, portfolio=portfolio)

        vector = state.to_vector(["AAPL"])
        # 0 sits at the centre of a -1..1 scale, so it normalizes to 0.
        assert np.allclose(vector, [0.0, 0.0, 0.0, 1.0])
    
    def test_full_state_to_vector_with_missing_symbols(self):
        """A symbol with no data falls back to neutral features, not raw prices."""
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        
        symbols = ["AAPL", "TSLA"]
        vector = state.to_vector(symbols)
        
        # No previous price and no holdings for either symbol; the missing
        # sentiment defaults to 0, which is the bottom of the 0..1 scale.
        expected = np.array([0.0, -1.0, 0.0, 0.0, -1.0, 0.0, 1.0], dtype=np.float32)
        assert np.array_equal(vector, expected)
    
    def test_full_state_to_vector_different_order(self):
        """to_vector lays symbols out in the order they are passed, not alphabetically."""
        market = MarketState(
            prices={"AAPL": 150.0, "TSLA": 700.0},
            previous_prices={"AAPL": 100.0, "TSLA": 700.0},
            sentiment_scores={"AAPL": 0.5, "TSLA": 0.7},
        )
        portfolio = PortfolioState(holdings={"TSLA": 5.0}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)

        cash_fraction = 5000.0 / (5000.0 + 5.0 * 700.0)
        forward = state.to_vector(["AAPL", "TSLA"])
        reverse = state.to_vector(["TSLA", "AAPL"])

        # AAPL: log(150/100), neutral sentiment, flat. TSLA: flat, 0.4 sentiment, held.
        assert np.allclose(forward, [math.log(1.5), 0.0, 0.0, 0.0, 0.4, 1.0, cash_fraction])
        assert np.allclose(reverse, [0.0, 0.4, 1.0, math.log(1.5), 0.0, 0.0, cash_fraction])

    def test_legacy_vector_keeps_the_raw_layout(self):
        """The pre-normalization features are still reachable for comparing runs."""
        market = MarketState(
            prices={"AAPL": 150.0},
            sentiment_scores={"AAPL": 0.5},
        )
        portfolio = PortfolioState(holdings={"AAPL": 10.0}, cash=100.0)
        state = FullState(market=market, portfolio=portfolio)

        assert state.legacy_vector(["AAPL"]).tolist() == [150.0, 10.0, 0.5, 100.0]

    def test_both_schemas_are_comparable(self):
        """The two layouts have the same length, so a run can be diffed feature by feature."""
        market = MarketState(
            prices={"AAPL": 150.0},
            sentiment_scores={"AAPL": 0.5},
        )
        portfolio = PortfolioState(holdings={"AAPL": 10.0}, cash=100.0)
        state = FullState(market=market, portfolio=portfolio)

        assert state.to_vector(["AAPL"], schema="legacy").shape == state.to_vector(["AAPL"], schema="normalized").shape

    def test_schema_flag_selects_the_layout(self, monkeypatch):
        """KDR_STATE_VECTOR_SCHEMA=legacy reproduces the raw features."""
        market = MarketState(prices={"AAPL": 150.0}, sentiment_scores={"AAPL": 0.5})
        portfolio = PortfolioState(holdings={"AAPL": 10.0}, cash=100.0)
        state = FullState(market=market, portfolio=portfolio)

        monkeypatch.setenv("KDR_STATE_VECTOR_SCHEMA", "legacy")
        assert state_vector_schema() == "legacy"
        assert state.to_vector(["AAPL"]).tolist() == [150.0, 10.0, 0.5, 100.0]

    def test_schema_defaults_to_normalized(self, monkeypatch):
        """An unset flag leaves the scaled features in effect."""
        monkeypatch.delenv("KDR_STATE_VECTOR_SCHEMA", raising=False)
        assert state_vector_schema() == "normalized"

    def test_unknown_schema_is_rejected(self, monkeypatch):
        """A typo in the flag fails loudly rather than silently picking a layout."""
        monkeypatch.setenv("KDR_STATE_VECTOR_SCHEMA", "normalised")
        with pytest.raises(ValueError) as exc:
            state_vector_schema()
        assert "KDR_STATE_VECTOR_SCHEMA" in exc.value.args[0]


class TestAction:
    """Tests for Action model."""
    
    def test_action_with_movement(self):
        """Test creating Action with movement."""
        action = Action(movement=TradeMovement.BUY, symbol="AAPL")
        assert action.movement == TradeMovement.BUY
        assert action.symbol == "AAPL"
        assert action.target_weights is None
    
    def test_action_with_target_weights(self):
        """Test creating Action with target_weights."""
        action = Action(target_weights={"AAPL": 0.6, "TSLA": 0.4})
        assert action.target_weights == {"AAPL": 0.6, "TSLA": 0.4}
        assert action.movement is None
        assert action.symbol is None
    
    def test_action_with_both_movement_and_target_weights(self):
        """Test creating Action with both attributes."""
        action = Action(
            movement=TradeMovement.SELL,
            symbol="AAPL",
            target_weights={"AAPL": 0.5, "TSLA": 0.5}
        )
        assert action.movement == TradeMovement.SELL
        assert action.symbol == "AAPL"
        assert action.target_weights == {"AAPL": 0.5, "TSLA": 0.5}


class TestTradeMovement:
    """Tests for TradeMovement enum."""
    
    def test_buy_movement(self):
        """Test BUY movement."""
        movement = TradeMovement.BUY
        # TradeMovement inherits from str and Enum, compare the value directly
        assert movement == "BUY"
    
    def test_sell_movement(self):
        """Test SELL movement."""
        movement = TradeMovement.SELL
        assert movement == "SELL"
    
    def test_hold_movement(self):
        """Test HOLD movement."""
        movement = TradeMovement.HOLD
        assert movement == "HOLD"