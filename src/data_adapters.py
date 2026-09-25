"""Data adapters for transforming external API responses into internal domain models."""
import logging
from typing import Any, List, Optional
from datetime import datetime, timezone

from src.domain import MarketState, PortfolioState, FullState

logger = logging.getLogger(__name__)


class AlpacaDataAdapter:
    """
    Adapter for parsing and structuring Alpaca API responses into market state format.

    Alpaca API returns trade data with fields like:
    - timestamp (datetime)
    - symbol (str)
    - price (float)
    - qty (float)
    - side (str: 'BUY' or 'SELL')

    We need to aggregate these to get current market state.
    """

    @staticmethod
    def _normalize_timestamp(raw_ts: Any) -> datetime:
        """Helper to normalize string, datetime or numeric timestamp."""
        if isinstance(raw_ts, datetime):
            return raw_ts
        if isinstance(raw_ts, str):
            # Replace Z with +00:00 for ISO parsing
            cleaned = raw_ts.replace('Z', '+00:00')
            return datetime.fromisoformat(cleaned)
        if isinstance(raw_ts, (int, float)):
            # If greater than 1e11 assume nanoseconds/milliseconds, otherwise seconds
            if raw_ts > 1e16:
                return datetime.fromtimestamp(raw_ts / 1e9, tz=timezone.utc)
            elif raw_ts > 1e11:
                return datetime.fromtimestamp(raw_ts / 1e3, tz=timezone.utc)
            else:
                return datetime.fromtimestamp(raw_ts, tz=timezone.utc)
        raise ValueError(f"Unsupported timestamp format: {raw_ts}")

    @staticmethod
    def parse_tick_data(tick_data: dict) -> Optional[dict]:
        """
        Parse a raw tick from Alpaca API into a standardized format.

        Args:
            tick_data: Raw tick data from Alpaca

        Returns:
            Dict with standardized format including symbol, price, size, timestamp
        """
        try:
            ts = AlpacaDataAdapter._normalize_timestamp(tick_data.get('timestamp'))
            return {
                'symbol': tick_data.get('symbol', '').upper(),
                'price': float(tick_data.get('price', 0.0)),
                'size': float(tick_data.get('qty', 0.0)),
                'timestamp': ts,
                'side': tick_data.get('side', 'UNKNOWN')
            }
        except (AttributeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse tick data: {e}, data: {tick_data}")
            return None

    @staticmethod
    def parse_bar_data(bar_data: dict) -> Optional[dict]:
        """
        Parse a OHLC bar from Alpaca API.

        Args:
            bar_data: Raw bar data from Alpaca

        Returns:
            Dict with open, high, low, close, volume data
        """
        try:
            ts = AlpacaDataAdapter._normalize_timestamp(bar_data.get('timestamp'))
            return {
                'symbol': bar_data.get('symbol', '').upper(),
                'timestamp': ts,
                'open': float(bar_data.get('open', 0.0)),
                'high': float(bar_data.get('high', 0.0)),
                'low': float(bar_data.get('low', 0.0)),
                'close': float(bar_data.get('close', 0.0)),
                'volume': float(bar_data.get('volume', 0.0))
            }
        except (AttributeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse bar data: {e}, data: {bar_data}")
            return None

    @staticmethod
    def create_market_state(ticks: List[dict]) -> MarketState:
        """
        Aggregate tick data into a MarketState.

        Args:
            ticks: List of parsed tick data

        Returns:
            MarketState with current prices and sentiment scores (default 0)
        """
        if not ticks:
            raise ValueError("Cannot create market state from empty tick data")

        # Aggregate all ticks from the stream
        # Build prices dict - if multiple ticks for same symbol, use the latest
        prices = {}
        sentiment_scores = {}

        for tick in ticks:
            if tick and 'symbol' in tick:
                symbol = tick['symbol']
                prices[symbol] = tick['price']
                # Default sentiment to 0, can be enriched with external APIs
                sentiment_scores[symbol] = 0.0

        return MarketState(
            prices=prices,
            sentiment_scores=sentiment_scores
        )

    @staticmethod
    def create_portfolio_state(positions: Optional[List[dict]] = None) -> PortfolioState:
        """
        Build PortfolioState from Alpaca positions.

        Args:
            positions: List of position data from Alpaca

        Returns:
            PortfolioState with holdings and cash
        """
        if positions is None:
            positions = []

        holdings = {}
        total_value = 0.0

        for position in positions:
            try:
                symbol = position.get('symbol', '').upper()
                qty = float(position.get('qty', 0.0))
                avg_price = float(position.get('avg_entry_price', 0.0))

                if qty > 0:
                    holdings[symbol] = qty
                    total_value += qty * avg_price
            except (AttributeError, ValueError) as e:
                logger.warning(f"Failed to parse position data: {e}")
                continue

        # Extract cash from Alpaca account
        cash = 0.0
        # Alpaca typically provides cash in account data
        # This is a simplified version - in production, you'd fetch account data

        return PortfolioState(
            holdings=holdings,
            cash=cash
        )

    @staticmethod
    def create_full_state(
        ticks: Optional[List[dict]] = None,
        positions: Optional[List[dict]] = None,
        timestamp: Optional[float] = None
    ) -> FullState:
        """
        Create a complete state combining market and portfolio data.

        Args:
            ticks: Recent market ticks
            positions: Current portfolio positions
            timestamp: Optional timestamp override

        Returns:
            FullState with all components
        """
        market_state = AlpacaDataAdapter.create_market_state(ticks or [])
        portfolio_state = AlpacaDataAdapter.create_portfolio_state(positions)

        return FullState(
            market=market_state,
            portfolio=portfolio_state,
            timestamp=timestamp
        )