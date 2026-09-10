"""Adapters for external data sources like Adanos sentiment analysis."""
import logging
from typing import Dict, List, Optional, Union
import datetime
import os

from src.domain import MarketState, PortfolioState, FullState

logger = logging.getLogger(__name__)


class AdanosDataAdapter:
    """
    Adapter for fetching and structuring sentiment data from Adanos API.

    Adanos provides sentiment analysis for financial data. Typical responses include:
    - symbol: The stock symbol
    - score: A sentiment score (could be -1 to 1, or similar)
    - source: The data source
    - timestamp: When the data was generated
    """

    ADANOS_API_KEY = os.getenv("ADANOS_API_KEY", "")

    @staticmethod
    def fetch_sentiment(
        symbols: List[str],
        timeframe: str = "1h",
        limit: int = 10
    ) -> List[dict]:
        """
        Fetch sentiment data from Adanos API for specified symbols.

        Args:
            symbols: List of stock symbols to fetch sentiment for
            timeframe: Timeframe for sentiment analysis
            limit: Maximum number of sentiment records to return

        Returns:
            List of sentiment data dictionaries
        """
        if not AdanosDataAdapter.ADANOS_API_KEY:
            logger.warning("ADANOS_API_KEY not found in environment. Skipping Adanos API call.")
            return []

        # Implementation would call Adanos API
        # For now, return mock data structure
        logger.info(f"Fetching sentiment for {len(symbols)} symbols")

        sentiment_data = []
        for symbol in symbols:
            # Mock sentiment response structure
            sentiment_record = {
                'symbol': symbol.upper(),
                'timestamp': datetime.datetime.now(),
                'score': 0.0,  # Will be populated by actual API
                'source': 'adanos_api',
                'timeframe': timeframe
            }
            sentiment_data.append(sentiment_record)

        return sentiment_data

    @staticmethod
    def parse_sentiment_response(response_data: Union[dict, List[dict]]) -> List[dict]:
        """
        Parse response from Adanos API into a standardized format.

        Args:
            response_data: Raw response from Adanos API

        Returns:
            List of sentiment data dictionaries
        """
        if isinstance(response_data, dict):
            response_data = response_data.get('data', response_data)

        if isinstance(response_data, list):
            parsed = []
            for item in response_data:
                try:
                    # Handle different response formats
                    score = float(item.get('score', 0.0))

                    sentiment = {
                        'symbol': item.get('symbol', '').upper(),
                        'timestamp': datetime.datetime.fromisoformat(
                            item.get('timestamp', '').replace('Z', '+00:00')
                        ),
                        'score': score,
                        'source': item.get('source', 'unknown'),
                        'timeframe': item.get('timeframe', '1h'),
                        'confidence': float(item.get('confidence', 0.5))
                    }
                    parsed.append(sentiment)
                except (AttributeError, ValueError, KeyError) as e:
                    logger.warning(f"Failed to parse sentiment item: {e}, item: {item}")
                    continue
            return parsed

        logger.warning("Unexpected response format from Adanos API")
        return []

    @staticmethod
    def create_market_state_with_sentiment(
        market_ticks: List[dict],
        sentiment_data: List[dict]
    ) -> MarketState:
        """
        Create MarketState enriched with sentiment scores.

        Args:
            market_ticks: List of market ticks
            sentiment_data: List of sentiment data

        Returns:
            MarketState with integrated sentiment scores
        """
        # Start with base market state
        base_prices = {}
        sentiment_scores = {}

        # Populate prices from ticks
        for tick in market_ticks:
            if tick and 'symbol' in tick and 'price' in tick:
                symbol = tick['symbol'].upper()
                base_prices[symbol] = tick['price']

        # Enrich with sentiment
        for sentiment in sentiment_data:
            symbol = sentiment.get('symbol', '').upper()
            score = sentiment.get('score', 0.0)
            sentiment_scores[symbol] = score

        # Ensure all market symbols have sentiment (default 0)
        for symbol in base_prices:
            if symbol not in sentiment_scores:
                sentiment_scores[symbol] = 0.0

        return MarketState(
            prices=base_prices,
            sentiment_scores=sentiment_scores
        )

    @staticmethod
    def create_full_state_with_sentiment(
        market_ticks: Optional[List[dict]] = None,
        positions: Optional[List[dict]] = None,
        sentiment_data: Optional[List[dict]] = None,
        timestamp: Optional[float] = None
    ) -> FullState:
        """
        Create a complete state with sentiment integration.

        Args:
            market_ticks: Recent market ticks
            positions: Current portfolio positions
            sentiment_data: Sentiment analysis data
            timestamp: Optional timestamp override

        Returns:
            FullState with sentiment-enriched state
        """
        if market_ticks is None:
            market_ticks = []

        if positions is None:
            positions = []

        if sentiment_data is None:
            sentiment_data = []

        # Enrich market state with sentiment
        market_state = AdanosDataAdapter.create_market_state_with_sentiment(
            market_ticks, sentiment_data
        )

        # Create portfolio state using Alpaca adapter
        from src.data_adapters import AlpacaDataAdapter
        portfolio_state = AlpacaDataAdapter.create_portfolio_state(positions)

        return FullState(
            market=market_state,
            portfolio=portfolio_state,
            timestamp=timestamp
        )

    @staticmethod
    def validate_sentiment_score(score: float) -> bool:
        """
        Validate sentiment score is within expected range.

        Args:
            score: Sentiment score to validate

        Returns:
            True if score is within reasonable range (e.g., -1 to 1)
        """
        return -1.0 <= score <= 1.0