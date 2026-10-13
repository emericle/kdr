"""Bellman decision model: value functions, the Bellman engine, and the training loop.

``from __future__ import annotations`` is not cosmetic here: ``from_dict`` below
annotates its return with the class it is written inside, and a class body runs
before that name is bound. 3.14 evaluates annotations where they are written only
when asked, 3.13 evaluates them immediately, and this module has to work on both.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Protocol

import numpy as np

from src.domain import Action, FullState, MarketState, PortfolioState

logger = logging.getLogger(__name__)


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

    def to_dict(self) -> dict:
        """The parameters as JSON-ready values, for the ``model_weights`` table."""
        return {
            "weights": [float(w) for w in self.weights] if self.weights is not None else [],
            "bias": float(self.bias),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> LinearValueFunction:
        """Rebuild a value function from :meth:`to_dict`.

        An empty payload is an untrained function rather than an error: a process
        that has never trained still has to decide, and zero weights mean "score
        on the immediate reward", which is a working policy.
        """
        weights = payload.get("weights") or None
        return cls(weights=weights, bias=float(payload.get("bias", 0.0)))


def load_value_function(db_manager, symbols: list[str]) -> LinearValueFunction:
    """The persisted value function for these symbols, or an untrained one.

    The key is the symbol list the state vector is laid out over, which is what
    makes a stored vector of another length a loud mismatch rather than a
    decision scored against nonsense: a function whose weights do not fit the
    vector is dropped, and the caller starts over from zero.
    """
    key = ",".join(symbols)
    payload = db_manager.get_model_weights(key)
    if not isinstance(payload, dict):
        return LinearValueFunction()
    restored = LinearValueFunction.from_dict(payload)
    expected = 3 * len(symbols) + 1
    if restored.weights is not None and len(restored.weights) != expected:
        logger.warning(
            "Stored weights for %s cover %d features, not %d; starting untrained",
            key,
            len(restored.weights),
            expected,
        )
        return LinearValueFunction()
    return restored


POSITION_WEIGHT = 0.25


def portfolio_transition(
    action: Action,
    state: FullState,
    position_weight: float = POSITION_WEIGHT,
) -> tuple[float, FullState]:
    """Apply one action to one state and report the reward that produced it.

    This is the environment's reward model, and it is the same one for the live
    path and for the backtest: the reward is the signed price change the action
    captured, so buying a bar that rose is positive, selling a bar that fell is
    positive and holding is zero. The successor state carries the prices this
    step observed as its previous prices, which is what makes the next bar's
    change measurable.

    A BUY spends ``position_weight`` of available cash; a SELL releases the same
    fraction of the portfolio's total value. Neither is a broker order: this is
    the model's own accounting, used to score a candidate before it is chosen.

    Args:
        action: The movement to apply.
        state: The state the action is applied to.
        position_weight: Fraction of cash a BUY spends, and of portfolio value a
            SELL releases.

    Returns:
        The immediate reward and the state that follows.

    Raises:
        ValueError: If ``action`` does not name a symbol, which is what makes the
            reward ambiguous.
    """
    symbol = action.symbol
    if symbol is None:
        raise ValueError("transition requires an action that names a symbol")
    price = state.market.prices[symbol]
    previous = state.market.previous_prices.get(symbol, price)
    change = price - previous
    if action.movement == "BUY":
        reward = change
    elif action.movement == "SELL":
        reward = -change
    else:
        reward = 0.0

    holdings = dict(state.portfolio.holdings)
    cash = state.portfolio.cash
    held = holdings.get(symbol, 0.0)
    if action.movement == "BUY" and price > 0:
        quantity = (cash * position_weight) / price
        holdings[symbol] = held + quantity
        cash -= quantity * price
    elif action.movement == "SELL" and held > 0:
        quantity = held if price <= 0 else min(held, (state.portfolio_value() * position_weight) / price)
        holdings[symbol] = held - quantity
        cash += quantity * price
    if cash <= 0:
        holdings = {s: q for s, q in holdings.items() if q > 0}

    next_market = MarketState(
        prices=dict(state.market.prices),
        previous_prices={s: state.market.prices[s] for s in state.symbols},
        sentiment_scores=dict(state.market.sentiment_scores),
        sentiment_min=state.market.sentiment_min,
        sentiment_max=state.market.sentiment_max,
    )
    next_portfolio = PortfolioState(cash=max(cash, 0.0), holdings=holdings)
    return reward, FullState(market=next_market, portfolio=next_portfolio, timestamp=state.timestamp)


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

    def score_actions(
        self,
        state: FullState,
        symbols: list[str],
        candidates: Iterable[Action],
        transition: Callable[[Action, FullState], tuple[float, FullState]],
        value_fn: Callable[[np.ndarray], float],
    ) -> list[tuple[Action, float]]:
        """Score every candidate as ``r + γ·V(s')`` and return the pairs in candidate order.

        :meth:`compute_action` is the argmax of this list; a caller that needs the
        margin between the winner and the rest (the dashboard's confidence, the
        live path's log line) asks for the scores themselves rather than scoring
        twice.

        Args:
            state: The current full state of the environment.
            symbols: The symbols used to vectorize every state.
            candidates: The actions to consider.
            transition: Maps (action, state) to (immediate reward, next state).
            value_fn: Estimates the value of a state vector.

        Returns:
            One ``(action, q_value)`` pair per candidate, in candidate order.
        """
        scored: list[tuple[Action, float]] = []
        for action in candidates:
            reward, next_state = transition(action, state)
            q_value = self.compute_target_q(reward, value_fn(next_state.to_vector(symbols)))
            scored.append((action, q_value))
        return scored

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

        Each candidate is scored as ``r + gamma * V(s')`` by :meth:`score_actions`,
        and the highest score wins; a tie keeps the earlier candidate. The discount
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
        scored = self.score_actions(state, symbols, candidates, transition, value_fn)
        if not scored:
            raise ValueError("compute_action requires at least one candidate action")
        return max(scored, key=lambda pair: pair[1])[0]


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

    def fit(
        self,
        transitions: Iterable[tuple[np.ndarray, float, np.ndarray]]
    ) -> int:
        """Run one pass of TD(0) over ``(state, reward, next_state)`` triples.

        The triples are what a replay of stored bars produces, which is why
        training and backtesting share one walk of the history: the transition a
        step learns from is the one the current policy actually took, and that is
        the on-policy assumption TD(0) makes.

        Args:
            transitions: One ``(state vector, immediate reward, next state vector)``
                per step, in the order the policy took them.

        Returns:
            The number of steps applied.
        """
        steps = 0
        for state_vector, reward, next_state_vector in transitions:
            self.train_step(state_vector, reward, next_state_vector)
            steps += 1
        return steps
