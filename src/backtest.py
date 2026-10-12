"""Replay stored market data through the decision model as a paper portfolio.

This is the harness the spec asks for (item 5, "Validating the implementation
via backtesting"): historical bars drive the same state and action space the
live system uses, so the value function can be scored on data whose outcome is
already known. Nothing here touches the broker or the live buffers.

Run it with::

    BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest

``BACKTEST_SYMBOLS`` is required; ``BACKTEST_CASH`` (default 100000) and
``BACKTEST_MINUTES`` (default 1440) are optional. ``BACKTEST_TRAIN_EPOCHS``
(default 0) additionally fits the value function to the same history and stores
the weights in the ``model_weights`` table, which is what the live path loads::

    BACKTEST_SYMBOLS=AAPL,TSLA BACKTEST_TRAIN_EPOCHS=3 python -m src.backtest
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import numpy as np

from src.database import DatabaseManager
from src.domain import Action, FullState, MarketState, PortfolioState, candidate_actions
from src.logging_config import configure_logging
from src.model import POSITION_WEIGHT, BellmanEngine, LinearValueFunction, RLTrainer, portfolio_transition
from src.sentiment_adapter import AdanosDataAdapter, get_adanos_api_key
from src.times import from_epoch, now_utc

logger = logging.getLogger(__name__)

NEUTRAL_SENTIMENT = 0.5


class Backtester:
    """Replays historical bars and reports what the policy would have earned."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        symbols: list[str],
        engine: BellmanEngine,
        value_fn,
        initial_cash: float = 100_000.0,
        position_weight: float = POSITION_WEIGHT,
    ):
        self.db_manager = db_manager
        self.symbols = symbols
        self.engine = engine
        self.value_fn = value_fn
        self.initial_cash = initial_cash
        self.position_weight = position_weight

    def load_bars(self, symbol: str, minutes: int = 1_440) -> list[dict]:
        """Historical bars for one symbol, oldest first."""
        start_time = now_utc() - timedelta(minutes=minutes)
        return self.db_manager.get_historical_market_data(symbol=symbol, start_time=start_time)

    def sentiment_for(self, symbol: str) -> float:
        """Latest sentiment score for a symbol, neutral when none is available."""
        if not get_adanos_api_key():
            return NEUTRAL_SENTIMENT
        records = AdanosDataAdapter.fetch_sentiment([symbol])
        if not records:
            return NEUTRAL_SENTIMENT
        return float(records[-1].get("score", NEUTRAL_SENTIMENT))

    def replay(
        self,
        minutes: int = 1_440
    ) -> tuple[list[dict], list[tuple[np.ndarray, float, np.ndarray]]]:
        """Walk the last ``minutes`` of bars for every symbol through the decision model.

        Returns:
            The decisions (one entry per bar: the timestamp, the symbol, the
            movement chosen, the reward it captured and the portfolio value after
            it) and the transitions (the ``(state vector, reward, next state
            vector)`` triples a training step fits). One walk answers both
            questions, so the report and the training pass cannot drift apart.
        """
        bars_by_symbol = {s: self.load_bars(s, minutes) for s in self.symbols}
        bar_times = sorted({b["timestamp"] for bars in bars_by_symbol.values() for b in bars})
        if not bar_times:
            return [], []

        prices: dict[str, float] = {}
        previous_prices: dict[str, float] = {}
        for s in self.symbols:
            bars = bars_by_symbol[s]
            if bars:
                prices[s] = bars[0]["close"]
                previous_prices[s] = bars[0]["close"]
        if not prices:
            return [], []

        state = FullState(
            market=MarketState(
                prices=prices,
                previous_prices=previous_prices,
                sentiment_scores={s: self.sentiment_for(s) for s in prices},
            ),
            portfolio=PortfolioState(cash=self.initial_cash, holdings={}),
            timestamp=from_epoch(min(bar_times)),
        )

        def step(action: Action, current: FullState) -> tuple[float, FullState]:
            return portfolio_transition(action, current, self.position_weight)

        decisions: list[dict] = []
        transitions: list[tuple[np.ndarray, float, np.ndarray]] = []
        for t in bar_times:
            for symbol in self.symbols:
                bar = next((b for b in bars_by_symbol[symbol] if b["timestamp"] == t), None)
                if bar is None:
                    continue
                state.market.prices[symbol] = bar["close"]
                candidates = candidate_actions([symbol], {})
                vector = state.to_vector(state.symbols)
                action = self.engine.compute_action(
                    state, state.symbols, candidates, step, self.value_fn.predict
                )
                reward, state = step(action, state)
                transitions.append((vector, reward, state.to_vector(state.symbols)))
                decisions.append(
                    {
                        "timestamp": t,
                        "symbol": symbol,
                        "action": action.movement,
                        "reward": reward,
                        "portfolio_value": state.portfolio_value(),
                    }
                )
        return decisions, transitions

    def run(self, minutes: int = 1_440) -> dict:
        """Replay the last ``minutes`` of bars as a paper portfolio.

        Returns:
            A report with the number of decisions, the starting and ending value
            of the paper portfolio, the return, the mean reward, and one entry
            per decision.
        """
        decisions, transitions = self.replay(minutes)
        rewards = [reward for _, reward, _ in transitions]
        final_value = decisions[-1]["portfolio_value"] if decisions else self.initial_cash
        return {
            "steps": len(rewards),
            "initial_value": self.initial_cash,
            "final_value": final_value,
            "return": final_value - self.initial_cash,
            "mean_reward": float(np.mean(rewards)) if rewards else 0.0,
            "decisions": decisions,
        }

    def train(self, trainer: RLTrainer, minutes: int = 1_440, epochs: int = 1) -> dict:
        """Fit the value function to the last ``minutes`` of bars.

        Each epoch replays the same history with the weights the previous epoch
        produced, so the transition a step learns from is the one the current
        policy took — the on-policy assumption TD(0) makes.

        Returns:
            A report with the epochs run, the steps applied and the mean reward
            of the last epoch.
        """
        steps = 0
        rewards: list[float] = []
        for _ in range(epochs):
            _decisions, transitions = self.replay(minutes)
            steps += trainer.fit(transitions)
            rewards = [reward for _, reward, _ in transitions]
        return {
            "epochs": epochs,
            "steps": steps,
            "mean_reward": float(np.mean(rewards)) if rewards else 0.0,
        }


def summarize(report: dict) -> str:
    """One line summary of a backtest report."""
    return (
        f"{report['steps']} decisions, {report['initial_value']:.0f} -> "
        f"{report['final_value']:.0f} ({report['return']:+.0f})"
    )


def main() -> None:
    """Run a backtest, and optionally a training pass, from the command line.

    ``BACKTEST_TRAIN_EPOCHS`` (default 0) also fits the value function to the
    same history and stores the weights in the ``model_weights`` table under the
    symbol list the vector covers, which is what the live path loads.
    """
    configure_logging()
    symbols = [s.strip() for s in os.environ.get("BACKTEST_SYMBOLS", "").split(",") if s.strip()]
    if not symbols:
        logger.error("Set BACKTEST_SYMBOLS (comma separated) to run a backtest")
        return
    cash = float(os.environ.get("BACKTEST_CASH", "100000"))
    minutes = int(os.environ.get("BACKTEST_MINUTES", "1440"))
    epochs = int(os.environ.get("BACKTEST_TRAIN_EPOCHS", "0"))

    db_manager = DatabaseManager()
    value_fn = LinearValueFunction()
    backtester = Backtester(
        db_manager,
        symbols,
        BellmanEngine(),
        value_fn,
        initial_cash=cash,
    )
    report = backtester.run(minutes=minutes)
    logger.info("Backtest: %s", summarize(report))
    print(json.dumps(report, indent=2, default=str))

    if epochs <= 0:
        return

    trained = backtester.train(RLTrainer(BellmanEngine(), value_fn), minutes=minutes, epochs=epochs)
    key = ",".join(symbols)
    if db_manager.save_model_weights(key, value_fn.to_dict()):
        logger.info("Trained %s steps over %s; weights stored for %s", trained["steps"], key, key)
    else:
        logger.warning("No database to store the trained weights in")
    print(json.dumps(trained, indent=2, default=str))


if __name__ == "__main__":
    main()
