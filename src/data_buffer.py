"""Minute-bucketed tick buffer: the aggregation half of the pipeline."""
import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.times import epoch_minute, from_epoch, minute_key, now_utc, to_utc

logger = logging.getLogger("DataIngestion")


@dataclass
class Tick:
    symbol: str
    price: float
    size: float
    timestamp: datetime

class DataStreamBuffer:
    """Thread-safe buffer to hold ticks for aggregation with O(1) running OHLCV state and integer buckets."""
    def __init__(self, max_ticks_per_minute: int = 1000):
        self.max_ticks_per_minute = max_ticks_per_minute
        # Nested dict: {symbol: {minute_string: deque[ticks]}}
        self.buffer: dict[str, dict[str, deque[dict]]] = {}
        # Running OHLCV: {symbol: {minute_bucket_int: {"key": minute_string, "open": ...,
# "high": ..., "low": ..., "close": ..., "volume": ...}}}
        self._running_bars: dict[str, dict[int, dict[str, Any]]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _parse_timestamp(ts: Any) -> datetime:
        """Read a tick timestamp in any shape as a UTC datetime."""
        if ts is None:
            return now_utc()
        return to_utc(ts)

    @staticmethod
    def _minute_bucket(ts: datetime) -> int:
        """Returns integer UTC epoch minute bucket for fast numerical comparisons."""
        return epoch_minute(ts)

    @staticmethod
    def _minute_key(ts: datetime) -> str:
        """Minute key string generation."""
        return minute_key(ts)

    def add_tick(self, symbol: str, tick_data: dict) -> None:
        with self.lock:
            if symbol not in self.buffer:
                self.buffer[symbol] = {}
                self._running_bars[symbol] = {}

            ts = self._parse_timestamp(tick_data['timestamp'])
            bucket = self._minute_bucket(ts)
            price = float(tick_data.get('price', 0.0))
            size = float(tick_data.get('size', 0.0))

            if bucket not in self._running_bars[symbol]:
                minute_key = self._minute_key(ts)
                self._running_bars[symbol][bucket] = {
                    "key": minute_key,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": size
                }
                self.buffer[symbol][minute_key] = deque(maxlen=self.max_ticks_per_minute)
            else:
                bar = self._running_bars[symbol][bucket]
                minute_key = str(bar["key"])
                # Standard OHLC semantics: open remains first tick, close updates to latest tick
                bar["high"] = max(bar["high"], price)
                bar["low"] = min(bar["low"], price)
                bar["close"] = price
                bar["volume"] += size

            self.buffer[symbol][minute_key].append(tick_data)

    def get_ready_bars(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        # Returns bars that are older than current minute in O(1) per ready bar using integer comparison.
        with self.lock:
            now = now_utc()
            current_bucket = self._minute_bucket(now)
            ready_data: dict[str, dict[str, Any]] = {}
            for symbol in symbols:
                if symbol not in self._running_bars:
                    continue
                for bucket in list(self._running_bars[symbol].keys()):
                    if bucket < current_bucket:
                        bar = self._running_bars[symbol][bucket]
                        bar_key = str(bar["key"])
                        ready_data[symbol] = {
                            "timestamp": from_epoch(bucket * 60),
                            "open": bar["open"],
                            "high": bar["high"],
                            "low": bar["low"],
                            "close": bar["close"],
                            "volume": bar["volume"]
                        }
                        del self._running_bars[symbol][bucket]
                        if symbol in self.buffer and bar_key in self.buffer[symbol]:
                            del self.buffer[symbol][bar_key]
            return ready_data

