"""DB writer: drains the queues and persists batches in one transaction."""
import logging
import queue
import threading
import time
from typing import Any

from src.config_gatekeeper import validate_configs
from src.database import DB_FAILURES, DatabaseManager
from src.domain import FullState
from src.logging_config import is_debug
from src.model import load_value_function
from src.state_mapper import StateMapper
from src.stream_client import AlpacaStreamProcessor
from src.symbols import (
    DEFAULT_SYMBOLS,
    normalize_symbols,
)
from src.times import now_utc
from src.trading_execution import AlpacaClient, TradingExecutor

logger = logging.getLogger("DataIngestion")

# Mapping a bar into the domain state touches the buffers, the recommendation
# path and the database, so a failure here is one of those, not a bug here.
MAPPING_FAILURES = (AttributeError, TypeError, ValueError, KeyError, *DB_FAILURES)

# Import web_server for real-time data streaming
try:
    from src.web_server import (
        compute_recommendation,
        decision_buffer,
        market_buffer,
        start_web_server_thread,
        stop_web_server,
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


class DBWriterWorker(threading.Thread):
    """Consumer thread that processes buffered data in batches, maps to domain state, and writes to Database."""
    def __init__(
        self,
        db_manager: DatabaseManager,
        state_mapper: StateMapper,
        data_queue: queue.Queue,
        batch_size: int = 50,
        executor: TradingExecutor | None = None,
    ):
        super().__init__(daemon=True)
        self.db_manager = db_manager
        self.state_mapper = state_mapper
        self.queue = data_queue
        self.running = True
        self.batch_size = batch_size
        # The model the live path decides with. It is optional because a writer
        # can be built on its own (a test, a notebook); without it the dashboard
        # falls back to the heuristic recommendation rather than going blank.
        self.executor = executor

    def _drain_batch(self) -> list | None:
        """Pull one batch off the queue.

        Returns the batch, an empty list when the one second read timed out, or
        ``None`` when the stop sentinel came through, which is how the writer is
        asked to leave.
        """
        try:
            item = self.queue.get(timeout=1)
        except queue.Empty:
            return []
        if item is None:
            return None

        batch = [item]
        while len(batch) < self.batch_size:
            try:
                next_item = self.queue.get_nowait()
            except queue.Empty:
                break
            if next_item is None:
                self.queue.put(None)
                break
            batch.append(next_item)
        return batch

    def _recommendation(self, symbol: str, state: FullState | None) -> dict[str, Any]:
        """The decision the dashboard shows for one bar.

        With an executor attached the answer is the Bellman argmax over the
        symbol's action space, which is the decision the spec asks for. Without
        one (a writer built on its own, in a test or a notebook) the dashboard
        keeps the heuristic recommendation it has always shown, so the split
        between "model decides" and "no model to ask" is visible rather than a
        blank chart.

        Args:
            symbol: The symbol the bar belongs to.
            state: The state to score, or ``None`` when there is no executor.

        Returns:
            A dict with ``action``, ``confidence`` and ``reasoning``.
        """
        if self.executor is None or state is None:
            return compute_recommendation(symbol)

        _action, report = self.executor.decide(state)
        return {
            "symbol": symbol,
            "action": report["action"],
            "confidence": report["confidence"],
            "reasoning": [
                f"Bellman argmax over {len(report['values'])} candidates",
                f"Q-value {report['score']:+.4f}",
                f"Margin over runner-up: {report['confidence'] - 0.5:+.2f} of the spread",
            ],
        }

    def _map_bar(self, symbol: str, bar_data: dict) -> None:
        """Attach one bar to the domain state and push its recommendation to the dashboard."""
        logger.info("Processing %s at %s", symbol, bar_data['timestamp'])
        try:
            tick_data = {
                'symbol': symbol,
                'price': bar_data['close'],
                'size': bar_data['volume'],
                'timestamp': bar_data['timestamp']
            }
            self.state_mapper.map_tick_data_to_state([tick_data])
            if WEB_SERVER_AVAILABLE and decision_buffer:
                state = None
                if self.executor is not None:
                    state = self.state_mapper.build_decision_state(
                        symbol=symbol,
                        price=bar_data['close'],
                        previous_price=bar_data.get('open'),
                        cash=self.executor.cash,
                        holdings=self.executor.order_tracker.get_positions(),
                    )
                rec = self._recommendation(symbol, state)
                decision_buffer.add_decision(
                    symbol=symbol,
                    action=rec["action"],
                    confidence=rec["confidence"],
                    reasoning=rec["reasoning"],
                    timestamp=bar_data['timestamp']
                )
        except MAPPING_FAILURES as map_error:
            logger.debug(f"Mapping tick to state: {map_error}")

    def run(self) -> None:
        logger.info("DB Writer Worker started.")
        logger.info("Domain state mapper attached for data transformation")

        while self.running:
            try:
                batch = self._drain_batch()
                if batch is None:  # Sentinel value to stop worker
                    break
                if not batch:
                    continue

                bars_to_write = []
                for symbol, bar_data in batch:
                    self._map_bar(symbol, bar_data)
                    # The queue's contract is ``(symbol, bar)``: a flushed minute bar
                    # carries no symbol of its own, so the record is assembled here
                    # or the database would store a bar nothing can look up.
                    bars_to_write.append({**bar_data, "symbol": symbol})

                if bars_to_write:
                    self.db_manager.add_market_data(bars_to_write)

                for _ in batch:
                    self.queue.task_done()
            except Exception as e:
                # Thread boundary: this is the writer thread's whole loop body,
                # and a batch that fails must not end the thread silently.
                logger.error("Error in DBWriterWorker: %s", e)
                time.sleep(1)


def _seed_dashboard(db_manager: DatabaseManager, resolved_symbols: list[str]) -> None:
    """Give the dashboard a first tick per symbol from what the database already holds.

    Without this the charts open at $0.00 until the next bar arrives, which is
    what makes a restarted pipeline look like it has no data.
    """
    for sym in resolved_symbols:
        if not (market_buffer and db_manager and hasattr(db_manager, "get_latest_market_record")):
            continue
        try:
            rec = db_manager.get_latest_market_record(sym)
            if rec and rec.get("price", 0) > 0:
                market_buffer.add_tick(
                    symbol=sym,
                    price=rec["price"],
                    size=rec.get("volume", 0.0),
                    timestamp=rec.get("timestamp")
                )
                if rec.get("open", 0) > 0:
                    market_buffer._open_prices[sym] = rec["open"]
        except DB_FAILURES as e:
            logger.debug("Could not seed initial tick for %s: %s", sym, e)


def _start_dashboard(db_manager: DatabaseManager, resolved_symbols: list[str]) -> None:
    """Run the dashboard in its own thread, and seed it. A missing dashboard is not fatal."""
    if not WEB_SERVER_AVAILABLE:
        return
    try:
        start_web_server_thread(host="0.0.0.0", port=8001)
        logger.info("Real-time Dashboard available at: http://localhost:8001")
        _seed_dashboard(db_manager, resolved_symbols)
    except Exception as ws_err:
        # Process boundary: the dashboard runs in its own thread and is optional,
        # so any failure starting it is reported and the pipeline continues.
        logger.warning("Could not start dashboard web server thread: %s", ws_err)


def _inject_mock_bars(data_queue: queue.Queue, resolved_symbols: list[str]) -> None:
    """Queue one synthetic bar per symbol, for a run that has no stream to feed it."""
    for sym in resolved_symbols:
        dummy_bar = {
            "timestamp": now_utc(),
            "open": 150.0,
            "high": 160.0,
            "low": 140.0,
            "close": 155.0,
            "volume": 1000
        }
        if is_debug():
            logger.debug("Injecting mock bar for %s into queue...", sym)
        data_queue.put((sym, dummy_bar))


def _wait_for_run(
    writer: DBWriterWorker,
    stop_event: threading.Event,
    duration_seconds: float | None,
    continuous: bool,
    resolved_symbols: list[str],
) -> None:
    """Hold the main thread alive until the run is asked to stop."""
    if not continuous:
        timeout = duration_seconds if duration_seconds is not None else 1.0
        stop_event.wait(timeout=timeout)
        return

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

def _shutdown(
    processor: AlpacaStreamProcessor,
    writer: DBWriterWorker,
    data_queue: queue.Queue,
    mock_fallback: bool,
) -> None:
    """Stop the dashboard, the stream processor and the DB writer, in that order."""
    if WEB_SERVER_AVAILABLE:
        try:
            stop_web_server()
        except Exception:
            # Shutdown boundary: a dashboard that will not stop cleanly must not
            # keep the pipeline from finishing its own teardown.
            pass

    if not mock_fallback:
        logger.info("Stopping stream processor...")
        processor.stop()
    if is_debug():
        logger.debug("Sending sentinel value to stop.")
    data_queue.put(None)
    writer.running = False  # Ensure the loop finishes
    logger.info("Worker command sent, waiting for DB writer...")
    if hasattr(writer, "join"):
        try:
            writer.join(timeout=3)
        except RuntimeError as e:
            logger.debug("Error joining writer: %s", e)
    logger.info("Worker finished.")


def run_worker_threads(
    symbols: str | list[str] | None = None,
    duration_seconds: float | None = None,
    continuous: bool = True,
    mock_fallback: bool = False,
    stop_event: threading.Event | None = None
) -> None:
    """Run the whole pipeline: dashboard, stream producer, DB writer, then shut down."""
    validate_configs()
    db_manager = DatabaseManager()
    state_mapper = StateMapper()

    # Explicitly initialize the database tables
    logger.info("Initializing database...")
    db_manager.connection.init_db()
    logger.info("Database initialization complete: %s", db_manager.connection.engine)

    data_queue: queue.Queue = queue.Queue()

    resolved_symbols = normalize_symbols(symbols, default=DEFAULT_SYMBOLS)

    # The pipeline asks this executor to decide, never to execute: nothing here
    # calls execute_decision, so no order reaches the broker. The client exists so
    # the executor is the same object the live trading path would use, and the
    # cash it carries is what the next decision is scored against. Its value
    # function is the one a previous training pass stored for these symbols; with
    # nothing stored yet it starts untrained, which scores on the immediate reward.
    executor = TradingExecutor(AlpacaClient(), value_function=load_value_function(db_manager, resolved_symbols))
    writer = DBWriterWorker(db_manager, state_mapper, data_queue, executor=executor)
    writer.start()
    logger.info("Producer and Consumer threads initialized.")
    logger.info("Data pipeline ready: Alpaca → Adapter → Domain State → Bellman → DB")

    _start_dashboard(db_manager, resolved_symbols)

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
            _inject_mock_bars(data_queue, resolved_symbols)
        _wait_for_run(writer, stop_event, duration_seconds, continuous, resolved_symbols)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    finally:
        _shutdown(processor, writer, data_queue, mock_fallback)

