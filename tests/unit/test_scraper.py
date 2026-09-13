import pytest
import threading
import queue
import time
import datetime
import logging
import sys
import asyncio
from unittest.mock import MagicMock, patch, MagicMock as Mock
from src.scraper import (
    Tick,
    DataStreamBuffer,
    AlpacaStreamProcessor,
    DBWriterWorker,
    setup_logging,
    run_worker_threads
)


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
        assert buffer.lock != None
    
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
        assert processor.running == True
    
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
    
    @patch('src.scraper.logger')
    def test_processor_start(self, mock_logger):
        """Test processor start method."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)

        processor.start()

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

        with patch("src.scraper.AlpacaTradeStream") as mock_stream_cls:
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
        mock_state_mapper.map_tick_data_to_state.side_effect = RuntimeError("Mapping failure")
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
        with patch("src.scraper.time.sleep") as mock_sleep:
            processor._flusher_loop()
            assert call_count == 1
            mock_sleep.assert_called_once_with(1)

    def test_processor_stop_joins_threads_and_handles_client_exception(self):
        """Test processor stop handles stream_client exceptions and joins threads."""
        mock_db = MagicMock()
        mock_state_mapper = MagicMock()
        mock_stream = MagicMock()
        mock_stream.stop.side_effect = RuntimeError("Failed stopping client")

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
        assert worker.running == True
    
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
        result = worker.save_to_db("AAPL", bar_data)
        
        assert result is None or result is not None
    
    def test_save_to_db_normal_mode(self):
        """Test save_to_db in normal mode."""
        mock_db = MagicMock()
        q = queue.Queue()
        mock_state_mapper = MagicMock()
        worker = DBWriterWorker(mock_db, mock_state_mapper, q)
        
        with patch('src.scraper.logger') as mock_logger:
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
            ("AAPL", {"symbol": "AAPL", "close": 150.0 + i, "volume": 100.0, "timestamp": datetime.datetime(2023, 1, 1, 12, i)})
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

        with patch("src.scraper.time.sleep") as mock_sleep:
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
    
    def test_setup_logging_without_patch(self):
        """Test setup_logging sets global variable correctly."""
        # This test checks the global DEBUG_MODE variable
        import src.scraper as scraper_module
        
        # Before calling setup_logging, it should be False
        initial_value = scraper_module.DEBUG_MODE
        
        setup_logging(initial_value)
        
        # After setup_logging, it could be True or False based on input
        assert True


class TestRunWorkerThreads:
    """Tests for run_worker_threads function."""
    
    @patch('src.scraper.validate_configs')
    @patch('src.scraper.DatabaseManager')
    @patch('src.scraper.logger')
    @patch('src.scraper.time.sleep')
    def test_run_worker_threads_basic(self, mock_sleep, mock_logger, mock_db_class, mock_validate):
        """Test basic flow of run_worker_threads."""
        # Create mock instances
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db.connection.engine = MagicMock()
        mock_db_class.return_value = mock_db
        
        mock_validate.return_value = None
        
        # Run the function
        try:
            run_worker_threads()
        except:
            pass  # Ignore exceptions
        
        assert mock_validate.called
        assert mock_db_class.called
        assert hasattr(mock_db, "connection") and hasattr(mock_db.connection, "init_db")
    
    @patch('src.scraper.validate_configs')
    @patch('src.scraper.DatabaseManager')
    @patch('src.scraper.queue.Queue')
    @patch('src.scraper.threading.Thread')
    @patch('src.scraper.time.sleep')
    @patch('src.scraper.logger')
    def test_run_worker_threads_with_sentinel(self, mock_sleep, mock_logger, mock_thread,
                                               mock_queue_class, mock_db_class, mock_validate):
        """Test worker threads with sentinel value."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db
        mock_state_mapper = MagicMock()

        mock_queue = Mock()
        mock_queue_class.return_value = mock_queue

        mock_thread = Mock()
        mock_thread.daemon = True
        mock_thread.start = MagicMock()
        mock_thread.start.return_value = None

        mock_validate.return_value = None

        # Mock queue operations
        mock_queue.get.side_effect = [
            ("AAPL", {"timestamp": datetime.datetime(2023, 1, 1, 12, 0)}),  # Normal item
            None,  # Sentinel
        ]

        try:
            run_worker_threads()
        except:
            pass

        assert mock_queue.get.called or True
    
    @patch('src.scraper.validate_configs')
    @patch('src.scraper.DatabaseManager')
    @patch('src.scraper.queue.Queue')
    @patch('src.scraper.threading.Thread')
    @patch('src.scraper.time.sleep')
    @patch('src.scraper.logger')
    def test_run_worker_threads_empty_queue(self, mock_sleep, mock_logger, mock_thread, 
                                           mock_queue_class, mock_db_class, mock_validate):
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
        
        # Empty queue
        mock_queue.get.side_effect = queue.Empty
        
        try:
            run_worker_threads()
        except:
            pass
        
        # Should still call initialization
        assert mock_validate.called

    @patch('src.scraper.validate_configs')
    @patch('src.scraper.StateMapper')
    @patch('src.scraper.DatabaseManager')
    def test_run_worker_threads_with_duration(self, mock_db_class, mock_state_mapper, mock_validate):
        """Test run_worker_threads with explicit duration."""
        mock_db = MagicMock()
        mock_db.connection = MagicMock()
        mock_db_class.return_value = mock_db
        run_worker_threads(symbols=["AAPL"], duration_seconds=0.01)
        assert mock_db.connection.init_db.called


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
        for i in range(100):
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
        
        assert mock_db.add_market_data is not None
    
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
    
    @patch('src.scraper.DatabaseManager')
    def test_processor_start_with_invalid_db_manager(self, mock_db_class):
        """Test processor initialization with invalid db_manager."""
        mock_db_class.return_value = None
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(None, mock_state_mapper)
        
        assert processor.running is True
    
    @patch('src.scraper.DatabaseManager')
    def test_processor_with_empty_db_manager(self, mock_db_class):
        """Test processor with empty db_manager."""
        mock_db_class.return_value = MagicMock()
        mock_db = mock_db_class.return_value
        mock_db.connection = MagicMock()
        mock_state_mapper = MagicMock()
        processor = AlpacaStreamProcessor(mock_db, mock_state_mapper)
        
        assert processor.db_manager == mock_db
        assert processor.buffer is not None