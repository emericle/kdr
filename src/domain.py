"""Domain models for the trading state and the actions the agent can take."""
from __future__ import annotations

import math
import os
from datetime import datetime
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.times import to_utc

# What a caller may hand to a timestamp field: epoch seconds, ISO text or a
# datetime. :meth:`FullState.timestamp` normalizes all three to UTC-aware.
TimestampInput = datetime | float | int | str | None

STATE_VECTOR_SCHEMA_ENV = "KDR_STATE_VECTOR_SCHEMA"
DEFAULT_STATE_VECTOR_SCHEMA = "normalized"
STATE_VECTOR_SCHEMAS = ("legacy", "normalized")


def state_vector_schema() -> str:
    """Which state vectorization is in effect: ``legacy`` or ``normalized``.

    Read when asked rather than at import, so a flag set in ``.env`` or on the
    command line is visible to whatever imports this module. ``legacy`` is the
    pre-normalization layout (raw prices, raw holdings, raw sentiment, raw cash)
    and exists so a run can be compared against the old model; ``normalized``
    is the default.
    """
    raw = os.getenv(STATE_VECTOR_SCHEMA_ENV, DEFAULT_STATE_VECTOR_SCHEMA).strip().lower()
    if raw not in STATE_VECTOR_SCHEMAS:
        raise ValueError(
            f"{STATE_VECTOR_SCHEMA_ENV} must be one of {STATE_VECTOR_SCHEMAS}, "
            f"got {raw!r}"
        )
    return raw


class TradeMovement(StrEnum):
    """Discrete trade movements."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketState(BaseModel):
    """Market conditions at a specific point in time.

    ``sentiment_min`` and ``sentiment_max`` bound the sentiment scale the scores
    were produced on (Adanos reports 0 to 1). They are configuration, not
    measured data: they let :meth:`FullState.to_vector` put sentiment on the same
    footing as the other features.
    """
    prices: dict[str, float] = Field(default_factory=dict)
    previous_prices: dict[str, float] = Field(default_factory=dict)
    sentiment_scores: dict[str, float] = Field(default_factory=dict)
    sentiment_min: float = 0.0
    sentiment_max: float = 1.0

    @field_validator("prices")
    @classmethod
    def prices_not_empty(cls, v):
        if not v:
            raise ValueError("Prices cannot be empty")
        return v

    @field_validator("sentiment_max")
    @classmethod
    def sentiment_range_ordered(cls, v, info):
        if v <= info.data["sentiment_min"]:
            raise ValueError("sentiment_max must be greater than sentiment_min")
        return v

    def normalize_sentiment(self, score: float) -> float:
        """Map a raw sentiment score onto roughly [-1, 1]."""
        center = (self.sentiment_min + self.sentiment_max) / 2.0
        half = (self.sentiment_max - self.sentiment_min) / 2.0
        return (score - center) / half


class PortfolioState(BaseModel):
    """Current state of the investor's portfolio."""
    holdings: dict[str, float] = Field(default_factory=dict)
    cash: float = Field(..., ge=0.0)

    @field_validator("holdings")
    @classmethod
    def holdings_not_negative(cls, v):
        for symbol, qty in v.items():
            if qty < 0:
                raise ValueError(f"Holding for {symbol} cannot be negative")
        return v

    @property
    def symbol_quantities(self) -> dict[str, float]:
        return self.holdings


class FullState(BaseModel):
    """The complete state perceived by the agent."""
    market: MarketState
    portfolio: PortfolioState
    timestamp: datetime | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def timestamp_as_utc(cls, v):
        """Accept epoch seconds, ISO text or a datetime; store UTC-aware or None."""
        if v is None:
            return None
        return to_utc(v)

    @property
    def symbols(self) -> list[str]:
        """Get all symbols in the state"""
        return list(self.market.prices.keys())

    def portfolio_value(self) -> float:
        """Cash plus the market value of everything held."""
        value = self.portfolio.cash
        for symbol, quantity in self.portfolio.holdings.items():
            value += quantity * self.market.prices.get(symbol, 0.0)
        return value

    def to_vector(self, symbols: list[str], schema: str | None = None) -> np.ndarray:
        """
        Converts the state into a flattened numpy array for the neural network.
        Expects a fixed list of symbols to ensure consistent vector length.

        The layout is chosen by ``schema``; when it is omitted the configured
        :func:`state_vector_schema` decides, which is how the ``KDR_STATE_VECTOR_SCHEMA``
        flag gates the new vectorization: ``legacy`` reproduces the pre-normalization
        features, ``normalized`` (the default) scales them.

        Every normalized feature is scaled so one policy network can serve symbols
        priced from a cent to six figures:

        * log price change from the previous price to the current one, 0 when
          there is no previous price,
        * sentiment z-scored against the configured ``sentiment_min``/``sentiment_max``,
        * one position flag per symbol, 1 when held and 0 when flat,
        * cash as the fraction of the portfolio's total value.

        The vector is ``3 * len(symbols) + 1`` long either way: the three per-symbol
        features in symbol order, then the cash term.

        Args:
            symbols: The symbols to lay out, in the order they appear in the vector.
            schema: ``legacy`` or ``normalized``; defaults to the configured flag.

        Raises:
            ValueError: If ``schema`` is not a known layout.
        """
        chosen = schema if schema is not None else state_vector_schema()
        if chosen == "legacy":
            return self.legacy_vector(symbols)
        if chosen == "normalized":
            return self.normalized_vector(symbols)
        raise ValueError(f"Unknown state vector schema {chosen!r}")

    def legacy_vector(self, symbols: list[str]) -> np.ndarray:
        """The pre-normalization features: raw price, raw holding, raw sentiment, raw cash."""
        vector: list[float] = []
        for symbol in symbols:
            vector.append(self.market.prices.get(symbol, 0.0))
            vector.append(self.portfolio.holdings.get(symbol, 0.0))
            vector.append(self.market.sentiment_scores.get(symbol, 0.0))
        vector.append(self.portfolio.cash)
        return np.array(vector, dtype=np.float32)

    def normalized_vector(self, symbols: list[str]) -> np.ndarray:
        """The scaled features described by :meth:`to_vector`."""
        vector: list[float] = []
        for symbol in symbols:
            price = self.market.prices.get(symbol)
            previous = self.market.previous_prices.get(symbol)
            if price is not None and previous is not None and price > 0.0 and previous > 0.0:
                vector.append(math.log(price / previous))
            else:
                vector.append(0.0)

            vector.append(self.market.normalize_sentiment(
                self.market.sentiment_scores.get(symbol, 0.0)
            ))

            vector.append(1.0 if self.portfolio.holdings.get(symbol, 0.0) > 0.0 else 0.0)

        total_value = self.portfolio_value()
        vector.append(self.portfolio.cash / total_value if total_value > 0.0 else 0.0)
        return np.array(vector, dtype=np.float32)


class Action(BaseModel):
    """
    Represents an action taken by the agent.
    Can be discrete or continuous.
    """
    # For continuous actions (e.g., target weight adjustments)
    target_weights: dict[str, float] | None = None

    # For discrete actions (if used instead)
    movement: TradeMovement | None = None
    symbol: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


def candidate_actions(symbols: list[str], current_weights: dict[str, float]) -> list[Action]:
    """
    Enumerate the action space: BUY, SELL and HOLD for every symbol.

    The target weights are carried through unchanged; the movement is what the
    Bellman engine scores.

    Args:
        symbols: The tradable symbols.
        current_weights: The portfolio's current target weights.

    Returns:
        Three actions per symbol, in symbol-then-movement order.
    """
    return [
        Action(movement=movement, symbol=symbol, target_weights=dict(current_weights))
        for symbol in symbols
        for movement in TradeMovement
    ]
