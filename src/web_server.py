"""
Real-time Trading Dashboard Web Server.

Provides WebSocket streams of live market data and trading decisions,
REST APIs for historical and detail data, and serves the frontend dashboard.
Designed to run in a background thread alongside the scraper pipeline.
"""

import asyncio
import json
import os
import time
import logging
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

logger = logging.getLogger("WebServer")

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        }


@dataclass
class TradingDecision:
    """A trading recommendation or execution decision."""
    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float  # 0.0 to 1.0
    reasoning: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": round(self.confidence, 2),
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        }


# ============================================================================
# Thread-Safe Buffers
# ============================================================================

# ============================================================================
# Persistent Index Store & Mappings
# ============================================================================

INDEX_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "index_cache.json")

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

SYMBOL_TO_INDEX_MAP: Dict[str, str] = {}
for _idx_key, _sym_list in INDEX_CANDIDATE_SYMBOLS.items():
    for _s in _sym_list:
        SYMBOL_TO_INDEX_MAP[_s.upper()] = _idx_key
        _clean = _s[1:].upper() if _s.startswith("^") else _s.upper()
        SYMBOL_TO_INDEX_MAP[_clean] = _idx_key

TRACKED_INDEXES = ["VIX", "DJIA", "SP500", "NASDAQ", "RUSSELL2000"]

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
        self._indexes: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        with self._lock:
            self._indexes = {k: dict(v) for k, v in DEFAULT_INDEX_VALUATIONS.items()}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, "r") as f:
                        saved = json.load(f)
                        if isinstance(saved, dict):
                            for k, v in saved.items():
                                k_upper = k.upper()
                                if isinstance(v, dict):
                                    if k_upper in self._indexes:
                                        self._indexes[k_upper].update(v)
                                    else:
                                        self._indexes[k_upper] = dict(v)
                except Exception as e:
                    logger.debug("Failed loading index cache: %s", e)

    def _save_unlocked(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(self._indexes, f, indent=2)
        except Exception as e:
            logger.debug("Failed saving index cache: %s", e)

    def save(self):
        with self._lock:
            self._save_unlocked()

    def update_index(
        self,
        index: str,
        current_price: float,
        change: Optional[float] = None,
        change_percent: Optional[float] = None,
        last_update: Optional[str] = None,
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
                    "lastUpdate": last_update or datetime.now().strftime("%H:%M:%S")
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
            item["lastUpdate"] = last_update or datetime.now().strftime("%H:%M:%S")
            self._save_unlocked()

    def get_index(self, index: str) -> Dict[str, Any]:
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

    def get_all(self, tracked: Optional[List[str]] = None) -> List[Dict[str, Any]]:
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
    change: Optional[float] = None,
    change_percent: Optional[float] = None
):
    sym_upper = symbol.upper()
    idx_key = SYMBOL_TO_INDEX_MAP.get(sym_upper)
    if not idx_key:
        return
    time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
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
        self._history: Dict[str, deque] = {}
        self._open_prices: Dict[str, float] = {}
        self._lock = threading.Lock()

    def add_tick(
        self,
        symbol: str,
        price: float,
        size: float = 0.0,
        timestamp: Optional[Any] = None
    ) -> MarketTick:
        symbol = symbol.upper()
        ts = time.time()
        if timestamp is not None:
            if isinstance(timestamp, (int, float)):
                ts = float(timestamp if timestamp < 1e11 else timestamp / 1e9)
            elif isinstance(timestamp, datetime):
                ts = timestamp.timestamp()

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
        except Exception as e:
            logger.debug("Failed updating index from tick for %s: %s", symbol, e)

        return tick

    def get_symbols(self) -> List[str]:
        with self._lock:
            return sorted(list(self._history.keys()))

    def get_latest_tick(self, symbol: str) -> Optional[MarketTick]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history and len(history) > 0:
                return max(reversed(history), key=lambda t: t.timestamp)
            return None

    def get_ticks(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol, deque())
            items = list(history)[-limit:]
            return [t.to_dict() for t in items]

    def get_price_and_change(self, symbol: str, duration: str = "24h") -> Optional[Dict[str, Any]]:
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
                time_str = datetime.fromtimestamp(latest_tick.timestamp).strftime("%H:%M:%S")

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
                    except Exception as e:
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
                except Exception as e:
                    logger.debug(f"Database lookup for {symbol} failed: {e}")

            return None

    def get_summary(self, symbol: str, duration: str = "24h") -> Dict[str, Any]:
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
            db = get_db_manager()

            # If no history in buffer, check database so symbols like AAPL/TSLA never show 0.00 on restart
            if not history:
                if db and hasattr(db, 'get_latest_market_record'):
                    try:
                        rec = db.get_latest_market_record(symbol)
                        if rec:
                            current = rec["price"]
                            start_price = None
                            if hasattr(db, 'get_price_at_interval_start'):
                                start_price = db.get_price_at_interval_start(symbol, duration)
                            if start_price is None or float(start_price) <= 0:
                                start_price = rec["open"] or current
                            start_p = float(start_price)
                            change = current - start_p
                            change_pct = (change / start_p * 100.0) if start_p > 0 else 0.0

                            dur_stats = {}
                            for dur in ["1h", "24h", "5d", "30d", "1y", "ytd"]:
                                dur_start = None
                                if hasattr(db, 'get_price_at_interval_start'):
                                    dur_start = db.get_price_at_interval_start(symbol, dur)
                                if dur_start is None or float(dur_start) <= 0:
                                    dur_start = start_p
                                d_start = float(dur_start)
                                d_chg = current - d_start
                                d_pct = (d_chg / d_start * 100.0) if d_start > 0 else 0.0
                                dur_stats[f"change{dur}"] = round(d_chg, 2)
                                dur_stats[f"changePercent{dur}"] = round(d_pct, 2)

                            return {
                                "symbol": symbol,
                                "currentPrice": round(current, 2),
                                "change": round(change, 2),
                                "changePercent": round(change_pct, 2),
                                "high": round(rec["high"], 2),
                                "low": round(rec["low"], 2),
                                "volume": round(rec["volume"], 2),
                                "lastUpdate": rec["time_str"],
                                **dur_stats
                            }
                    except Exception as e:
                        logger.debug("Failed retrieving DB summary for %s: %s", symbol, e)

                return {
                    "symbol": symbol,
                    "currentPrice": 0.0,
                    "change": 0.0,
                    "changePercent": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "volume": 0.0,
                    "lastUpdate": None
                }

            latest_tick = max(reversed(history), key=lambda t: t.timestamp)
            earliest_tick = min(history, key=lambda t: t.timestamp)
            current = latest_tick.price

            # Calculate raw change and percentage change for all available durations
            dur_stats = {}
            for dur in ["1h", "24h", "5d", "30d", "1y", "ytd"]:
                interval_start_price = None
                try:
                    if db and hasattr(db, 'get_price_at_interval_start'):
                        interval_start_price = db.get_price_at_interval_start(symbol, dur)
                except Exception:
                    pass

                # Fallback to buffer tick if database lookup fails
                if interval_start_price is None:
                    interval_start_price = self._open_prices.get(symbol, earliest_tick.price)

                try:
                    start_val = float(interval_start_price) if interval_start_price is not None else 0.0
                    if start_val > 0:
                        d_chg = current - start_val
                        d_pct = (d_chg / start_val * 100.0)
                        dur_stats[f"change{dur}"] = round(d_chg, 2)
                        dur_stats[f"changePercent{dur}"] = round(d_pct, 2)
                    else:
                        dur_stats[f"change{dur}"] = 0.0
                        dur_stats[f"changePercent{dur}"] = 0.0
                except (ValueError, TypeError):
                    dur_stats[f"change{dur}"] = 0.0
                    dur_stats[f"changePercent{dur}"] = 0.0

            # Calculate for selected duration
            if db and hasattr(db, 'get_price_at_interval_start'):
                try:
                    interval_start_price = db.get_price_at_interval_start(symbol, duration)
                    try:
                        start_price = float(interval_start_price) if interval_start_price is not None else None
                        if start_price is not None and start_price > 0:
                            change = current - start_price
                            change_pct = (change / start_price * 100.0)
                        else:
                            initial = self._open_prices.get(symbol, earliest_tick.price)
                            change = current - initial
                            change_pct = ((current - initial) / initial * 100.0) if initial and initial > 0 else 0.0
                    except (ValueError, TypeError):
                        initial = self._open_prices.get(symbol, earliest_tick.price)
                        change = current - initial
                        change_pct = ((current - initial) / initial * 100.0) if initial and initial > 0 else 0.0
                except Exception as e:
                    logger.debug(f"Database interval price lookup failed, falling back to buffer: {e}")
                    initial = self._open_prices.get(symbol, earliest_tick.price)
                    change = current - initial
                    change_pct = ((current - initial) / initial * 100.0) if initial and initial > 0 else 0.0
            else:
                initial = self._open_prices.get(symbol, earliest_tick.price)
                change = current - initial
                change_pct = ((current - initial) / initial * 100.0) if initial and initial > 0 else 0.0

            prices = [t.price for t in history]
            total_vol = sum(t.size for t in history)

            result = {
                "symbol": symbol,
                "currentPrice": round(current, 2),
                "change": round(change, 2),
                "changePercent": round(change_pct, 2),
                "high": round(max(prices), 2),
                "low": round(min(prices), 2),
                "volume": round(total_vol, 2),
                "lastUpdate": datetime.fromtimestamp(latest_tick.timestamp).strftime("%H:%M:%S")
            }
            result.update(dur_stats)
            return result


class DecisionBuffer:
    """Thread-safe buffer storing trading decisions."""

    def __init__(self, max_decisions: int = 100):
        self.max_decisions = max_decisions
        self._history: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def add_decision(
        self,
        symbol: Any,
        action: Optional[str] = None,
        confidence: float = 0.75,
        reasoning: Optional[List[str]] = None,
        timestamp: Optional[Any] = None
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
                timestamp=time.time()
            )
        else:
            sym = str(symbol).upper()
            ts = time.time()
            if timestamp is not None:
                if isinstance(timestamp, (int, float)):
                    ts = float(timestamp if timestamp < 1e11 else timestamp / 1e9)
                elif isinstance(timestamp, datetime):
                    ts = timestamp.timestamp()

            decision = TradingDecision(
                symbol=sym,
                action=str(action or "HOLD").upper(),
                confidence=float(confidence),
                reasoning=reasoning or [],
                timestamp=ts
            )

        with self._lock:
            if sym not in self._history:
                self._history[sym] = deque(maxlen=self.max_decisions)
            self._history[sym].append(decision)

        return decision

    def get_latest_decision(self, symbol: str) -> Optional[TradingDecision]:
        symbol = symbol.upper()
        with self._lock:
            history = self._history.get(symbol)
            if history and len(history) > 0:
                return history[-1]
            return None

    def get_history(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
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
_db_manager_instance: Optional[Any] = None
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
        except Exception as e:
            logger.debug("DatabaseManager not initialized in web_server: %s", e)
    return _db_manager_instance


def _query_historical_ticks_safe_for_buffer(
    db: Any,
    symbol: str,
    start_time: Optional[datetime],
    limit: int
) -> List[Dict[str, Any]]:
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
    except Exception as db_err:
        _db_is_available = False
        logger.debug("Database query for historical market data (buffer) failed: %s", db_err)
        return []


async def _query_historical_ticks_safe(
    db: Any,
    symbol: str,
    start_time: Optional[datetime],
    limit: int
) -> List[Dict[str, Any]]:
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
    except Exception as db_err:
        _db_is_available = False
        logger.debug("Database query for historical market data skipped or failed: %s", db_err)
        return []


def compute_recommendation(symbol: str) -> Dict[str, Any]:
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
        "timestamp": time.time(),
        "time_str": datetime.now().strftime("%H:%M:%S")
    }


# ============================================================================
# WebSocket Connection Manager
# ============================================================================

class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, register: bool = True):
        await websocket.accept()
        if register:
            async with self._lock:
                self.active_connections.add(websocket)
            logger.info("WebSocket client connected. Active: %d", len(self.active_connections))

    async def register(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info("WebSocket client registered. Active: %d", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Active: %d", len(self.active_connections))

    async def broadcast_json(self, message: Dict[str, Any]):
        async with self._lock:
            clients = list(self.active_connections)

        if not clients:
            return

        dead_clients = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self.active_connections.discard(ws)


manager = ConnectionManager()


async def broadcast_loop(interval: float = 1.0):
    """Broadcasts market state and decisions every 1 second."""
    logger.info("WebSocket broadcast loop started (%ss interval)", interval)
    while True:
        try:
            symbols = market_buffer.get_symbols()
            symbol_summaries = []
            decisions = []

            for sym in symbols:
                summary = market_buffer.get_summary(sym)
                rec = compute_recommendation(sym)
                summary["recommendation"] = rec["action"]
                summary["confidence"] = rec["confidence"]
                symbol_summaries.append(summary)
                decisions.append(rec)

            # Get current market indexes
            market_indexes = await fetch_tracked_indexes()

            payload = {
                "type": "update",
                "timestamp": time.time(),
                "time_str": datetime.now().strftime("%H:%M:%S"),
                "symbols": symbol_summaries,
                "decisions": decisions,
                "market_indexes": market_indexes,
                "marketIndexes": market_indexes
            }

            await manager.broadcast_json(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Broadcast loop tick error: %s", e)

        await asyncio.sleep(interval)


# ============================================================================
# FastAPI Application & Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure index store is loaded
    index_store.load()
    task = asyncio.create_task(broadcast_loop(interval=1.0))
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="KDR Real-Time Trading Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
TEST_WS_HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "test-websocket.html")


@app.get("/", response_class=HTMLResponse)
@app.get("/symbol/{symbol}", response_class=HTMLResponse)
async def serve_dashboard(symbol: Optional[str] = None):
    if os.path.exists(STATIC_HTML_PATH):
        with open(STATIC_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard HTML template not found.</h1>", status_code=404)


@app.get("/test-websocket", response_class=HTMLResponse)
@app.get("/test-websocket.html", response_class=HTMLResponse)
async def serve_test_websocket():
    if os.path.exists(TEST_WS_HTML_PATH):
        with open(TEST_WS_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Test WebSocket HTML template not found.</h1>", status_code=404)


@app.get("/api/status")
async def get_status():
    symbols = market_buffer.get_symbols()
    return {
        "status": "online",
        "symbols_tracked": len(symbols),
        "symbols": symbols,
        "timestamp": time.time()
    }


@app.get("/api/symbols")
async def get_symbols():
    symbols = market_buffer.get_symbols()
    results = []
    for sym in symbols:
        summary = market_buffer.get_summary(sym)
        rec = compute_recommendation(sym)
        summary["recommendation"] = rec["action"]
        summary["confidence"] = rec["confidence"]
        results.append(summary)
    return JSONResponse(results)


@app.get("/api/market-index/{index}")
async def get_market_index(index: str) -> Dict[str, Any]:
    """
    Returns market index data for specific indices.
    Data is persisted so valuations are never $0.00 when markets are closed.
    Fast retrieval without historical database lookups.
    """
    index = index.upper()
    display_name = INDEX_DISPLAY_NAMES.get(index, index)
    ticker = INDEX_TICKERS.get(index, index)
    clean_ticker = ticker[1:] if ticker.startswith("^") else ticker
    candidates = INDEX_CANDIDATE_SYMBOLS.get(index, [index, clean_ticker])

    # Check live in-memory buffer ONLY (fast, non-blocking, no historical DB query)
    for sym in candidates:
        tick = market_buffer.get_latest_tick(sym)
        if tick and tick.price > 0:
            open_p = market_buffer._open_prices.get(sym.upper(), tick.price)
            change = tick.price - open_p if open_p > 0 else 0.0
            change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
            time_str = datetime.fromtimestamp(tick.timestamp).strftime("%H:%M:%S")
            index_store.update_index(
                index=index,
                current_price=tick.price,
                change=change,
                change_percent=change_pct,
                last_update=time_str,
                price_type="live"
            )
            break

    # Return persisted latest value - fast, non-blocking, never 0.00
    res = index_store.get_index(index)
    res["displayName"] = display_name
    res["ticker"] = clean_ticker
    return res


async def fetch_tracked_indexes() -> List[Dict[str, Any]]:
    """Fetch data for all tracked market indices from persistent store (never $0.00).
    Uses fast in-memory checks without historical database lookups.
    """
    for idx in TRACKED_INDEXES:
        candidates = INDEX_CANDIDATE_SYMBOLS.get(idx, [idx])
        for sym in candidates:
            # Check live in-memory buffer ONLY (no historical DB lookup)
            tick = market_buffer.get_latest_tick(sym)
            if tick and tick.price > 0:
                open_p = market_buffer._open_prices.get(sym.upper(), tick.price)
                change = tick.price - open_p if open_p > 0 else 0.0
                change_pct = (change / open_p * 100.0) if open_p > 0 else 0.0
                time_str = datetime.fromtimestamp(tick.timestamp).strftime("%H:%M:%S")
                index_store.update_index(
                    index=idx,
                    current_price=tick.price,
                    change=change,
                    change_percent=change_pct,
                    last_update=time_str,
                    price_type="live"
                )
                break
    return index_store.get_all(TRACKED_INDEXES)


@app.get("/api/market-indexes")
async def get_all_market_indexes() -> List[Dict[str, Any]]:
    """
    Returns data for all market indexes (VIX, DJIA, S&P500, NASDAQ, Russell 2000)
    in a single request for efficiency.
    """
    results = await fetch_tracked_indexes()
    return results


@app.get("/api/symbols/{symbol}")
async def get_symbol_detail(
    symbol: str,
    duration: Optional[str] = "24h",
    limit: Optional[int] = 1000
):
    symbol = symbol.upper()
    summary = market_buffer.get_summary(symbol, duration=duration or "24h")

    # Determine start_time based on requested duration window
    now = datetime.now()
    start_time: Optional[datetime] = None
    if duration == "1h":
        start_time = now - timedelta(hours=1)
    elif duration == "24h":
        start_time = now - timedelta(hours=24)
    elif duration == "5d":
        start_time = now - timedelta(days=5)
    elif duration == "30d":
        start_time = now - timedelta(days=30)
    elif duration == "1y":
        start_time = now - timedelta(days=365)
    elif duration == "ytd":
        start_time = datetime(now.year, 1, 1)

    historical_ticks = []
    db = get_db_manager()
    if db and hasattr(db, "get_historical_market_data"):
        historical_ticks = await _query_historical_ticks_safe(
            db=db,
            symbol=symbol,
            start_time=start_time,
            limit=limit or 1000
        )

    # In-memory buffer ticks
    buffer_ticks = market_buffer.get_ticks(symbol, limit=limit or 500)
    if start_time is not None:
        start_ts = start_time.timestamp()
        buffer_ticks = [t for t in buffer_ticks if t.get("timestamp", 0) >= start_ts]

    # Combine historical db data and in-memory buffer ticks without duplicates (O(N+M))
    seen_db_keys = set()
    all_ticks = []
    for t in historical_ticks:
        ts = float(t.get("timestamp", 0))
        seen_db_keys.add((round(ts, 2), t.get("price")))
        all_ticks.append(t)

    for t in buffer_ticks:
        ts = float(t.get("timestamp", 0))
        if (round(ts, 2), t.get("price")) not in seen_db_keys:
            all_ticks.append(t)

    all_ticks.sort(key=lambda x: x.get("timestamp", 0))
    if limit and len(all_ticks) > limit:
        all_ticks = all_ticks[-limit:]

    # Fallback seed tick if no ticks are available yet but summary has a live price
    if not all_ticks and summary.get("currentPrice", 0.0) > 0:
        all_ticks.append({
            "symbol": symbol,
            "price": summary["currentPrice"],
            "size": summary.get("volume", 0.0),
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S")
        })

    decision = compute_recommendation(symbol)
    decisions_history = decision_buffer.get_history(symbol, limit=20)

    # Compute moving averages (50-day and 200-day) if enough data
    prices = [t.get("price", 0.0) for t in all_ticks]
    ma_50 = round(sum(prices[-50:]) / 50.0, 2) if len(prices) >= 50 else (prices[-1] if prices else 0.0)
    ma_200 = round(sum(prices[-200:]) / 200.0, 2) if len(prices) >= 200 else (prices[-1] if prices else 0.0)

    return JSONResponse({
        "summary": summary,
        "recommendation": decision,
        "ticks": all_ticks,
        "decisions": decisions_history,
        "moving_averages": {
            "200_day": ma_200,
            "50_day": ma_50
        }
    })


@app.websocket("/ws")
@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("WebSocket request received")
    await manager.connect(websocket, register=False)
    try:
        logger.info("WebSocket connection accepted, preparing initial state")
        # Immediately send current state upon connection
        logger.info("Fetching symbols from market buffer")
        symbols = market_buffer.get_symbols()
        logger.info(f"Found {len(symbols)} symbols to report")

        summaries = []
        decisions = []
        for sym in symbols:
            s = market_buffer.get_summary(sym)
            r = compute_recommendation(sym)
            s["recommendation"] = r["action"]
            s["confidence"] = r["confidence"]
            summaries.append(s)
            decisions.append(r)

        # Get initial market indexes
        market_indexes = await fetch_tracked_indexes()

        logger.info(f"Sending init message with {len(summaries)} symbols and {len(market_indexes)} market indexes")
        await websocket.send_json({
            "type": "init",
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "symbols": summaries,
            "decisions": decisions,
            "market_indexes": market_indexes,
            "marketIndexes": market_indexes
        })
        await manager.register(websocket)
        logger.info("Init message sent successfully, entering receive loop")

        while True:
            # Keep socket open and accept incoming ping/client messages
            msg = await websocket.receive_text()
            logger.debug(f"Received message from client: {msg[:50]}")
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {type(e).__name__}: {e}", exc_info=True)
        await manager.disconnect(websocket)


# ============================================================================
# Server Thread Runner
# ============================================================================

_server_instance: Optional[uvicorn.Server] = None
_server_thread: Optional[threading.Thread] = None


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def start_web_server_thread(host: str = "0.0.0.0", port: int = 8001) -> Optional[uvicorn.Server]:
    """
    Starts the FastAPI web server in a background daemon thread.
    Returns the running uvicorn.Server instance.
    """
    global _server_instance, _server_thread

    if _server_thread is not None and _server_thread.is_alive():
        logger.info("Web server thread already running.")
        return _server_instance

    if not is_port_available(port, host):
        logger.info("Port %d is already in use. Web server thread start skipped.", port)
        return _server_instance

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False
    )
    _server_instance = uvicorn.Server(config)

    _server_thread = threading.Thread(
        target=_server_instance.run,
        daemon=True,
        name="DashboardWebServerThread"
    )
    _server_thread.start()

    logger.info("Real-time Trading Dashboard active at http://localhost:%d", port)
    return _server_instance


def stop_web_server():
    """Stops the running web server thread."""
    global _server_instance, _server_thread
    if _server_instance is not None:
        _server_instance.should_exit = True
        logger.info("Web server shutdown requested.")
    if _server_thread is not None and _server_thread.is_alive():
        try:
            _server_thread.join(timeout=2.0)
        except Exception:
            pass
    _server_instance = None
    _server_thread = None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
