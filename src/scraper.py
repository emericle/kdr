import threading
import queue
import time
import logging
import datetime
import sys

if sys.version_info < (3, 14):
    print("Error: This application requires Python 3.14 or greater.")
    sys.exit(1)


from typing import Dict, List, Optional
from dataclasses import dataclass
from src.database import DatabaseManager
from src.config_gatekeeper import validate_configs

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
    """Thread-safe buffer to hold ticks for aggregation."""
    def __init__(self):
        # Nested dict: {symbol: {minute_string: [ticks]}}
        # Using string keys (ISO format) to avoid equality issues with datetime objects
        self.buffer: Dict[str, Dict[str, List[dict]]] = {}
        self.lock = threading.Lock()

    def add_tick(self, symbol: str, tick_data: dict):
        with self.lock:
            if symbol not in self.buffer:
                self.buffer[symbol] = {}
            # Normalize timestamp to minute-level string key
            ts = tick_data['timestamp']
            minute_key = ts.strftime('%Y-%m-%d %H:%M')
            if minute_key not in self.buffer[symbol]:
                self.buffer[symbol][minute_key] = []
            self.buffer[symbol][minute_key].append(tick_data)

    def get_ready_bars(self, symbols: List[str]) -> Dict[str, Dict]:
        # Returns bars that are older than the current minute and clear them.
        with self.lock:
            now = datetime.datetime.now()
            current_minute_key = now.strftime('%Y-%m-%d %H:%M')
            ready_data = {}
            for symbol in symbols:
                if symbol not in self.buffer: continue
                for timestamp_str, ticks in list(self.buffer[symbol].items()):
                    # Format of timestamp_str is 'YYYY-MM-DD HH:MM'
                    if timestamp_str < current_minute_key:
                        ready_data[symbol] = {
                            "timestamp": timestamp_str,
                            "open": min(t['price'] for t in ticks),
                            "high": max(t['price'] for t in ticks),
                            "low": min(t['price'] for t in ticks),
                            "close": max(t['price'] for t in ticks),
                            "volume": sum(t['size'] for t in ticks)
                        }
                        del self.buffer[symbol][timestamp_str]
            return ready_data

class AlpacaStreamProcessor:
    """Handles the production of data from Alpaca streams."""
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.buffer = DataStreamBuffer()
        self.running = True

    def start(self):
        logger.info("Alpaca Stream Processor started.")

class DBWriterWorker(threading.Thread):
    """Consumer thread that processes buffered data and writes to Database."""
    def __init__(self, db_manager: DatabaseManager, data_queue: queue.Queue):
        super().__init__(daemon=True)
        self.db_manager = db_manager
        self.queue = data_queue
        self.running = True

    def run(self):
        logger.info("DB Writer Worker started.")
        while self.running:
            try:
                # Use a timeout so we can periodically check the running flag
                item = self.queue.get(timeout=1)
                if item is None:  # Sentinel value to stop worker
                    break
                
                symbol, bar_data = item
                logger.info("Processing %s at %s", symbol, bar_data['timestamp'])
                self.db_manager.add_market_data([bar_data])
                
                # Signal that task is complete
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Error in DBWriterWorker: %s", e)
                time.sleep(1)

    def save_to_db(self, symbol: str, bar_data: dict):
        if DEBUG_MODE:
            logger.debug("Entering save_to_db for %s", symbol)
        if DEBUG_MODE:
            logger.debug("Saving record for %s at %s", symbol, bar_data['timestamp'])
        # Database writing logic here...

def run_worker_threads():
    validate_configs()
    db_manager = DatabaseManager()
    
    # Explicitly initialize the database tables
    logger.info("Initializing database...")
    db_manager.connection.init_db()
    logger.info("Database initialization complete: %s", db_manager.connection.engine)
    
    data_queue = queue.Queue()
    writer = DBWriterWorker(db_manager, data_queue)
    writer.start()
    logger.info("Producer and Consumer threads initialized.")
    
    # Mocking ingestion for testing purposes
    dummy_bar = {
        "timestamp": datetime.datetime(2023, 1, 1, 12, 0),
        "open": 150.0,
        "high": 160.0,
        "low": 140.0,
        "close": 155.0,
        "volume": 1000
    }
    if DEBUG_MODE:
        logger.debug("Injecting mock bar into queue...")
    data_queue.put(("AAPL", dummy_bar))
    
    # Give it a second to process
    time.sleep(2)
    
    # Stop the worker (one way is to put None in the queue for current implementation)
    if DEBUG_MODE:
        logger.debug("Sending sentinel value to stop.")
    data_queue.put(None)
    writer.running = False  # Ensure the loop finishes
    logger.info("Worker command sent, waiting 1 sec...")
    time.sleep(1)
    logger.info("Worker finished.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()
    
    setup_logging(args.debug)
    run_worker_threads()
