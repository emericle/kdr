from src.domain import Action, FullState, TradeMovement
from src.model import BellmanEngine
import numpy as np

def test_bellman_engine_returns_action():
    # Setup
    symbols = ["AAPL", "GOOGL"]
    market_prices = {"AAPL": 150.0, "GOOGL": 2800.0}
    market_sentiment = {"AAPL": 0.5, "GOOGL": 0.5}
    
    # We need to manually construct because FullState expects MarketState and PortfolioState
    from src.domain import MarketState, PortfolioState
    market = MarketState(prices=market_prices, sentiment_scores=market_sentiment)
    portfolio = PortfolioState(holdings={"AAPL": 10.0, "GOOGL": 5.0}, cash=1000.0)
    state = FullState(market=market, portfolio=portfolio, timestamp=1672531200.0)
    
    engine = BellmanEngine(discount_factor=0.99)
    
    # Define a dummy policy function
    def dummy_policy(state_vector: np.ndarray) -> Action:
        return Action(movement=TradeMovement.HOLD, symbol="AAPL")

    action = engine.compute_action(state, symbols, policy_fn=dummy_policy)
    
    assert isinstance(action, Action)
    assert action.movement == TradeMovement.HOLD
    assert action.symbol == "AAPL"

def test_bellman_target_calculation():
    engine = BellmanEngine(discount_factor=0.9)
    reward = 10.0
    next_max_q = 50.0
    
    # target = reward + gamma * next_max_q
    # target = 10.0 + 0.9 * 50.0 = 10.0 + 45.0 = 55.0
    target = engine.compute_target_q(reward, next_max_q)
    
    assert target == 55.0
