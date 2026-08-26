import unittest
from src.scraper import DataStreamBuffer
import datetime

class TestProcessing(unittest.TestCase):
    def test_buffer_logic(self):
        buffer = DataStreamBuffer()
        ts1 = datetime.datetime(2023, 1, 1, 12, 0, 5)
        ts2 = datetime.datetime(2023, 1, 1, 12, 0, 10)
    
        # Expected data payloads
        tick1_data = {"price": 150.0, "size": 100, "timestamp": ts1}
        tick2_data = {"price": 151.0, "size": 200, "timestamp": ts2}
    
        # Ensure correct number of arguments are passed to add_tick(symbol, tick)
        buffer.add_tick("AAPL", tick1_data)
        buffer.add_tick("AAPL", tick2_data)
    
        # Verify proper retrieval from the same minute bucket
        self.assertEqual(len(buffer.buffer["AAPL"]["2023-01-01 12:00"]), 2)
    
        # Simulation: check if bars are ready for retrieval at 12:01
        now = datetime.datetime(2023, 1, 1, 12, 1)
        results = buffer.get_ready_bars(["AAPL"])

        self.assertIn("AAPL", results)
        self.assertEqual(results["AAPL"]["timestamp"], "2023-01-01 12:00")
        self.assertEqual(results["AAPL"]["open"], 150.0)
        self.assertEqual(results["AAPL"]["high"], 151.0)
        self.assertEqual(results["AAPL"]["low"], 150.0)
        self.assertEqual(results["AAPL"]["close"], 151.0)
        self.assertEqual(results["AAPL"]["volume"], 300)

    def test_multiple_symbols(self):
        buffer = DataStreamBuffer()
        ts1 = datetime.datetime(2023, 1, 1, 12, 0, 5)
        tick1_data = {"price": 100.0, "size": 10}
        tick1_data["timestamp"] = ts1
        
        buffer.add_tick("AAPL", tick1_data)
        buffer.add_tick("GOOG", tick1_data)
        
        self.assertIn("AAPL", buffer.buffer)
        self.assertIn("GOOG", buffer.buffer)
        self.assertEqual(len(buffer.buffer["AAPL"]), 1)
        self.assertEqual(len(buffer.buffer["GOOG"]), 1)

if __name__ == "__main__":
    unittest.main()

