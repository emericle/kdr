"""Stream client: pulls Alpaca trades and bars into the buffers."""
import logging
import os
import queue
import threading
import time
from typing import Any

from src.data_buffer import DataStreamBuffer
from src.database import DatabaseManager
from src.logging_config import is_debug
from src.state_mapper import StateMapper
from src.symbols import (
    DEFAULT_SYMBOLS,
    from_alpaca_symbol,
    is_crypto_symbol,
    normalize_symbols,
    to_alpaca_symbol,
)

# Import web_server for real-time data streaming
try:
    from src.web_server import (
        decision_buffer,
        market_buffer,
    )
    WEB_SERVER_AVAILABLE = True
except ImportError:
    WEB_SERVER_AVAILABLE = False
    # The buffers are singletons owned by web_server; without it there is nothing to
    # push to. Every use site checks WEB_SERVER_AVAILABLE first.
    market_buffer = None  # type: ignore[assignment]
    decision_buffer = None  # type: ignore[assignment]

try:
    from alpaca_trade_api.stream import Stream as AlpacaTradeStream
except ImportError:
    AlpacaTradeStream = None
logger = logging.getLogger("DataIngestion")

# The Alpaca Trade Stream connects over TLS. The SDK builds its websocket SSL
# context from the interpreter's default CA path, and an interpreter that has no
# CA bundle there (a bare CPython build, a fresh venv on the Pi) fails the
# handshake with "CERTIFICATE_VERIFY_FAILED: unable to get local issuer
# certificate". That failure is silent to the dashboard: no tick ever reaches
# the market buffer, so the charts sit at "Waiting for Market Stream" and the
# SDK retries the dead socket every ~150 ms and floods the log. certifi ships a
# CA bundle in the venv, so point the process at it before the stream starts.
# An explicit SSL_CERT_FILE in the environment wins, so a host that already
# configured its own bundle is not overridden.
def configure_ca_certificates() -> str | None:
    """Point the process's SSL at certifi's CA bundle, and return its path.

    Returns the bundle path that is now in effect, or ``None`` when certifi is
    not installed and the interpreter's default is left alone.
    """
    try:
        import certifi
    except ImportError:
        logger.warning(
            "certifi not installed; the Alpaca stream relies on the interpreter's "
            "default CA path and may fail TLS verification. Add certifi to requirements.txt."
        )
        return None
    bundle = certifi.where()
    # Honour a caller-supplied bundle: only fill the default when nothing set it.
    if not os.getenv("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = bundle
    if not os.getenv("SSL_CERT_DIR"):
        os.environ["SSL_CERT_DIR"] = os.path.dirname(bundle)
    logger.info("CA certificates for the Alpaca stream resolved to: %s", bundle)
    return bundle

# A malformed wire payload: an attribute the Alpaca entity does not have, a
# value that will not convert, a key the payload did not carry.
PAYLOAD_FAILURES = (AttributeError, TypeError, ValueError, KeyError)

# The same, plus what a socket or an SDK client construction raises.
STREAM_FAILURES = (*PAYLOAD_FAILURES, ConnectionError, OSError)


class AlpacaStreamProcessor:
    """Handles the production of data from Alpaca streams and maps to domain state."""
    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        state_mapper: StateMapper | None = None,
        symbols: list[str] | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        data_feed: str = "iex",
        data_queue: queue.Queue | None = None,
        stream_client: Any | None = None,
    ):
        self.db_manager = db_manager
        self.state_mapper = state_mapper
        self.symbols: list[str] = normalize_symbols(symbols, default=DEFAULT_SYMBOLS)
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = base_url or os.getenv("ALPACA_BASE_URL", "")
        self.data_feed = data_feed
        self.data_queue = data_queue
        self.buffer = DataStreamBuffer()
        self.running = True
        self.stream_client = stream_client
        self.rest_client: Any | None = self._init_rest_client()
        self._stream_thread: threading.Thread | None = None
        self._flusher_thread: threading.Thread | None = None

    def _init_rest_client(self) -> Any | None:
        """Build the Alpaca REST client once in ``__init__`` when credentials are present."""
        if not (self.api_key and self.api_secret):
            return None
        try:
            from alpaca_trade_api.rest import REST

            return REST(self.api_key, self.api_secret, self.base_url or None)
        except (ImportError, *STREAM_FAILURES) as e:
            logger.debug("Alpaca REST client not initialized: %s", e)
            return None

    def _extract_market_payload(self, msg: Any) -> dict[str, Any] | None:
        """Unified normalization helper for raw tick or bar messages."""
        if msg is None:
            return None

        def safe_float(val: Any, default: float | None = None) -> float | None:
            if val is None or isinstance(val, (list, tuple, dict, set)):
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        try:
            if isinstance(msg, dict):
                symbol = msg.get('symbol') or msg.get('S')
                price = msg.get('price') if 'price' in msg else msg.get('p')
                size = msg.get('size') if 'size' in msg else (msg.get('qty') or msg.get('s', 0.0))
                open_p = msg.get('open') if 'open' in msg else msg.get('o')
                high_p = msg.get('high') if 'high' in msg else msg.get('h')
                low_p = msg.get('low') if 'low' in msg else msg.get('l')
                # Note: In Alpaca trade messages, 'c' is trade conditions (list of strings).
                # Only treat 'c' as close price if 'T' is a bar ('b', 'u', 'd') or close is explicitly present.
                msg_type = msg.get('T')
                if 'close' in msg:
                    close_p = msg.get('close')
                elif msg_type in ('b', 'u', 'd') or ('o' in msg and 'h' in msg and 'l' in msg):
                    close_p = msg.get('c')
                else:
                    close_p = None
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

            canonical_symbol = from_alpaca_symbol(str(symbol).upper())
            parsed_ts = DataStreamBuffer._parse_timestamp(ts)
            return {
                'symbol': canonical_symbol,
                'price': safe_float(price),
                'size': safe_float(size, 0.0),
                'open': safe_float(open_p),
                'high': safe_float(high_p),
                'low': safe_float(low_p),
                'close': safe_float(close_p),
                'volume': safe_float(vol, safe_float(size, 0.0)),
                'timestamp': parsed_ts
            }
        except PAYLOAD_FAILURES as e:
            logger.warning("Error normalizing payload: %s", e)
            return None

    def _parse_trade(self, trade: Any) -> dict[str, Any] | None:
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
        if is_debug():
            logger.debug("Received tick for %s: price=%s, size=%s", symbol, parsed['price'], parsed['size'])

        # Push to web server's market buffer for real-time streaming
        if WEB_SERVER_AVAILABLE and market_buffer:
            try:
                market_buffer.add_tick(
                    symbol=symbol,
                    price=parsed['price'],
                    size=parsed['size'],
                    timestamp=parsed['timestamp'],
                )
            except PAYLOAD_FAILURES as e:
                logger.debug("Error pushing to market buffer: %s", e)

        if self.state_mapper:
            try:
                self.state_mapper.map_tick_data_to_state([parsed])
            except PAYLOAD_FAILURES as e:
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

        # Push to web server's market buffer
        if WEB_SERVER_AVAILABLE and market_buffer:
            try:
                market_buffer.add_tick(
                    symbol=bar_dict['symbol'],
                    price=bar_dict['close'],
                    size=bar_dict['volume']
                )
            except PAYLOAD_FAILURES as e:
                logger.debug("Error pushing bar to market buffer: %s", e)

    async def _async_handle_bar(self, bar: Any) -> None:
        """Async callback for Alpaca websocket stream bar subscriptions."""
        self.handle_bar(bar)

    def flush_ready_bars(self) -> dict[str, dict[str, Any]]:
        """Returns ready minute bars and pushes them to the queue if available."""
        ready_bars = self.buffer.get_ready_bars(self.symbols)
        if self.data_queue:
            for sym, bar in ready_bars.items():
                self.data_queue.put((sym, bar))
        return ready_bars

    def _fetch_crypto_snapshot(self) -> None:
        """Fetches latest crypto trades from Alpaca REST to guarantee real-time BTC data."""
        crypto_syms = [s for s in self.symbols if is_crypto_symbol(s)]
        if not crypto_syms or not (self.api_key and self.api_secret):
            return
        try:
            if self.rest_client is None:
                self.rest_client = self._init_rest_client()
            if self.rest_client is None:
                return
            alpaca_syms = [to_alpaca_symbol(s) for s in crypto_syms]
            trades = self.rest_client.get_latest_crypto_trades(alpaca_syms)
            for alpaca_s, t in trades.items():
                app_s = from_alpaca_symbol(alpaca_s)
                tick = {
                    'symbol': app_s,
                    'price': float(t.price),
                    'size': float(t.size or 0.0),
                    'timestamp': t.timestamp
                }
                self.handle_trade(tick)
        except STREAM_FAILURES as e:
            logger.debug("Crypto snapshot fetch error: %s", e)

    def _flusher_loop(self) -> None:
        """Background thread loop that flushes ready bars every second and refreshes crypto ticks."""
        last_crypto_fetch = 0.0
        while self.running:
            try:
                self.flush_ready_bars()
                if time.time() - last_crypto_fetch >= 3.0:
                    self._fetch_crypto_snapshot()
                    last_crypto_fetch = time.time()
            except Exception as e:
                # Thread boundary: this is the flusher thread's whole loop body,
                # and a tick that fails must not end the thread silently.
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
            kwargs: dict[str, Any] = {
                "key_id": self.api_key,
                "secret_key": self.api_secret,
                "data_feed": self.data_feed,
                "raw_data": True
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            client = AlpacaTradeStream(**kwargs)
            # Patch outdated v1beta2 crypto endpoint in alpaca_trade_api to active v1beta3
            if (
                hasattr(client, "_crypto_ws")
                and hasattr(client._crypto_ws, "_endpoint")
                and "v1beta2" in str(client._crypto_ws._endpoint)
            ):
                client._crypto_ws._endpoint = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
            return client
        except STREAM_FAILURES as e:
            logger.warning("Failed to initialize Alpaca Stream client: %s", e)
            return None

    def _run_stream(self) -> None:
        """Starts the blocking stream loop."""
        try:
            if self.stream_client and hasattr(self.stream_client, "run"):
                self.stream_client.run()
        except Exception as e:
            # Stream boundary: ``run`` is the blocking loop the SDK owns, and it
            # ends on a disconnect, a socket error or an SDK bug alike.
            logger.error("Error in Alpaca stream run: %s", e)

    def _subscribe_feed(self, method: str, handler, *syms: str) -> None:
        """Call ``method`` on the stream client when that client version has it."""
        if self.stream_client and hasattr(self.stream_client, method):
            getattr(self.stream_client, method)(handler, *syms)

    def _subscribe(self) -> None:
        """Subscribe the stream client to the equity and crypto feeds it has symbols for."""
        try:
            stock_syms = [s for s in self.symbols if not is_crypto_symbol(s)]
            crypto_syms = [to_alpaca_symbol(s) for s in self.symbols if is_crypto_symbol(s)]

            if stock_syms:
                self._subscribe_feed("subscribe_trades", self._async_handle_trade, *stock_syms)
                self._subscribe_feed("subscribe_bars", self._async_handle_bar, *stock_syms)

            if crypto_syms:
                self._subscribe_feed("subscribe_crypto_trades", self._async_handle_trade, *crypto_syms)
                self._subscribe_feed("subscribe_crypto_bars", self._async_handle_bar, *crypto_syms)
        except STREAM_FAILURES as e:
            logger.warning("Failed to subscribe stream client to %s: %s", self.symbols, e)

    def _ensure_flusher(self) -> None:
        """Start the queue flusher once, if it is not already running."""
        if self.data_queue and (self._flusher_thread is None or not self._flusher_thread.is_alive()):
            self._flusher_thread = threading.Thread(target=self._flusher_loop, daemon=True)
            self._flusher_thread.start()

    def _start_stream(self, run_in_background: bool) -> None:
        """Run the stream loop in its own thread, or in this one."""
        if run_in_background:
            self._stream_thread = threading.Thread(target=self._run_stream, daemon=True)
            self._stream_thread.start()
        else:
            self._run_stream()

    def start(self, run_in_background: bool = False) -> None:
        """Starts stream subscriptions and flusher."""
        self.running = True
        logger.info("Alpaca Stream Processor started.")
        logger.info("State mapper initialized for domain state mapping")

        # Resolve the CA bundle before any TLS connection: the crypto snapshot
        # below and the websocket stream both need it, and the websocket path in
        # particular fails without a bundle on a bare interpreter.
        configure_ca_certificates()

        # Fetch initial snapshot for crypto assets
        self._fetch_crypto_snapshot()

        if self.stream_client is None:
            self.stream_client = self._init_stream_client()

        if self.stream_client:
            self._subscribe()

        self._ensure_flusher()

        if self.stream_client:
            self._start_stream(run_in_background)

    def stop(self) -> None:
        """Stops the stream processor and background threads."""
        self.running = False
        if self.stream_client and hasattr(self.stream_client, "stop"):
            try:
                self.stream_client.stop()
            except STREAM_FAILURES as e:
                logger.debug("Error stopping stream client: %s", e)
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=2)
        if self._flusher_thread and self._flusher_thread.is_alive():
            self._flusher_thread.join(timeout=2)
        logger.info("Alpaca Stream Processor stopped.")

