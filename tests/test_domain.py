import pytest

from src.domain import Action, FullState, MarketState, PortfolioState, TradeMovement


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
    # Normalized layout: log price change, sentiment on [-1, 1], position flag,
    # cash as a fraction of portfolio value. Vector length = len(symbols) * 3 + 1.
    vector = fs.to_vector(symbols)

    assert len(vector) == 4
    # No previous price, so the change is 0; sentiment 0.5 is the centre of 0..1.
    assert vector[0] == 0.0
    assert vector[1] == 0.0
    assert vector[2] == 1.0
    # Portfolio value is 100 cash + 10 * 150, so cash is a sixteenth of it.
    assert vector[3] == pytest.approx(100.0 / 1600.0)

    # The pre-normalization layout is still reachable, so an old and a new run
    # can be compared feature by feature.
    legacy = fs.to_vector(symbols, schema="legacy")
    assert legacy.tolist() == [150.0, 10.0, 0.5, 100.0]

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
