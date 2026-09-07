from src.domain import MarketState, PortfolioState, FullState, Action, TradeMovement
import numpy as np
import pytest

def test_market_state_validation():
    # Test prices not empty
    with pytest.raises(ValueError, match="Prices cannot be empty"):
        MarketState(prices={})

    ms = MarketState(prices={"AAPL": 150.0}, sentiment_scores={"AAPL": 0.8})
    assert ms.prices["AAPL"] == 150.0
    assert ms.sentiment_scores["AAPL"] == 0.8

def test_portfolio_state_validation():
    # Test holdings not negative
    with pytest.raises(ValueError, match="Holding for AAPL cannot be negative"):
        PortfolioState(holdings={"AAPL": -1.0}, cash=100.0)

    ps = PortfolioState(holdings={"AAPL": 10.0}, cash=50.0)
    assert ps.holdings["AAPL"] == 10.0
    assert ps.cash == 50.0

def test_full_state_vectorization():
    ms = MarketState(prices={"AAPL": 150.0}, sentiment_scores={"AAPL": 0.5})
    ps = PortfolioState(holdings={"AAPL": 10.0}, cash=100.0)
    fs = FullState(market=ms, portfolio=ps, timestamp=1625000000.0)

    symbols = ["AAPL"]
    # Expected vector: [price, holding, sentiment, cash]
    # Vector length = len(symbols) * 3 + 1 = 1 * 3 + 1 = 4
    vector = fs.to_vector(symbols)
    
    assert len(vector) == 4
    assert vector[0] == 150.0
    assert vector[1] == 10.0
    assert vector[2] == 0.5
    assert vector[3] == 100.0

def test_action_creation():
    # Discrete action
    a1 = Action(movement=TradeMovement.BUY, symbol="AAPL")
    assert a1.movement == TradeMovement.BUY
    assert a1.symbol == "AAPL"

    # Continuous action
    a2 = Action(target_weights={"AAPL": 0.5})
    assert a2.target_weights == {"AAPL": 0.5}
    assert a2.movement is None

if __name__ == "__main__":
    pytest.main([__file__])
