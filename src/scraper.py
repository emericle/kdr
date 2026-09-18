import os
import threading
import queue
import time
import logging
import datetime
import sys
import asyncio
import signal
import argparse

if sys.version_info < (3, 14):
    print("Error: This application requires Python 3.14 or greater.")
    sys.exit(1)

# Ensure project root is in sys.path when executed directly
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from src.database import DatabaseManager
from src.config_gatekeeper import validate_configs
from src.state_mapper import StateMapper

try:
    from alpaca_trade_api.stream import Stream as AlpacaTradeStream
except ImportError:
    AlpacaTradeStream = None

# Configure Logging
DEBUG_MODE = False  # Default to false unless specified

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("DataIngestion")

def setup_logging(debug: bool = False):
    global DEBUG_MODE
    DEBUG_MODE = debug
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

@dataclass
class Tick:
    symbol: str
    price: float
    size: float
    timestamp: datetime.datetime

class DataStreamBuffer:
    """Thread-safe buffer to hold ticks for aggregation with O(1) running OHLCV state and integer buckets."""
    def __init__(self):
        # Nested dict: {symbol: {minute_string: [ticks]}}
        self.buffer: Dict[str, Dict[str, List[dict]]] = {}
        # Running OHLCV: {symbol: {minute_bucket_int: {"key": minute_string, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}}}
        self._running_bars: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _parse_timestamp(ts: Any) -> datetime.datetime:
        if isinstance(ts, str):
            return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if isinstance(ts, (int, float)):
            if ts > 1e11:
                return datetime.datetime.fromtimestamp(ts / 1e9, tz=datetime.timezone.utc)
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        if isinstance(ts, datetime.datetime):
            return ts
        return datetime.datetime.now()

    @staticmethod
    def _minute_bucket(ts: datetime.datetime) -> int:
        """Returns integer epoch minute bucket for fast numerical comparisons."""
        return int(ts.timestamp() // 60)

    @staticmethod
    def _minute_key(ts: datetime.datetime) -> str:
        """Minute key string generation."""
        return f"{ts.year:04d}-{ts.month:02d}-{ts.day:02d} {ts.hour:02d}:{ts.minute:02d}"

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
                self.buffer[symbol][minute_key] = []
            else:
                bar = self._running_bars[symbol][bucket]
                minute_key = str(bar["key"])
                # Standard OHLC semantics: open remains first tick, close updates to latest tick
                bar["high"] = max(bar["high"], price)
                bar["low"] = min(bar["low"], price)
                bar["close"] = price
                bar["volume"] += size

            self.buffer[symbol][minute_key].append(tick_data)

    def get_ready_bars(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        # Returns bars that are older than current minute in O(1) per ready bar using integer comparison.
        with self.lock:
            now = datetime.datetime.now()
            current_bucket = self._minute_bucket(now)
            ready_data: Dict[str, Dict[str, Any]] = {}
            for symbol in symbols:
                if symbol not in self._running_bars:
                    continue
                for bucket in list(self._running_bars[symbol].keys()):
                    if bucket < current_bucket:
                        bar = self._running_bars[symbol][bucket]
                        minute_key = str(bar["key"])
                        ready_data[symbol] = {
                            "timestamp": minute_key,
                            "open": bar["open"],
                            "high": bar["high"],
                            "low": bar["low"],
                            "close": bar["close"],
                            "volume": bar["volume"]
                        }
                        del self._running_bars[symbol][bucket]
                        if symbol in self.buffer and minute_key in self.buffer[symbol]:
                            del self.buffer[symbol][minute_key]
            return ready_data

class AlpacaStreamProcessor:
    """Handles the production of data from Alpaca streams and maps to domain state."""
    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        state_mapper: Optional[StateMapper] = None,
        symbols: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        data_feed: str = "iex",
        data_queue: Optional[queue.Queue] = None,
        stream_client: Optional[Any] = None,
    ):
        self.db_manager = db_manager
        self.state_mapper = state_mapper
        self.symbols: List[str] = [s.upper() for s in (symbols or ["AAPL", "TSLA"])]
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = base_url or os.getenv("ALPACA_BASE_URL", "")
        self.data_feed = data_feed
        self.data_queue = data_queue
        self.buffer = DataStreamBuffer()
        self.running = True
        self.stream_client = stream_client
        self._stream_thread: Optional[threading.Thread] = None
        self._flusher_thread: Optional[threading.Thread] = None

    def _extract_market_payload(self, msg: Any) -> Optional[Dict[str, Any]]:
        """Unified normalization helper for raw tick or bar messages."""
        if msg is None:
            return None
        try:
            if isinstance(msg, dict):
                symbol = msg.get('symbol') or msg.get('S')
                price = msg.get('price') if 'price' in msg else msg.get('p')
                size = msg.get('size') if 'size' in msg else (msg.get('qty') or msg.get('s', 0.0))
                open_p = msg.get('open') if 'open' in msg else msg.get('o')
                high_p = msg.get('high') if 'high' in msg else msg.get('h')
                low_p = msg.get('low') if 'low' in msg else msg.get('l')
                close_p = msg.get('close') if 'close' in msg else msg.get('c')
                vol = msg.get('volume') if 'volume' in msg else msg.get('v')
                ts = msg.get('timestamp') or msg.get('t')
            else:
                symbol = getattr(msg, 'symbol', None)
                price = getattr(msg, 'price', None)
                size = getattr(msg, 'size', getattr(msg, 'qty', 0.0))
                open_p = getattr(msg, 'open', None)
                high_p = getattr(msg, 'high', None)
                low_p = getattr(msg, 'low', None)
                close_p = getattr(msg, 'close', None)
                vol = getattr(msg, 'volume', None)
                ts = getattr(msg, 'timestamp', None)

            if not symbol:
                return None

            parsed_ts = DataStreamBuffer._parse_timestamp(ts)
            return {
                'symbol': str(symbol).upper(),
                'price': float(price) if price is not None else None,
                'size': float(size) if size is not None else 0.0,
                'open': float(open_p) if open_p is not None else None,
                'high': float(high_p) if high_p is not None else None,
                'low': float(low_p) if low_p is not None else None,
                'close': float(close_p) if close_p is not None else None,
                'volume': float(vol) if vol is not None else float(size or 0.0),
                'timestamp': parsed_ts
            }
        except Exception as e:
            logger.warning("Error normalizing payload: %s", e)
            return None

    def _parse_trade(self, trade: Any) -> Optional[Dict[str, Any]]:
        """Parses a trade dictionary or Alpaca Trade entity into a normalized tick dictionary."""
        payload = self._extract_market_payload(trade)
        if not payload or payload.get('price') is None:
            return None
        return {
            'symbol': payload['symbol'],
            'price': payload['price'],
            'size': payload['size'],
            'timestamp': payload['timestamp']
        }

    def handle_trade(self, trade: Any) -> None:
        """Processes an incoming tick and stores it in the aggregation buffer."""
        parsed = self._parse_trade(trade)
        if not parsed:
            return

        symbol = parsed['symbol']
        self.buffer.add_tick(symbol, parsed)
        if DEBUG_MODE:
            logger.debug("Received tick for %s: price=%s, size=%s", symbol, parsed['price'], parsed['size'])

        if self.state_mapper:
            try:
                self.state_mapper.map_tick_data_to_state([parsed])
            except Exception as e:
                logger.debug("State mapping error: %s", e)

    async def _async_handle_trade(self, trade: Any) -> None:
        """Async callback for Alpaca websocket stream trade subscriptions."""
        self.handle_trade(trade)

    def handle_bar(self, bar: Any) -> None:
        """Processes an incoming OHLC bar and routes it directly to the worker queue."""
        payload = self._extract_market_payload(bar)
        if not payload:
            return

        bar_dict = {
            'symbol': payload['symbol'],
            'open': payload['open'] or 0.0,
            'high': payload['high'] or 0.0,
            'low': payload['low'] or 0.0,
            'close': payload['close'] or (payload['price'] or 0.0),
            'volume': payload['volume'],
            'timestamp': payload['timestamp']
        }
        if self.data_queue:
            self.data_queue.put((bar_dict['symbol'], bar_dict))

    async def _async_handle_bar(self, bar: Any) -> None:
        """Async callback for Alpaca websocket stream bar subscriptions."""
        self.handle_bar(bar)

    def flush_ready_bars(self) -> Dict[str, Dict[str, Any]]:
        """Returns ready minute bars and pushes them to the queue if available."""
        ready_bars = self.buffer.get_ready_bars(self.symbols)
        if self.data_queue:
            for sym, bar in ready_bars.items():
                self.data_queue.put((sym, bar))
        return ready_bars

    def _flusher_loop(self) -> None:
        """Background thread loop that flushes ready bars every second."""
        while self.running:
            try:
                self.flush_ready_bars()
            except Exception as e:
                logger.debug("Flusher loop error: %s", e)
            time.sleep(1)

    def _init_stream_client(self):
        """Initializes the Alpaca Trade Stream client if credentials are present."""
        if self.stream_client is not None:
            return self.stream_client
        if AlpacaTradeStream is None:
            logger.info("alpaca_trade_api not available; operating without live stream client.")
            return None
        if not (self.api_key and self.api_secret):
            logger.info("Alpaca credentials not configured; stream client not started.")
            return None
        try:
            kwargs: Dict[str, Any] = {
                "key_id": self.api_key,
                "secret_key": self.api_secret,
                "data_feed": self.data_feed,
                "raw_data": True
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            return AlpacaTradeStream(**kwargs)
        except Exception as e:
            logger.warning("Failed to initialize Alpaca Stream client: %s", e)
            return None

    def _run_stream(self) -> None:
        """Starts the blocking stream loop."""
        try:
            if self.stream_client and hasattr(self.stream_client, "run"):
                self.stream_client.run()
        except Exception as e:
            logger.error("Error in Alpaca stream run: %s", e)

    def start(self, run_in_background: bool = False) -> None:
        """Starts stream subscriptions and flusher."""
        self.running = True
        logger.info("Alpaca Stream Processor started.")
        logger.info("State mapper initialized for domain state mapping")

        if self.stream_client is None:
            self.stream_client = self._init_stream_client()

        if self.stream_client:
            try:
                if hasattr(self.stream_client, "subscribe_trades"):
                    self.stream_client.subscribe_trades(self._async_handle_trade, *self.symbols)
                if hasattr(self.stream_client, "subscribe_bars"):
                    self.stream_client.subscribe_bars(self._async_handle_bar, *self.symbols)
            except Exception as e:
                logger.warning("Failed to subscribe stream client to %s: %s", self.symbols, e)

        if self.data_queue and (self._flusher_thread is None or not self._flusher_thread.is_alive()):
            self._flusher_thread = threading.Thread(target=self._flusher_loop, daemon=True)
            self._flusher_thread.start()

        if self.stream_client:
            if run_in_background:
                self._stream_thread = threading.Thread(target=self._run_stream, daemon=True)
                self._stream_thread.start()
            else:
                self._run_stream()

    def stop(self) -> None:
        """Stops the stream processor and background threads."""
        self.running = False
        if self.stream_client and hasattr(self.stream_client, "stop"):
            try:
                self.stream_client.stop()
            except Exception as e:
                logger.debug("Error stopping stream client: %s", e)
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=2)
        if self._flusher_thread and self._flusher_thread.is_alive():
            self._flusher_thread.join(timeout=2)
        logger.info("Alpaca Stream Processor stopped.")

class DBWriterWorker(threading.Thread):
    """Consumer thread that processes buffered data in batches, maps to domain state, and writes to Database."""
    def __init__(
        self,
        db_manager: DatabaseManager,
        state_mapper: StateMapper,
        data_queue: queue.Queue,
        batch_size: int = 50
    ):
        super().__init__(daemon=True)
        self.db_manager = db_manager
        self.state_mapper = state_mapper
        self.queue = data_queue
        self.running = True
        self.batch_size = batch_size

    def run(self) -> None:
        logger.info("DB Writer Worker started.")
        logger.info("Domain state mapper attached for data transformation")

        while self.running:
            try:
                # Use a timeout so we can periodically check the running flag
                item = self.queue.get(timeout=1)
                if item is None:  # Sentinel value to stop worker
                    break

                batch = [item]
                while len(batch) < self.batch_size:
                    try:
                        next_item = self.queue.get_nowait()
                        if next_item is None:
                            self.queue.put(None)
                            break
                        batch.append(next_item)
                    except queue.Empty:
                        break

                bars_to_write = []
                for symbol, bar_data in batch:
                    logger.info("Processing %s at %s", symbol, bar_data['timestamp'])

                    try:
                        tick_data = {
                            'symbol': symbol,
                            'price': bar_data['close'],
                            'size': bar_data['volume'],
                            'timestamp': bar_data['timestamp']
                        }
                        self.state_mapper.map_tick_data_to_state([tick_data])
                    except Exception as map_error:
                        logger.debug(f"Mapping tick to state: {map_error}")

                    bars_to_write.append(bar_data)

                if bars_to_write:
                    self.db_manager.add_market_data(bars_to_write)

                for _ in batch:
                    self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Error in DBWriterWorker: %s", e)
                time.sleep(1)

    def save_to_db(self, symbol: str, bar_data: dict) -> None:
        if DEBUG_MODE:
            logger.debug("Entering save_to_db for %s", symbol)
        if DEBUG_MODE:
            logger.debug("Saving record for %s at %s", symbol, bar_data['timestamp'])
        # Database writing logic here...

def normalize_symbols(symbols: Optional[Union[str, List[str]]]) -> List[str]:
    """Helper to normalize string, comma-separated string, or list of symbols to uppercase."""
    if not symbols:
        return ["AAPL", "TSLA"]
    if isinstance(symbols, str):
        raw_list = symbols.split(",")
    else:
        raw_list = symbols
    cleaned = [s.strip().upper() for s in raw_list if s and s.strip()]
    return cleaned or ["AAPL", "TSLA"]

def run_worker_threads(
    symbols: Optional[Union[str, List[str]]] = None,
    duration_seconds: Optional[float] = None,
    continuous: bool = True,
    mock_fallback: bool = False,
    stop_event: Optional[threading.Event] = None
) -> None:
    validate_configs()
    db_manager = DatabaseManager()
    state_mapper = StateMapper()

    # Explicitly initialize the database tables
    logger.info("Initializing database...")
    db_manager.connection.init_db()
    logger.info("Database initialization complete: %s", db_manager.connection.engine)

    data_queue: queue.Queue = queue.Queue()
    writer = DBWriterWorker(db_manager, state_mapper, data_queue)
    writer.start()
    logger.info("Producer and Consumer threads initialized.")
    logger.info("Data pipeline ready: Alpaca → Adapter → Domain State → DB")

    resolved_symbols = normalize_symbols(symbols)
    processor = AlpacaStreamProcessor(
        db_manager=db_manager,
        state_mapper=state_mapper,
        symbols=resolved_symbols,
        data_queue=data_queue
    )

    if not mock_fallback:
        processor.start(run_in_background=True)

    if stop_event is None:
        stop_event = threading.Event()

    try:
        if mock_fallback:
            for sym in resolved_symbols:
                dummy_bar = {
                    "timestamp": datetime.datetime.now(),
                    "open": 150.0,
                    "high": 160.0,
                    "low": 140.0,
                    "close": 155.0,
                    "volume": 1000
                }
                if DEBUG_MODE:
                    logger.debug("Injecting mock bar for %s into queue...", sym)
                data_queue.put((sym, dummy_bar))

        if continuous:
            logger.info("Running in continuous mode for symbols: %s. Press Ctrl+C to stop.", resolved_symbols)
            start_time = time.time()
            while not stop_event.is_set():
                if duration_seconds is not None and (time.time() - start_time) >= duration_seconds:
                    logger.info("Duration of %s seconds reached. Stopping...", duration_seconds)
                    break
                if hasattr(writer, "is_alive") and not writer.is_alive():
                    logger.warning("DBWriterWorker thread terminated unexpectedly.")
                    break
                stop_event.wait(timeout=0.5)
        else:
            timeout = duration_seconds if duration_seconds is not None else 1.0
            stop_event.wait(timeout=timeout)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    finally:
        # Stop processor and worker
        if not mock_fallback:
            logger.info("Stopping stream processor...")
            processor.stop()
        if DEBUG_MODE:
            logger.debug("Sending sentinel value to stop.")
        data_queue.put(None)
        writer.running = False  # Ensure the loop finishes
        logger.info("Worker command sent, waiting for DB writer...")
        if hasattr(writer, "join"):
            try:
                writer.join(timeout=3)
            except Exception as e:
                logger.debug("Error joining writer: %s", e)
        logger.info("Worker finished.")

def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KDR Data Ingestion and Streaming Processor")
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument(
        '--continuous',
        dest='continuous',
        action='store_true',
        default=True,
        help='Run continuously (default: True)'
    )
    parser.add_argument(
        '--no-continuous',
        dest='continuous',
        action='store_false',
        help='Run once without continuous streaming'
    )
    parser.add_argument(
        '--symbols',
        type=str,
        default=None,
        help='Comma-separated list of symbols (e.g. AAPL,TSLA,MSFT)'
    )
    parser.add_argument(
        '--duration',
        type=float,
        default=None,
        help='Duration in seconds to run before shutting down (optional)'
    )
    parser.add_argument(
        '--mock',
        action='store_true',
        help='Inject mock fallback bar for testing'
    )
    return parser.parse_args(args)

if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.debug)

    stop_event = threading.Event()
    def _sig_handler(sig, frame):
        logger.info("Received signal %s, initiating graceful shutdown...", sig)
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig_handler)
        except (ValueError, AttributeError):
            pass

    run_worker_threads(
        symbols=args.symbols,
        duration_seconds=args.duration,
        continuous=args.continuous,
        mock_fallback=args.mock,
        stop_event=stop_event
    )
