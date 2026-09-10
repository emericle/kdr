from src.domain import Action, FullState
import numpy as np
from typing import List, Callable, Protocol

class ValueFunction(Protocol):
    """Protocol for a value function that can predict and update."""
    def predict(self, state_vector: np.ndarray) -> float: ...
    def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None: ...

class BellmanEngine:
    """
    Core math engine that implements the Bellman equation logic.
    """
    def __init__(self, discount_factor: float = 0.99):
        self.discount_factor = discount_factor

    def compute_target_q(self, reward: float, next_state_max_q: float) -> float:
        """
        Computes the Bellman target: r + gamma * max(Q(s', a'))
        
        Args:
            reward: The immediate reward received after taking an action.
            next_state_max_q: The maximum Q-value for the next state.
            
        Returns:
            The computed Bellman target.
        """
        return reward + self.discount_factor * next_state_max_q

    def compute_action(self, state: FullState, symbols: List[str], policy_fn: Callable[[np.ndarray], Action]) -> Action:
        """
        Computes the optimal action based on a provided policy function.
        
        Args:
            state: The current full state of the environment.
            symbols: The list of symbols used to vectorize the state.
            policy_fn: A function that takes a state vector (np.ndarray) and returns an Action.
            
        Returns:
            The recommended action.
        """
        state_vector = state.to_vector(symbols)
        return policy_fn(state_vector)

class RLTrainer:
    """
    Handles the training loop using a BellmanEngine and a ValueFunction.
    """
    def __init__(
        self, 
        engine: BellmanEngine, 
        value_function: ValueFunction, 
        learning_rate: float = 0.01
    ):
        self.engine = engine
        self.vf = value_function
        self.learning_rate = learning_rate

    def train_step(
        self, 
        state_vector: np.ndarray, 
        reward: float, 
        next_state_vector: np.ndarray
    ) -> None:
        """
        Performs a single training step: calculates target and updates the value function.
        """
        # Predict the value of the next state
        next_v = self.vf.predict(next_state_vector)
        
        # Compute the Bellman target: r + gamma * V(s')
        target = self.engine.compute_target_q(reward, next_v)
        
        # Update the value function toward the target
        self.vf.update(state_vector, target, self.learning_rate)
