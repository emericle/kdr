"""The dashboard's data layer: buffers, the persistent index store, DB access.

This half owns everything the routes read from: the thread-safe tick and decision
buffers, the persisted index store, and the lazy database handle. The routes
themselves live in :mod:`src.web_server.api_layer`, which imports this module.
"""

import asyncio
import atexit
import itertools
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.database import DB_FAILURES
from src.times import epoch_seconds, hhmmss, now_utc, to_utc

logger = logging.getLogger(__name__)

# A dashboard read is allowed to fail: the route answers with what the buffers
# have and the next refresh tries again. This names the failures that mean
# "the database did not answer" (a lost connection, a refused statement, a
# query that timed out) as opposed to a bug in this file, which stays raised.
DB_READ_FAILURES = (*DB_FAILURES, TimeoutError, ConnectionError, OSError)

# The index cache is a plain JSON file: it may be missing, unreadable or hold a
# shape this store does not expect.
CACHE_FAILURES = (OSError, TypeError, ValueError, AttributeError)

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class MarketTick:
    """A single tick data point."""
    symbol: str
    price: float
    size: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp,
            "time_str": hhmmss(self.timestamp)
        }


@dataclass
class TradingDecision:
    """A trading recommendation or execution decision.

    ``timestamp`` is a timezone-aware UTC ``datetime``: every producer hands the
    buffer a datetime (``now_utc()`` or a parsed bar time), and ``to_dict`` renders
    it as epoch seconds, which is the unit the dashboard's charts read.
    """
    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float  # 0.0 to 1.0
    reasoning: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=now_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning,
            "timestamp": epoch_seconds(self.timestamp),
            "time_str": hhmmss(self.timestamp)
        }


# ============================================================================
# Thread-Safe Buffers
# ============================================================================

# ============================================================================
# Persistent Index Store & Mappings
# ============================================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX_CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "index_cache.json")

INDEX_DISPLAY_NAMES = {
    "VIX": "VIX",
    "DJIA": "DJIA",
    "SP500": "S&P 500",
    "NASDAQ": "NASDAQ",
    "RUSSELL2000": "Russel 2k"
}

INDEX_TICKERS = {
    "VIX": "^VIX",
    "DJIA": "^DJI",
    "SP500": "SPX",
    "NASDAQ": "^IXIC",
    "RUSSELL2000": "RUT"
}

INDEX_CANDIDATE_SYMBOLS = {
    "VIX": ["VIX", "^VIX", "VXX", "UVXY"],
    "DJIA": ["DJIA", "^DJI", "DJI", "DIA"],
    "SP500": ["SP500", "SPX", "^GSPC", "^SPX", "SPY"],
    "NASDAQ": ["NASDAQ", "^IXIC", "IXIC", "COMP", "QQQ"],
    "RUSSELL2000": ["RUSSELL2000", "RUT", "^RUT", "IWM", "RUSSELL2K", "RUSSELL"]
}

SYMBOL_TO_INDEX_MAP: dict[str, str] = {}
for _idx_key, _sym_list in INDEX_CANDIDATE_SYMBOLS.items():
    for _s in _sym_list:
        SYMBOL_TO_INDEX_MAP[_s.upper()] = _idx_key
        _clean = _s[1:].upper() if _s.startswith("^") else _s.upper()
        SYMBOL_TO_INDEX_MAP[_clean] = _idx_key

TRACKED_INDEXES = ["VIX", "DJIA", "SP500", "NASDAQ", "RUSSELL2000"]

# The durations the dashboard asks a summary for, in the order its charts read
# them. One list so the summary and the batched DB read cannot drift apart.
SUMMARY_DURATIONS = ("1h", "24h", "5d", "30d", "1y", "ytd")

_LEGACY_SEED_PRICES: dict[str, float] = {
    "VIX": 15.25,
    "DJIA": 43910.50,
    "SP500": 5865.75,
    "NASDAQ": 18518.60,
    "RUSSELL2000": 2240.10,
}

DEFAULT_INDEXES: dict[str, dict[str, Any]] = {
    "VIX": {
        "index": "VIX",
        "displayName": "VIX",
        "ticker": "^VIX",
        "currentPrice": None,
        "openPrice": None,
        "change": None,
        "changePercent": None,
        "priceType": "placeholder",
        "lastUpdate": None,
        "timestamp": None,
    },
    "DJIA": {
        "index": "DJIA",
        "displayName": "DJIA",
        "ticker": "^DJI",
        "currentPrice": None,
        "openPrice": None,
        "change": None,
        "changePercent": None,
        "priceType": "placeholder",
        "lastUpdate": None,
        "timestamp": None,
    },
    "SP500": {
        "index": "SP500",
        "displayName": "S&P 500",
        "ticker": "SPX",
        "currentPrice": None,
        "openPrice": None,
        "change": None,
        "changePercent": None,
        "priceType": "placeholder",
        "lastUpdate": None,
        "timestamp": None,
    },
    "NASDAQ": {
        "index": "NASDAQ",
        "displayName": "NASDAQ",
        "ticker": "^IXIC",
        "currentPrice": None,
        "openPrice": None,
        "change": None,
        "changePercent": None,
        "priceType": "placeholder",
        "lastUpdate": None,
        "timestamp": None,
    },
    "RUSSELL2000": {
        "index": "RUSSELL2000",
        "displayName": "Russel 2k",
        "ticker": "RUT",
        "currentPrice": None,
        "openPrice": None,
        "change": None,
        "changePercent": None,
        "priceType": "placeholder",
        "lastUpdate": None,
        "timestamp": None,
    },
}

DEFAULT_INDEX_VALUATIONS = DEFAULT_INDEXES


class IndexStore:
    """Thread-safe persistent store for index valuations."""
    def __init__(self, cache_file: str = INDEX_CACHE_FILE, flush_interval: float = 5.0):
        self.cache_file = cache_file
        self._lock = threading.Lock()
        self._indexes: dict[str, dict[str, Any]] = {}
        self._dirty: bool = False
        self._last_flush_mono: float = 0.0
        self._flush_interval: float = flush_interval
        self.load()
        atexit.register(self.flush_cache, False)

    @staticmethod
    def _is_placeholder_or_fabricated(key: str, entry: dict[str, Any]) -> bool:
        price = entry.get("currentPrice")
        price_type = entry.get("priceType")
        if price is None or price_type == "placeholder":
            return True
        return bool(
            entry.get("lastUpdate") == "16:00:00"
            and price_type in ("closing", "persisted")
            and price == _LEGACY_SEED_PRICES.get(key.upper())
        )

    def load(self):
        with self._lock:
            self._indexes = {k: dict(v) for k, v in DEFAULT_INDEXES.items()}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file) as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            for k, v in saved.items():
                                k_upper = k.upper()
                                if isinstance(v, dict) and not self._is_placeholder_or_fabricated(k_upper, v):
                                    if k_upper in self._indexes:
                                        self._indexes[k_upper].update(v)
                                    else:
                                        self._indexes[k_upper] = dict(v)
                except CACHE_FAILURES as e:
                    logger.debug("Failed loading index cache: %s", e)

    def _write_snapshot(self, snapshot: dict[str, dict[str, Any]]) -> None:
        persisted = {
            k: v
            for k, v in snapshot.items()
            if isinstance(v, dict)
            and v.get("priceType") != "placeholder"
            and v.get("currentPrice") is not None
        }
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(persisted, f, indent=2)
        except CACHE_FAILURES as e:
            logger.debug("Failed saving index cache: %s", e)

    def flush_cache(self, force: bool = True) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            snapshot = {k: dict(v) for k, v in self._indexes.items()}
            self._dirty = False
            self._last_flush_mono = time.monotonic()
        self._write_snapshot(snapshot)

    def flush_if_due(self) -> None:
        if self._dirty and (time.monotonic() - self._last_flush_mono) >= self._flush_interval:
            self.flush_cache(force=False)

    def save(self):
        self.flush_cache(force=True)

    @staticmethod
    def _placeholder_entry(index: str) -> dict[str, Any]:
        idx_upper = index.upper()
        display_name = INDEX_DISPLAY_NAMES.get(idx_upper, idx_upper)
        ticker = INDEX_TICKERS.get(idx_upper, idx_upper)
        clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
        return {
            "index": idx_upper,
            "displayName": display_name,
            "ticker": clean_ticker,
            "currentPrice": None,
            "openPrice": None,
            "change": None,
            "changePercent": None,
            "priceType": "placeholder",
            "lastUpdate": None,
            "timestamp": None,
        }

    def update_index(
        self,
        index: str,
        current_price: float,
        change: float | None = None,
        change_percent: float | None = None,
        last_update: str | None = None,
        price_type: str = "live",
        timestamp: float | None = None,
    ):
        index = index.upper()
        now_ts = timestamp if timestamp is not None else epoch_seconds(now_utc())
        with self._lock:
            if index not in self._indexes:
                entry = self._placeholder_entry(index)
                entry.update({
                    "currentPrice": current_price,
                    "openPrice": current_price,
                    "change": 0.0,
                    "changePercent": 0.0,
                    "priceType": price_type,
                    "lastUpdate": last_update or hhmmss(now_utc()),
                    "timestamp": now_ts,
                })
                self._indexes[index] = entry
            item = self._indexes[index]
            prev_price = item.get("currentPrice") or current_price
            open_price = item.get("openPrice")
            if open_price is None:
                if item.get("change") is not None and prev_price is not None:
                    open_price = prev_price - item.get("change", 0.0)
                else:
                    open_price = prev_price

            if change is not None:
                item["change"] = round(float(change), 2)
                item["openPrice"] = round(float(current_price - change), 2)
            else:
                if item.get("openPrice") is None:
                    item["openPrice"] = round(float(open_price), 2)
                c = current_price - open_price if open_price > 0 else 0.0
                item["change"] = round(float(c), 2)

            if change_percent is not None:
                item["changePercent"] = round(float(change_percent), 2)
            else:
                base = item.get("openPrice") or prev_price
                cp = (item["change"] / base * 100.0) if base > 0 else 0.0
                item["changePercent"] = round(float(cp), 2)

            item["currentPrice"] = round(float(current_price), 2)
            item["priceType"] = price_type
            item["lastUpdate"] = last_update or hhmmss(now_utc())
            item["timestamp"] = now_ts
            self._dirty = True
            now_mono = time.monotonic()
            should_flush = (
                self._last_flush_mono == 0.0
                or (now_mono - self._last_flush_mono) >= self._flush_interval
            )
        if should_flush:
            self.flush_cache(force=False)

    def get_index(self, index: str) -> dict[str, Any]:
        index = index.upper()
        with self._lock:
            if index in self._indexes:
                return dict(self._indexes[index])
            return self._placeholder_entry(index)

    def get_all(self, tracked: list[str] | None = None) -> list[dict[str, Any]]:
        indices = tracked or TRACKED_INDEXES
        with self._lock:
            return [
                dict(self._indexes[idx.upper()])
                if idx.upper() in self._indexes
                else self._placeholder_entry(idx)
                for idx in indices
            ]


index_store = IndexStore()


def update_index_from_tick(
    symbol: str,
    price: float,
    timestamp: float,
    change: float | None = None,
    change_percent: float | None = None
):
    sym_upper = symbol.upper()
    idx_key = SYMBOL_TO_INDEX_MAP.get(sym_upper)
    if not idx_key:
        return
    time_str = hhmmss(timestamp)
    index_store.update_index(
        index=idx_key,
        current_price=price,
        change=change,
        change_percent=change_percent,
        last_update=time_str,
        price_type="live",
        timestamp=timestamp,
    )


class MarketDataBuffer:
    """Thread-safe circular buffer storing recent ticks per symbol."""

    def __init__(self, max_ticks: int = 500):
        self.max_ticks = max_ticks
        self._history: dict[str, deque[MarketTick]] = {}
        self._open_prices: dict[str, float] = {}
        self._running_volume: dict[str, float] = {}
        self._running_high: dict[str, float] = {}
        self._running_low: dict[str, float] = {}
        self._lock = threading.Lock()

    def add_tick(
        self,
        symbol: str,
        price: float,
        size: float = 0.0,
        timestamp: Any | None = None
    ) -> MarketTick:
        symbol = symbol.upper()
        ts = epoch_seconds(now_utc())
        if timestamp is not None:
            ts = epoch_seconds(timestamp)

        # Reject zero or negative price ticks to protect open price and calculations
        if float(price) <= 0:
            return MarketTick(symbol=symbol, price=0.0, size=0.0, timestamp=ts)

        tick = MarketTick(symbol=symbol, price=float(price), size=float(size or 0.0), timestamp=ts)

        with self._lock:
            history = self._history.get(symbol)
            if history is None or len(history) == 0:
                history = deque(maxlen=self.max_ticks)
                self._history[symbol] = history
                self._open_prices[symbol] = tick.price
                history.append(tick)
                self._running_volume[symbol] = tick.size
                self._running_high[symbol] = tick.price
                self._running_low[symbol] = tick.price
            else:
                evicted: MarketTick | None = None
                if len(history) == self.max_ticks:
                    evicted = history.popleft()

                if not history or ts >= history[-1].timestamp:
                    history.append(tick)
                else:
                    insert_idx = len(history)
                    while insert_idx > 0 and history[insert_idx - 1].timestamp > ts:
                        insert_idx -= 1
                    history.insert(insert_idx, tick)

                prev_vol = self._running_volume.get(symbol, 0.0)
                prev_high = self._running_high.get(symbol, tick.price)
                prev_low = self._running_low.get(symbol, tick.price)

                if evicted is not None:
                    self._running_volume[symbol] = max(0.0, prev_vol - evicted.size) + tick.size
                    if evicted.price >= prev_high or evicted.price <= prev_low:
                        self._running_high[symbol] = max(t.price for t in history)
                        self._running_low[symbol] = min(t.price for t in history)
                    else:
                        self._running_high[symbol] = max(prev_high, tick.price)
                        self._running_low[symbol] = min(prev_low, tick.price)
                else:
                    self._running_volume[symbol] = prev_vol + tick.size
                    self._running_high[symbol] = max(prev_high, tick.price)
                    self._running_low[symbol] = min(prev_low, tick.price)

            open_p = self._open_prices.get(symbol, tick.price)

        # Update index persistent store if this symbol corresponds to a tracked index or proxy
        try:
            change = tick.price - open_p if open_p > 0 else 0.0
            change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
            update_index_from_tick(symbol, float(price), ts, change=change, change_percent=change_pct)
        except CACHE_FAILURES as e:
            logger.debug("Failed updating index from tick for %s: %s", symbol, e)

        return tick

    def get_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._history.keys())

    def get_latest_tick(self, symbol: str) -> MarketTick | None:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history:
                return history[-1]
            return None

    def get_open_price(self, symbol: str, default: float = 0.0) -> float:
        symbol = symbol.upper()
        with self._lock:
            return self._open_prices.get(symbol, default)

    def get_ticks(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if not history or limit <= 0:
                return []
            items = list(itertools.islice(history, max(0, len(history) - limit), None))
            return [t.to_dict() for t in items]

    def get_price_and_change(self, symbol: str, duration: str = "24h") -> dict[str, Any] | None:
        """Thread-safe retrieval of current price, daily change, and percent change based on interval.

        Args:
            symbol: Stock/crypto symbol
            duration: Time interval for price change calculation (1h, 24h, 5d, 30d, 1y, ytd)
        Returns:
            Dict with currentPrice, change, changePercent, and lastUpdate, or None if not found
        """
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            db = get_db_manager()

            # Priority 1: Use buffer history if available
            if history:
                latest_tick = history[-1]
                earliest_tick = history[0]
                current_price = latest_tick.price
                time_str = hhmmss(latest_tick.timestamp)

                if db and hasattr(db, 'get_price_at_interval_start'):
                    try:
                        interval_start_price = db.get_price_at_interval_start(symbol, duration)
                        if interval_start_price is not None and float(interval_start_price) > 0:
                            start_p = float(interval_start_price)
                            change = current_price - start_p
                            change_pct = (change / start_p * 100.0)
                            return {
                                "currentPrice": current_price,
                                "change": change,
                                "changePercent": change_pct,
                                "lastUpdate": time_str
                            }
                    except DB_READ_FAILURES as e:
                        logger.debug(f"Database interval price lookup failed, falling back to buffer: {e}")

                fallback_open_price = self._open_prices.get(symbol, earliest_tick.price)
                change = current_price - fallback_open_price
                change_pct = (change / fallback_open_price * 100.0) if fallback_open_price > 0 else 0.0
                return {
                    "currentPrice": current_price,
                    "change": change,
                    "changePercent": change_pct,
                    "lastUpdate": time_str
                }

            # Priority 2: Try database for buffer-less symbols
            if db and hasattr(db, 'get_latest_market_record'):
                try:
                    rec = db.get_latest_market_record(symbol)
                    if rec:
                        current_price = rec["price"]
                        start_price = None
                        if hasattr(db, 'get_price_at_interval_start'):
                            start_price = db.get_price_at_interval_start(symbol, duration)
                        if start_price is None or float(start_price) <= 0:
                            start_price = rec["open"] or current_price
                        start_p = float(start_price)
                        change = current_price - start_p
                        change_pct = (change / start_p * 100.0) if start_p > 0 else 0.0
                        return {
                            "currentPrice": current_price,
                            "change": change,
                            "changePercent": change_pct,
                            "lastUpdate": rec["time_str"]
                        }
                except DB_READ_FAILURES as e:
                    logger.debug(f"Database lookup for {symbol} failed: {e}")

            return None

    def _interval_starts(self, symbol: str) -> dict[str, float | None]:
        """One batched read of every dashboard duration, or ``{}`` when the DB cannot serve it.

        A single query per symbol covers all six durations, which is what keeps
        the dashboard from costing two to four queries per symbol per refresh.
        """
        db = get_db_manager()
        if db and hasattr(db, "get_prices_at_interval_starts"):
            try:
                res = db.get_prices_at_interval_starts(symbol)
                if isinstance(res, dict):
                    return res
            except DB_READ_FAILURES as e:
                logger.debug("Batched interval lookup for %s failed: %s", symbol, e)
        return {}

    def _interval_anchor(self, starts: dict[str, float | None], duration: str, fallback: float) -> float:
        """The stored price at the start of ``duration``, or ``fallback`` when there is none."""
        raw = starts.get(duration) if starts else None
        try:
            value = float(raw) if raw is not None else None
        except (ValueError, TypeError):
            value = None
        if value is None or value <= 0:
            return fallback
        return value

    def _duration_stats(self, current: float, starts: dict[str, float | None], fallback: float) -> dict[str, float]:
        """``change<dur>``/``changePercent<dur>`` for every duration the dashboard charts."""
        stats: dict[str, float] = {}
        for dur in SUMMARY_DURATIONS:
            anchor = self._interval_anchor(starts, dur, fallback)
            change = current - anchor
            stats[f"change{dur}"] = round(change, 2)
            stats[f"changePercent{dur}"] = round((change / anchor * 100.0) if anchor > 0 else 0.0, 2)
        return stats

    def _change_for_duration(
        self,
        symbol: str,
        current: float,
        duration: str,
        fallback: float,
        starts: dict[str, float | None] | None = None,
    ) -> tuple[float, float]:
        """``(change, change_percent)`` for one duration, from the DB anchor or the buffer's open."""
        if starts is not None and duration in starts:
            anchor_val = self._interval_anchor(starts, duration, fallback)
            change = current - anchor_val
            return change, (change / anchor_val * 100.0) if anchor_val > 0 else 0.0

        db = get_db_manager()
        if db and hasattr(db, "get_price_at_interval_start"):
            try:
                raw_anchor = db.get_price_at_interval_start(symbol, duration)
            except DB_READ_FAILURES as e:
                logger.debug("Database interval price lookup failed, falling back to buffer: %s", e)
                raw_anchor = None
            anchor_val = self._interval_anchor({duration: raw_anchor}, duration, fallback)
            change = current - anchor_val
            return change, (change / anchor_val * 100.0) if anchor_val > 0 else 0.0
        change = current - fallback
        return change, ((current - fallback) / fallback * 100.0) if fallback and fallback > 0 else 0.0

    def _db_only_summary(self, symbol: str, duration: str) -> dict[str, Any] | None:
        """Summary for a symbol the buffer has never seen, read from the database.

        This is the restart path: a fresh process has no ticks yet, so the
        dashboard would otherwise show $0.00 for symbols that already have bars.
        """
        db = get_db_manager()
        if not (db and hasattr(db, "get_latest_market_record")):
            return None
        try:
            rec = db.get_latest_market_record(symbol)
            if not rec:
                return None
            current = rec["price"]
            starts = self._interval_starts(symbol)
            if duration in starts:
                anchor = starts[duration]
            elif hasattr(db, "get_price_at_interval_start"):
                anchor = db.get_price_at_interval_start(symbol, duration)
            else:
                anchor = None
            start_p = self._interval_anchor({duration: anchor}, duration, float(rec["open"] or current))
            change = current - start_p
            return {
                "symbol": symbol,
                "currentPrice": round(current, 2),
                "change": round(change, 2),
                "changePercent": round((change / start_p * 100.0) if start_p > 0 else 0.0, 2),
                "high": round(rec["high"], 2),
                "low": round(rec["low"], 2),
                "volume": round(rec["volume"], 2),
                "lastUpdate": rec["time_str"],
                "timestamp": rec.get("timestamp"),
                "priceType": "closing",
                **self._duration_stats(current, starts, start_p),
            }
        except DB_READ_FAILURES as e:
            logger.debug("Failed retrieving DB summary for %s: %s", symbol, e)
            return None

    def _empty_summary(self, symbol: str) -> dict[str, Any]:
        """The shape every summary caller expects, with nothing to report."""
        return {
            "symbol": symbol,
            "currentPrice": 0.0,
            "change": 0.0,
            "changePercent": 0.0,
            "high": 0.0,
            "low": 0.0,
            "volume": 0.0,
            "lastUpdate": None,
            "timestamp": None,
            "priceType": "placeholder",
        }

    def get_summary(
        self, symbol: str, duration: str = "24h", use_db: bool = True
    ) -> dict[str, Any]:
        """Thread-safe summary calculation including price change based on interval.

        Args:
            symbol: Stock/crypto symbol
            duration: Time interval for price change calculation (1h, 24h, 5d, 30d, 1y, ytd)
            use_db: Whether to query the database for historical anchors or cold symbols
        Returns:
            Dict with symbol, currentPrice, change, changePercent, high, low, volume, lastUpdate
                  plus all duration-based price changes and percentage changes.
        """
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())

            # If no history in buffer, check database so symbols like AAPL/TSLA never show 0.00 on restart
            if not history:
                if use_db:
                    db_summary = self._db_only_summary(symbol, duration)
                    if db_summary:
                        return db_summary
                return self._empty_summary(symbol)

            latest_tick = history[-1]
            earliest_tick = history[0]
            current = latest_tick.price
            open_price = self._open_prices.get(symbol, earliest_tick.price)

            # Per-duration changes, measured from the stored interval anchors (or buffer open when use_db=False).
            starts = self._interval_starts(symbol) if use_db else {}
            dur_stats = self._duration_stats(current, starts, open_price)

            # Change for the duration the caller asked about.
            if use_db:
                change, change_pct = self._change_for_duration(
                    symbol, current, duration, open_price, starts=starts
                )
            else:
                change = current - open_price
                change_pct = ((change / open_price) * 100.0) if open_price > 0 else 0.0

            high_price = self._running_high.get(symbol, current)
            low_price = self._running_low.get(symbol, current)
            total_vol = self._running_volume.get(symbol, 0.0)

            result = {
                "symbol": symbol,
                "currentPrice": round(current, 2),
                "change": round(change, 2),
                "changePercent": round(change_pct, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "volume": round(total_vol, 2),
                "lastUpdate": hhmmss(latest_tick.timestamp),
                "timestamp": latest_tick.timestamp,
                "priceType": "live",
            }
            result.update(dur_stats)
            return result


class DecisionBuffer:
    """Thread-safe buffer storing trading decisions."""

    def __init__(self, max_decisions: int = 100):
        self.max_decisions = max_decisions
        self._history: dict[str, deque] = {}
        self._lock = threading.Lock()

    def add_decision(
        self,
        symbol: Any,
        action: str | None = None,
        confidence: float = 0.75,
        reasoning: list[str] | None = None,
        timestamp: Any | None = None
    ) -> TradingDecision:
        if isinstance(symbol, TradingDecision):
            decision = symbol
            sym = decision.symbol.upper()
        elif hasattr(symbol, "symbol") and hasattr(symbol, "action"):
            sym = str(symbol.symbol).upper()
            decision = TradingDecision(
                symbol=sym,
                action=str(symbol.action).upper(),
                confidence=float(getattr(symbol, "confidence", confidence)),
                reasoning=getattr(symbol, "reasoning", reasoning) or [],
                timestamp=to_utc(timestamp) if timestamp is not None else now_utc()
            )
        else:
            sym = str(symbol).upper()
            decision = TradingDecision(
                symbol=sym,
                action=str(action or "HOLD").upper(),
                confidence=float(confidence),
                reasoning=reasoning or [],
                timestamp=to_utc(timestamp) if timestamp is not None else now_utc()
            )

        with self._lock:
            if sym not in self._history:
                self._history[sym] = deque(maxlen=self.max_decisions)
            self._history[sym].append(decision)

        return decision

    def get_latest_decision(self, symbol: str) -> TradingDecision | None:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history and len(history) > 0:
                return history[-1]
            return None

    def get_history(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if not history or limit <= 0:
                return []
            items = list(itertools.islice(history, max(0, len(history) - limit), None))
            return [d.to_dict() for d in reversed(items)]


# ============================================================================
# Global Buffers
# ============================================================================

market_buffer = MarketDataBuffer()
decision_buffer = DecisionBuffer()
_db_manager_instance: Any | None = None
_db_is_available: bool = True
_db_last_attempt_time: float = 0.0
_DB_RETRY_INTERVAL: float = 15.0  # seconds before attempting reconnect if DB failed


def get_db_manager():
    """Lazily instantiate or retrieve DatabaseManager."""
    global _db_manager_instance
    if _db_manager_instance is None:
        try:
            from src.database import DatabaseManager
            _db_manager_instance = DatabaseManager()
        except DB_READ_FAILURES as e:
            logger.debug("DatabaseManager not initialized in web_server: %s", e)
    return _db_manager_instance


def _query_historical_ticks_safe_for_buffer(
    db: Any,
    symbol: str,
    start_time: datetime | None,
    limit: int
) -> list[dict[str, Any]]:
    """
    Non-async version for buffer usage. Queries historical market data synchronously.
    Note: This is only used when buffer has no history (for market indices).
    """
    global _db_is_available, _db_last_attempt_time
    now_ts = time.time()
    if not _db_is_available and (now_ts - _db_last_attempt_time < _DB_RETRY_INTERVAL):
        return []

    _db_last_attempt_time = now_ts
    try:
        ticks = db.get_historical_market_data(
            symbol=symbol,
            start_time=start_time,
            limit=limit
        )
        _db_is_available = True
        return ticks or []
    except DB_READ_FAILURES as db_err:
        _db_is_available = False
        logger.debug("Database query for historical market data (buffer) failed: %s", db_err)
        return []


async def _query_historical_ticks_safe(
    db: Any,
    symbol: str,
    start_time: datetime | None,
    limit: int
) -> list[dict[str, Any]]:
    """
    Safely queries historical market data in a background thread with a fast timeout
    and circuit-breaker behavior so slow/unreachable databases never block the web server.
    """
    global _db_is_available, _db_last_attempt_time
    now_ts = time.time()
    if not _db_is_available and (now_ts - _db_last_attempt_time < _DB_RETRY_INTERVAL):
        return []

    _db_last_attempt_time = now_ts
    try:
        ticks = await asyncio.wait_for(
            asyncio.to_thread(
                db.get_historical_market_data,
                symbol=symbol,
                start_time=start_time,
                limit=limit
            ),
            timeout=1.0
        )
        _db_is_available = True
        return ticks or []
    except DB_READ_FAILURES as db_err:
        _db_is_available = False
        logger.debug("Database query for historical market data skipped or failed: %s", db_err)
        return []


def compute_recommendation(
    symbol: str, summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return the recorded decision for a symbol, or a momentum fallback.

    The fallback is what runs when nothing has been decided yet: it reads the
    price change the buffer last saw and compares it with a fixed band. Its
    reasoning names that, because the dashboard prints these strings next to the
    confidence it prints, and a string claiming the Bellman model ran would be
    showing a number the model never produced.
    """
    latest = decision_buffer.get_latest_decision(symbol)
    if latest:
        return latest.to_dict()

    summary = summary if summary is not None else market_buffer.get_summary(symbol)
    change = summary.get("changePercent", 0.0)

    # The band is the whole rule: outside it the direction of the move is the
    # recommendation, inside it nothing is recommended.
    if change > 0.15:
        action = "BUY"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Upward price momentum (+{change:.2f}%)",
            "Momentum crossed the +0.15% buy threshold",
            "No model decision recorded for this symbol yet"
        ]
    elif change < -0.15:
        action = "SELL"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Downward price pressure ({change:.2f}%)",
            "Momentum crossed the -0.15% sell threshold",
            "No model decision recorded for this symbol yet"
        ]
    else:
        action = "HOLD"
        conf = 0.70
        reasons = [
            "Price consolidating in neutral zone",
            "Momentum inside the +/-0.15% hold band",
            "No model decision recorded for this symbol yet"
        ]

    return {
        "symbol": symbol,
        "action": action,
        "confidence": round(conf, 2),
        "reasoning": reasons,
        "timestamp": epoch_seconds(now_utc()),
        "time_str": hhmmss(now_utc())
    }


def resolve_market_index(index: str) -> dict[str, Any]:
    """Resolve one market index from live buffer ticks or the persistent index store."""
    idx_key = index.upper()
    display_name = INDEX_DISPLAY_NAMES.get(idx_key, idx_key)
    ticker = INDEX_TICKERS.get(idx_key, idx_key)
    clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
    candidates = INDEX_CANDIDATE_SYMBOLS.get(idx_key, [idx_key, clean_ticker])

    live_found = False
    for sym in candidates:
        tick = market_buffer.get_latest_tick(sym)
        if tick and tick.price > 0:
            open_p = market_buffer.get_open_price(sym, tick.price)
            change = tick.price - open_p if open_p > 0 else 0.0
            change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
            index_store.update_index(
                index=idx_key,
                current_price=tick.price,
                change=change,
                change_percent=change_pct,
                last_update=hhmmss(tick.timestamp),
                price_type="live",
                timestamp=tick.timestamp,
            )
            live_found = True
            break

    res = index_store.get_index(idx_key)
    if not live_found:
        if res.get("currentPrice") is not None and res.get("priceType") != "placeholder":
            res["priceType"] = (
                "closing"
                if res.get("priceType") == "live"
                else res.get("priceType", "closing")
            )
        else:
            res["currentPrice"] = None
            res["priceType"] = "placeholder"
    res["displayName"] = display_name
    res["ticker"] = clean_ticker
    return res


async def fetch_tracked_indexes() -> list[dict[str, Any]]:
    """Fetch data for all tracked market indices from persistent store.
    Uses fast in-memory checks without historical database lookups.
    """
    return [resolve_market_index(idx_key) for idx_key in TRACKED_INDEXES]



