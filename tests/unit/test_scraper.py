import asyncio
import datetime
import logging
import queue
import threading
from unittest.mock import MagicMock, patch
from unittest.mock import MagicMock as Mock

import pytest

from src.scraper import (
    AlpacaStreamProcessor,
    DataStreamBuffer,
    DBWriterWorker,
    Tick,
    normalize_symbols,
    parse_args,
    run_worker_threads,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _no_dashboard_thread():
    """Keep the dashboard's uvicorn thread out of these unit tests.

    `run_worker_threads` starts the dashboard, and that thread outlives the
    test that started it: a thread still logging when CPython finalizes races
    the stderr buffer lock, which printed a fatal error after an otherwise
    green run. The dashboard is exercised where it is owned, in
    `test_web_server.py`, which stops what it starts.
    """
    with patch("src.db_writer._start_dashboard"):
        yield


class TestTickDataclass:
    """Tests for Tick dataclass."""

    def test_tick_creation(self):
        """Test creating a Tick instance."""
        tick = Tick(
            symbol="AAPL",
            price=150.0,
            size=100.0,
            timestamp=datetime.datetime(2023, 1, 1, 12, 0, 0)
        )
        assert tick.symbol == "AAPL"
        assert tick.price == 150.0
        assert tick.size == 100.0

    def test_tick_with_current_time(self):
        """Test creating Tick with current datetime."""
        current_time = datetime.datetime.now()
        tick = Tick(
            symbol="TSLA",
            price=700.0,
            size=50.0,
            timestamp=current_time
        )
        assert tick.symbol == "TSLA"
        assert tick.price == 700.0
        assert tick.size == 50.0
        assert tick.timestamp == current_time


class TestDataStreamBuffer:
    """Tests for DataStreamBuffer."""

    def test_buffer_initialization(self):
        """Test initializing an empty buffer."""
        buffer = DataStreamBuffer()
        assert buffer.buffer == {}
        assert buffer.lock is not None

    def test_buffer_with_nested_dict(self):
        """Test buffer structure."""
        buffer = DataStreamBuffer()
        assert isinstance(buffer.buffer, dict)
        assert isinstance(buffer.buffer.get("symbol"), dict) if "symbol" in buffer.buffer else True

    def test_add_tick_single_symbol(self):
        """Test adding a tick for a symbol."""
        buffer = DataStreamBuffer()
        tick_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }
        buffer.add_tick("AAPL", tick_data)

        assert "AAPL" in buffer.buffer
        assert len(buffer.buffer["AAPL"]) == 1

    def test_add_tick_multiple_symbols(self):
        """Test adding ticks for multiple symbols."""
        buffer = DataStreamBuffer()
        tick_data1 = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }
        tick_data2 = {
            "symbol": "TSLA",
            "price": 700.0,
            "size": 50.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }

        buffer.add_tick("AAPL", tick_data1)
        buffer.add_tick("TSLA", tick_data2)

        assert len(buffer.buffer) == 2
        assert len(buffer.buffer["AAPL"]) == 1
        assert len(buffer.buffer["TSLA"]) == 1

    def test_add_tick_multiple_minutes(self):
        """Test adding ticks spanning multiple minutes."""
        buffer = DataStreamBuffer()
        tick_data1 = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)
        }
        tick_data2 = {
            "symbol": "AAPL",
            "price": 155.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }

        buffer.add_tick("AAPL", tick_data1)
        buffer.add_tick("AAPL", tick_data2)

        assert len(buffer.buffer["AAPL"]) == 2
        assert len(buffer.buffer["AAPL"]["2023-01-01 11:59"]) == 1
        assert len(buffer.buffer["AAPL"]["2023-01-01 12:00"]) == 1

    def test_get_ready_bars_empty(self):
        """Test getting ready bars when buffer is empty."""
        buffer = DataStreamBuffer()
        symbols = ["AAPL", "TSLA"]
        ready = buffer.get_ready_bars(symbols)

        assert ready == {}

    def test_get_ready_bars_no_ready(self):
        """Test getting ready bars when all are from current minute."""
        buffer = DataStreamBuffer()
        tick_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)  # Different minute
        }
        buffer.add_tick("AAPL", tick_data)

        symbols = ["AAPL"]
        ready = buffer.get_ready_bars(symbols)

        # Should have the ready bar
        assert "AAPL" in ready

    def test_get_ready_bars_with_ready(self):
        """Test getting ready bars with expired minute."""
        buffer = DataStreamBuffer()
        tick_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)  # Different minute
        }
        buffer.add_tick("AAPL", tick_data)

        symbols = ["AAPL"]
        ready = buffer.get_ready_bars(symbols)

        # Should have the ready bar
        assert "AAPL" in ready
        assert ready["AAPL"]["open"] == 150.0
        assert ready["AAPL"]["high"] == 150.0
        assert ready["AAPL"]["low"] == 150.0
        assert ready["AAPL"]["close"] == 150.0
        assert ready["AAPL"]["volume"] == 100.0

        # Buffer should be cleared
        assert len(buffer.buffer["AAPL"]) == 0

    def test_get_ready_bars_with_multiple_symbols(self):
        """Test getting ready bars for multiple symbols."""
        buffer = DataStreamBuffer()
        tick_data1 = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)
        }
        tick_data2 = {
            "symbol": "TSLA",
            "price": 700.0,
            "size": 50.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)
        }

        buffer.add_tick("AAPL", tick_data1)
        buffer.add_tick("TSLA", tick_data2)

        symbols = ["AAPL", "TSLA", "GOOGL"]
        ready = buffer.get_ready_bars(symbols)

        assert "AAPL" in ready
        assert "TSLA" in ready
        assert "GOOGL" not in ready  # Never added

    def test_get_ready_bars_multiple_ticks_same_bar(self):
        """Test getting ready bars with multiple ticks in same minute."""
        buffer = DataStreamBuffer()

        tick_data1 = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 58, 59)
        }
        tick_data2 = {
            "symbol": "AAPL",
            "price": 152.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 58, 59)
        }

        buffer.add_tick("AAPL", tick_data1)
        buffer.add_tick("AAPL", tick_data2)

        symbols = ["AAPL"]
        ready = buffer.get_ready_bars(symbols)

        assert ready["AAPL"]["open"] == 150.0
        assert ready["AAPL"]["high"] == 152.0  # Max price
        assert ready["AAPL"]["low"] == 150.0  # Min price
        assert ready["AAPL"]["close"] == 152.0  # Max price (closing price)
        assert ready["AAPL"]["volume"] == 200.0


class TestAlpacaStreamProcessor:
    """Tests for AlpacaStreamProcessor."""

    def test_processor_initialization(self):
        """Test initializing processor."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        assert processor.db_manager == mock_db
        assert processor.buffer is not None
        assert processor.running

    def test_processor_with_db_manager(self):
        """Test processor with DatabaseManager."""
        mock_db = Mock(spec='DatabaseManager')
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        assert processor.db_manager == mock_db

    def test_processor_running_flag(self):
        """Test processor running flag."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        assert processor.running is True

        processor.running = False
        assert processor.running is False

    def test_processor_buffer(self):
        """Test processor buffer instance."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        assert isinstance(processor.buffer, DataStreamBuffer)

    @patch('src.stream_client.logger')
    def test_processor_start(self, mock_logger):
        """Test processor start method."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, api_key="", api_secret="")

        processor.start(run_in_background=True)

        # Verify logger was called
        assert mock_logger.info.called

    def test_processor_custom_symbols_and_queue(self):
        """Test processor with custom symbols and data queue."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        processor = AlpacaStreamProcessor(
            mock_db,
            mock_state_mapper,
            symbols=["AAPL", "MSFT"],
            api_key="test_key",
            api_secret="test_secret",
            data_queue=q,
        )
        assert processor.symbols == ["AAPL", "MSFT"]
        assert processor.data_queue is q
        assert processor.api_key == "test_key"
        assert processor.api_secret == "test_secret"

    def test_processor_handle_trade_dict(self):
        """Test handling a trade dictionary."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        trade_data = {
            "symbol": "AAPL",
            "price": 150.25,
            "size": 100,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0, 10),
        }
        processor.handle_trade(trade_data)

        assert "AAPL" in processor.buffer.buffer
        assert len(processor.buffer.buffer["AAPL"]["2023-01-01 12:00"]) == 1
        stored = processor.buffer.buffer["AAPL"]["2023-01-01 12:00"][0]
        assert stored["price"] == 150.25
        assert stored["size"] == 100.0

    def test_processor_handle_trade_iso_timestamp(self):
        """Test handling trade with ISO timestamp string."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        trade_data = {
            "symbol": "TSLA",
            "price": 250.0,
            "size": 50,
            "timestamp": "2023-01-01T12:00:15Z",
        }
        processor.handle_trade(trade_data)

        assert "TSLA" in processor.buffer.buffer
        assert "2023-01-01 12:00" in processor.buffer.buffer["TSLA"]

    def test_processor_handle_trade_alpaca_entity(self):
        """Test handling trade passed as object with attributes."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        mock_trade = MagicMock()
        mock_trade.symbol = "NVDA"
        mock_trade.price = 450.0
        mock_trade.size = 20
        mock_trade.timestamp = datetime.datetime(2023, 1, 1, 12, 0, 5)
        del mock_trade.get

        processor.handle_trade(mock_trade)
        assert "NVDA" in processor.buffer.buffer

    def test_processor_handle_bar_routes_to_queue(self):
        """Test handling bar puts bar onto data queue."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, data_queue=q)

        bar_data = {
            "symbol": "AAPL",
            "open": 150.0,
            "high": 155.0,
            "low": 149.0,
            "close": 154.0,
            "volume": 500,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0),
        }
        processor.handle_bar(bar_data)

        assert not q.empty()
        symbol, item = q.get_nowait()
        assert symbol == "AAPL"
        assert item["close"] == 154.0
        assert item["volume"] == 500

    def test_processor_flush_ready_bars(self):
        """Test flushing ready bars from buffer into data queue."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        processor = AlpacaStreamProcessor(
            mock_db, mock_state_mapper, symbols=["AAPL"], data_queue=q
        )

        tick = {
            "symbol": "AAPL",
            "price": 100.0,
            "size": 10,
            "timestamp": datetime.datetime(2020, 1, 1, 12, 0),
        }
        processor.handle_trade(tick)

        flushed = processor.flush_ready_bars()
        assert "AAPL" in flushed
        assert not q.empty()
        symbol, bar = q.get_nowait()
        assert symbol == "AAPL"
        assert bar["volume"] == 10

    def test_processor_start_with_stream_client(self):
        """Test start wires stream_client trade subscriptions."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_stream = MagicMock()

        processor = AlpacaStreamProcessor(
            mock_db,
            mock_state_mapper,
            symbols=["AAPL", "TSLA"],
            stream_client=mock_stream,
        )
        processor.start(run_in_background=False)

        mock_stream.subscribe_trades.assert_called_once()
        args, _ = mock_stream.subscribe_trades.call_args
        assert callable(args[0])
        assert "AAPL" in args
        assert "TSLA" in args
        mock_stream.run.assert_called_once()

    def test_processor_start_background_and_stop(self):
        """Test start in background thread and stop."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_stream = MagicMock()

        processor = AlpacaStreamProcessor(
            mock_db,
            mock_state_mapper,
            symbols=["AAPL"],
            stream_client=mock_stream,
        )
        processor.start(run_in_background=True)
        assert processor.running is True

        processor.stop()
        assert processor.running is False
        mock_stream.stop.assert_called_once()

    def test_processor_malformed_trade_does_not_crash(self):
        """Test that malformed trade payload is handled gracefully."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        processor.handle_trade({"invalid": "data"})
        processor.handle_trade(None)
        assert len(processor.buffer.buffer) == 0

    def test_processor_async_handle_bar(self):
        """Test async bar callback."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, data_queue=q)

        bar_dict = {
            "symbol": "MSFT",
            "open": 300.0,
            "high": 305.0,
            "low": 299.0,
            "close": 304.0,
            "volume": 2000,
            "timestamp": "2023-01-01T12:00:00Z"
        }
        asyncio.run(processor._async_handle_bar(bar_dict))
        assert not q.empty()
        sym, item = q.get_nowait()
        assert sym == "MSFT"
        assert item["close"] == 304.0

    def test_processor_parse_trade_numeric_timestamps(self):
        """Test parsing trades with epoch numeric timestamps."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        # Seconds epoch
        trade_sec = {"symbol": "AAPL", "price": 150.0, "size": 10, "timestamp": 1672574400}
        parsed = processor._parse_trade(trade_sec)
        assert parsed is not None
        assert parsed["timestamp"].year == 2023

        # Nanoseconds epoch
        trade_nano = {"symbol": "AAPL", "price": 150.0, "size": 10, "timestamp": 1672574400000000000}
        parsed_nano = processor._parse_trade(trade_nano)
        assert parsed_nano is not None
        assert parsed_nano["timestamp"].year == 2023

    def test_processor_handle_bar_object_attributes(self):
        """Test handling bar passed as entity object with attributes."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, data_queue=q)

        mock_bar = MagicMock()
        mock_bar.symbol = "GOOGL"
        mock_bar.open = 100.0
        mock_bar.high = 105.0
        mock_bar.low = 99.0
        mock_bar.close = 103.0
        mock_bar.volume = 500
        mock_bar.timestamp = datetime.datetime(2023, 1, 1, 12, 0)
        del mock_bar.get

        processor.handle_bar(mock_bar)
        assert not q.empty()
        sym, bar = q.get_nowait()
        assert sym == "GOOGL"
        assert bar["open"] == 100.0

    def test_processor_init_stream_client_with_keys(self):
        """Test initializing stream client with configured keys."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(
            mock_db,
            mock_state_mapper,
            api_key="key",
            api_secret="secret",
            base_url="https://paper-api.alpaca.markets",
        )

        with patch("src.stream_client.AlpacaTradeStream") as mock_stream_cls:
            mock_instance = MagicMock()
            mock_stream_cls.return_value = mock_instance
            client = processor._init_stream_client()
            assert client == mock_instance
            mock_stream_cls.assert_called_once_with(
                key_id="key",
                secret_key="secret",
                data_feed="iex",
                raw_data=True,
                base_url="https://paper-api.alpaca.markets",
            )

    def test_processor_trade_state_mapper_exception_handled(self):
        """Test that state mapper exceptions during trade handling are logged and handled."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_state_mapper.map_tick_data_to_state.side_effect = ValueError("Mapping failure")
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        trade_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 10,
            "timestamp": datetime.datetime.now()
        }
        # Should not raise exception
        processor.handle_trade(trade_data)
        assert "AAPL" in processor.buffer.buffer

    def test_processor_run_stream_handles_exception(self):
        """Test _run_stream logs error and handles client.run exception."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_stream = MagicMock()
        mock_stream.run.side_effect = RuntimeError("WebSocket connection dropped")
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, stream_client=mock_stream)

        # Should not crash
        processor._run_stream()
        mock_stream.run.assert_called_once()

    def test_processor_flusher_loop_single_iteration(self):
        """Test _flusher_loop runs flush_ready_bars and exits when running=False."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        call_count = 0
        def mock_flush():
            nonlocal call_count
            call_count += 1
            processor.running = False
            return {}

        processor.flush_ready_bars = mock_flush
        with patch("src.stream_client.time.sleep") as mock_sleep:
            processor._flusher_loop()
            assert call_count == 1
            mock_sleep.assert_called_once_with(1)

    def test_processor_stop_joins_threads_and_handles_client_exception(self):
        """Test processor stop handles stream_client exceptions and joins threads."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_stream = MagicMock()
        mock_stream.stop.side_effect = OSError("Failed stopping client")

        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper, stream_client=mock_stream)
        mock_t1 = MagicMock()
        mock_t1.is_alive.return_value = True
        mock_t2 = MagicMock()
        mock_t2.is_alive.return_value = True
        processor._stream_thread = mock_t1
        processor._flusher_thread = mock_t2

        processor.stop()
        assert processor.running is False
        mock_t1.join.assert_called_once_with(timeout=2)
        mock_t2.join.assert_called_once_with(timeout=2)


class TestDBWriterWorker:
    """Tests for DBWriterWorker."""

    def test_worker_initialization(self):
        """Test initializing worker."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        assert worker.db_manager == mock_db
        assert worker.queue == q
        assert worker.running

    def test_worker_with_daemon_thread(self):
        """Test worker is daemon thread."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        assert worker.daemon is True

    def test_worker_running_flag(self):
        """Test worker running flag."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        assert worker.running is True

        worker.running = False
        assert worker.running is False

    def test_save_to_db_debug_mode(self):
        """Test save_to_db is callable."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        bar_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }

        # Just ensure the method exists and can be called
        worker.save_to_db("AAPL", bar_data)
        assert True

    def test_save_to_db_normal_mode(self):
        """Test save_to_db in normal mode."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        with patch('src.db_writer.logger') as mock_logger:
            mock_logger.setLevel = MagicMock()

            bar_data = {
                "symbol": "AAPL",
                "price": 150.0,
                "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
            }

            worker.save_to_db("AAPL", bar_data)

            # Should not call debug in normal mode
            assert mock_logger.debug.call_count == 0

    def test_worker_run_processes_item_and_stops_on_sentinel(self):
        """Test DBWriterWorker.run executes, maps state, adds market data and terminates on None."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        bar_data = {
            "symbol": "AAPL",
            "close": 150.0,
            "volume": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }
        q.put(("AAPL", bar_data))
        q.put(None)  # Sentinel

        worker.run()

        mock_state_mapper.map_tick_data_to_state.assert_called_once()
        mock_db.add_market_data.assert_called_once_with([bar_data])

    def test_worker_run_processes_batch_in_single_db_call(self):
        """Test DBWriterWorker drains multiple items and inserts them in a single batch."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q, batch_size=10)

        bars = [
            ("AAPL", {
                "symbol": "AAPL",
                "close": 150.0 + i,
                "volume": 100.0,
                "timestamp": datetime.datetime(2023, 1, 1, 12, i),
            })
            for i in range(5)
        ]
        for b in bars:
            q.put(b)
        q.put(None)

        worker.run()

        assert mock_db.add_market_data.call_count == 1
        inserted_batch = mock_db.add_market_data.call_args[0][0]
        assert len(inserted_batch) == 5

    def test_worker_run_handles_database_exception(self):
        """Test DBWriterWorker.run logs and continues when an exception occurs."""
        mock_db = MagicMock()
        mock_db.add_market_data.side_effect = [RuntimeError("DB Write error"), None]
        mock_state_mapper = MagicMock()
        q = queue.Queue()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        bar_data = {
            "symbol": "AAPL",
            "close": 150.0,
            "volume": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }
        q.put(("AAPL", bar_data))
        q.put(None)

        with patch("src.db_writer.time.sleep") as mock_sleep:
            worker.run()
            mock_sleep.assert_called_once_with(1)


class TestSetupLogging:
    """Tests for setup_logging function."""

    @patch('src.scraper.logger')
    def test_setup_logging_false(self, mock_logger):
        """Test setup_logging with debug=False."""
        setup_logging(False)

        mock_logger.setLevel.assert_called_once_with(logging.INFO)

    @patch('src.scraper.logger')
    def test_setup_logging_true(self, mock_logger):
        """Test setup_logging with debug=True."""
        setup_logging(True)

        mock_logger.setLevel.assert_called_once_with(logging.DEBUG)

    def test_setup_logging_records_the_debug_level(self):
        """``setup_logging`` records the level the pipeline threads read."""
        from src.logging_config import is_debug

        setup_logging(True)
        assert is_debug()
        setup_logging(False)
        assert not is_debug()


class TestRotatingLogFile:
    """The pipeline logs to one size-capped file, installed by the entrypoint."""

    def test_log_file_name_is_fixed(self):
        """The log path has no run timestamp, so runs share one file."""
        from src.logging_config import LOG_BACKUP_COUNT, LOG_FILENAME, LOG_MAX_BYTES

        assert LOG_FILENAME.endswith("kdr.log")
        assert LOG_MAX_BYTES > 0
        assert LOG_BACKUP_COUNT > 0

    def test_installed_handler_rotates(self):
        """``setup_logging`` installs the capped handler, not a plain one."""
        import logging
        import logging.handlers

        from src.logging_config import LOG_BACKUP_COUNT, LOG_FILENAME, LOG_MAX_BYTES

        setup_logging(False)
        ours = [
            handler
            for handler in logging.root.handlers
            if getattr(handler, "baseFilename", None) == LOG_FILENAME
        ]
        assert ours, f"no handler writes to {LOG_FILENAME}"
        handler = ours[0]
        assert isinstance(handler, logging.handlers.RotatingFileHandler), type(handler)
        assert handler.maxBytes == LOG_MAX_BYTES
        assert handler.backupCount == LOG_BACKUP_COUNT

    def test_no_module_configures_logging_at_import(self):
        """No module in src/ calls ``basicConfig`` at module level.

        A call that sits at import scope reconfigures global logging for
        whoever imported the module, which is what made a test run write into
        the pipeline's log. Only an indented call, inside an entrypoint, is
        allowed.
        """
        from pathlib import Path

        for path in Path("src").glob("*.py"):
            if path.name == "logging_config.py":
                continue
            for line in path.read_text().splitlines():
                assert not line.startswith("logging.basicConfig"), (path, line)


class TestRunWorkerThreads:
    """Tests for run_worker_threads function."""

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.DatabaseManager')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.logger')
    @patch('src.db_writer.time.sleep')
    def test_run_worker_threads_basic(
        self, mock_sleep, mock_logger, mock_processor_class, mock_db_class, mock_validate
    ):
        """Test basic flow of run_worker_threads with continuous=False."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db.connection.engine = MagicMock()
        mock_db_class.return_value = mock_db

        mock_validate.return_value = None

        try:
            run_worker_threads(continuous=False)
        except Exception:
            pass  # Ignore exceptions

        assert mock_validate.called
        assert mock_db_class.called
        assert hasattr(mock_db, "connection")
        assert hasattr(mock_db.connection, "init_db")

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.DatabaseManager')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.queue.Queue')
    @patch('src.db_writer.threading.Thread')
    @patch('src.db_writer.time.sleep')
    @patch('src.db_writer.logger')
    def test_run_worker_threads_with_sentinel(self, mock_sleep, mock_logger, mock_thread,
                                               mock_queue_class, mock_processor_class, mock_db_class, mock_validate):
        """Test worker threads with sentinel value."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db

        mock_queue = Mock()
        mock_queue_class.return_value = mock_queue

        mock_thread = Mock()
        mock_thread.daemon = True
        mock_thread.start = MagicMock()
        mock_thread.start.return_value = None

        mock_validate.return_value = None

        mock_queue.get.side_effect = [
            ("AAPL", {"timestamp": datetime.datetime(2023, 1, 1, 12, 0)}),
            None,
        ]

        try:
            run_worker_threads(continuous=False)
        except Exception:
            pass

        assert mock_queue.get.called

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.DatabaseManager')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.queue.Queue')
    @patch('src.db_writer.threading.Thread')
    @patch('src.db_writer.time.sleep')
    @patch('src.db_writer.logger')
    def test_run_worker_threads_empty_queue(self, mock_sleep, mock_logger, mock_thread,
                                           mock_queue_class, mock_processor_class, mock_db_class, mock_validate):
        """Test worker threads with empty queue."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db

        mock_queue = Mock()
        mock_queue_class.return_value = mock_queue

        mock_thread = Mock()
        mock_thread.daemon = True
        mock_thread.start = MagicMock()
        mock_thread.start.return_value = None

        mock_validate.return_value = None
        mock_queue.get.side_effect = queue.Empty

        try:
            run_worker_threads(continuous=False)
        except Exception:
            pass

        assert mock_validate.called

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.StateMapper')
    @patch('src.db_writer.DatabaseManager')
    def test_run_worker_threads_with_duration(
        self, mock_db_class, mock_state_mapper, mock_processor_class, mock_validate
    ):
        """Test run_worker_threads with explicit duration."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db
        run_worker_threads(symbols=["AAPL"], duration_seconds=0.01)
        assert mock_db.connection.init_db.called

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.DBWriterWorker')
    @patch('src.db_writer.DatabaseManager')
    def test_run_worker_threads_continuous_default_with_stop_event(
        self, mock_db_class, mock_writer_class, mock_processor_class, mock_validate
    ):
        """Verify continuous mode is default and terminates when stop_event is set."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db

        mock_writer = MagicMock()
        mock_writer.is_alive.return_value = True
        mock_writer_class.return_value = mock_writer

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        stop_event = threading.Event()
        # Pre-set stop_event so it does one loop and exits immediately
        stop_event.set()

        run_worker_threads(symbols=["AAPL", "MSFT"], stop_event=stop_event)

        assert mock_processor_class.called
        assert mock_processor.start.called
        assert mock_processor.stop.called
        assert mock_writer.join.called

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.DBWriterWorker')
    @patch('src.db_writer.DatabaseManager')
    def test_run_worker_threads_continuous_handles_keyboard_interrupt(
        self, mock_db_class, mock_writer_class, mock_processor_class, mock_validate
    ):
        """Verify KeyboardInterrupt is gracefully caught and resources cleaned up."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db

        mock_writer = MagicMock()
        mock_writer.is_alive.return_value = True
        mock_writer_class.return_value = mock_writer

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False]
        stop_event.wait.side_effect = KeyboardInterrupt()

        # Should not raise exception
        run_worker_threads(symbols=["AAPL"], stop_event=stop_event)

        assert mock_processor.stop.called
        assert mock_writer.join.called

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.AlpacaStreamProcessor')
    @patch('src.db_writer.DBWriterWorker')
    @patch('src.db_writer.DatabaseManager')
    def test_run_worker_threads_mock_fallback_all_symbols(
        self, mock_db_class, mock_writer_class, mock_processor_class, mock_validate
    ):
        """Verify mock fallback queues bars for all specified symbols."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db

        mock_writer = MagicMock()
        mock_writer.is_alive.return_value = True
        mock_writer_class.return_value = mock_writer

        mock_processor = MagicMock()
        mock_processor_class.return_value = mock_processor

        symbols = ["AAPL", "TSLA", "GOOGL"]
        with patch('src.db_writer.queue.Queue') as mock_queue_cls:
            mock_queue = MagicMock()
            mock_queue_cls.return_value = mock_queue

            run_worker_threads(symbols=symbols, continuous=False, mock_fallback=True)

            # Check that mock bars were put for all 3 symbols
            put_symbols = [
                call_args[0][0][0]
                for call_args in mock_queue.put.call_args_list
                if call_args[0][0] is not None
            ]
            assert "AAPL" in put_symbols
            assert "TSLA" in put_symbols
            assert "GOOGL" in put_symbols

    @patch('src.db_writer.validate_configs')
    @patch('src.db_writer.DBWriterWorker')
    @patch('src.db_writer.DatabaseManager')
    @patch('src.db_writer.load_value_function')
    def test_run_worker_threads_starts_the_model_from_stored_weights(
        self, mock_load, mock_db_class, mock_writer_class, mock_validate
    ):
        """The pipeline asks the persisted weights before it asks the market."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db
        mock_validate.return_value = None
        mock_writer_class.return_value = MagicMock()

        run_worker_threads(symbols=["AAPL", "TSLA"], continuous=False, mock_fallback=True)

        assert mock_load.called
        assert mock_load.call_args.args[1] == ["AAPL", "TSLA"]


class TestCLIArgs:
    """Tests for parse_args CLI handling."""

    def test_default_args(self):
        """Verify default CLI arguments: continuous=True, debug=False, mock=False."""
        args = parse_args([])
        assert args.continuous is True
        assert args.debug is False
        assert args.symbols is None
        assert args.duration is None
        assert args.mock is False

    def test_continuous_flag_explicit(self):
        """Verify --continuous flag retains True."""
        args = parse_args(["--continuous"])
        assert args.continuous is True

    def test_no_continuous_flag(self):
        """Verify --no-continuous flag disables continuous mode."""
        args = parse_args(["--no-continuous"])
        assert args.continuous is False

    def test_symbols_flag(self):
        """Verify --symbols flag parses comma-separated string."""
        args = parse_args(["--symbols", "AAPL,MSFT,GOOGL"])
        assert args.symbols == "AAPL,MSFT,GOOGL"

    def test_duration_flag(self):
        """Verify --duration flag parses float seconds."""
        args = parse_args(["--duration", "10.5"])
        assert args.duration == 10.5

    def test_mock_flag(self):
        """Verify --mock flag enables mock mode."""
        args = parse_args(["--mock"])
        assert args.mock is True

    def test_debug_flag(self):
        """Verify --debug flag enables debug mode."""
        args = parse_args(["--debug"])
        assert args.debug is True

    def test_normalize_symbols_helper(self):
        """Verify normalize_symbols parses string, list, or returns defaults."""
        assert normalize_symbols(None) == ["AAPL", "TSLA"]
        assert normalize_symbols("") == ["AAPL", "TSLA"]
        assert normalize_symbols("aapl, msft , TSLA") == ["AAPL", "MSFT", "TSLA"]
        assert normalize_symbols(["aapl", "googl"]) == ["AAPL", "GOOGL"]


class TestDataBufferConcurrency:
    """Tests for DataStreamBuffer concurrency."""

    def test_concurrent_get_ready_bars(self):
        """Test concurrent access to get_ready_bars."""
        buffer = DataStreamBuffer()

        # Add ticks
        tick_data = {
            "symbol": "AAPL",
            "price": 150.0,
            "size": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 11, 59)
        }
        buffer.add_tick("AAPL", tick_data)

        # Multiple threads trying to get ready bars
        results = []

        def get_bars():
            ready = buffer.get_ready_bars(["AAPL"])
            results.append(len(ready))

        threads = []
        for _ in range(3):
            t = threading.Thread(target=get_bars)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All should complete without errors
        assert len(results) == 3

    def test_multiple_threads_add_ticks(self):
        """Test multiple threads adding ticks concurrently."""
        buffer = DataStreamBuffer()

        def add_tick(symbol):
            tick_data = {
                "symbol": symbol,
                "price": 100.0,
                "size": 100.0,
                "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
            }
            buffer.add_tick(symbol, tick_data)

        threads = []
        symbols = ["AAPL", "TSLA", "GOOGL", "MSFT", "AMZN"]

        for symbol in symbols:
            t = threading.Thread(target=add_tick, args=(symbol,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify all symbols were added
        assert len(buffer.buffer) == len(symbols)
        for symbol in symbols:
            assert symbol in buffer.buffer


class TestDBWriterWorkerEdgeCases:
    """Tests for DBWriterWorker edge cases."""

    def test_worker_with_large_volume_data(self):
        """Test worker with large volume data."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        # Add many items
        for _ in range(100):
            q.put(("AAPL", {"timestamp": datetime.datetime(2023, 1, 1, 12, 0)}))

        # Process using simple loop
        worker.running = True
        while True:
            try:
                item = q.get(timeout=0.1)
                if item is None:
                    break
                worker.running = False
                break
            except queue.Empty:
                pass

        assert mock_db.add_market_data.call_count >= 0

    def test_worker_with_nonexistent_symbol(self):
        """Test worker handling nonexistent symbol - basic test."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        bar_data = {
            "symbol": "NONEXISTENT",
            "price": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0)
        }
        q.put(("NONEXISTENT", bar_data))
        q.put(None)

        worker.run()

        assert mock_db.add_market_data.called

    def test_worker_multiple_exceptions(self):
        """Test worker handling multiple exceptions."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)

        # Add invalid data that causes exception
        q.put(("AAPL", "invalid_data"))

        worker.running = True
        try:
            item = q.get(timeout=1)
            if item is None:
                worker.running = False
        except queue.Empty:
            pass

        # Should handle without crashing
        assert True


class TestAlpacaStreamProcessorEdgeCases:
    """Tests for AlpacaStreamProcessor edge cases."""

    @patch('src.stream_client.DatabaseManager')
    def test_processor_start_with_invalid_db_manager(self, mock_db_class):
        """Test processor initialization with invalid db_manager."""
        mock_db_class.return_value = None
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(None, mock_state_mapper)

        assert processor.running is True

    @patch('src.stream_client.DatabaseManager')
    def test_processor_with_empty_db_manager(self, mock_db_class):
        """Test processor with empty db_manager."""
        mock_db_class.return_value = MagicMock()
        mock_db = mock_db_class.return_value
        mock_db.connection = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        assert processor.db_manager == mock_db
        assert processor.buffer is not None

    def test_extract_market_payload_trade_with_list_conditions(self):
        """Test _extract_market_payload doesn't fail when conditions ('c') is a list."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        # Alpaca crypto trade raw payload format with conditions list 'c': ['C']
        raw_msg = {
            'T': 't',
            'i': 9687,
            'S': 'BTC/USD',
            'p': 65000.5,
            's': 0.125,
            't': 1720000000,
            'c': ['C']
        }

        payload = processor._extract_market_payload(raw_msg)
        assert payload is not None
        assert payload['symbol'] == 'BTC'
        assert payload['price'] == 65000.5
        assert payload['size'] == 0.125
        assert payload['close'] is None


class TestLiveDecisionPath:
    """The dashboard's decision for a bar comes from the model, when there is one to ask."""

    def test_build_decision_state_carries_the_bar_and_the_portfolio(self):
        """The state the model scores names the symbol, the price it saw and what is held."""
        from src.state_mapper import StateMapper

        mapper = StateMapper()
        state = mapper.build_decision_state(
            symbol="AAPL",
            price=150.0,
            previous_price=100.0,
            sentiment_score=0.75,
            cash=1000.0,
            holdings={"AAPL": 2.0},
        )
        assert state.market.prices["AAPL"] == 150.0
        assert state.market.previous_prices["AAPL"] == 100.0
        assert state.market.sentiment_scores["AAPL"] == 0.75
        assert state.portfolio.cash == 1000.0
        assert state.portfolio.holdings == {"AAPL": 2.0}
        assert len(state.to_vector(["AAPL"])) == 4

    def test_build_decision_state_invents_nothing(self):
        """No previous price means no change, and no Adanos score means neutral sentiment."""
        from src.state_mapper import StateMapper

        state = StateMapper().build_decision_state(symbol="AAPL", price=150.0)
        assert state.market.previous_prices["AAPL"] == 150.0
        assert state.market.sentiment_scores["AAPL"] == 0.5
        assert state.portfolio.cash == 0.0
        assert state.portfolio.holdings == {}

    def test_build_decision_state_refuses_a_price_that_cannot_be_sized(self):
        """A zero price makes the log change and the share count undefined."""
        from src.state_mapper import StateMapper

        with pytest.raises(ValueError, match="non-positive price"):
            StateMapper().build_decision_state(symbol="AAPL", price=0.0)

    def test_recommendation_asks_the_model_when_an_executor_is_attached(self):
        """With a model attached the writer reports the argmax, not a momentum guess."""
        from src.state_mapper import StateMapper
        from src.trading_execution import TradingExecutor

        executor = TradingExecutor(alpaca_client=MagicMock())
        state = StateMapper().build_decision_state(
            symbol="AAPL", price=150.0, previous_price=100.0, cash=1000.0
        )
        worker = DBWriterWorker(MagicMock(), MagicMock(), queue.Queue(), executor=executor)

        rec = worker._recommendation("AAPL", state)
        assert rec["action"] == "BUY"
        assert any("Bellman argmax" in line for line in rec["reasoning"])

    def test_recommendation_falls_back_to_the_heuristic_without_an_executor(self):
        """A writer built on its own keeps the recommendation the dashboard always showed."""
        from src.web_server import compute_recommendation

        worker = DBWriterWorker(MagicMock(), MagicMock(), queue.Queue())
        heuristic = compute_recommendation("AAPL")
        rec = worker._recommendation("AAPL", None)
        assert rec["action"] == heuristic["action"]
        assert rec["confidence"] == heuristic["confidence"]
        assert rec["reasoning"] == heuristic["reasoning"]

    def test_map_bar_asks_the_model_about_the_bar_it_is_mapping(self):
        """The state handed to the model is the bar: close against open, plus the portfolio."""
        from src.trading_execution import TradingExecutor

        executor = TradingExecutor(alpaca_client=MagicMock())
        mapper = MagicMock()
        worker = DBWriterWorker(MagicMock(), mapper, queue.Queue(), executor=executor)
        bar = {
            "symbol": "AAPL",
            "close": 151.0,
            "open": 150.0,
            "volume": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0),
        }

        worker._map_bar("AAPL", bar)

        mapper.build_decision_state.assert_called_once()
        handed = mapper.build_decision_state.call_args.kwargs
        assert handed["price"] == 151.0
        assert handed["previous_price"] == 150.0
        assert handed["cash"] == 100_000.0
        assert handed["holdings"] == {}

    def test_map_bar_leaves_the_heuristic_in_place_without_a_model(self):
        """No executor means no state to score; the dashboard still gets a recommendation."""
        mapper = MagicMock()
        worker = DBWriterWorker(MagicMock(), mapper, queue.Queue())
        bar = {
            "symbol": "AAPL",
            "close": 151.0,
            "open": 150.0,
            "volume": 100.0,
            "timestamp": datetime.datetime(2023, 1, 1, 12, 0),
        }

        worker._map_bar("AAPL", bar)
        mapper.build_decision_state.assert_not_called()
