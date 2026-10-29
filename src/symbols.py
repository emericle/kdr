"""
Centralized symbol definitions and utilities for market data tracking.

This module provides a single place to define, add, or remove symbols tracked
by the KDR trading application (both equity and crypto assets).
"""


# ============================================================================
# Central Symbol Configuration
# Add, remove, or modify default tracked symbols here.
# ============================================================================
DEFAULT_SYMBOLS: list[str] = [
    "AAPL",
    "TSLA",
    "BTC",  # Bitcoin (gathered from Alpaca)
]

# Equity ETF proxies used to populate the 5 top market index trackers (VIX, DJIA,
# SP500, NASDAQ, RUSSELL2000) via Alpaca's live market data feed.
INDEX_PROXY_SYMBOLS: list[str] = [
    "VXX",
    "DIA",
    "SPY",
    "QQQ",
    "IWM",
]

# Set of recognized cryptocurrency symbols
CRYPTO_SYMBOLS: set[str] = {
    "BTC",
    "BTC/USD",
    "BTCUSD",
    "ETH",
    "ETH/USD",
    "ETHUSD",
}

# Mapping between application display symbol and Alpaca API symbol
APP_TO_ALPACA_MAP = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
}

ALPACA_TO_APP_MAP = {
    "BTC/USD": "BTC",
    "BTCUSD": "BTC",
    "ETH/USD": "ETH",
    "ETHUSD": "ETH",
}


def is_crypto_symbol(symbol: str) -> bool:
    """Checks if a symbol represents a cryptocurrency asset."""
    sym = symbol.upper().strip()
    return sym in CRYPTO_SYMBOLS or "/" in sym or sym.startswith("BTC") or sym.startswith("ETH")


def to_alpaca_symbol(symbol: str) -> str:
    """Translates an internal symbol (e.g. 'BTC') to Alpaca API format (e.g. 'BTC/USD')."""
    sym = symbol.upper().strip()
    return APP_TO_ALPACA_MAP.get(sym, sym)


def from_alpaca_symbol(symbol: str) -> str:
    """Translates an Alpaca symbol (e.g. 'BTC/USD') to application symbol (e.g. 'BTC')."""
    sym = symbol.upper().strip()
    return ALPACA_TO_APP_MAP.get(sym, sym)


def normalize_symbols(
    symbols: str | list[str] | None = None,
    default: list[str] | None = None
) -> list[str]:
    """
    Normalizes a list, comma-separated string, or None to a standard list of symbols.
    If symbols is None or empty, returns default (or ['AAPL', 'TSLA'] for backward compatibility).
    """
    if symbols is None or symbols == "" or symbols == []:
        return list(default if default is not None else ["AAPL", "TSLA"])
    raw_list = symbols.split(",") if isinstance(symbols, str) else symbols
    cleaned = [from_alpaca_symbol(s.strip().upper()) for s in raw_list if s and s.strip()]
    return cleaned or list(default if default is not None else ["AAPL", "TSLA"])
