"""Replay stored market data through the decision model as a paper portfolio.

This is the harness the spec asks for (item 5, "Validating the implementation
via backtesting"): historical bars drive the same state and action space the
live system uses, so the value function can be scored on data whose outcome is
already known. Nothing here touches the broker or the live buffers.

Run it with::

    BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest

``BACKTEST_SYMBOLS`` is required; ``BACKTEST_CASH`` (default 100000) and
``BACKTEST_MINUTES`` (default 1440) are optional.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from typing import Callable, Dict, List, Tuple

import numpy as np

from src.database import DatabaseManager
from src.domain import Action, FullState, MarketState, PortfolioState, candidate_actions
from src.model import BellmanEngine, LinearValueFunction
from src.sentiment_adapter import AdanosDataAdapter, get_adanos_api_key
from src.times import from_epoch, now_utc

logger = logging.getLogger(__name__)

POSITION_WEIGHT = 0.25
NEUTRAL_SENTIMENT = 0.5


def _transition(
    action: Action,
    state: FullState,
    position_weight: float = POSITION_WEIGHT,
) -> Tuple[float, FullState]:
    """Apply one action to one state and report the reward that produced it.

    Reward is the signed price change the agent captured: buying a bar that rose
    is positive, selling a bar that fell is positive, holding is always zero. The
    successor carries the prices this step observed as its previous prices, so the
    next bar's change is measured against them.

    Args:
        action: The movement to apply.
        state: The state the action is applied to.
        position_weight: Fraction of available cash a BUY spends, and of portfolio
            value a SELL releases.

    Returns:
        The immediate reward and the state that follows.
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


class Backtester:
    """Replays historical bars and reports what the policy would have earned."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        symbols: List[str],
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

    def load_bars(self, symbol: str, minutes: int = 1_440) -> List[dict]:
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

    def run(self, minutes: int = 1_440) -> dict:
        """Replay the last ``minutes`` of bars for every configured symbol.

        Returns:
            A report with the number of decisions, the starting and ending value
            of the paper portfolio, the return, the mean reward, and one entry
            per decision.
        """
        bars_by_symbol = {s: self.load_bars(s, minutes) for s in self.symbols}
        bar_times = sorted({b["timestamp"] for bars in bars_by_symbol.values() for b in bars})
        if not bar_times:
            return {"steps": 0, "initial_value": self.initial_cash, "final_value": self.initial_cash,
                    "return": 0.0, "mean_reward": 0.0, "decisions": []}

        prices: Dict[str, float] = {}
        previous_prices: Dict[str, float] = {}
        for s in self.symbols:
            bars = bars_by_symbol[s]
            if bars:
                prices[s] = bars[0]["close"]
                previous_prices[s] = bars[0]["close"]
        if not prices:
            return {"steps": 0, "initial_value": self.initial_cash, "final_value": self.initial_cash,
                    "return": 0.0, "mean_reward": 0.0, "decisions": []}

        state = FullState(
            market=MarketState(
                prices=prices,
                previous_prices=previous_prices,
                sentiment_scores={s: self.sentiment_for(s) for s in prices},
            ),
            portfolio=PortfolioState(cash=self.initial_cash, holdings={}),
            timestamp=from_epoch(min(bar_times)),
        )

        transition: Callable[[Action, FullState], Tuple[float, FullState]] = (
            lambda a, s: _transition(a, s, self.position_weight)
        )
        decisions: List[dict] = []
        rewards: List[float] = []
        for t in bar_times:
            for symbol in self.symbols:
                bar = next((b for b in bars_by_symbol[symbol] if b["timestamp"] == t), None)
                if bar is None:
                    continue
                state.market.prices[symbol] = bar["close"]
                candidates = candidate_actions([symbol], {})
                action = self.engine.compute_action(state, state.symbols, candidates, transition, self.value_fn.predict)
                reward, state = transition(action, state)
                rewards.append(reward)
                decisions.append(
                    {
                        "timestamp": t,
                        "symbol": symbol,
                        "action": action.movement,
                        "reward": reward,
                        "portfolio_value": state.portfolio_value(),
                    }
                )

        final_value = state.portfolio_value()
        return {
            "steps": len(rewards),
            "initial_value": self.initial_cash,
            "final_value": final_value,
            "return": final_value - self.initial_cash,
            "mean_reward": float(np.mean(rewards)) if rewards else 0.0,
            "decisions": decisions,
        }


def summarize(report: dict) -> str:
    """One line summary of a backtest report."""
    return (
        f"{report['steps']} decisions, {report['initial_value']:.0f} -> "
        f"{report['final_value']:.0f} ({report['return']:+.0f})"
    )


def main() -> None:
    """Run a backtest from the command line using DATABASE_URL and BACKTEST_*."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    symbols = [s.strip() for s in os.environ.get("BACKTEST_SYMBOLS", "").split(",") if s.strip()]
    if not symbols:
        logger.error("Set BACKTEST_SYMBOLS (comma separated) to run a backtest")
        return
    cash = float(os.environ.get("BACKTEST_CASH", "100000"))
    minutes = int(os.environ.get("BACKTEST_MINUTES", "1440"))
    db_manager = DatabaseManager()
    report = Backtester(
        db_manager,
        symbols,
        BellmanEngine(),
        LinearValueFunction(),
        initial_cash=cash,
    ).run(minutes=minutes)
    logger.info("Backtest: %s", summarize(report))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
