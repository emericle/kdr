"""The dashboard's data layer: buffers, the persistent index store, DB access.

This half owns everything the routes read from: the thread-safe tick and decision
buffers, the persisted index store, and the lazy database handle. The routes
themselves live in :mod:`src.web_server.api_layer`, which imports this module.
"""

import asyncio
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

logger = logging.getLogger("WebServer")

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

DEFAULT_INDEX_VALUATIONS = {
    "VIX": {
        "index": "VIX",
        "displayName": "VIX",
        "ticker": "^VIX",
        "currentPrice": 15.25,
        "change": -0.45,
        "changePercent": -2.87,
        "priceType": "persisted",
        "lastUpdate": "16:00:00"
    },
    "DJIA": {
        "index": "DJIA",
        "displayName": "DJIA",
        "ticker": "^DJI",
        "currentPrice": 43910.50,
        "change": 145.20,
        "changePercent": 0.33,
        "priceType": "persisted",
        "lastUpdate": "16:00:00"
    },
    "SP500": {
        "index": "SP500",
        "displayName": "S&P 500",
        "ticker": "SPX",
        "currentPrice": 5865.75,
        "change": 22.40,
        "changePercent": 0.38,
        "priceType": "persisted",
        "lastUpdate": "16:00:00"
    },
    "NASDAQ": {
        "index": "NASDAQ",
        "displayName": "NASDAQ",
        "ticker": "^IXIC",
        "currentPrice": 18518.60,
        "change": 115.30,
        "changePercent": 0.63,
        "priceType": "persisted",
        "lastUpdate": "16:00:00"
    },
    "RUSSELL2000": {
        "index": "RUSSELL2000",
        "displayName": "Russel 2k",
        "ticker": "RUT",
        "currentPrice": 2240.10,
        "change": 12.80,
        "changePercent": 0.57,
        "priceType": "persisted",
        "lastUpdate": "16:00:00"
    }
}


class IndexStore:
    """Thread-safe persistent store for index valuations."""
    def __init__(self, cache_file: str = INDEX_CACHE_FILE):
        self.cache_file = cache_file
        self._lock = threading.Lock()
        self._indexes: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self):
        with self._lock:
            self._indexes = {k: dict(v) for k, v in DEFAULT_INDEX_VALUATIONS.items()}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file) as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            for k, v in saved.items():
                                k_upper = k.upper()
                                if isinstance(v, dict):
                                    if k_upper in self._indexes:
                                        self._indexes[k_upper].update(v)
                                    else:
                                        self._indexes[k_upper] = dict(v)
                except CACHE_FAILURES as e:
                    logger.debug("Failed loading index cache: %s", e)

    def _save_unlocked(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(self._indexes, f, indent=2)
        except CACHE_FAILURES as e:
            logger.debug("Failed saving index cache: %s", e)

    def save(self):
        with self._lock:
            self._save_unlocked()

    def update_index(
        self,
        index: str,
        current_price: float,
        change: float | None = None,
        change_percent: float | None = None,
        last_update: str | None = None,
        price_type: str = "live"
    ):
        index = index.upper()
        with self._lock:
            if index not in self._indexes:
                display_name = INDEX_DISPLAY_NAMES.get(index, index)
                ticker = INDEX_TICKERS.get(index, index)
                clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
                self._indexes[index] = {
                    "index": index,
                    "displayName": display_name,
                    "ticker": clean_ticker,
                    "currentPrice": current_price,
                    "openPrice": current_price,
                    "change": 0.0,
                    "changePercent": 0.0,
                    "priceType": price_type,
                    "lastUpdate": last_update or hhmmss(now_utc())
                }
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
            self._save_unlocked()

    def get_index(self, index: str) -> dict[str, Any]:
        index = index.upper()
        with self._lock:
            if index in self._indexes:
                return dict(self._indexes[index])
            display_name = INDEX_DISPLAY_NAMES.get(index, index)
            ticker = INDEX_TICKERS.get(index, index)
            clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
            return {
                "index": index,
                "displayName": display_name,
                "ticker": clean_ticker,
                "currentPrice": 0.0,
                "change": 0.0,
                "changePercent": 0.0,
                "priceType": "no_data",
                "lastUpdate": None
            }

    def get_all(self, tracked: list[str] | None = None) -> list[dict[str, Any]]:
        indices = tracked or TRACKED_INDEXES
        with self._lock:
            results = []
            for idx in indices:
                idx_upper = idx.upper()
                if idx_upper in self._indexes:
                    results.append(dict(self._indexes[idx_upper]))
                else:
                    results.append(self.get_index(idx))
            return results


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
        price_type="live"
    )


class MarketDataBuffer:
    """Thread-safe circular buffer storing recent ticks per symbol."""

    def __init__(self, max_ticks: int = 500):
        self.max_ticks = max_ticks
        self._history: dict[str, deque] = {}
        self._open_prices: dict[str, float] = {}
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
            if symbol not in self._history:
                self._history[symbol] = deque(maxlen=self.max_ticks)
                self._open_prices[symbol] = tick.price

            self._history[symbol].append(tick)
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
            if history and len(history) > 0:
                return max(reversed(history), key=lambda t: t.timestamp)
            return None

    def get_ticks(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())
            items = list(history)[-limit:]
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
                latest_tick = max(reversed(history), key=lambda t: t.timestamp)
                earliest_tick = min(history, key=lambda t: t.timestamp)
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
                return db.get_prices_at_interval_starts(symbol)
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

    def _change_for_duration(self, symbol: str, current: float, duration: str, fallback: float) -> tuple[float, float]:
        """``(change, change_percent)`` for one duration, from the DB anchor or the buffer's open."""
        db = get_db_manager()
        if db and hasattr(db, "get_price_at_interval_start"):
            try:
                anchor = db.get_price_at_interval_start(symbol, duration)
                start = float(anchor) if anchor is not None else None
            except DB_READ_FAILURES as e:
                logger.debug("Database interval price lookup failed, falling back to buffer: %s", e)
                start = None
            if start is not None and start > 0:
                change = current - start
                return change, change / start * 100.0
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
            anchor = None
            if hasattr(db, "get_price_at_interval_start"):
                anchor = db.get_price_at_interval_start(symbol, duration)
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
                **self._duration_stats(current, self._interval_starts(symbol), start_p),
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
        }

    def get_summary(self, symbol: str, duration: str = "24h") -> dict[str, Any]:
        """Thread-safe summary calculation including price change based on interval.

        Args:
            symbol: Stock/crypto symbol
            duration: Time interval for price change calculation (1h, 24h, 5d, 30d, 1y, ytd)
        Returns:
            Dict with symbol, currentPrice, change, changePercent, high, low, volume, lastUpdate
                  plus all duration-based price changes and percentage changes.
        """
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())

            # If no history in buffer, check database so symbols like AAPL/TSLA never show 0.00 on restart
            if not history:
                db_summary = self._db_only_summary(symbol, duration)
                if db_summary:
                    return db_summary
                return self._empty_summary(symbol)

            latest_tick = max(reversed(history), key=lambda t: t.timestamp)
            earliest_tick = min(history, key=lambda t: t.timestamp)
            current = latest_tick.price
            open_price = self._open_prices.get(symbol, earliest_tick.price)

            # Per-duration changes, measured from the stored interval anchors.
            dur_stats = self._duration_stats(current, self._interval_starts(symbol), open_price)

            # Change for the duration the caller asked about.
            change, change_pct = self._change_for_duration(symbol, current, duration, open_price)

            prices = [t.price for t in history if t.price > 0]
            if not prices:
                prices = [current]
            total_vol = sum(t.size for t in history)

            result = {
                "symbol": symbol,
                "currentPrice": round(current, 2),
                "change": round(change, 2),
                "changePercent": round(change_pct, 2),
                "high": round(max(prices), 2),
                "low": round(min(prices), 2),
                "volume": round(total_vol, 2),
                "lastUpdate": hhmmss(latest_tick.timestamp)
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
            history = self._history.get(symbol, deque())
            items = list(history)[-limit:]
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


def compute_recommendation(symbol: str) -> dict[str, Any]:
    """
    Returns latest decision or generates an algorithmic recommendation based on price action.
    """
    latest = decision_buffer.get_latest_decision(symbol)
    if latest:
        return latest.to_dict()

    summary = market_buffer.get_summary(symbol)
    change = summary.get("changePercent", 0.0)

    if change > 0.15:
        action = "BUY"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Upward price momentum (+{change:.2f}%)",
            "Bellman target Q-value above threshold",
            "Positive trend trajectory detected"
        ]
    elif change < -0.15:
        action = "SELL"
        conf = min(0.95, 0.65 + abs(change) * 0.1)
        reasons = [
            f"Downward price pressure ({change:.2f}%)",
            "Risk boundary triggered",
            "Bellman expected return negative"
        ]
    else:
        action = "HOLD"
        conf = 0.70
        reasons = [
            "Price consolidating in neutral zone",
            "Waiting for directional breakout signal",
            "Portfolio allocation optimal"
        ]

    return {
        "symbol": symbol,
        "action": action,
        "confidence": round(conf, 2),
        "reasoning": reasons,
        "timestamp": epoch_seconds(now_utc()),
        "time_str": hhmmss(now_utc())
    }


