import pytest
import numpy as np
from pydantic import ValidationError
from src.domain import (
    MarketState, 
    PortfolioState, 
    FullState, 
    Action, 
    TradeMovement
)


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
        assert state.timestamp == 1234567890.0
    
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
        
        # Vector should have: [price_AAPL, holdings_AAPL, sentiment_AAPL, price_TSLA, holdings_TSLA, sentiment_TSLA, cash]
        expected = np.array([150.0, 10.0, 0.5, 700.0, 5.0, 0.7, 5000.0], dtype=np.float32)
        assert np.array_equal(vector, expected)
    
    def test_full_state_to_vector_with_missing_symbols(self):
        """Test to_vector with missing symbols returns zeros."""
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        
        symbols = ["AAPL", "TSLA"]
        vector = state.to_vector(symbols)
        
        # TSLA should have zeros for missing data
        expected = np.array([150.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5000.0], dtype=np.float32)
        assert np.array_equal(vector, expected)
    
    def test_full_state_to_vector_different_order(self):
        """Test to_vector respects symbol order."""
        market = MarketState(prices={"AAPL": 150.0, "TSLA": 700.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        
        symbols = ["TSLA", "AAPL"]
        vector = state.to_vector(symbols)
        
        expected = np.array([700.0, 0.0, 0.0, 150.0, 0.0, 0.0, 5000.0], dtype=np.float32)
        assert np.array_equal(vector, expected)


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