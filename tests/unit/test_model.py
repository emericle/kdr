import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from src.domain import Action, FullState, MarketState, PortfolioState
from src.model import BellmanEngine, RLTrainer
from typing import Callable


class TestValueFunctionProtocol:
    """Tests for the ValueFunction protocol implementation."""
    
    def test_value_function_predict(self):
        """Test a ValueFunction implementation with predict method."""
        class MockValueFunction:
            def predict(self, state_vector: np.ndarray) -> float:
                return float(state_vector[0])
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                pass
        
        vf = MockValueFunction()
        vector = np.array([100.0], dtype=np.float32)
        result = vf.predict(vector)
        assert result == 100.0
    
    def test_value_function_update(self):
        """Test a ValueFunction implementation with update method."""
        class MockValueFunction:
            def predict(self, state_vector: np.ndarray) -> float:
                return 0.0
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                self.last_update = (state_vector, target, learning_rate)
        
        vf = MockValueFunction()
        vector = np.array([50.0], dtype=np.float32)
        vf.update(vector, 100.0, 0.01)
        
        assert vf.last_update == (vector, 100.0, 0.01)


class TestBellmanEngine:
    """Tests for BellmanEngine."""
    
    def test_engine_initialization(self):
        """Test BellmanEngine initialization."""
        engine = BellmanEngine()
        assert engine.discount_factor == 0.99
    
    def test_engine_with_custom_discount(self):
        """Test BellmanEngine with custom discount factor."""
        engine = BellmanEngine(discount_factor=0.95)
        assert engine.discount_factor == 0.95
    
    def test_compute_target_q(self):
        """Test computing Bellman target Q-value."""
        engine = BellmanEngine(discount_factor=0.99)
        reward = 10.0
        next_state_max_q = 20.0
        result = engine.compute_target_q(reward, next_state_max_q)
        expected = 10.0 + 0.99 * 20.0
        assert result == expected
    
    def test_compute_target_q_zero_reward(self):
        """Test computing target Q with zero reward."""
        engine = BellmanEngine(discount_factor=0.5)
        reward = 0.0
        next_state_max_q = 100.0
        result = engine.compute_target_q(reward, next_state_max_q)
        expected = 0.0 + 0.5 * 100.0
        assert result == expected
    
    def test_compute_target_q_discount_one(self):
        """Test computing target Q with discount factor of 1.0."""
        engine = BellmanEngine(discount_factor=1.0)
        reward = 50.0
        next_state_max_q = 50.0
        result = engine.compute_target_q(reward, next_state_max_q)
        expected = 50.0 + 1.0 * 50.0
        assert result == expected
    
    def test_compute_action(self):
        """Test computing action from state."""
        engine = BellmanEngine()
        
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        
        def simple_policy(state_vector: np.ndarray) -> Action:
            return Action(target_weights={"AAPL": 1.0})
        
        symbols = ["AAPL"]
        action = engine.compute_action(state, symbols, simple_policy)
        
        assert action.target_weights == {"AAPL": 1.0}
    
    def test_compute_action_with_no_symbols(self):
        """Test compute_action with empty symbols list returns action."""
        engine = BellmanEngine()
        
        market = MarketState(prices={"AAPL": 150.0})
        portfolio = PortfolioState(holdings={}, cash=5000.0)
        state = FullState(market=market, portfolio=portfolio)
        
        def policy(state_vector: np.ndarray) -> Action:
            return Action()
        
        symbols = []
        action = engine.compute_action(state, symbols, policy)
        
        assert action is not None


class TestRLTrainer:
    """Tests for RLTrainer."""
    
    def test_trainer_initialization(self):
        """Test RLTrainer initialization."""
        engine = BellmanEngine()
        
        class MockVF:
            def predict(self, state_vector: np.ndarray) -> float:
                return 0.0
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                pass
        
        trainer = RLTrainer(engine, MockVF())
        assert trainer.engine == engine
        assert trainer.learning_rate == 0.01
    
    def test_trainer_with_custom_learning_rate(self):
        """Test RLTrainer with custom learning rate."""
        engine = BellmanEngine()
        
        class MockVF:
            def predict(self, state_vector: np.ndarray) -> float:
                return 0.0
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                pass
        
        trainer = RLTrainer(engine, MockVF(), learning_rate=0.05)
        assert trainer.learning_rate == 0.05
    
    def test_train_step(self):
        """Test performing a single training step."""
        engine = BellmanEngine(discount_factor=0.99)
        
        class MockValueFunction:
            def __init__(self):
                self.last_update = None
                self.predict_count = 0
                self.update_count = 0
            
            def predict(self, state_vector: np.ndarray) -> float:
                self.predict_count += 1
                return float(state_vector.sum())
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                self.update_count += 1
                self.last_update = (target, learning_rate)
        
        vf = MockValueFunction()
        trainer = RLTrainer(engine, vf, learning_rate=0.1)
        
        state_vector = np.array([50.0, 10.0], dtype=np.float32)
        next_state_vector = np.array([60.0, 12.0], dtype=np.float32)
        reward = 1.0
        
        trainer.train_step(state_vector, reward, next_state_vector)
        
        assert vf.predict_count == 1
        assert vf.update_count == 1
    
    def test_train_step_with_zero_reward(self):
        """Test training step with zero reward."""
        engine = BellmanEngine(discount_factor=0.5)
        
        class MockValueFunction:
            def predict(self, state_vector: np.ndarray) -> float:
                return 0.0
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                pass
        
        vf = MockValueFunction()
        trainer = RLTrainer(engine, vf)
        
        state_vector = np.array([50.0], dtype=np.float32)
        next_state_vector = np.array([60.0], dtype=np.float32)
        reward = 0.0
        
        trainer.train_step(state_vector, reward, next_state_vector)
        
        assert True  # Should not raise exception
    
    def test_train_step_with_negative_reward(self):
        """Test training step with negative reward."""
        engine = BellmanEngine(discount_factor=0.99)
        
        class MockValueFunction:
            def __init__(self):
                self.last_update = None
            
            def predict(self, state_vector: np.ndarray) -> float:
                return 0.0
            
            def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
                self.last_update = (target, learning_rate)
        
        vf = MockValueFunction()
        trainer = RLTrainer(engine, vf, learning_rate=0.01)
        
        state_vector = np.array([50.0], dtype=np.float32)
        next_state_vector = np.array([60.0], dtype=np.float32)
        reward = -5.0
        
        trainer.train_step(state_vector, reward, next_state_vector)
        
        assert vf.last_update[0] < 0.0


class MockValueFunction:
    """Simple mock value function for testing."""
    def __init__(self):
        self.last_update = None
    
    def predict(self, state_vector: np.ndarray) -> float:
        return 0.0
    
    def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None:
        self.last_update = (state_vector, target, learning_rate)