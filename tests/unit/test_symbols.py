"""Unit tests for centralized symbols module."""

from src.symbols import (
    DEFAULT_SYMBOLS,
    CRYPTO_SYMBOLS,
    is_crypto_symbol,
    to_alpaca_symbol,
    from_alpaca_symbol,
    normalize_symbols
)


def test_default_symbols_contains_btc():
    assert "BTC" in DEFAULT_SYMBOLS
    assert "AAPL" in DEFAULT_SYMBOLS
    assert "TSLA" in DEFAULT_SYMBOLS


def test_is_crypto_symbol():
    assert is_crypto_symbol("BTC") is True
    assert is_crypto_symbol("btc") is True
    assert is_crypto_symbol("BTC/USD") is True
    assert is_crypto_symbol("ETH") is True
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("TSLA") is False
    assert is_crypto_symbol("MSFT") is False


def test_to_alpaca_symbol():
    assert to_alpaca_symbol("BTC") == "BTC/USD"
    assert to_alpaca_symbol("btc") == "BTC/USD"
    assert to_alpaca_symbol("ETH") == "ETH/USD"
    assert to_alpaca_symbol("AAPL") == "AAPL"


def test_from_alpaca_symbol():
    assert from_alpaca_symbol("BTC/USD") == "BTC"
    assert from_alpaca_symbol("BTCUSD") == "BTC"
    assert from_alpaca_symbol("AAPL") == "AAPL"


def test_normalize_symbols():
    # Empty or None defaults to legacy stock symbols for backward compatibility
    assert normalize_symbols() == ["AAPL", "TSLA"]
    assert normalize_symbols([]) == ["AAPL", "TSLA"]
    assert normalize_symbols("") == ["AAPL", "TSLA"]
    assert normalize_symbols(default=DEFAULT_SYMBOLS) == DEFAULT_SYMBOLS

    # Comma-separated string
    assert normalize_symbols("AAPL, TSLA, BTC") == ["AAPL", "TSLA", "BTC"]
    assert normalize_symbols("AAPL,BTC/USD") == ["AAPL", "BTC"]

    # List of strings
    assert normalize_symbols(["aapl", "btc/usd"]) == ["AAPL", "BTC"]
