"""State mapping logic for transforming raw data into domain state models."""
import logging
from typing import Dict, List, Optional, Tuple

from src.domain import MarketState, PortfolioState, FullState
from src.data_adapters import AlpacaDataAdapter
from src.sentiment_adapter import AdanosDataAdapter
from src.config_gatekeeper import validate_configs
import src.sentiment_adapter

logger = logging.getLogger(__name__)


class StateMapper:
    """
    Maps raw data from external sources into internal domain state models.

    This class provides a clean interface for transforming:
    - Alpaca API responses → MarketState, PortfolioState, FullState
    - Adanos sentiment API → MarketState (with sentiment scores)
    - Combined → FullState with complete context
    """

    def __init__(self):
        """Initialize the state mapper with configured adapters."""
        validate_configs()
        self.alpaca_adapter = AlpacaDataAdapter()
        self.adanos_adapter = AdanosDataAdapter()

    def map_bar_data_to_state(
        self,
        bar_data: dict
    ) -> Tuple[MarketState, Optional[PortfolioState]]:
        """
        Map a single OHLC bar to market state.

        Args:
            bar_data: Bar data from Alpaca

        Returns:
            Tuple of (MarketState, optional PortfolioState)
        """
        parsed_bar = self.alpaca_adapter.parse_bar_data(bar_data)
        if not parsed_bar:
            raise ValueError(f"Failed to parse bar data: {bar_data}")

        # For bar data, use 'close' as the price
        parsed_bar_with_price = {**parsed_bar, 'price': parsed_bar['close']}

        market_state = self.alpaca_adapter.create_market_state([parsed_bar_with_price])
        portfolio_state = None

        return market_state, portfolio_state

    def map_multiple_bars_to_state(
        self,
        bars: List[dict]
    ) -> MarketState:
        """
        Map multiple bars into current market state.

        Args:
            bars: List of bar data from Alpaca

        Returns:
            MarketState with latest prices
        """
        parsed_bars = [self.alpaca_adapter.parse_bar_data(bar) for bar in bars]
        parsed_bars = [bar for bar in parsed_bars if bar is not None]

        # Add 'price' field (use 'close' from bar data)
        for bar in parsed_bars:
            bar['price'] = bar['close']

        return self.alpaca_adapter.create_market_state(parsed_bars)

    def map_tick_data_to_state(
        self,
        ticks: List[dict]
    ) -> MarketState:
        """
        Map tick data to current market state.

        Args:
            ticks: List of tick data from Alpaca

        Returns:
            MarketState with latest prices
        """
        parsed_ticks = [self.alpaca_adapter.parse_tick_data(tick) for tick in ticks]
        parsed_ticks = [tick for tick in parsed_ticks if tick is not None]

        return self.alpaca_adapter.create_market_state(parsed_ticks)

    def map_positions_to_portfolio_state(
        self,
        positions: List[dict]
    ) -> PortfolioState:
        """
        Map portfolio positions to portfolio state.

        Args:
            positions: List of position data from Alpaca

        Returns:
            PortfolioState with holdings and cash
        """
        return self.alpaca_adapter.create_portfolio_state(positions)

    def map_and_combine_with_sentiment(
        self,
        market_ticks: List[dict],
        sentiment_data: Optional[List[dict]] = None
    ) -> MarketState:
        """
        Create market state enriched with sentiment.

        Args:
            market_ticks: Recent market ticks
            sentiment_data: Optional sentiment data from Adanos

        Returns:
            MarketState with integrated sentiment scores
        """
        if sentiment_data is None:
            sentiment_data = []

        # Use Adanos adapter to create sentiment-enriched state
        return self.adanos_adapter.create_market_state_with_sentiment(
            market_ticks, sentiment_data
        )

    def create_full_state_from_raw_data(
        self,
        raw_data: dict,
        include_sentiment: bool = True
    ) -> FullState:
        """
        Create FullState from any raw data format with optional sentiment.

        This is a flexible method that can handle various raw data structures.

        Args:
            raw_data: Raw data dict with keys like 'ticks', 'positions', 'sentiment'
            include_sentiment: Whether to fetch/enrich with sentiment data

        Returns:
            FullState with all components populated
        """
        # Extract components from raw data
        ticks = raw_data.get('ticks', [])
        positions = raw_data.get('positions', [])

        # Enrich with sentiment if requested and API key available
        sentiment_data = None
        if include_sentiment:
            if isinstance(ticks, list) and len(ticks) > 0 and 'symbol' in ticks[0]:
                symbols = list(set(tick.get('symbol', '') for tick in ticks if isinstance(tick, dict)))
                sentiment_data = self.adanos_adapter.fetch_sentiment(symbols)
                # Parse and enrich
                parsed_sentiment = self.adanos_adapter.parse_sentiment_response(sentiment_data)
                sentiment_data = parsed_sentiment

        # Create full state
        full_state = self.adanos_adapter.create_full_state_with_sentiment(
            market_ticks=ticks,
            positions=positions,
            sentiment_data=sentiment_data
        )

        return full_state

    def create_full_state_for_symbol(
        self,
        symbol: str,
        market_data: Dict[str, dict],
        sentiment_score: float = 0.0,
        portfolio_data: Optional[Dict[str, float]] = None
    ) -> FullState:
        """
        Create a focused FullState for a single symbol.

        Args:
            symbol: The symbol to focus on
            market_data: Dict mapping symbols to their bar/tick data
            sentiment_score: Optional sentiment score for the symbol
            portfolio_data: Optional dict of holdings (symbol: quantity)

        Returns:
            FullState with relevant context for the symbol
        """
        # Create minimal market state for the symbol
        if symbol in market_data:
            tick_data = market_data[symbol]
            parsed_tick = self.alpaca_adapter.parse_tick_data(tick_data)
        else:
            # Try bar data
            if symbol in market_data:
                bar_data = market_data[symbol]
                parsed_tick = self.alpaca_adapter.parse_bar_data(bar_data)
            else:
                raise ValueError(f"No market data found for symbol {symbol}")

        market_state = MarketState(
            prices={symbol: parsed_tick['price']},
            sentiment_scores={symbol: sentiment_score}
        )

        # Build portfolio holdings
        holdings = portfolio_data.get(symbol, 0.0) if portfolio_data else 0.0
        portfolio_state = PortfolioState(
            holdings={symbol: holdings},
            cash=0.0  # Simplified - would use Alpaca account data
        )

        return FullState(
            market=market_state,
            portfolio=portfolio_state,
            timestamp=None
        )

    def aggregate_state_from_components(
        self,
        market_state: MarketState,
        portfolio_state: PortfolioState
    ) -> FullState:
        """
        Aggregate market and portfolio states into a full state.

        This is useful for combining partial states.

        Args:
            market_state: Market state (may have subset of symbols)
            portfolio_state: Portfolio state with holdings

        Returns:
            FullState combining both components
        """
        return FullState(
            market=market_state,
            portfolio=portfolio_state,
            timestamp=None
        )

    def sync_with_database(
        self,
        db_manager,
        symbols: List[str],
        recent_minutes: int = 5
    ) -> List[dict]:
        """
        Fetch recent market data from database and map to state.

        Args:
            db_manager: DatabaseManager instance
            symbols: List of symbols to fetch
            recent_minutes: How many minutes back to fetch

        Returns:
            List of ticks/bars that can be used to create state
        """
        # In production, this would query the database for recent bars
        # For now, return empty list
        logger.info(f"Would fetch {recent_minutes} minutes of data for {symbols}")
        return []