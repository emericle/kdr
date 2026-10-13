"""Bellman decision model: value functions, the Bellman engine, and the training loop."""
import math
from collections.abc import Callable, Iterable
from typing import Protocol

import numpy as np

from src.domain import Action, FullState


class ValueFunction(Protocol):
    """Protocol for a value function that can predict and update."""
    def predict(self, state_vector: np.ndarray) -> float: ...
    def update(self, state_vector: np.ndarray, target: float, learning_rate: float) -> None: ...


class LinearValueFunction:
    """Concrete TD(0) value function: ``V(s) = w·s + b``.

    This is the value function the spec's training loop fits: :meth:`update`
    moves the weights toward a Bellman target by the learning rate, which is the
    TD(0) step :class:`RLTrainer` asks for. Weights are sized on first contact
    with a state vector, so a caller only has to say how many symbols it trades.
    """

    def __init__(self, weights: np.ndarray | None = None, bias: float = 0.0):
        self.weights: np.ndarray | None = (
            np.asarray(weights, dtype=np.float32) if weights is not None else None
        )
        self.bias = float(bias)

    def predict(self, state_vector: np.ndarray) -> float:
        """Estimate the value of ``state_vector``."""
        vector = np.asarray(state_vector, dtype=np.float32)
        if self.weights is None:
            self.weights = np.zeros_like(vector)
        return float(np.dot(self.weights, vector) + self.bias)

    def update(
        self,
        state_vector: np.ndarray,
        target: float,
        learning_rate: float,
    ) -> None:
        """Nudge the weights toward ``target`` by ``learning_rate`` of the error."""
        vector = np.asarray(state_vector, dtype=np.float32)
        if self.weights is None:
            self.weights = np.zeros_like(vector)
        error = float(target) - self.predict(vector)
        self.weights = self.weights + learning_rate * error * vector
        self.bias = self.bias + learning_rate * error


class BellmanEngine:
    """
    Core math engine that implements the Bellman equation logic.
    """
    def __init__(self, discount_factor: float = 0.99):
        self.discount_factor = discount_factor

    def compute_target_q(self, reward: float, next_state_max_q: float) -> float:
        """
        Computes the Bellman target: r + gamma * best_value_of_next_state

        Args:
            reward: The immediate reward received after taking an action.
            next_state_max_q: The best value available for the next state.

        Returns:
            The computed Bellman target.
        """
        return reward + self.discount_factor * next_state_max_q

    def compute_action(
        self,
        state: FullState,
        symbols: list[str],
        candidates: Iterable[Action],
        transition: Callable[[Action, FullState], tuple[float, FullState]],
        value_fn: Callable[[np.ndarray], float],
    ) -> Action:
        """
        Chooses the action with the best one-step Bellman value.

        Each candidate is scored as ``r + gamma * V(s')``, where ``transition``
        supplies the immediate reward and the state that follows. The discount
        factor therefore shapes the decision: a small ``discount_factor`` leans
        on the immediate reward, a large one leans on the future.

        Args:
            state: The current full state of the environment.
            symbols: The symbols used to vectorize every state.
            candidates: The actions to consider.
            transition: Maps (action, state) to (immediate reward, next state).
            value_fn: Estimates the value of a state vector.

        Returns:
            The candidate with the highest Bellman value.

        Raises:
            ValueError: If ``candidates`` is empty.
        """
        best_action: Action | None = None
        best_q = -math.inf

        for action in candidates:
            reward, next_state = transition(action, state)
            q_value = self.compute_target_q(reward, value_fn(next_state.to_vector(symbols)))
            if q_value > best_q:
                best_q = q_value
                best_action = action

        if best_action is None:
            raise ValueError("compute_action requires at least one candidate action")

        return best_action


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
