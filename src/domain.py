from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict
import numpy as np

class TradeMovement(str, Enum):
    """Discrete trade movements."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class MarketState(BaseModel):
    """Market conditions at a specific point in time."""
    prices: Dict[str, float] = Field(default_factory=dict)
    sentiment_scores: Dict[str, float] = Field(default_factory=dict)

    @field_validator("prices")
    @classmethod
    def prices_not_empty(cls, v):
        if not v:
            raise ValueError("Prices cannot be empty")
        return v

class PortfolioState(BaseModel):
    """Current state of the investor's portfolio."""
    holdings: Dict[str, float] = Field(default_factory=dict)
    cash: float = Field(..., ge=0.0)

    @field_validator("holdings")
    @classmethod
    def holdings_not_negative(cls, v):
        for symbol, qty in v.items():
            if qty < 0:
                raise ValueError(f"Holding for {symbol} cannot be negative")
        return v

class FullState(BaseModel):
    """The complete state perceived by the agent."""
    market: MarketState
    portfolio: PortfolioState
    timestamp: Optional[float] = None

    def to_vector(self, symbols: List[str]) -> np.ndarray:
        """
        Converts the state into a flattened numpy array for the neural network.
        Expects a fixed list of symbols to ensure consistent vector length.
        """
        vector = []
        for s in symbols:
            vector.append(self.market.prices.get(s, 0.0))
            vector.append(self.portfolio.holdings.get(s, 0.0))
            vector.append(self.market.sentiment_scores.get(s, 0.0))
        
        vector.append(self.portfolio.cash)
        return np.array(vector, dtype=np.float32)

class Action(BaseModel):
    """
    Represents an action taken by the agent.
    Can be discrete or continuous.
    """
    # For continuous actions (e.g., target weight adjustments)
    target_weights: Optional[Dict[str, float]] = None
    
    # For discrete actions (if used instead)
    movement: Optional[TradeMovement] = None
    symbol: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
