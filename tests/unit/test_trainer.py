from src.model import BellmanEngine, RLTrainer
import numpy as np
from src.domain import Action, FullState, TradeMovement

class MockValueFunction:
    def __init__(self, vector_size: int):
        self.weights = np.zeros(vector_size)
    def predict(self, state_vector: np.ndarray) -> float:
        return np.dot(self.weights, state_vector)
    def update(self, state_vector: np.ndarray, target: float, learning_rate: float):
        prediction = self.predict(state_vector)
        error = target - prediction
        self.weights += learning_rate * error * state_vector

def test_trainer_step():
    engine = BellmanEngine(discount_factor=0.9)
    # state vector size: 2 symbols * 3 + 1 = 7
    vf = MockValueFunction(7)
    trainer = RLTrainer(engine, vf)
    
    state_vec = np.array([1.0, 0.0, 0.5, 0.0, 0.0, 0.5, 100.0], dtype=np.float32)
    reward = 10.0
    next_state_vec = np.array([1.1, 0.1, 0.5, 0.0, 0.0, 0.5, 110.0], dtype=np.float32)
    
    initial_weights = vf.weights.copy()
    trainer.train_step(state_vec, reward, next_state_vec)
    
    assert not np.array_equal(vf.weights, initial_weights)
