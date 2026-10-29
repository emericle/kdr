"""Unit tests for the equity ETF proxies behind the dashboard's index strip.

Alpaca has no index feed, so VIX, DJIA, SP500, NASDAQ and RUSSELL2000 are
populated from VXX, DIA, SPY, QQQ and IWM. Those proxies must reach the index
store without becoming tradable symbols or dashboard symbol cards.
"""

import queue as queue_module

from src.symbols import INDEX_PROXY_SYMBOLS
from src.stream_client import AlpacaStreamProcessor
from src.web_server.data_layer import (
    SYMBOL_TO_INDEX_MAP,
    TRACKED_INDEXES,
    IndexStore,
    MarketDataBuffer,
    index_store,
    update_index_from_tick,
)

# 2026-10-28 20:41:00 UTC, the shape the market buffer stores.
TICK_TS = 1793220060.0


def test_proxies_cover_every_tracked_index():
    """Each of the 5 dashboard indexes has a proxy ticker in INDEX_PROXY_SYMBOLS."""
    covered = {SYMBOL_TO_INDEX_MAP[symbol.upper()] for symbol in INDEX_PROXY_SYMBOLS}
    assert covered == set(TRACKED_INDEXES)


def test_proxy_tick_updates_the_index_store():
    """A proxy tick writes a live entry into the index it maps to."""
    MarketDataBuffer()  # the store update is a side effect of add_tick, not of a singleton
    index_store.get_all()
    update_index_from_tick("SPY", 670.25, TICK_TS, change=4.0, change_percent=0.6)
    entry = index_store.get_index("SP500")
    assert entry["currentPrice"] == 670.25
    assert entry["priceType"] == "live"


def test_proxy_symbols_are_not_offered_as_symbol_cards():
    """The card list keeps traded symbols and drops index proxies."""
    buffer = MarketDataBuffer()
    buffer.add_tick(symbol="AAPL", price=345.0, size=10.0, timestamp=TICK_TS)
    buffer.add_tick(symbol="SPY", price=670.0, size=100.0, timestamp=TICK_TS)
    assert buffer.get_symbols() == ["AAPL"]


def test_proxy_history_still_feeds_the_index_strip():
    """A proxy tick routed through the buffer updates the index store it maps to."""
    buffer = MarketDataBuffer()
    buffer.add_tick(symbol="VXX", price=17.99, size=50.0, timestamp=TICK_TS)
    entry = index_store.get_index("VIX")
    assert entry["currentPrice"] == 17.99
    assert entry["priceType"] == "live"


def test_snapshot_open_price_survives_the_first_streamed_tick():
    """A seeded previous close is the reference; the first tick must not replace it."""
    buffer = MarketDataBuffer()
    buffer.set_open_price("AAPL", 332.39)
    buffer.add_tick(symbol="AAPL", price=345.02, size=10.0, timestamp=TICK_TS)
    assert buffer.get_open_price("AAPL") == 332.39
    summary = buffer.get_price_and_change("AAPL", duration="24h")
    assert summary is not None
    assert round(summary["currentPrice"] - 332.39, 2) == 12.63


def test_set_open_price_ignores_a_missing_reference():
    """A null or non-positive reference must not become a bogus baseline."""
    buffer = MarketDataBuffer()
    buffer.set_open_price("TSLA", 0.0)
    buffer.add_tick(symbol="TSLA", price=402.6, size=5.0, timestamp=TICK_TS)
    assert buffer.get_open_price("TSLA", default=0.0) == 402.6


def test_index_only_ticks_stay_out_of_the_trading_pipeline():
    """A proxy tick reaches the dashboard but never the aggregation or DB pipeline."""
    data_queue = queue_module.Queue()
    processor = AlpacaStreamProcessor(
        symbols=["AAPL", "TSLA", "BTC"],
        data_queue=data_queue,
    )
    processor.handle_trade(
        {
            "symbol": "SPY",
            "price": 670.0,
            "size": 100.0,
            "timestamp": TICK_TS,
        }
    )
    assert "SPY" not in processor.buffer.buffer
    assert data_queue.qsize() == 0

    processor.handle_trade({"symbol": "AAPL", "price": 345.0, "size": 10.0, "timestamp": TICK_TS})
    assert "AAPL" in processor.buffer.buffer


def test_index_only_bars_are_not_queued_for_the_db_writer():
    """Proxy bars update the market buffer only; the DB queue stays empty."""
    data_queue = queue_module.Queue()
    processor = AlpacaStreamProcessor(symbols=["AAPL"], data_queue=data_queue)
    processor.handle_bar(
        {
            "symbol": "IWM",
            "open": 279.0,
            "high": 282.0,
            "low": 278.0,
            "close": 281.6,
            "volume": 500.0,
            "timestamp": TICK_TS,
        }
    )
    assert data_queue.qsize() == 0

    processor.handle_bar(
        {
            "symbol": "AAPL",
            "open": 344.0,
            "high": 346.0,
            "low": 343.0,
            "close": 345.0,
            "volume": 80.0,
            "timestamp": TICK_TS,
        }
    )
    assert data_queue.qsize() == 1


def test_index_store_isolates_a_proxy_from_other_indexes(tmp_path):
    """Feeding one proxy leaves the other indexes untouched."""
    store = IndexStore(cache_file=str(tmp_path / "index_isolation.json"))
    store.update_index("SP500", current_price=670.0, change=1.0, change_percent=0.15)
    assert store.get_index("DJIA")["currentPrice"] is None
    assert store.get_index("SP500")["currentPrice"] == 670.0
