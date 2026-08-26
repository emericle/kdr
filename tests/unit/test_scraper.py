import unittest
from unittest.mock import MagicMock, patch
import datetime
from queue import Queue
from src.scraper import run_worker_threads, DataStreamBuffer

class TestScraper(unittest.TestCase):
    def test_buffer_add_tick(self):
        buffer = DataStreamBuffer()
        ts = datetime.datetime(2023, 1, 1, 12, 0, 0)
        buffer.add_tick("AAPL", {"timestamp": ts, "price": 150.0, "size": 100})
        
        # Test if it's in the buffer
        self.assertIn("AAPL", buffer.buffer)
        self.assertIn("2023-01-01 12:00", buffer.buffer["AAPL"])
        
    def test_buffer_get_ready_bars(self):
        buffer = DataStreamBuffer()
        # Simulate a past minute
        past_ts = datetime.datetime(2023, 1, 1, 11, 59, 0)
        # Current minute would be 12:00
    
        buffer.add_tick("AAPL", {"timestamp": past_ts, "price": 150.0, "size": 100})
    
        # Mocking current time to be 12:01
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.side_effect = lambda: datetime.datetime(2023, 1, 1, 12, 1)
            
            ready_bars = buffer.get_ready_bars(["AAPL"])
    
            self.assertIn("AAPL", ready_bars)
            self.assertEqual(ready_bars["AAPL"]["timestamp"], "2023-01-01 11:59")
            self.assertEqual(ready_bars["AAPL"]["open"], 150.0)

    @patch('src.scraper.DatabaseManager')
    @patch('src.scraper.validate_configs')
    def test_run_worker_threads(self, mock_validate, mock_db_manager):
        # Setup mock database manager
        mock_instance = mock_db_manager.return_value
        mock_instance.connection.init_db.return_value = True
        
        # Simulate the execution of run_worker_threads
        # This will run the worker thread which consumes from the queue
        try:
            run_worker_threads()
        except Exception as e:
            # The worker might be running in a thread, ensure it doesn't hang
            # We'll verify the mock calls
            pass

        # Verify that init_db was called
        mock_instance.connection.init_db.assert_called()

if __name__ == '__main__':
    unittest.main()
