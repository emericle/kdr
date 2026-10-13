"""Command line entry point for the KDR data ingestion pipeline.

The pipeline itself lives in four modules, and this one only composes them:

  :mod:`src.data_buffer`    minute-bucketed tick aggregation
  :mod:`src.stream_client`  the Alpaca stream client
  :mod:`src/db_writer`      queue draining, the DB writer thread, and the
                            thread orchestration :func:`run_worker_threads`
  :mod:`src.logging_config` the one logging configuration

Run it with::

    python src/scraper.py --debug

``--symbols`` and ``--duration`` narrow a run; ``--no-continuous`` makes it
process what is already queued and exit.
"""
import argparse
import logging
import os
import signal
import sys
import threading

if sys.version_info < (3, 13):
    print("Error: This application requires Python 3.13 or greater.")
    sys.exit(1)

# Ensure project root is in sys.path when executed directly
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config_gatekeeper import validate_configs
from src.data_buffer import DataStreamBuffer, Tick
from src.db_writer import DBWriterWorker, run_worker_threads
from src.logging_config import LOG_BACKUP_COUNT, LOG_FILENAME, LOG_MAX_BYTES, configure_logging
from src.stream_client import AlpacaStreamProcessor
from src.symbols import DEFAULT_SYMBOLS, normalize_symbols

# This module is the pipeline's entry point and its public import surface at once:
# the test packages and ``bin/`` scripts still say ``from src.scraper import
# AlpacaStreamProcessor``, so the names above are re-exports of the split modules
# rather than unused aliases.
__all__ = [
    "AlpacaStreamProcessor",
    "DataStreamBuffer",
    "DBWriterWorker",
    "Tick",
    "DEFAULT_SYMBOLS",
    "normalize_symbols",
    "parse_args",
    "run_worker_threads",
    "setup_logging",
    "validate_configs",
]

logger = logging.getLogger("DataIngestion")


def setup_logging(debug: bool = False):
    """Install the one logging configuration and set the pipeline's level.

    Called from the entry point below, not at import: importing a module used to
    reconfigure global logging and open a log file, which made a test run or a
    notebook write into the pipeline's log.
    """
    configure_logging(verbose=debug)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.info("Starting KDR Data Ingestion Pipeline")
    logger.info(
        "Log file: %s (rotates at %s bytes, %s kept)",
        LOG_FILENAME,
        LOG_MAX_BYTES,
        LOG_BACKUP_COUNT,
    )


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
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
        help=f'Comma-separated list of symbols (default: {",".join(DEFAULT_SYMBOLS)})'
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
